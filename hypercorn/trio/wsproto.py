from typing import Optional, Type, Union

import h11
import trio
import wsproto

from .base import HTTPServer
from ..common.wsproto import WebsocketMixin
from ..config import Config
from ..typing import ASGIFramework
from ..utils import WebsocketState

MAX_RECV = 2 ** 16


class ConnectionClosed(Exception):
    pass


class WebsocketServer(HTTPServer, WebsocketMixin):

    def __init__(
            self,
            app: Type[ASGIFramework],
            config: Config,
            stream: trio.abc.Stream,
            *,
            upgrade_request: Optional[h11.Request]=None,
    ) -> None:
        super().__init__(stream, 'wsproto')
        self.app = app
        self.config = config
        self.connection = wsproto.connection.WSConnection(
            wsproto.connection.SERVER, extensions=[wsproto.extensions.PerMessageDeflate()],
        )
        self.app_queue = trio.Queue(10)
        self.response: Optional[dict] = None
        self.scope: Optional[dict] = None
        self.state = WebsocketState.HANDSHAKE

        self._buffer: Optional[Union[bytes, str]] = None

        if upgrade_request is not None:
            fake_client = h11.Connection(h11.CLIENT)
            self.connection.receive_bytes(fake_client.send(upgrade_request))

    async def handle_connection(self) -> None:
        try:
            request = await self.read_request()
            async with trio.open_nursery() as nursery:
                nursery.start_soon(self.handle_websocket, request)
                await self.read_messages()
        except (
                ConnectionClosed, trio.TooSlowError, trio.BrokenResourceError,
                trio.ClosedResourceError,
        ):
            await self.aclose()

    async def awrite(self, data: bytes) -> None:
        await self.stream.send_all(data)

    async def aclose(self) -> None:
        self.app_queue.put_nowait({'type': 'websocket.disconnect'})
        await super().aclose()

    async def read_request(self) -> wsproto.events.ConnectionRequested:
        for event in self.connection.events():
            if isinstance(event, wsproto.events.ConnectionRequested):
                return event
            else:
                raise ConnectionClosed()

    async def read_messages(self) -> None:
        while True:
            data = await self.stream.receive_some(MAX_RECV)
            self.connection.receive_bytes(data)
            for event in self.connection.events():
                if isinstance(event, wsproto.events.DataReceived):
                    if self._buffer is None:
                        if isinstance(event, wsproto.events.TextReceived):
                            self._buffer = ''
                        else:
                            self._buffer = b''
                        self._buffer += event.data
                    if len(self._buffer) > self.config.websocket_max_message_size:
                        self.connection.close(1009)  # CLOSE_TOO_LARGE
                        await self.awrite(self.connection.bytes_to_send())
                        raise ConnectionClosed()
                    if event.message_finished:
                        if isinstance(event, wsproto.events.BytesReceived):
                            await self.app_queue.put({
                                'type': 'websocket.receive',
                                'bytes': self._buffer,
                                'text': None,
                            })
                        else:
                            await self.app_queue.put({
                                'type': 'websocket.receive',
                                'bytes': None,
                                'text': self._buffer,
                            })
                        self._buffer = None
                elif isinstance(event, wsproto.events.ConnectionClosed):
                    raise ConnectionClosed()

    @property
    def scheme(self) -> str:
        return 'wss' if self._is_ssl else 'ws'
