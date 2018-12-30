import asyncio
from typing import Optional, Type, Union

import h11
import wsproto.connection
import wsproto.events
import wsproto.extensions

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
        self.connection = wsproto.connection.WSConnection(
            wsproto.connection.SERVER, extensions=[wsproto.extensions.PerMessageDeflate()]
        )

        self.app_queue: asyncio.Queue = asyncio.Queue()
        self.response: Optional[dict] = None
        self.scope: Optional[dict] = None
        self.state = ASGIWebsocketState.HANDSHAKE
        self.task: Optional[asyncio.Future] = None

        self.buffer = WebsocketBuffer(self.config.websocket_max_message_size)

        if upgrade_request is not None:
            fake_client = h11.Connection(h11.CLIENT)
            self.data_received(fake_client.send(upgrade_request))

    def connection_lost(self, error: Optional[Exception]) -> None:
        if error is not None:
            self.app_queue.put_nowait({"type": "websocket.disconnect"})

    def eof_received(self) -> bool:
        self.data_received(None)
        return True

    def data_received(self, data: Optional[bytes]) -> None:
        self.connection.receive_bytes(data)
        self.handle_events()

    def handle_events(self) -> None:
        for event in self.connection.events():
            if isinstance(event, wsproto.events.ConnectionRequested):
                self.task = self.loop.create_task(self.handle_websocket(event))
                self.task.add_done_callback(self.maybe_close)
            elif isinstance(event, wsproto.events.DataReceived):
                try:
                    self.buffer.extend(event)
                except FrameTooLarge:
                    self.connection.close(1009)  # CLOSE_TOO_LARGE
                    self.write(self.connection.bytes_to_send())
                    self.app_queue.put_nowait({"type": "websocket.disconnect"})
                    self.close()
                    break

                if event.message_finished:
                    self.app_queue.put_nowait(self.buffer.to_message())
                    self.buffer.clear()
            elif isinstance(event, wsproto.events.ConnectionClosed):
                self.write(self.connection.bytes_to_send())
                self.app_queue.put_nowait({"type": "websocket.disconnect"})
                self.close()
                break

    def maybe_close(self, future: asyncio.Future) -> None:
        # Close the connection iff a HTTP response was sent
        if self.state == ASGIWebsocketState.HTTPCLOSED:
            self.close()

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
        self.write(data)

    async def asgi_put(self, message: dict) -> None:
        await self.app_queue.put(message)

    async def asgi_receive(self) -> dict:
        """Called by the ASGI instance to receive a message."""
        return await self.app_queue.get()

    @property
    def scheme(self) -> str:
        return "wss" if self.ssl_info is not None else "ws"
