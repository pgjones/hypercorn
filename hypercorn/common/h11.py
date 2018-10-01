from itertools import chain
from time import time
from typing import List, Optional, Tuple, Type, Union
from urllib.parse import unquote

import h11

from .run import H2CProtocolRequired, WebsocketProtocolRequired
from ..config import Config
from ..logging import AccessLogAtoms
from ..typing import ASGIFramework
from ..utils import ASGIState, suppress_body


class H11Mixin:
    app: Type[ASGIFramework]
    config: Config
    response: Optional[dict]
    state: ASGIState

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

    def maybe_upgrade_request(self, event: h11.Request) -> None:
        upgrade_value = ''
        connection_value = ''
        has_body = False
        for name, value in event.headers:
            sanitised_name = name.decode().lower()
            if sanitised_name == 'upgrade':
                upgrade_value = value.decode().strip()
            elif sanitised_name == 'connection':
                connection_value = value.decode().strip()
            elif sanitised_name == 'content-length':
                has_body = True
            elif sanitised_name == 'transfer-encoding':
                has_body = True

        connection_tokens = connection_value.lower().split(',')
        if (
                any(token.strip() == 'upgrade' for token in connection_tokens) and
                upgrade_value.lower() == 'websocket' and
                event.method.decode().upper() == 'GET'
        ):
            raise WebsocketProtocolRequired(event)
        # h2c Upgrade requests with a body are a pain as the body must
        # be fully recieved in HTTP/1.1 before the upgrade response
        # and HTTP/2 takes over, so Hypercorn ignores the upgrade and
        # responds in HTTP/1.1. Use a preflight OPTIONS request to
        # initiate the upgrade if really required (or just use h2).
        elif upgrade_value.lower() == 'h2c' and not has_body:
            self.asend(
                h11.InformationalResponse(
                    status_code=101, headers=[(b'upgrade', b'h2c')] + self.response_headers(),
                ),
            )
            raise H2CProtocolRequired(event)

    async def handle_request(self, request: h11.Request) -> None:
        path, _, query_string = request.target.partition(b'?')
        self.scope = {
            'type': 'http',
            'http_version': request.http_version.decode(),
            'asgi': {'version': '2.0'},
            'method': request.method.decode().upper(),
            'scheme': self.scheme,
            'path': unquote(path.decode('ascii')),
            'query_string': query_string,
            'root_path': self.config.root_path,
            'headers': request.headers,
            'client': self.client,
            'server': self.server,
        }
        await self.handle_asgi_app()

    async def asend(
        self,
        event: Union[h11.Data, h11.EndOfMessage, h11.InformationalResponse, h11.Response],
    ) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def handle_asgi_app(self) -> None:
        start_time = time()
        must_close = False
        try:
            asgi_instance = self.app(self.scope)
            await asgi_instance(self.asgi_receive, self.asgi_send)
        except Exception as error:
            if self.config.error_logger is not None:
                self.config.error_logger.exception('Error in ASGI Framework')
            must_close = True

        # If the application doesn't send a response, it has errored -
        # send a 500 for it and force close the connection.
        if self.response is None:
            must_close = True
            await self.asend(h11.Response(status_code=500, headers=self.response_headers()))
            await self.asend(h11.EndOfMessage())
            self.response = {'status': 500, 'headers': []}

        if self.config.access_logger is not None:
            self.config.access_logger.info(
                self.config.access_log_format,
                AccessLogAtoms(self.scope, self.response, time() - start_time),
            )

        if must_close:
            await self.aclose()

    async def asgi_receive(self) -> dict:
        """Called by the ASGI instance to receive a message."""
        return await self.app_queue.get()  # type: ignore

    async def asgi_send(self, message: dict) -> None:
        """Called by the ASGI instance to send a message."""
        if message['type'] == 'http.response.start' and self.state == ASGIState.REQUEST:
            self.response = message
        elif (
                message['type'] == 'http.response.body'
                and self.state in {ASGIState.REQUEST, ASGIState.RESPONSE}
        ):
            if self.state == ASGIState.REQUEST:
                headers = chain(
                    (
                        (bytes(key).strip(), bytes(value).strip())
                        for key, value in self.response['headers']
                    ),
                    self.response_headers(),
                )
                await self.asend(
                    h11.Response(status_code=int(self.response['status']), headers=headers),
                )
                self.state = ASGIState.RESPONSE
            if (
                    not suppress_body(self.scope['method'], int(self.response['status']))
                    and message.get('body', b'') != b''
            ):
                await self.asend(h11.Data(data=bytes(message['body'])))
            if not message.get('more_body', False):
                if self.state != ASGIState.CLOSED:
                    await self.asend(h11.EndOfMessage())
                    self.app_queue.put_nowait({'type': 'http.disconnect'})  # type: ignore
                    self.state = ASGIState.CLOSED
        else:
            raise Exception(
                f"Unexpected message type, {message['type']} given the state {self.state}",
            )
