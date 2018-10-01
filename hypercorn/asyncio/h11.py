import asyncio
from itertools import chain
from typing import Optional, Type, Union

import h11

from .base import HTTPServer
from ..common.h11 import H11Mixin
from ..config import Config
from ..typing import ASGIFramework
from ..utils import ASGIState


class H11Server(HTTPServer, H11Mixin):

    def __init__(
            self,
            app: Type[ASGIFramework],
            loop: asyncio.AbstractEventLoop,
            config: Config,
            transport: asyncio.BaseTransport,
    ) -> None:
        super().__init__(loop, config, transport, 'h11')
        self.app = app
        self.connection = h11.Connection(
            h11.SERVER, max_incomplete_event_size=self.config.h11_max_incomplete_size,
        )

        self.app_queue: asyncio.Queue = asyncio.Queue(loop=loop)
        self.response: Optional[dict] = None
        self.scope: Optional[dict] = None
        self.state = ASGIState.REQUEST

    def data_received(self, data: bytes) -> None:
        super().data_received(data)
        self.connection.receive_data(data)
        self.handle_events()

    def eof_received(self) -> bool:
        self.connection.receive_data(b'')
        return True

    def send(
            self,
            event: Union[h11.Data, h11.EndOfMessage, h11.InformationalResponse, h11.Response],
    ) -> None:
        self.write(self.connection.send(event))  # type: ignore

    async def asend(
            self,
            event: Union[h11.Data, h11.EndOfMessage, h11.InformationalResponse, h11.Response],
    ) -> None:
        self.send(event)
        await self.drain()

    def close(self) -> None:
        if not self.closed:
            self.app_queue.put_nowait({'type': 'http.disconnect'})
        super().close()

    async def aclose(self) -> None:
        self.close()

    def handle_events(self) -> None:
        while True:
            if self.connection.they_are_waiting_for_100_continue:
                self.send(
                    h11.InformationalResponse(status_code=100, headers=self.response_headers()),
                )
            try:
                event = self.connection.next_event()
            except h11.RemoteProtocolError:
                self.handle_error()
                self.close()
                break
            else:
                if isinstance(event, h11.Request):
                    self.stop_keep_alive_timeout()
                    self.maybe_upgrade_request(event)  # Raises on upgrade
                    self.task = self.loop.create_task(self.handle_request(event))
                    self.task.add_done_callback(self.after_request)
                elif isinstance(event, h11.EndOfMessage):
                    self.app_queue.put_nowait({
                        'type': 'http.request',
                        'body': b'',
                        'more_body': False,
                    })
                elif isinstance(event, h11.Data):
                    self.app_queue.put_nowait({
                        'type': 'http.request',
                        'body': event.data,
                        'more_body': True,
                    })
                elif (
                        isinstance(event, h11.ConnectionClosed)
                        or event is h11.NEED_DATA
                        or event is h11.PAUSED
                ):
                    break
        if self.connection.our_state is h11.MUST_CLOSE:
            self.close()

    def handle_error(self) -> None:
        self.send(
            h11.Response(
                status_code=400, headers=chain(
                    [(b'content-length', b'0'), (b'connection', b'close')],
                    self.response_headers(),
                ),
            ),
        )
        self.send(h11.EndOfMessage())

    def after_request(self, future: asyncio.Future) -> None:
        if self.connection.our_state is h11.DONE:
            self.recycle()
        self.handle_events()

    def recycle(self) -> None:
        """Recycle the state in order to accept a new request.

        This is vital if this connection can be re-used.
        """
        self.connection.start_next_cycle()
        self.app_queue = asyncio.Queue(loop=self.loop)
        self.response = None
        self.scope = None
        self.state = ASGIState.REQUEST
        self.start_keep_alive_timeout()
