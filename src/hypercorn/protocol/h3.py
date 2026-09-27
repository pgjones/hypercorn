from __future__ import annotations

from collections.abc import Awaitable, Callable

from aioquic.h3.connection import (
    H3Connection,
    HeadersState,
    FrameType,
    FrameUnexpected,
    ProtocolError,
    encode_frame
)
from aioquic.h3.events import DataReceived, HeadersReceived , Headers
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

class H3Protocol:
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
        self.connection = H3Wrapper(quic)
        self.send = send
        self.server = server
        self.streams: dict[int, HTTPStream | WSStream] = {}
        self.task_group = task_group
        self.state = state

    async def handle(self, quic_event: QuicEvent) -> None:
        for event in self.connection.handle_event(quic_event):
            if isinstance(event, HeadersReceived):
                if not self.context.terminated.is_set():
                    await self._create_stream(event)
                    if event.stream_ended:
                        await self.streams[event.stream_id].handle(
                            EndBody(stream_id=event.stream_id)
                        )
            elif isinstance(event, DataReceived):
                await self.streams[event.stream_id].handle(
                    Body(stream_id=event.stream_id, data=event.data)
                )
                if event.stream_ended:
                    await self.streams[event.stream_id].handle(EndBody(stream_id=event.stream_id))

    async def stream_send(self, event: StreamEvent) -> None:
        if isinstance(event, (InformationalResponse, Response)):
            is_informational = isinstance(event,InformationalResponse)
            self.connection.send_headers(
                event.stream_id,
                [(b":status", b"%d" % event.status_code)]
                + event.headers
                + self.config.response_headers("h3"),
                is_informational=is_informational
            )
            await self.send()
        elif isinstance(event, (Body, Data)):
            self.connection.send_data(event.stream_id, event.data, False)
            await self.send()
        elif isinstance(event, (EndBody, EndData)):
            self.connection.send_data(event.stream_id, b"", True)
            await self.send()
        elif isinstance(event, Trailers):
            self.connection.send_headers(event.stream_id, event.headers)
            await self.send()
        elif isinstance(event, StreamClosed):
            self.streams.pop(event.stream_id, None)
        elif isinstance(event, Request):
            await self._create_server_push(event.stream_id, event.raw_path, event.headers)

    async def _create_stream(self, request: HeadersReceived) -> None:
        for name, value in request.headers:
            if name == b":method":
                method = value.decode("ascii").upper()
            elif name == b":path":
                raw_path = value

        if method == "CONNECT":
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
                stream_id=push_stream_id, stream_ended=True, headers=request_headers
            )
            await self._create_stream(event)
            await self.streams[event.stream_id].handle(EndBody(stream_id=event.stream_id))


class H3Wrapper(H3Connection):
    """
    High level wrapper for H3Connection
    """
    def __init__(self, quic, enable_webtransport = False):
        super().__init__(quic, enable_webtransport)

    def send_headers(
        self, 
        stream_id: int, 
        headers: Headers, 
        end_stream: bool = False , 
        is_informational: bool = False
    ) -> None:
        """
            Send headers on the given stream.
            
            .. aioquic_transmit::
            
            :param stream_id: The stream ID on which to send the headers.
            :param headers: The HTTP headers to send.
            :param end_stream: Whether to end the stream.
            :param is_informational: Wheather headers contains informational response headers
        """
        if is_informational and end_stream:
            raise ProtocolError("Informational headers (1xx) cannot end the stream.")  

        # check HEADERS frame is allowed
        with self._get_or_create_stream(stream_id) as stream:
            if stream.headers_send_state == HeadersState.AFTER_TRAILERS:
                raise FrameUnexpected("HEADERS frame is not allowed in this state") 

            # Cannot send informational headers after final headers were already sent
            if (
                is_informational
                and stream.headers_send_state != HeadersState.INITIAL
            ):
                raise FrameUnexpected("Informational headers cannot be sent after final headers.")

            if end_stream:
                stream.finish_sending()
            frame_data = self._encode_headers(stream_id, headers)
            # log frame
            if self._quic_logger is not None:
                self._quic_logger.log_event(
                    category="http",
                    event="frame_created",
                    data=self._quic_logger.encode_http3_headers_frame(
                        length=len(frame_data), headers=headers, stream_id=stream_id
                    ),
                )
            # update state and send headers
            if not is_informational:
                if stream.headers_send_state == HeadersState.INITIAL:
                    stream.headers_send_state = HeadersState.AFTER_HEADERS
                else:
                    stream.headers_send_state = HeadersState.AFTER_TRAILERS
            self._quic.send_stream_data(
                stream_id, encode_frame(FrameType.HEADERS, frame_data), end_stream
            )   
