from typing import Optional, Type, Union

import h11
import wsproto

import trio
from ..asgi.wsproto import (
    AcceptConnection,
    ASGIWebsocketState,
    CloseConnection,
    Data,
    FrameTooLarge,
    WebsocketBuffer,
    WebsocketMixin,
    WsprotoEvent,
)
from ..config import Config
from ..typing import ASGIFramework, H11SendableEvent
from .base import HTTPServer

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
        self.connection = wsproto.connection.WSConnection(
            wsproto.connection.SERVER, extensions=[wsproto.extensions.PerMessageDeflate()]
        )
        self.response: Optional[dict] = None
        self.scope: Optional[dict] = None
        self.state = ASGIWebsocketState.HANDSHAKE

        self.buffer = WebsocketBuffer(self.config.websocket_max_message_size)
        self.app_send_channel, self.app_receive_channel = trio.open_memory_channel(10)

        if upgrade_request is not None:
            fake_client = h11.Connection(h11.CLIENT)
            self.connection.receive_bytes(fake_client.send(upgrade_request))

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
            await self.stream.send_all(self.connection.bytes_to_send())
        finally:
            await self.aclose()

    async def read_request(self) -> wsproto.events.ConnectionRequested:
        for event in self.connection.events():
            if isinstance(event, wsproto.events.ConnectionRequested):
                return event

    async def read_messages(self) -> None:
        while True:
            data = await self.stream.receive_some(MAX_RECV)
            if data == b"":
                data = None  # wsproto expects None rather than b"" for EOF
            self.connection.receive_bytes(data)
            for event in self.connection.events():
                if isinstance(event, wsproto.events.DataReceived):
                    try:
                        self.buffer.extend(event)
                    except FrameTooLarge:
                        self.connection.close(1009)  # CLOSE_TOO_LARGE
                        await self.asgi_put({"type": "websocket.disconnect"})
                        raise MustCloseError()

                    if event.message_finished:
                        await self.asgi_put(self.buffer.to_message())
                        self.buffer.clear()
                elif isinstance(event, wsproto.events.ConnectionClosed):
                    await self.asgi_put({"type": "websocket.disconnect"})
                    raise MustCloseError()

    async def asend(self, event: Union[H11SendableEvent, WsprotoEvent]) -> None:
        if isinstance(event, AcceptConnection):
            self.connection.accept(event.request)
            data = self.connection.bytes_to_send()
        elif isinstance(event, Data):
            self.connection.send_data(event.data)
            data = self.connection.bytes_to_send()
        elif isinstance(event, CloseConnection):
            self.connection.close(event.code)
            data = self.connection.bytes_to_send()
        else:
            data = self.connection._upgrade_connection.send(event)
        await self.stream.send_all(data)

    async def asgi_put(self, message: dict) -> None:
        await self.app_send_channel.send(message)

    async def asgi_receive(self) -> dict:
        return await self.app_receive_channel.receive()

    @property
    def scheme(self) -> str:
        return "wss" if self._is_ssl else "ws"
