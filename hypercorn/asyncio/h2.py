import asyncio
from functools import partial
from itertools import chain
from typing import Callable, Dict, Iterable, List, Optional, Tuple, Type

import h2.config
import h2.connection
import h2.events
import h2.exceptions
import h11
import wsproto
import wsproto.connection
import wsproto.events

from .base import HTTPServer
from ..asgi.h2 import (
    Data,
    EndStream,
    H2Event,
    H2HTTPStreamMixin,
    H2WebsocketStreamMixin,
    Response,
    ServerPush,
)
from ..asgi.utils import ASGIHTTPState, ASGIWebsocketState
from ..config import Config
from ..typing import ASGIFramework, H2SyncStream


class H2HTTPStream(H2HTTPStreamMixin):
    """A HTTP Stream."""

    def __init__(self, app: Type[ASGIFramework], config: Config, asend: Callable) -> None:
        self.app = app
        self.config = config
        self.response: Optional[dict] = None
        self.scope: Optional[dict] = None
        self.state = ASGIHTTPState.REQUEST

        self.asend = asend  # type: ignore
        self.to_app: asyncio.Queue = asyncio.Queue()

    def data_received(self, data: bytes) -> None:
        self.to_app.put_nowait({"type": "http.request", "body": data, "more_body": True})

    def ended(self) -> None:
        self.to_app.put_nowait({"type": "http.request", "body": b"", "more_body": False})

    def reset(self) -> None:
        self.to_app.put_nowait({"type": "http.disconnect"})

    def close(self) -> None:
        self.to_app.put_nowait({"type": "http.disconnect"})

    async def asgi_receive(self) -> dict:
        return await self.to_app.get()


class H2WebsocketStream(H2WebsocketStreamMixin):
    """A Websocket Stream."""

    def __init__(
        self, app: Type[ASGIFramework], config: Config, asend: Callable, send: Callable
    ) -> None:
        self.app = app
        self.config = config
        self.response: Optional[dict] = None
        self.scope: Optional[dict] = None
        self.state = ASGIWebsocketState.CONNECTED
        self.connection: Optional[wsproto.connection.Connection] = None

        self.asend = asend  # type: ignore
        self.send = send
        self.to_app: asyncio.Queue = asyncio.Queue()

    def data_received(self, data: bytes) -> None:
        self.connection.receive_data(data)
        for event in self.connection.events():
            if isinstance(event, wsproto.events.TextMessage):
                self.to_app.put_nowait({"type": "websocket.receive", "text": event.data})
            elif isinstance(event, wsproto.events.BytesMessage):
                self.to_app.put_nowait({"type": "websocket.receive", "bytes": event.data})
            elif isinstance(event, wsproto.events.Ping):
                self.send(Data(self.connection.send(event.response())))
            elif isinstance(event, wsproto.events.CloseConnection):
                if self.connection.state == wsproto.connection.ConnectionState.REMOTE_CLOSING:
                    self.send(Data(self.connection.send(event.response())))
                self.to_app.put_nowait({"type": "websocket.disconnect"})
                break

    def ended(self) -> None:
        self.to_app.put_nowait({"type": "websocket.disconnect"})

    def reset(self) -> None:
        self.to_app.put_nowait({"type": "websocket.disconnect"})

    def close(self) -> None:
        self.to_app.put_nowait({"type": "websocket.disconnect"})

    async def asgi_put(self, message: dict) -> None:
        await self.to_app.put(message)

    async def asgi_receive(self) -> dict:
        return await self.to_app.get()


class H2Server(HTTPServer):
    def __init__(
        self,
        app: Type[ASGIFramework],
        loop: asyncio.AbstractEventLoop,
        config: Config,
        transport: asyncio.BaseTransport,
        *,
        upgrade_request: Optional[h11.Request] = None,
        received_data: Optional[bytes] = None,
    ) -> None:
        super().__init__(loop, config, transport, "h2")
        self.app = app
        self.streams: Dict[int, H2SyncStream] = {}  # type: ignore
        self.flow_control: Dict[int, asyncio.Event] = {}

        self.connection = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=False, header_encoding=None)
        )
        self.connection.DEFAULT_MAX_INBOUND_FRAME_SIZE = config.h2_max_inbound_frame_size
        self.connection.local_settings = h2.settings.Settings(
            client=False,
            initial_values={
                h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS: config.h2_max_concurrent_streams,
                h2.settings.SettingCodes.MAX_HEADER_LIST_SIZE: config.h2_max_header_list_size,
                h2.settings.SettingCodes.ENABLE_CONNECT_PROTOCOL: 1,
            },
        )

        if upgrade_request is None:
            self.connection.initiate_connection()
            if received_data:
                self.data_received(received_data)
        else:
            settings = ""
            headers = []
            for name, value in upgrade_request.headers:
                if name.lower() == b"http2-settings":
                    settings = value.decode()
                elif name.lower() == b"host":
                    headers.append((b":authority", value))
                headers.append((name, value))
            headers.append((b":method", upgrade_request.method))
            headers.append((b":path", upgrade_request.target))
            self.connection.initiate_upgrade_connection(settings)
            event = h2.events.RequestReceived()
            event.stream_id = 1
            event.headers = headers
            self.create_stream(event, complete=True)
        self.flush()

    def connection_lost(self, error: Optional[Exception]) -> None:
        if error is not None:
            self.close()

    def eof_received(self) -> bool:
        self.data_received(b"")
        return True

    def data_received(self, data: bytes) -> None:
        try:
            events = self.connection.receive_data(data)
        except h2.exceptions.ProtocolError:
            self.flush()
            self.close()
        else:
            self.handle_events(events)

    def close(self) -> None:
        for stream in self.streams.values():
            stream.close()
        super().close()

    def create_stream(self, event: h2.events.RequestReceived, *, complete: bool = False) -> None:
        method: str
        for name, value in event.headers:
            if name == b":method":
                method = value.decode("ascii").upper()
        if method == "CONNECT":
            self.streams[event.stream_id] = H2WebsocketStream(
                self.app,
                self.config,
                partial(self.asend, event.stream_id),
                partial(self.send, event.stream_id),
            )
        else:
            self.streams[event.stream_id] = H2HTTPStream(
                self.app, self.config, partial(self.asend, event.stream_id)
            )
        if complete:
            self.streams[event.stream_id].ended()
        self.loop.create_task(self.handle_request(event))

    async def handle_request(self, event: h2.events.RequestReceived) -> None:
        await self.streams[event.stream_id].handle_request(
            event, self.scheme, self.client, self.server
        )
        if (
            self.connection.state_machine.state is not h2.connection.ConnectionState.CLOSED
            and event.stream_id in self.connection.streams
            and not self.connection.streams[event.stream_id].closed
        ):
            # The connection is not closed and there has been an error
            # preventing the stream from closing correctly.
            self.connection.reset_stream(event.stream_id)
        self.streams[event.stream_id].close()
        del self.streams[event.stream_id]
        if len(self.streams) == 0:
            self.start_keep_alive_timeout()

    def handle_events(self, events: List[h2.events.Event]) -> None:
        for event in events:
            if isinstance(event, h2.events.RequestReceived):
                self.stop_keep_alive_timeout()
                self.create_stream(event)
            elif isinstance(event, h2.events.DataReceived):
                self.streams[event.stream_id].data_received(event.data)
                self.connection.acknowledge_received_data(
                    event.flow_controlled_length, event.stream_id
                )
            elif isinstance(event, h2.events.StreamReset):
                self.streams[event.stream_id].reset()
            elif isinstance(event, h2.events.StreamEnded):
                self.streams[event.stream_id].ended()
            elif isinstance(event, h2.events.WindowUpdated):
                self.window_updated(event.stream_id)
            elif isinstance(event, h2.events.ConnectionTerminated):
                self.close()
                return
        self.flush()

    def flush(self) -> None:
        data = self.connection.data_to_send()
        if data != b"":
            self.write(data)

    def send(self, stream_id: int, event: H2Event) -> None:
        # Solely for websocket stream WSPing and WSClose data to be
        # sent, need to find a better way.
        connection_state = self.connection.state_machine.state
        stream_state = self.connection.streams[stream_id].state_machine.state
        if (
            connection_state == h2.connection.ConnectionState.CLOSED
            or stream_state == h2.stream.StreamState.CLOSED
        ):
            return
        if isinstance(event, Data):
            self.connection.send_data(stream_id, event.data)
            self.flush()

    async def asend(self, stream_id: int, event: H2Event) -> None:
        connection_state = self.connection.state_machine.state
        stream_state = self.connection.streams[stream_id].state_machine.state
        if (
            connection_state == h2.connection.ConnectionState.CLOSED
            or stream_state == h2.stream.StreamState.CLOSED
        ):
            return
        if isinstance(event, Response):
            self.connection.send_headers(
                stream_id, event.headers + self.response_headers()  # type: ignore
            )
            self.flush()
        elif isinstance(event, EndStream):
            self.connection.end_stream(stream_id)
            self.flush()
        elif isinstance(event, Data):
            await self.send_data(stream_id, event.data)
        elif isinstance(event, ServerPush):
            self.server_push(stream_id, event.path, event.headers)

    async def send_data(self, stream_id: int, data: bytes) -> None:
        while True:
            while not self.connection.local_flow_control_window(stream_id):
                await self.wait_for_flow_control(stream_id)

            chunk_size = min(len(data), self.connection.local_flow_control_window(stream_id))
            chunk_size = min(chunk_size, self.connection.max_outbound_frame_size)
            if chunk_size < 1:  # Pathological client sending negative window sizes
                continue
            self.connection.send_data(stream_id, data[:chunk_size])
            await self.drain()
            self.flush()
            data = data[chunk_size:]
            if not data:
                break

    async def wait_for_flow_control(self, stream_id: int) -> None:
        event = asyncio.Event()
        self.flow_control[stream_id] = event
        await event.wait()

    def window_updated(self, stream_id: Optional[int]) -> None:
        if stream_id is None or stream_id == 0:
            # Unblock all streams
            stream_ids = list(self.flow_control.keys())
            for stream_id in stream_ids:
                event = self.flow_control.pop(stream_id)
                event.set()
        elif stream_id is not None:
            if stream_id in self.flow_control:
                event = self.flow_control.pop(stream_id)
                event.set()

    def server_push(
        self, stream_id: int, path: str, headers: Iterable[Tuple[bytes, bytes]]
    ) -> None:
        push_stream_id = self.connection.get_next_available_stream_id()
        stream = self.streams[stream_id]
        for name, value in stream.scope["headers"]:
            if name == b":authority":
                authority = value
        request_headers = [
            (name, value)
            for name, value in chain(
                [
                    (b":method", b"GET"),
                    (b":path", path.encode()),
                    (b":scheme", stream.scope["scheme"].encode()),
                    (b":authority", authority),
                ],
                headers,
                self.response_headers(),
            )
        ]
        try:
            self.connection.push_stream(
                stream_id=stream_id,
                promised_stream_id=push_stream_id,
                request_headers=request_headers,
            )
        except h2.exceptions.ProtocolError:
            # Client does not accept push promises or we are trying to
            # push on a push promises request.
            pass
        else:
            event = h2.events.RequestReceived()
            event.stream_id = push_stream_id
            event.headers = request_headers
            self.create_stream(event, complete=True)

    @property
    def scheme(self) -> str:
        return "https" if self.ssl_info is not None else "http"

    def handle_timeout(self) -> None:
        self.connection.close_connection()
        self.flush()
        super().handle_timeout()
