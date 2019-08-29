from typing import Awaitable, Callable, Dict, Optional, Tuple, Union

from aioquic.h3.connection import H3Connection
from aioquic.h3.events import DataReceived, RequestReceived
from aioquic.quic.connection import QuicConnection
from aioquic.quic.events import QuicEvent

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


class H3Protocol:
    def __init__(
        self,
        config: Config,
        client: Optional[Tuple[str, int]],
        server: Optional[Tuple[str, int]],
        spawn_app: Callable[[dict, Callable], Awaitable[Callable]],
        quic: QuicConnection,
        send: Callable[[], Awaitable[None]],
    ) -> None:
        self.client = client
        self.config = config
        self.connection = H3Connection(quic)
        self.send = send
        self.server = server
        self.spawn_app = spawn_app
        self.streams: Dict[int, Union[HTTPStream, WSStream]] = {}

    async def handle(self, quic_event: QuicEvent) -> None:
        for event in self.connection.handle_event(quic_event):
            if isinstance(event, RequestReceived):
                await self._create_stream(event)
                if event.stream_ended:
                    await self.streams[event.stream_id].handle(EndBody(stream_id=event.stream_id))
            elif isinstance(event, DataReceived):
                await self.streams[event.stream_id].handle(
                    Body(stream_id=event.stream_id, data=event.data)
                )
                if event.stream_ended:
                    await self.streams[event.stream_id].handle(EndBody(stream_id=event.stream_id))

    async def stream_send(self, event: StreamEvent) -> None:
        if isinstance(event, Response):
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
            self.connection.send_data(event.stream_id, b"", True)
            await self.send()
        elif isinstance(event, StreamClosed):
            pass  # ??

    async def _create_stream(self, request: RequestReceived) -> None:
        for name, value in request.headers:
            if name == b":method":
                method = value.decode("ascii").upper()
            elif name == b":path":
                raw_path = value

        if method == "CONNECT":
            self.streams[request.stream_id] = WSStream(
                self.config,
                True,
                self.client,
                self.server,
                self.stream_send,
                self.spawn_app,
                request.stream_id,
            )
        else:
            self.streams[request.stream_id] = HTTPStream(
                self.config,
                True,
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
                http_version="3",
                method=method,
                raw_path=raw_path,
            )
        )
