from typing import Optional, Type

import h11
import trio

from .base import HTTPServer
from ..common.h11 import H11Mixin
from ..common.run import WrongProtocolError
from ..config import Config
from ..typing import ASGIFramework, H11SendableEvent
from ..utils import ASGIState

MAX_RECV = 2 ** 16


class MustCloseError(Exception):
    pass


class H11Server(HTTPServer, H11Mixin):

    def __init__(
            self,
            app: Type[ASGIFramework],
            config: Config,
            stream: trio.abc.Stream,
    ) -> None:
        super().__init__(stream, 'h11')
        self.app = app
        self.config = config
        self.connection = h11.Connection(
            h11.SERVER, max_incomplete_event_size=self.config.h11_max_incomplete_size,
        )
        self.app_queue = trio.Queue(10)
        self.response: Optional[dict] = None
        self.scope: Optional[dict] = None
        self.state = ASGIState.REQUEST

    async def handle_connection(self) -> None:
        try:
            # Loop over the requests in order of receipt (either
            # pipelined or due to keep-alive).
            while True:
                with trio.fail_after(self.config.keep_alive_timeout):
                    request = await self.read_request()
                self.maybe_upgrade_request(request)

                async with trio.open_nursery() as nursery:
                    nursery.start_soon(self.handle_request, request)
                    await self.read_body()

                self.recycle_or_close()
        except (trio.BrokenResourceError, trio.ClosedResourceError):
            self.app_queue.put_nowait({'type': 'http.disconnect'})
            await self.aclose()
        except (trio.TooSlowError, MustCloseError):
            await self.aclose()
        except WrongProtocolError:
            raise  # Do not close the connection

    async def read_request(self) -> h11.Request:
        while True:
            try:
                event = self.connection.next_event()
            except h11.RemoteProtocolError:
                await self.send_error()
                raise MustCloseError()
            else:
                if event is h11.NEED_DATA:
                    await self.read_data()
                elif isinstance(event, h11.Request):
                    return event
                else:
                    raise MustCloseError()

    async def read_body(self) -> None:
        while True:
            try:
                event = self.connection.next_event()
            except h11.RemoteProtocolError:
                await self.send_error()
                self.app_queue.put_nowait({'type': 'http.disconnect'})
                raise MustCloseError()
            else:
                if event is h11.NEED_DATA:
                    await self.read_data()
                elif isinstance(event, h11.EndOfMessage):
                    self.app_queue.put_nowait({
                        'type': 'http.request',
                        'body': b'',
                        'more_body': False,
                    })
                    return
                elif isinstance(event, h11.Data):
                    self.app_queue.put_nowait({
                        'type': 'http.request',
                        'body': event.data,
                        'more_body': True,
                    })
                elif isinstance(event, h11.ConnectionClosed) or event is h11.PAUSED:
                    break

    def recycle_or_close(self) -> None:
        if self.connection.our_state in {h11.ERROR, h11.MUST_CLOSE}:
            raise MustCloseError()
        elif self.connection.our_state is h11.DONE:
            self.connection.start_next_cycle()
            self.app_queue = trio.Queue(10)
            self.response = None
            self.scope = None
            self.state = ASGIState.REQUEST

    async def asend(self, event: H11SendableEvent) -> None:
        data = self.connection.send(event)
        try:
            await self.stream.send_all(data)
        except trio.BrokenResourceError:
            pass

    async def close(self) -> None:
        await super().aclose()

    async def read_data(self) -> None:
        if self.connection.they_are_waiting_for_100_continue:
            await self.asend(
                h11.InformationalResponse(status_code=100, headers=self.response_headers()),
            )
        data = await self.stream.receive_some(MAX_RECV)
        self.connection.receive_data(data)

    async def send_error(self) -> None:
        await self.asend(self.error_response(400))
        await self.asend(h11.EndOfMessage())

    @property
    def scheme(self) -> str:
        return 'https' if self._is_ssl else 'http'
