import asyncio
from enum import auto, Enum
from functools import partial
from typing import Optional, Type, Union
from urllib.parse import unquote, urlparse

import h11
import wsproto.connection
import wsproto.events
import wsproto.extensions

from .base import HTTPServer
from .config import Config
from .logging import AccessLogAtoms
from .typing import ASGIFramework


class WebsocketState(Enum):
    # Hypercorn supports the ASGI websocket HTTP response extension,
    # which allows HTTP responses rather than acceptance.
    HANDSHAKE = auto()
    CONNECTED = auto()
    RESPONSE_BODY = auto()
    ENDED = auto()


class WebsocketServer(HTTPServer):

    def __init__(
            self,
            app: Type[ASGIFramework],
            loop: asyncio.AbstractEventLoop,
            config: Config,
            transport: asyncio.BaseTransport,
    ) -> None:
        super().__init__(loop, config, transport, 'wsproto')
        self.app = app
        self.connection = wsproto.connection.WSConnection(
            wsproto.connection.SERVER, extensions=[wsproto.extensions.PerMessageDeflate()],
        )
        self.task: Optional[asyncio.Future] = None
        self.queue: asyncio.Queue = asyncio.Queue()
        self.scope: Optional[dict] = None
        self.state = WebsocketState.HANDSHAKE
        self._buffer: Optional[Union[bytes, str]] = None

    def initialise(self, request: h11.Request) -> None:
        fake_client = h11.Connection(h11.CLIENT)
        # wsproto has a bug in the acceptance of Connection headers,
        # which this works
        # around. https://github.com/python-hyper/wsproto/pull/56
        headers = []
        for name, value in request.headers:
            if name.lower() == b'connection':
                headers.append((b'Connection', b'Upgrade'))
            else:
                headers.append((name, value))
        request.headers = headers

        self.data_received(fake_client.send(request))

    def data_received(self, data: bytes) -> None:
        super().data_received(data)
        self.connection.receive_bytes(data)
        for event in self.connection.events():
            if isinstance(event, wsproto.events.ConnectionRequested):
                self.handle_websocket(event)
            elif isinstance(event, wsproto.events.DataReceived):
                if self._buffer is None:
                    if isinstance(event, wsproto.events.TextReceived):
                        self._buffer = ''
                    else:
                        self._buffer = b''
                self._buffer += event.data
                if len(self._buffer) > self.config.websocket_max_message_size:
                    self.write(self.connection.bytes_to_send())
                    # NEED TO SEND ERROR CODE
                    self.close()
                if event.message_finished:
                    if isinstance(event, wsproto.events.BytesReceived):
                        self.queue.put_nowait({
                            'type': 'websocket.receive',
                            'bytes': self._buffer,
                            'text': None,
                        })
                    else:
                        self.queue.put_nowait({
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
        self.queue.put_nowait({
            'type': 'websocket.disconnect',
        })
        super().close()

    @property
    def active(self) -> bool:
        return self.connection._state == wsproto.connection.ConnectionState.OPEN

    def handle_websocket(self, event: wsproto.events.ConnectionRequested) -> None:
        scheme = 'wss' if self.ssl_info is not None else 'ws'
        parsed_path = urlparse(event.h11request.target)
        self.scope = {
            'type': 'websocket',
            'scheme': scheme,
            'path': unquote(parsed_path.path.decode()),
            'query_string': parsed_path.query,
            'root_path': '',
            'headers': event.h11request.headers,
            'client': self.transport.get_extra_info('sockname'),
            'server': self.transport.get_extra_info('peername'),
            'subprotocols': [],
            'extensions': {
                'websocket.http.response.start': {},
                'websocket.http.response.body': {},
            },
        }
        self.task = self.loop.create_task(self._handle_websocket(event))
        self.task.add_done_callback(self.cleanup_task)

    async def _handle_websocket(self, event: wsproto.events.ConnectionRequested) -> None:
        asgi_instance = self.app(self.scope)
        self.queue.put_nowait({
            'type': 'websocket.connect',
        })
        try:
            await asgi_instance(self._asgi_receive, partial(self._asgi_send, event))
        except Exception as error:
            self.config.error_logger.exception("Error in ASGI Framework")
            # SEND ERROR OR 500 HERE

    async def _asgi_receive(self) -> dict:
        """Called by the ASGI instance to receive a message."""
        return await self.queue.get()

    async def _asgi_send(
            self,
            request_event: wsproto.events.ConnectionRequested,
            message: dict,
    ) -> None:
        """Called by the ASGI instance to send a message."""
        if message['type'] == 'websocket.accept' and self.state == WebsocketState.HANDSHAKE:
            self.connection.accept(request_event)
            self.write(self.connection.bytes_to_send())
            self.state = WebsocketState.CONNECTED
            self.config.access_logger.error(
                self.config.access_log_format,
                AccessLogAtoms(self.scope, {'status': 101, 'headers': []}, 0),
            )
        elif (
                message['type'] == 'websocket.http.response.start'
                and self.state == WebsocketState.HANDSHAKE
        ):
            pass
        elif message['type'] == 'websocket.send' and self.state == WebsocketState.CONNECTED:
            data = message['bytes'] if message['bytes'] is not None else message['text']
            self.connection.send_data(data)
            self.write(self.connection.bytes_to_send())
        elif message['type'] == 'websocket.close':
            self.connection.close(message['code'])
            self.write(self.connection.bytes_to_send())
            self.close()
            self.state = WebsocketState.ENDED
        else:
            raise Exception(
                f"Unexpected message type, {message['type']} given the state {self.state}",
            )
