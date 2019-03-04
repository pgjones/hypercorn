import asyncio
from typing import Optional, Type

import h11
from wsproto import ConnectionType, WSConnection
from wsproto.connection import ConnectionState
from wsproto.events import CloseConnection, Event, Message, Ping, Request
from wsproto.frame_protocol import CloseReason

from .base import HTTPServer
from ..asgi.utils import ASGIWebsocketState, FrameTooLarge, WebsocketBuffer
from ..asgi.wsproto import WebsocketMixin
from ..config import Config
from ..typing import ASGIFramework


class WebsocketServer(HTTPServer, WebsocketMixin):
    def __init__(
        self,
        app: Type[ASGIFramework],
        loop: asyncio.AbstractEventLoop,
        config: Config,
        transport: asyncio.BaseTransport,
        *,
        upgrade_request: Optional[h11.Request] = None,
    ) -> None:
        super().__init__(loop, config, transport, "wsproto")
        self.stop_keep_alive_timeout()
        self.app = app
        self.connection = WSConnection(ConnectionType.SERVER)

        self.app_queue: asyncio.Queue = asyncio.Queue()
        self.response: Optional[dict] = None
        self.scope: Optional[dict] = None
        self.state = ASGIWebsocketState.HANDSHAKE
        self.task: Optional[asyncio.Future] = None

        self.buffer = WebsocketBuffer(self.config.websocket_max_message_size)

        if upgrade_request is not None:
            self.connection.initiate_upgrade_connection(
                upgrade_request.headers, upgrade_request.target
            )
            self.handle_events()

    def connection_lost(self, error: Optional[Exception]) -> None:
        if error is not None:
            self.app_queue.put_nowait({"type": "websocket.disconnect"})

    def eof_received(self) -> bool:
        self.data_received(None)
        return True

    def data_received(self, data: Optional[bytes]) -> None:
        self.connection.receive_data(data)
        self.handle_events()

    def handle_events(self) -> None:
        for event in self.connection.events():
            if isinstance(event, Request):
                self.task = self.loop.create_task(self.handle_websocket(event))
                self.task.add_done_callback(self.maybe_close)
            elif isinstance(event, Message):
                try:
                    self.buffer.extend(event)
                except FrameTooLarge:
                    self.write(
                        self.connection.send(CloseConnection(code=CloseReason.MESSAGE_TOO_BIG))
                    )
                    self.app_queue.put_nowait({"type": "websocket.disconnect"})
                    self.close()
                    break

                if event.message_finished:
                    self.app_queue.put_nowait(self.buffer.to_message())
                    self.buffer.clear()
            elif isinstance(event, Ping):
                self.write(self.connection.send(event.response()))
            elif isinstance(event, CloseConnection):
                if self.connection.state == ConnectionState.REMOTE_CLOSING:
                    self.write(self.connection.send(event.response()))
                self.app_queue.put_nowait({"type": "websocket.disconnect"})
                self.close()
                break

    def maybe_close(self, future: asyncio.Future) -> None:
        # Close the connection iff a HTTP response was sent
        if self.state == ASGIWebsocketState.HTTPCLOSED:
            self.close()

    async def asend(self, event: Event) -> None:
        await self.drain()
        self.write(self.connection.send(event))

    async def asgi_put(self, message: dict) -> None:
        await self.app_queue.put(message)

    async def asgi_receive(self) -> dict:
        """Called by the ASGI instance to receive a message."""
        return await self.app_queue.get()

    @property
    def scheme(self) -> str:
        return "wss" if self.ssl_info is not None else "ws"
