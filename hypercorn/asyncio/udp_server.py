import asyncio
from functools import partial
from typing import Awaitable, Callable, Optional, Tuple

from .spawn_app import spawn_app
from ..config import Config
from ..events import Closed, Event, RawData
from ..typing import ASGIFramework


class UDPServer(asyncio.DatagramProtocol):
    def __init__(self, app: ASGIFramework, loop: asyncio.AbstractEventLoop, config: Config) -> None:
        from ..protocol.quic import QuicProtocol  # h3/Quic is an optional part of Hypercorn

        self.app = app
        self.config = config
        self.loop = loop
        self.protocol = QuicProtocol(
            config,
            partial(spawn_app, self.app, self.loop, self.config),
            self.protocol_send,
            self._call_at,
            self.loop.time,
        )
        self.protocol_queue: asyncio.Queue = asyncio.Queue(10)
        self.transport: Optional[asyncio.DatagramTransport] = None

        self.loop.create_task(self._consume_events())

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore
        self.transport = transport

    def datagram_received(self, data: bytes, address: Tuple[bytes, str]) -> None:  # type: ignore
        try:
            self.protocol_queue.put_nowait(RawData(data=data, address=address))  # type: ignore
        except asyncio.QueueFull:
            pass  # Just throw the data away, is UDP

    async def protocol_send(self, event: Event) -> None:
        if isinstance(event, RawData):
            self.transport.sendto(event.data, event.address)

    async def _consume_events(self) -> None:
        while True:
            event = await self.protocol_queue.get()
            await self.protocol.handle(event)
            if isinstance(event, Closed):
                break

    def _call_at(self, time: float, func: Callable[[], Awaitable[None]]) -> None:
        wait = max(0, time - self.loop.time())

        async def _call_at() -> None:
            await asyncio.sleep(wait)
            await func()

        self.loop.create_task(_call_at())
