from __future__ import annotations

from collections.abc import Awaitable, Callable

from aioquic.h3.connection import H3Connection
from aioquic.h3.events import DataReceived, HeadersReceived
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
from .h3_send import H3SendScheduler
from .http_stream import HTTPStream
from .queued_stream import QueuedStream
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
        self.connection = H3Connection(quic)
        self.closed = False
        self.send = send
        self.server = server
        self.streams: dict[int, QueuedStream] = {}
        self.task_group = task_group
        self.state = state
        self.sender = H3SendScheduler(self.connection, self.context.event_class, self.send)
        self.task_group.spawn(self.sender.run, lambda: self.closed)

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
            await self.sender.headers(
                event.stream_id,
                [(b":status", b"%d" % event.status_code)]
                + event.headers
                + self.config.response_headers("h3"),
            )
        elif isinstance(event, (Body, Data)):
            await self.sender.data(event.stream_id, event.data)
        elif isinstance(event, (EndBody, EndData)):
            await self.sender.data(event.stream_id, b"", end_stream=True)
        elif isinstance(event, Trailers):
            await self.sender.headers(event.stream_id, event.headers)
        elif isinstance(event, StreamClosed):
            self.streams.pop(event.stream_id, None)
        elif isinstance(event, Request):
            await self._create_server_push(event.stream_id, event.raw_path, event.headers)

    async def close(self) -> None:
        self.closed = True
        await self.sender.close()

    async def _create_stream(self, request: HeadersReceived) -> None:
        for name, value in request.headers:
            if name == b":method":
                method = value.decode("ascii").upper()
            elif name == b":path":
                raw_path = value

        if method == "CONNECT":
            stream = WSStream(
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
            stream = HTTPStream(
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
        self.streams[request.stream_id] = QueuedStream(
            stream,
            self.task_group,
            self.context,
            self.config.max_app_queue_size,
            self.config.max_app_queue_bytes,
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
