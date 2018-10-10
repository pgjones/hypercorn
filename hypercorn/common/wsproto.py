from functools import partial
from itertools import chain
from time import time
from typing import List, Optional, Tuple, Type, Union
from urllib.parse import unquote

import h11
import wsproto

from ..config import Config
from ..logging import AccessLogAtoms
from ..typing import ASGIFramework, Queue
from ..utils import suppress_body, WebsocketState


class WebsocketMixin:
    app: Type[ASGIFramework]
    app_queue: Queue
    config: Config
    connection: wsproto.connection.WSConnection
    response: Optional[dict]
    state: WebsocketState

    @property
    def scheme(self) -> str:
        pass

    @property
    def client(self) -> Tuple[str, int]:
        pass

    @property
    def server(self) -> Tuple[str, int]:
        pass

    def response_headers(self) -> List[Tuple[bytes, bytes]]:
        pass

    async def handle_websocket(self, event: wsproto.events.ConnectionRequested) -> None:
        path, _, query_string = event.h11request.target.partition(b'?')
        self.scope = {
            'type': 'websocket',
            'asgi': {'version': '2.0'},
            'scheme': self.scheme,
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
        await self.handle_asgi_app(event)

    async def awrite(self, data: bytes) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def handle_asgi_app(self, event: wsproto.events.ConnectionRequested) -> None:
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
                await self.awrite(self.connection.bytes_to_send())
            await self.aclose()
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
            await self.awrite(self.connection.bytes_to_send())
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
                await self.awrite(
                    self.connection._upgrade_connection.send(
                        h11.Response(status_code=int(self.response['status']), headers=headers),
                    ),
                )
                self.state = WebsocketState.RESPONSE
            if (
                    not suppress_body('GET', self.response['status'])
                    and message.get('body', b'') != b''
            ):
                await self.awrite(
                    self.connection._upgrade_connection.send(
                        h11.Data(data=bytes(message.get('body', b''))),
                    ),
                )
            if not message.get('more_body', False):
                if self.state != WebsocketState.CLOSED:
                    await self.awrite(self.connection._upgrade_connection.send(h11.EndOfMessage()))
                    await self.aclose()
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
            await self.awrite(self.connection.bytes_to_send())
        elif message['type'] == 'websocket.close':
            self.connection.close(int(message['code']))
            await self.awrite(self.connection.bytes_to_send())
            await self.aclose()
            self.state = WebsocketState.CLOSED
        else:
            raise Exception(
                f"Unexpected message type, {message['type']} given the state {self.state}",
            )
