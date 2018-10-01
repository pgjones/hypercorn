from itertools import chain
from typing import List, Optional, Tuple, Type, Union

import h11
import trio

from ..common.h11 import H11Mixin
from ..config import Config
from ..typing import ASGIFramework
from ..utils import ASGIState, parse_socket_addr, response_headers

MAX_RECV = 2 ** 16


class ConnectionClosed(Exception):
    pass


class H11Server(H11Mixin):

    def __init__(
            self,
            app: Type[ASGIFramework],
            config: Config,
            stream: trio.abc.Stream,
    ) -> None:
        self.app = app
        self.config = config
        self.connection = h11.Connection(
            h11.SERVER, max_incomplete_event_size=self.config.h11_max_incomplete_size,
        )
        self.app_queue = trio.Queue(10)
        self.response: Optional[dict] = None
        self.scope: Optional[dict] = None
        self.state = ASGIState.REQUEST
        self.stream = stream

    async def handle_connection(self):
        try:
            while True:
                with trio.fail_after(self.config.keep_alive_timeout):
                    request = await self.read_request()
                self.maybe_upgrade_request(request)
                async with trio.open_nursery() as nursery:
                    nursery.start_soon(self.handle_request, request)
                    await self.read_body()
                if self.connection.our_state is h11.MUST_CLOSE:
                    break
                elif self.connection.our_state is h11.DONE:
                    self.recycle()
        except (
                ConnectionClosed, trio.TooSlowError, trio.BrokenResourceError,
                trio.ClosedResourceError,
        ):
            await self.aclose()

    async def asend(
            self,
            event: Union[h11.Data, h11.EndOfMessage, h11.InformationalResponse, h11.Response],
    ) -> None:
        data = self.connection.send(event)
        try:
            await self.stream.send_all(data)
        except trio.BrokenResourceError:
            pass

    async def aclose(self) -> None:
        self.app_queue.put_nowait({'type': 'http.disconnect'})
        try:
            await self.stream.send_eof()
        except (trio.BrokenResourceError, AttributeError):
            # They're already gone, nothing to do
            # Or it is a SSL stream
            return
        await self.stream.aclose()

    def recycle(self) -> None:
        """Recycle the state in order to accept a new request.

        This is vital if this connection can be re-used.
        """
        self.connection.start_next_cycle()
        self.app_queue = trio.Queue(10)
        self.response = None
        self.scope = None
        self.state = ASGIState.REQUEST

    async def read_data(self) -> None:
        if self.connection.they_are_waiting_for_100_continue:
            await self.asend(
                h11.InformationalResponse(status_code=100, headers=self.response_headers()),
            )
        try:
            data = await self.stream.receive_some(MAX_RECV)
        except ConnectionError:
            data = b''
        self.connection.receive_data(data)

    async def read_request(self) -> h11.Request:
        while True:
            try:
                event = self.connection.next_event()
            except h11.RemoteProtocolError:
                await self.send_error()
                raise ConnectionClosed()
            else:
                if event is h11.NEED_DATA:
                    await self.read_data()
                elif isinstance(event, h11.Request):
                    return event
                elif isinstance(event, h11.ConnectionClosed):
                    raise ConnectionClosed()

    async def send_error(self) -> None:
        await self.asend(
            h11.Response(
                status_code=400, headers=chain(
                    [(b'content-length', b'0'), (b'connection', b'close')],
                    self.response_headers(),
                ),
            ),
        )
        await self.asend(h11.EndOfMessage())

    async def read_body(self) -> None:
        while True:
            try:
                event = self.connection.next_event()
            except h11.RemoteProtocolError:
                await self.send_error()
                raise
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
                elif isinstance(event, h11.ConnectionClosed):
                    raise ConnectionClosed()

    @property
    def _is_ssl(self) -> bool:
        return isinstance(self.stream, trio.ssl.SSLStream)

    @property
    def scheme(self) -> str:
        return 'https' if self._is_ssl else 'http'

    @property
    def client(self) -> Tuple[str, int]:
        if self._is_ssl:
            socket = self.stream.transport_stream.socket
        else:
            socket = self.stream.socket
        return parse_socket_addr(socket.family, socket.getpeername())

    @property
    def server(self) -> Tuple[str, int]:
        if self._is_ssl:
            socket = self.stream.transport_stream.socket
        else:
            socket = self.stream.socket
        return parse_socket_addr(socket.family, socket.getsockname())

    def response_headers(self) -> List[Tuple[bytes, bytes]]:
        return response_headers('h11')
