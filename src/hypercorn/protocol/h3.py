"""
H3 Protocol implementation with native WebTransport support.

This module provides HTTP/3 protocol handling including:
- Standard HTTP/3 requests
- WebSocket over HTTP/3
- WebTransport sessions (when enabled)
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Dict, Optional

from aioquic.h3.connection import H3Connection
from aioquic.h3.events import (
    DataReceived,
    HeadersReceived,
    WebTransportStreamDataReceived,
    DatagramReceived,
)
from aioquic.h3.exceptions import NoAvailablePushIDError
from aioquic.quic.connection import QuicConnection
from aioquic.quic.events import QuicEvent

from .events import (
    Body,
    Data,
    EndBody,
    EndData,
    Event as StreamEvent,
    InformationalResponse,
    Request,
    Response,
    StreamClosed,
    Trailers,
)
from .http_stream import HTTPStream
from .ws_stream import WSStream
from ..config import Config
from ..typing import AppWrapper, ConnectionState, TaskGroup, WorkerContext
from ..utils import filter_pseudo_headers


class WebTransportSession:
    """Manages a WebTransport session.
    
    This class handles the ASGI lifecycle for a WebTransport connection,
    including:
    - Generating the proper 'webtransport' ASGI scope
    - Managing the receive queue for incoming data
    - Handling send operations (bypassing H3 state machine for streams)
    """
    
    def __init__(
        self,
        app: AppWrapper,
        config: Config,
        context: WorkerContext,
        task_group: TaskGroup,
        client: tuple[str, int] | None,
        server: tuple[str, int] | None,
        send_callback: Callable[[], Awaitable[None]],
        session_id: int,
        h3_connection: H3Connection,
    ) -> None:
        self.app = app
        self.config = config
        self.context = context
        self.task_group = task_group
        self.client = client
        self.server = server
        self.send_callback = send_callback
        self.session_id = session_id
        self.h3_connection = h3_connection
        self._accepted = False
        self._closed = False
        self._receive_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._task: Optional[asyncio.Task[None]] = None
        self._scope: Dict[str, Any] = {}
    
    async def handle_headers(
        self, headers: list[tuple[bytes, bytes]], path: bytes
    ) -> None:
        """Handle the initial CONNECT request headers and start the ASGI app."""
        # Parse query string from path
        path_str = path.decode("ascii") if path else "/"
        if "?" in path_str:
            path_only, query_string = path_str.split("?", 1)
            query_bytes = query_string.encode("ascii")
        else:
            path_only = path_str
            query_bytes = b""
        
        # Build the ASGI scope
        self._scope = {
            "type": "webtransport",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "3",
            "scheme": "https",
            "path": path_only,
            "raw_path": path,
            "query_string": query_bytes,
            "root_path": self.config.root_path,
            "headers": headers,
            "server": self.server,
            "client": self.client,
            "extensions": {
                "webtransport": {
                    "session_id": self.session_id,
                }
            },
        }
        
        # Start the ASGI app task
        self._task = self.task_group.spawn(self._run_app)
    
    async def _run_app(self) -> None:
        """Run the ASGI application."""
        try:
            # Hypercorn's ASGIWrapper expects 5 arguments:
            # (scope, receive, send, sync_spawn, call_soon)
            async def sync_spawn(func: Callable[[], Any]) -> Any:
                """Run a sync function in a thread pool."""
                return func()
            
            def call_soon(func: Callable[[], None]) -> None:
                """Schedule a function to run soon."""
                asyncio.get_event_loop().call_soon(func)
            
            await self.app(
                self._scope, self._receive, self._send, sync_spawn, call_soon
            )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            # Log the error but don't crash the server
            import traceback
            traceback.print_exc()
    
    async def _receive(self) -> Dict[str, Any]:
        """ASGI receive callable."""
        if not self._accepted:
            # First call - send connect event
            self._accepted = True
            return {"type": "webtransport.connect"}
        
        # Wait for incoming data
        return await self._receive_queue.get()
    
    async def _send(self, message: Dict[str, Any]) -> None:
        """ASGI send callable.
        
        Handles the following message types:
        - webtransport.accept: Accept the connection (200 OK)
        - webtransport.close: Close the session
        - webtransport.datagram.send: Send unreliable datagram
        - webtransport.stream.send: Send on a QUIC stream (bypasses H3)
        """
        msg_type = message.get("type", "")
        
        if msg_type == "webtransport.accept":
            # Send 200 OK response to accept the WebTransport session
            self.h3_connection.send_headers(
                stream_id=self.session_id,
                headers=[
                    (b":status", b"200"),
                    (b"sec-webtransport-http3-draft", b"draft02"),
                ],
            )
            await self.send_callback()
            
        elif msg_type == "webtransport.close":
            # Close the session
            self._closed = True
            if not self._accepted:
                # Send error response if not accepted
                self.h3_connection.send_headers(
                    stream_id=self.session_id,
                    headers=[(b":status", b"403")],
                    end_stream=True,
                )
                await self.send_callback()
            
        elif msg_type == "webtransport.datagram.send":
            # Send unreliable datagram via H3
            data = message.get("data", b"")
            self.h3_connection.send_datagram(self.session_id, data)
            await self.send_callback()
            
        elif msg_type == "webtransport.stream.send":
            # Send on a specific QUIC stream
            # CRITICAL: Bypass H3Connection.send_data() because it enforces
            # the HTTP/3 state machine which doesn't apply to raw WebTransport streams.
            # Use the underlying QuicConnection directly.
            stream_id = message.get("stream_id")
            data = message.get("data", b"")
            # Support both 'end_stream' and 'finish' keys for compatibility
            end_stream = message.get("end_stream", message.get("finish", False))
            
            if stream_id is not None:
                try:
                    self.h3_connection._quic.send_stream_data(
                        stream_id, data, end_stream
                    )
                    await self.send_callback()
                except (ValueError, AssertionError):
                    # Stream was already closed or reset by peer
                    pass
    
    async def handle_datagram(self, data: bytes) -> None:
        """Handle incoming datagram."""
        await self._receive_queue.put({
            "type": "webtransport.datagram.receive",
            "data": data,
        })
    
    async def handle_stream_data(
        self, stream_id: int, data: bytes, end_stream: bool
    ) -> None:
        """Handle incoming stream data."""
        await self._receive_queue.put({
            "type": "webtransport.stream.receive",
            "stream_id": stream_id,
            "data": data,
            "more_body": not end_stream,
        })
    
    async def close(self) -> None:
        """Close the session and notify the ASGI app."""
        self._closed = True
        await self._receive_queue.put({"type": "webtransport.disconnect"})
        if self._task:
            self._task.cancel()


class H3Protocol:
    """HTTP/3 protocol handler with WebTransport support.
    
    This class handles HTTP/3 connections including:
    - Standard HTTP/3 requests (via HTTPStream)
    - WebSocket over HTTP/3 (via WSStream)
    - WebTransport sessions (when config.enable_webtransport is True)
    """
    
    def __init__(
        self,
        app: AppWrapper,
        config: Config,
        context: WorkerContext,
        task_group: TaskGroup,
        state: ConnectionState,
        client: tuple[str, int] | None,
        server: tuple[str, int] | None,
        quic: QuicConnection,
        send: Callable[[], Awaitable[None]],
    ) -> None:
        self.app = app
        self.client = client
        self.config = config
        self.context = context
        # Enable WebTransport in H3Connection if configured
        self.connection = H3Connection(
            quic, enable_webtransport=config.enable_webtransport
        )
        self.send = send
        self.server = server
        self.streams: dict[int, HTTPStream | WSStream] = {}
        self.webtransport_sessions: dict[int, WebTransportSession] = {}
        self.task_group = task_group
        self.state = state

    async def handle(self, quic_event: QuicEvent) -> None:
        """Handle incoming QUIC events."""
        for event in self.connection.handle_event(quic_event):
            if isinstance(event, HeadersReceived):
                if not self.context.terminated.is_set():
                    await self._create_stream(event)
                    if event.stream_ended:
                        stream = self.streams.get(event.stream_id)
                        if stream:
                            await stream.handle(
                                EndBody(stream_id=event.stream_id)
                            )
                            
            elif isinstance(event, DataReceived):
                stream = self.streams.get(event.stream_id)
                if stream:
                    await stream.handle(
                        Body(stream_id=event.stream_id, data=event.data)
                    )
                    if event.stream_ended:
                        await stream.handle(
                            EndBody(stream_id=event.stream_id)
                        )
                        
            elif isinstance(event, WebTransportStreamDataReceived):
                # WebTransport stream data - route to the appropriate session
                session = self.webtransport_sessions.get(event.session_id)
                if session:
                    await session.handle_stream_data(
                        event.stream_id,
                        event.data,
                        event.stream_ended,
                    )
                    
            elif isinstance(event, DatagramReceived):
                # WebTransport datagram - find the session
                # The session_id for datagrams comes from the flow_id
                # For now, iterate sessions (usually only 1 per connection)
                for session in self.webtransport_sessions.values():
                    await session.handle_datagram(event.data)
                    break

    async def stream_send(self, event: StreamEvent) -> None:
        """Send events to HTTP/3 or WebSocket streams."""
        if isinstance(event, (InformationalResponse, Response)):
            self.connection.send_headers(
                event.stream_id,
                [(b":status", b"%d" % event.status_code)]
                + event.headers
                + self.config.response_headers("h3"),
            )
            await self.send()
        elif isinstance(event, (Body, Data)):
            self.connection.send_data(event.stream_id, event.data, False)
            await self.send()
        elif isinstance(event, (EndBody, EndData)):
            try:
                self.connection.send_data(event.stream_id, b"", True)
                await self.send()
            except AssertionError:
                # Stream was reset by peer
                pass
        elif isinstance(event, Trailers):
            self.connection.send_headers(event.stream_id, event.headers)
            await self.send()
        elif isinstance(event, StreamClosed):
            self.streams.pop(event.stream_id, None)
        elif isinstance(event, Request):
            await self._create_server_push(
                event.stream_id, event.raw_path, event.headers
            )

    async def _create_stream(self, request: HeadersReceived) -> None:
        """Create a stream handler based on the request type.
        
        Determines if the request is:
        - WebTransport CONNECT (protocol: webtransport)
        - WebSocket CONNECT
        - Standard HTTP request
        """
        method = None
        raw_path = b"/"
        protocol = None
        
        for name, value in request.headers:
            if name == b":method":
                method = value.decode("ascii").upper()
            elif name == b":path":
                raw_path = value
            elif name == b":protocol":
                protocol = value.decode("ascii").lower()

        # Check if this is a WebTransport CONNECT request
        if (
            method == "CONNECT"
            and protocol == "webtransport"
            and self.config.enable_webtransport
        ):
            # Create a WebTransport session
            session = WebTransportSession(
                self.app,
                self.config,
                self.context,
                self.task_group,
                self.client,
                self.server,
                self.send,
                request.stream_id,
                self.connection,
            )
            self.webtransport_sessions[request.stream_id] = session
            await session.handle_headers(
                filter_pseudo_headers(request.headers),
                raw_path,
            )
            await self.context.mark_request()
            return
            
        # Standard HTTP/3 or WebSocket over HTTP/3
        if method == "CONNECT":
            # WebSocket upgrade
            self.streams[request.stream_id] = WSStream(
                self.app,
                self.config,
                self.context,
                self.task_group,
                True,
                self.client,
                self.server,
                self.stream_send,
                request.stream_id,
            )
        else:
            # Regular HTTP request
            self.streams[request.stream_id] = HTTPStream(
                self.app,
                self.config,
                self.context,
                self.task_group,
                True,
                self.client,
                self.server,
                self.stream_send,
                request.stream_id,
            )

        await self.streams[request.stream_id].handle(
            Request(
                stream_id=request.stream_id,
                headers=filter_pseudo_headers(request.headers),
                http_version="3",
                method=method,
                raw_path=raw_path,
                state=self.state,
            )
        )
        await self.context.mark_request()

    async def _create_server_push(
        self, stream_id: int, path: bytes, headers: list[tuple[bytes, bytes]]
    ) -> None:
        """Create a server push stream."""
        request_headers = [(b":method", b"GET"), (b":path", path)]
        request_headers.extend(headers)
        request_headers.extend(self.config.response_headers("h3"))
        try:
            push_stream_id = self.connection.send_push_promise(
                stream_id=stream_id, headers=request_headers
            )
        except NoAvailablePushIDError:
            # Client does not accept push promises or we are trying to
            # push on a push promises request.
            pass
        else:
            event = HeadersReceived(
                stream_id=push_stream_id,
                stream_ended=True,
                headers=request_headers,
            )
            await self._create_stream(event)
            stream = self.streams.get(event.stream_id)
            if stream:
                await stream.handle(EndBody(stream_id=event.stream_id))
