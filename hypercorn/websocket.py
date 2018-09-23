import asyncio
from enum import auto, Enum
from functools import partial
from itertools import chain
from time import time
from typing import Optional, Type, Union
from urllib.parse import unquote

import h11
import wsproto.connection
import wsproto.events
import wsproto.extensions

from .base import HTTPServer, suppress_body
from .config import Config
from .logging import AccessLogAtoms
from .typing import ASGIFramework


class WebsocketState(Enum):
    # Hypercorn supports the ASGI websocket HTTP response extension,
    # which allows HTTP responses rather than acceptance.
    HANDSHAKE = auto()
    CONNECTED = auto()
    RESPONSE = auto()
    CLOSED = auto()


class WebsocketServer(HTTPServer):

    def __init__(
            self,
            app: Type[ASGIFramework],
            loop: asyncio.AbstractEventLoop,
            config: Config,
            transport: asyncio.BaseTransport,
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

    def initialise(self, request: h11.Request) -> None:
        fake_client = h11.Connection(h11.CLIENT)
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
        if not self.closed:
            self.app_queue.put_nowait({'type': 'websocket.disconnect'})
        super().close()

    @property
    def active(self) -> bool:
        return self.connection._state == wsproto.connection.ConnectionState.OPEN

    def handle_websocket(self, event: wsproto.events.ConnectionRequested) -> None:
        scheme = 'wss' if self.ssl_info is not None else 'ws'
        path, _, query_string = event.h11request.target.partition(b'?')
        self.scope = {
            'type': 'websocket',
            'asgi': {'version': '2.0'},
            'scheme': scheme,
            'path': unquote(path.decode('ascii')),
            'query_string': query_string,
            'root_path': self.config.root_path,
            'headers': event.h11request.headers,
            'client': self.client,
            'server': self.server,
            'subprotocols': [],
            'extensions': {
                'websocket.http.response': {},
            },
        }
        self.task = self.loop.create_task(self._handle_websocket(event))

    async def _handle_websocket(self, event: wsproto.events.ConnectionRequested) -> None:
        self.start_time = time()
        self.app_queue.put_nowait({'type': 'websocket.connect'})
        try:
            asgi_instance = self.app(self.scope)
            await asgi_instance(self.asgi_receive, partial(self.asgi_send, event))
        except Exception as error:
            if self.config.error_logger is not None:
                self.config.error_logger.exception("Error in ASGI Framework")
            if self.state == WebsocketState.CONNECTED:
                self.connection.close(1006)  # Close abnormal
                self.write(self.connection.bytes_to_send())
            self.close()
        else:
            if self.response is not None and self.config.access_logger is not None:
                self.config.access_logger.info(
                    self.config.access_log_format,
                    AccessLogAtoms(self.scope, self.response, time() - self.start_time),
                )

    async def asgi_receive(self) -> dict:
        """Called by the ASGI instance to receive a message."""
        return await self.app_queue.get()

    async def asgi_send(
            self,
            request_event: wsproto.events.ConnectionRequested,
            message: dict,
    ) -> None:
        """Called by the ASGI instance to send a message."""
        if message['type'] == 'websocket.accept' and self.state == WebsocketState.HANDSHAKE:
            self.connection.accept(request_event)
            self.write(self.connection.bytes_to_send())
            self.state = WebsocketState.CONNECTED
            if self.config.access_logger is not None:
                self.config.access_logger.info(
                    self.config.access_log_format,
                    AccessLogAtoms(
                        self.scope, {'status': 101, 'headers': []}, time() - self.start_time,
                    ),
                )
        elif (
                message['type'] == 'websocket.http.response.start'
                and self.state == WebsocketState.HANDSHAKE
        ):
            self.response = message
        elif (
                message['type'] == 'websocket.http.response.body'
                and self.state in {WebsocketState.HANDSHAKE, WebsocketState.RESPONSE}
        ):
            if self.state == WebsocketState.HANDSHAKE:
                headers = chain(
                    (
                        (bytes(key).strip(), bytes(value).strip())
                        for key, value in self.response['headers']
                    ),
                    self.response_headers(),
                )
                self.write(
                    self.connection._upgrade_connection.send(
                        h11.Response(status_code=int(self.response['status']), headers=headers),
                    ),
                )
                self.state = WebsocketState.RESPONSE
            if (
                    not suppress_body('GET', self.response['status'])
                    and message.get('body', b'') != b''
            ):
                self.write(
                    self.connection._upgrade_connection.send(
                        h11.Data(data=bytes(message.get('body', b''))),
                    ),
                )
                await self.drain()
            if not message.get('more_body', False):
                if self.state != WebsocketState.CLOSED:
                    self.write(self.connection._upgrade_connection.send(h11.EndOfMessage()))
                    self.close()
                self.state = WebsocketState.CLOSED
        elif message['type'] == 'websocket.send' and self.state == WebsocketState.CONNECTED:
            data: Union[bytes, str]
            if message.get('bytes') is not None:
                data = bytes(message['bytes'])
            elif not isinstance(message['text'], str):
                raise ValueError('text should be a str')
            else:
                data = message['text']
            self.connection.send_data(data)
            self.write(self.connection.bytes_to_send())
            await self.drain()
        elif message['type'] == 'websocket.close':
            self.connection.close(int(message['code']))
            self.write(self.connection.bytes_to_send())
            self.close()
            self.state = WebsocketState.CLOSED
        else:
            raise Exception(
                f"Unexpected message type, {message['type']} given the state {self.state}",
            )
