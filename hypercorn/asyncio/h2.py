import asyncio
from time import time
from typing import Dict, List, Optional, Type

import h11
import h2.config
import h2.connection
import h2.events
import h2.exceptions

from .base import HTTPServer
from ..common.h2 import H2Mixin, H2StreamBase
from ..config import Config
from ..typing import ASGIFramework


class Stream(H2StreamBase):

    def __init__(self) -> None:
        super().__init__()
        self.app_queue: asyncio.Queue = asyncio.Queue()
        self.blocked: Optional[asyncio.Event] = None

    def unblock(self) -> None:
        if self.blocked is not None:
            self.blocked.set()
            self.blocked = None

    async def block(self) -> None:
        self.blocked = asyncio.Event()
        await self.blocked.wait()


class H2Server(HTTPServer, H2Mixin):

    def __init__(
            self,
            app: Type[ASGIFramework],
            loop: asyncio.AbstractEventLoop,
            config: Config,
            transport: asyncio.BaseTransport,
            *,
            upgrade_request: Optional[h11.Request]=None,
    ) -> None:
        super().__init__(loop, config, transport, 'h2')
        self.app = app
        self.streams: Dict[int, Stream] = {}  # type: ignore

        self.connection = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=False, header_encoding=None),
        )
        self.connection.DEFAULT_MAX_INBOUND_FRAME_SIZE = config.h2_max_inbound_frame_size

        if upgrade_request is None:
            self.connection.initiate_connection()
        else:
            settings = ''
            headers = []
            for name, value in upgrade_request.headers:
                if name.lower() == b'http2-settings':
                    settings = value.decode()
                elif name.lower() == b'host':
                    headers.append((b':authority', value))
                headers.append((name, value))
            headers.append((b':method', upgrade_request.method))
            headers.append((b':path', upgrade_request.target))
            self.connection.initiate_upgrade_connection(settings)
            event = h2.events.RequestReceived()
            event.stream_id = 1
            event.headers = headers
            self.streams[event.stream_id] = Stream()
            self.loop.create_task(self.handle_request(event, complete=True))
        self.send()

    def data_received(self, data: bytes) -> None:
        super().data_received(data)
        self.last_activity = time()
        try:
            events = self.connection.receive_data(data)
        except h2.exceptions.ProtocolError:
            self.send()
            self.close()
        else:
            self.handle_events(events)
            self.send()

    def close(self) -> None:
        for stream in self.streams.values():
            stream.close()
        super().close()

    async def aclose(self) -> None:
        self.close()

    def create_stream(self) -> Stream:  # type: ignore
        return Stream()

    def handle_events(self, events: List[h2.events.Event]) -> None:
        for event in events:
            if isinstance(event, h2.events.RequestReceived):
                self.stop_keep_alive_timeout()
                self.streams[event.stream_id] = Stream()
                self.task = self.loop.create_task(self.handle_request(event))
                self.task.add_done_callback(self.after_request)
            elif isinstance(event, h2.events.DataReceived):
                self.streams[event.stream_id].append(event.data)
            elif isinstance(event, h2.events.StreamReset):
                self.streams[event.stream_id].close()
            elif isinstance(event, h2.events.StreamEnded):
                self.streams[event.stream_id].complete()
            elif isinstance(event, h2.events.WindowUpdated):
                self.window_updated(event.stream_id)
            elif isinstance(event, h2.events.ConnectionTerminated):
                self.close()
                return

            self.send()

    def after_request(self, future: asyncio.Future) -> None:
        if len(self.streams) == 0:
            self.start_keep_alive_timeout()

    def send(self) -> None:
        self.last_activity = time()
        self.write(self.connection.data_to_send())  # type: ignore

    async def asend(self) -> None:
        await self.drain()
        self.send()

    @property
    def scheme(self) -> str:
        return 'https' if self.ssl_info is not None else 'http'
