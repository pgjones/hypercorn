import trio

from .context import Context
from ..config import Config
from ..events import Event, RawData
from ..typing import ASGIFramework
from ..utils import parse_socket_addr

MAX_RECV = 2 ** 16


class UDPServer:
    def __init__(
        self,
        app: ASGIFramework,
        config: Config,
        socket: trio.socket.socket,
        nursery: trio._core._run.Nursery,
    ) -> None:
        from ..protocol.quic import QuicProtocol  # h3/Quic is an optional part of Hypercorn

        self.app = app
        self.config = config
        self.nursery = nursery
        self.socket = trio.socket.from_stdlib_socket(socket)
        context = Context(nursery)
        server = parse_socket_addr(socket.family, socket.getsockname())
        self.protocol = QuicProtocol(self.app, self.config, context, server, self.protocol_send)

    async def run(
        self, task_status: trio._core._run._TaskStatus = trio.TASK_STATUS_IGNORED
    ) -> None:
        task_status.started()
        while True:
            data, address = await self.socket.recvfrom(MAX_RECV)
            await self.protocol.handle(RawData(data=data, address=address))

    async def protocol_send(self, event: Event) -> None:
        if isinstance(event, RawData):
            await self.socket.sendto(event.data, event.address)
