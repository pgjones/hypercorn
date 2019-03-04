from typing import Optional, Type

import h11
import trio
from wsproto import ConnectionType, WSConnection
from wsproto.connection import ConnectionState
from wsproto.events import CloseConnection, Event, Message, Ping, Request
from wsproto.frame_protocol import CloseReason

from .base import HTTPServer
from ..asgi.utils import ASGIWebsocketState, FrameTooLarge, WebsocketBuffer
from ..asgi.wsproto import WebsocketMixin
from ..config import Config
from ..typing import ASGIFramework

MAX_RECV = 2 ** 16


class MustCloseError(Exception):
    pass


class WebsocketServer(HTTPServer, WebsocketMixin):
    def __init__(
        self,
        app: Type[ASGIFramework],
        config: Config,
        stream: trio.abc.Stream,
        *,
        upgrade_request: Optional[h11.Request] = None,
    ) -> None:
        super().__init__(stream, "wsproto")
        self.app = app
        self.config = config
        self.connection = WSConnection(ConnectionType.SERVER)
        self.response: Optional[dict] = None
        self.scope: Optional[dict] = None
        self.send_lock = trio.Lock()
        self.state = ASGIWebsocketState.HANDSHAKE

        self.buffer = WebsocketBuffer(self.config.websocket_max_message_size)
        self.app_send_channel, self.app_receive_channel = trio.open_memory_channel(10)

        if upgrade_request is not None:
            self.connection.initiate_upgrade_connection(
                upgrade_request.headers, upgrade_request.target
            )

    async def handle_connection(self) -> None:
        try:
            request = await self.read_request()
            async with trio.open_nursery() as nursery:
                nursery.start_soon(self.read_messages)
                await self.handle_websocket(request)
                if self.state == ASGIWebsocketState.HTTPCLOSED:
                    raise MustCloseError()
        except (trio.BrokenResourceError, trio.ClosedResourceError):
            await self.asgi_put({"type": "websocket.disconnect"})
        except MustCloseError:
            pass
        finally:
            await self.aclose()

    async def read_request(self) -> Request:
        for event in self.connection.events():
            if isinstance(event, Request):
                return event

    async def read_messages(self) -> None:
        while True:
            data = await self.stream.receive_some(MAX_RECV)
            if data == b"":
                data = None  # wsproto expects None rather than b"" for EOF
            self.connection.receive_data(data)
            for event in self.connection.events():
                if isinstance(event, Message):
                    try:
                        self.buffer.extend(event)
                    except FrameTooLarge:
                        await self.asend(CloseConnection(code=CloseReason.MESSAGE_TOO_BIG))
                        await self.asgi_put({"type": "websocket.disconnect"})
                        raise MustCloseError()

                    if event.message_finished:
                        await self.asgi_put(self.buffer.to_message())
                        self.buffer.clear()
                elif isinstance(event, Ping):
                    await self.asend(event.response())
                elif isinstance(event, CloseConnection):
                    if self.connection.state == ConnectionState.REMOTE_CLOSING:
                        await self.asend(event.response())
                    await self.asgi_put({"type": "websocket.disconnect"})
                    raise MustCloseError()

    async def asend(self, event: Event) -> None:
        async with self.send_lock:
            await self.stream.send_all(self.connection.send(event))

    async def asgi_put(self, message: dict) -> None:
        await self.app_send_channel.send(message)

    async def asgi_receive(self) -> dict:
        return await self.app_receive_channel.receive()

    @property
    def scheme(self) -> str:
        return "wss" if self._is_ssl else "ws"
