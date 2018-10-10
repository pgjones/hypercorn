import asyncio
from typing import Optional, Type, Union

import h11
import wsproto.connection
import wsproto.events
import wsproto.extensions

from .base import HTTPServer
from ..common.wsproto import WebsocketMixin
from ..config import Config
from ..typing import ASGIFramework
from ..utils import WebsocketState


class WebsocketServer(HTTPServer, WebsocketMixin):

    def __init__(
            self,
            app: Type[ASGIFramework],
            loop: asyncio.AbstractEventLoop,
            config: Config,
            transport: asyncio.BaseTransport,
            *,
            upgrade_request: Optional[h11.Request]=None,
    ) -> None:
        super().__init__(loop, config, transport, 'wsproto')
        self.stop_keep_alive_timeout()
        self.app = app
        self.connection = wsproto.connection.WSConnection(
            wsproto.connection.SERVER, extensions=[wsproto.extensions.PerMessageDeflate()],
        )
        self.task: Optional[asyncio.Future] = None

        self.app_queue: asyncio.Queue = asyncio.Queue()
        self.response: Optional[dict] = None
        self.scope: Optional[dict] = None
        self.state = WebsocketState.HANDSHAKE

        self._buffer: Optional[Union[bytes, str]] = None

        if upgrade_request is not None:
            fake_client = h11.Connection(h11.CLIENT)
            self.data_received(fake_client.send(upgrade_request))

    def data_received(self, data: bytes) -> None:
        super().data_received(data)
        self.connection.receive_bytes(data)
        for event in self.connection.events():
            if isinstance(event, wsproto.events.ConnectionRequested):
                self.loop.create_task(self.handle_websocket(event))
            elif isinstance(event, wsproto.events.DataReceived):
                if self._buffer is None:
                    if isinstance(event, wsproto.events.TextReceived):
                        self._buffer = ''
                    else:
                        self._buffer = b''
                self._buffer += event.data
                if len(self._buffer) > self.config.websocket_max_message_size:
                    self.connection.close(1009)  # CLOSE_TOO_LARGE
                    self.write(self.connection.bytes_to_send())
                    self.close()
                if event.message_finished:
                    if isinstance(event, wsproto.events.BytesReceived):
                        self.app_queue.put_nowait({
                            'type': 'websocket.receive',
                            'bytes': self._buffer,
                            'text': None,
                        })
                    else:
                        self.app_queue.put_nowait({
                            'type': 'websocket.receive',
                            'bytes': None,
                            'text': self._buffer,
                        })
                    self._buffer = None
            elif isinstance(event, wsproto.events.ConnectionClosed):
                self.write(self.connection.bytes_to_send())
                self.close()
                return
            self.write(self.connection.bytes_to_send())

    def close(self) -> None:
        self.app_queue.put_nowait({'type': 'websocket.disconnect'})
        super().close()

    @property
    def scheme(self) -> str:
        return 'wss' if self.ssl_info is not None else 'ws'

    async def awrite(self, data: bytes) -> None:
        self.write(data)

    async def aclose(self) -> None:
        self.close()
