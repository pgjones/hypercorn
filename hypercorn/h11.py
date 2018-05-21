import asyncio
from itertools import chain
from typing import Type, Union

import h11

from .config import Config
from .http import HTTPRequestResponseServer
from .typing import ASGIFramework


class WrongProtocolError(Exception):

    def __init__(self, request: h11.Request) -> None:
        self.request = request


class WebsocketProtocolRequired(WrongProtocolError):
    pass


class H2CProtocolRequired(WrongProtocolError):
    pass


class H11Server(HTTPRequestResponseServer):

    def __init__(
            self,
            app: Type[ASGIFramework],
            loop: asyncio.AbstractEventLoop,
            config: Config,
            transport: asyncio.BaseTransport,
    ) -> None:
        super().__init__(app, loop, config, transport, 'h11')
        self.connection = h11.Connection(
            h11.SERVER, max_incomplete_event_size=self.config.h11_max_incomplete_size,
        )

    def data_received(self, data: bytes) -> None:
        super().data_received(data)
        self.connection.receive_data(data)
        self._handle_events()

    def eof_received(self) -> bool:
        self.connection.receive_data(b'')
        return True

    def _handle_events(self) -> None:
        while True:
            if self.connection.they_are_waiting_for_100_continue:
                self._send(
                    h11.InformationalResponse(status_code=100, headers=self.response_headers()),
                )
            try:
                event = self.connection.next_event()
            except h11.RemoteProtocolError:
                self._handle_error()
                self.close()
                break
            else:
                if isinstance(event, h11.Request):
                    self._maybe_upgrade_request(event)
                    self.handle_request(
                        0, event.method, event.target, event.http_version.decode(), event.headers,
                    )
                elif isinstance(event, h11.EndOfMessage):
                    self.streams[0].complete()
                elif isinstance(event, h11.Data):
                    self.streams[0].append(event.data)
                elif event is h11.NEED_DATA or event is h11.PAUSED:
                    break
                elif isinstance(event, h11.ConnectionClosed):
                    break
        if self.connection.our_state is h11.MUST_CLOSE:
            self.close()

    def _maybe_upgrade_request(self, event: h11.Request) -> None:
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
            self._keep_alive_timeout_handle.cancel()
            raise WebsocketProtocolRequired(event)
        # h2c Upgrade requests with a body are a pain as the body must
        # be fully recieved in HTTP/1.1 before the upgrade response
        # and HTTP/2 takes over, so Hypercorn ignores the upgrade and
        # responds in HTTP/1.1. Use a preflight OPTIONS request to
        # initiate the upgrade if really required (or just use h2).
        elif upgrade_value.lower() == 'h2c' and not has_body:
            self._keep_alive_timeout_handle.cancel()
            self._send(
                h11.InformationalResponse(
                    status_code=101, headers=[(b'upgrade', b'h2c')] + self.response_headers(),
                ),
            )
            raise H2CProtocolRequired(event)

    def after_request(self, stream_id: int, future: asyncio.Future) -> None:
        super().after_request(stream_id, future)
        if self.connection.our_state is h11.DONE:
            self.connection.start_next_cycle()
        self._handle_events()

    def _handle_error(self) -> None:
        self._send(
            h11.Response(
                status_code=400, headers=chain(
                    [(b'content-length', b'0'), (b'connection', b'close')],
                    self.response_headers(),
                ),
            ),
        )
        self._send(h11.EndOfMessage())

    def _send(
            self, event: Union[h11.Data, h11.EndOfMessage, h11.InformationalResponse, h11.Response],
    ) -> None:
        self.write(self.connection.send(event))  # type: ignore

    async def begin_response(self, stream_id: int) -> None:
        response = self.streams[stream_id].response
        headers = chain(
            ((key.strip(), value.strip()) for key, value in response['headers']),
            self.response_headers(),
        )
        self._send(h11.Response(status_code=response['status'], headers=headers))

    async def send_body(self, stream_id: int, data: bytes) -> None:
        self._send(h11.Data(data=data))
        await self.drain()

    async def end_response(self, stream_id: int) -> None:
        self._send(h11.EndOfMessage())
