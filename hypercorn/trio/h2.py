from typing import Dict, Optional, Type

import h11
import h2.config
import h2.connection
import h2.events
import h2.exceptions
import trio

from .base import HTTPServer
from ..common.h2 import H2Mixin, H2StreamBase
from ..config import Config
from ..typing import ASGIFramework

MAX_RECV = 2 ** 16


class ConnectionClosed(Exception):
    pass


class Stream(H2StreamBase):

    def __init__(self) -> None:
        super().__init__()
        self.app_queue = trio.Queue(10)
        self.blocked: Optional[trio.Event] = None

    def unblock(self) -> None:
        if self.blocked is not None:
            self.blocked.set()
            self.blocked = None

    async def block(self) -> None:
        self.blocked = trio.Event()
        await self.blocked.wait()


class H2Server(HTTPServer, H2Mixin):

    def __init__(
            self,
            app: Type[ASGIFramework],
            config: Config,
            stream: trio.abc.Stream,
            *,
            upgrade_request: Optional[h11.Request]=None,
    ) -> None:
        super().__init__(stream, 'h2')
        self.app = app
        self.config = config

        self.streams: Dict[int, Stream] = {}  # type: ignore
        self._send_queue = trio.Queue(10)

        self.connection = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=False, header_encoding=None),
        )
        self.connection.DEFAULT_MAX_INBOUND_FRAME_SIZE = config.h2_max_inbound_frame_size

        if upgrade_request is None:
            self.connection.initiate_connection()

    def create_stream(self) -> Stream:  # type: ignore
        return Stream()

    async def handle_connection(self) -> None:
        await self.asend()
        try:
            async with trio.open_nursery() as nursery:
                nursery.start_soon(self.send_queue)
                while True:
                    data = await self.read_data()
                    await self.handle_data(data, nursery)
        except (
                ConnectionClosed, trio.TooSlowError, trio.BrokenResourceError,
                trio.ClosedResourceError,
        ):
            await self.aclose()

    async def handle_data(self, data: bytes, nursery: trio._core._run.Nursery) -> None:
        try:
            events = self.connection.receive_data(data)
        except h2.exceptions.ProtocolError:
            await self.asend()
            await self.aclose()
        else:
            for event in events:
                if isinstance(event, h2.events.RequestReceived):
                    self.streams[event.stream_id] = Stream()
                    nursery.start_soon(self.handle_request, event)
                elif isinstance(event, h2.events.DataReceived):
                    self.streams[event.stream_id].append(event.data)
                elif isinstance(event, h2.events.StreamReset):
                    self.streams[event.stream_id].close()
                elif isinstance(event, h2.events.StreamEnded):
                    self.streams[event.stream_id].complete()
                elif isinstance(event, h2.events.WindowUpdated):
                    self.window_updated(event.stream_id)
                elif isinstance(event, h2.events.ConnectionTerminated):
                    await self.aclose()
                    raise ConnectionClosed()
                await self.asend()

    async def read_data(self) -> bytes:
        try:
            return await self.stream.receive_some(MAX_RECV)
        except ConnectionError:
            return b''

    async def asend(self) -> None:
        data = self.connection.data_to_send()
        await self._send_queue.put(data)

    async def send_queue(self) -> None:
        while True:
            data = await self._send_queue.get()
            await self.stream.send_all(data)

    @property
    def scheme(self) -> str:
        return 'https' if self._is_ssl else 'http'
