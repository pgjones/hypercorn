from typing import Awaitable, Callable, Dict, List, Optional, Tuple, Type, Union

import h2
import h2.connection
import h2.events
import h2.exceptions

from .events import (
    Body,
    Data,
    EndBody,
    EndData,
    Event as StreamEvent,
    Request,
    Response,
    StreamClosed,
)
from .http_stream import HTTPStream
from .ws_stream import WSStream
from ..config import Config
from ..events import Closed, Event, RawData, Updated
from ..typing import Event as IOEvent


class H2Protocol:
    def __init__(
        self,
        config: Config,
        ssl: bool,
        client: Optional[Tuple[str, int]],
        server: Optional[Tuple[str, int]],
        send: Callable[[Event], Awaitable[None]],
        spawn_app: Callable[[dict, Callable], Awaitable[Callable]],
        event_class: Type[IOEvent],
    ) -> None:
        self.client = client
        self.config = config

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

        self.event_class = event_class
        self.flow_control: Dict[int, IOEvent] = {}
        self.send = send
        self.server = server
        self.spawn_app = spawn_app
        self.ssl = ssl
        self.streams: Dict[int, Union[HTTPStream, WSStream]] = {}

    @property
    def idle(self) -> bool:
        return len(self.streams) == 0 or all(stream.idle for stream in self.streams.values())

    async def initiate(
        self, headers: Optional[List[Tuple[bytes, bytes]]] = None, settings: Optional[str] = None
    ) -> None:
        if settings is not None:
            self.connection.initiate_upgrade_connection(settings)
        else:
            self.connection.initiate_connection()
        await self._flush()
        if headers is not None:
            event = h2.events.RequestReceived()
            event.stream_id = 1
            event.headers = headers
            await self._create_stream(event)
            await self.streams[event.stream_id].handle(EndBody(stream_id=event.stream_id))

    async def handle(self, event: Event) -> None:
        if isinstance(event, RawData):
            try:
                events = self.connection.receive_data(event.data)
            except h2.exceptions.ProtocolError:
                await self._flush()
                await self.send(Closed())
            else:
                await self._handle_events(events)
        elif isinstance(event, Closed):
            stream_ids = list(self.streams.keys())
            for stream_id in stream_ids:
                await self._close_stream(stream_id)
            for flow_event in self.flow_control.values():
                await flow_event.set()
            self.flow_control = {}

    async def stream_send(self, event: StreamEvent) -> None:
        try:
            if isinstance(event, Response):
                self.connection.send_headers(
                    event.stream_id,
                    [(b":status", b"%d" % event.status_code)]
                    + event.headers
                    + self.config.response_headers("h2"),
                )
                await self._flush()
            elif isinstance(event, (Body, Data)):
                await self._send_data(event.stream_id, event.data)
            elif isinstance(event, (EndBody, EndData)):
                self.connection.end_stream(event.stream_id)
                await self._flush()
            elif isinstance(event, StreamClosed):
                await self._close_stream(event.stream_id)
                await self.send(Updated())
            elif isinstance(event, Request):
                await self._create_server_push(event.stream_id, event.raw_path, event.headers)
        except h2.exceptions.ProtocolError:
            # Connection has closed whilst blocked on flow control or
            # connection has advanced ahead of the last emitted event.
            return

    async def _handle_events(self, events: List[h2.events.Event]) -> None:
        for event in events:
            if isinstance(event, h2.events.RequestReceived):
                await self._create_stream(event)
            elif isinstance(event, h2.events.DataReceived):
                await self.streams[event.stream_id].handle(
                    Body(stream_id=event.stream_id, data=event.data)
                )
                self.connection.acknowledge_received_data(
                    event.flow_controlled_length, event.stream_id
                )
            elif isinstance(event, h2.events.StreamEnded):
                await self.streams[event.stream_id].handle(EndBody(stream_id=event.stream_id))
            elif isinstance(event, h2.events.StreamReset):
                await self._close_stream(event.stream_id)
                await self._window_updated(event.stream_id)
            elif isinstance(event, h2.events.WindowUpdated):
                await self._window_updated(event.stream_id)
            elif isinstance(event, h2.events.RemoteSettingsChanged):
                if h2.settings.SettingCodes.INITIAL_WINDOW_SIZE in event.changed_settings:
                    await self._window_updated(None)
            elif isinstance(event, h2.events.ConnectionTerminated):
                await self.send(Closed())
        await self._flush()

    async def _flush(self) -> None:
        data = self.connection.data_to_send()
        if data != b"":
            await self.send(RawData(data=data))

    async def _send_data(self, stream_id: int, data: bytes) -> None:
        while True:
            while self.connection.local_flow_control_window(stream_id) < 1:
                await self._wait_for_flow_control(stream_id)
                if stream_id not in self.streams:  # Stream has been cancelled
                    return

            chunk_size = min(len(data), self.connection.local_flow_control_window(stream_id))
            chunk_size = min(chunk_size, self.connection.max_outbound_frame_size)
            if chunk_size < 1:  # Pathological client sending negative window sizes
                continue
            self.connection.send_data(stream_id, data[:chunk_size])
            await self._flush()
            data = data[chunk_size:]
            if not data:
                return

    async def _wait_for_flow_control(self, stream_id: int) -> None:
        event = self.event_class()
        self.flow_control[stream_id] = event
        await event.wait()

    async def _window_updated(self, stream_id: Optional[int]) -> None:
        if stream_id is None or stream_id == 0:
            # Unblock all streams
            stream_ids = list(self.flow_control.keys())
            for stream_id in stream_ids:
                event = self.flow_control.pop(stream_id)
                await event.set()
        elif stream_id is not None:
            if stream_id in self.flow_control:
                event = self.flow_control.pop(stream_id)
                await event.set()

    async def _create_stream(self, request: h2.events.RequestReceived) -> None:
        for name, value in request.headers:
            if name == b":method":
                method = value.decode("ascii").upper()
            elif name == b":path":
                raw_path = value

        if method == "CONNECT":
            self.streams[request.stream_id] = WSStream(
                self.config,
                self.ssl,
                self.client,
                self.server,
                self.stream_send,
                self.spawn_app,
                request.stream_id,
            )
        else:
            self.streams[request.stream_id] = HTTPStream(
                self.config,
                self.ssl,
                self.client,
                self.server,
                self.stream_send,
                self.spawn_app,
                request.stream_id,
            )
        await self.streams[request.stream_id].handle(
            Request(
                stream_id=request.stream_id,
                headers=request.headers,
                http_version="2.0",
                method=method,
                raw_path=raw_path,
            )
        )

    async def _create_server_push(
        self, stream_id: int, path: bytes, headers: List[Tuple[bytes, bytes]]
    ) -> None:
        push_stream_id = self.connection.get_next_available_stream_id()
        request_headers = [(b":method", b"GET"), (b":path", path)]
        request_headers.extend(headers)
        request_headers.extend(self.config.response_headers("h2"))
        try:
            self.connection.push_stream(
                stream_id=stream_id,
                promised_stream_id=push_stream_id,
                request_headers=request_headers,
            )
            await self._flush()
        except h2.exceptions.ProtocolError:
            # Client does not accept push promises or we are trying to
            # push on a push promises request.
            pass
        else:
            event = h2.events.RequestReceived()
            event.stream_id = push_stream_id
            event.headers = request_headers
            await self._create_stream(event)
            await self.streams[event.stream_id].handle(EndBody(stream_id=event.stream_id))

    async def _close_stream(self, stream_id: int) -> None:
        if stream_id in self.streams:
            await self.streams[stream_id].handle(StreamClosed(stream_id=stream_id))
            del self.streams[stream_id]
