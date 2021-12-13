from __future__ import annotations

from functools import partial
from typing import Awaitable, Callable, Dict, Optional, Tuple

from aioquic.buffer import Buffer
from aioquic.h3.connection import H3_ALPN
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import QuicConnection
from aioquic.quic.events import (
    ConnectionIdIssued,
    ConnectionIdRetired,
    ConnectionTerminated,
    ProtocolNegotiated,
)
from aioquic.quic.packet import (
    encode_quic_version_negotiation,
    PACKET_TYPE_INITIAL,
    pull_quic_header,
)

from .h3 import H3Protocol
from ..config import Config
from ..events import Closed, Event, RawData
from ..typing import ASGIFramework, TaskGroup, WorkerContext


class QuicProtocol:
    def __init__(
        self,
        app: ASGIFramework,
        config: Config,
        context: WorkerContext,
        task_group: TaskGroup,
        server: Optional[Tuple[str, int]],
        send: Callable[[Event], Awaitable[None]],
    ) -> None:
        self.app = app
        self.config = config
        self.context = context
        self.connections: Dict[bytes, QuicConnection] = {}
        self.http_connections: Dict[QuicConnection, H3Protocol] = {}
        self.send = send
        self.server = server
        self.task_group = task_group

        self.quic_config = QuicConfiguration(alpn_protocols=H3_ALPN, is_client=False)
        self.quic_config.load_cert_chain(certfile=config.certfile, keyfile=config.keyfile)

    @property
    def idle(self) -> bool:
        return len(self.connections) == 0 and len(self.http_connections) == 0

    async def handle(self, event: Event) -> None:
        if isinstance(event, RawData):
            try:
                header = pull_quic_header(Buffer(data=event.data), host_cid_length=8)
            except ValueError:
                return
            if (
                header.version is not None
                and header.version not in self.quic_config.supported_versions
            ):
                data = encode_quic_version_negotiation(
                    source_cid=header.destination_cid,
                    destination_cid=header.source_cid,
                    supported_versions=self.quic_config.supported_versions,
                )
                await self.send(RawData(data=data, address=event.address))
                return

            connection = self.connections.get(header.destination_cid)
            if (
                connection is None
                and len(event.data) >= 1200
                and header.packet_type == PACKET_TYPE_INITIAL
            ):
                connection = QuicConnection(
                    configuration=self.quic_config,
                    original_destination_connection_id=header.destination_cid,
                )
                self.connections[header.destination_cid] = connection
                self.connections[connection.host_cid] = connection

            if connection is not None:
                connection.receive_datagram(event.data, event.address, now=self.context.time())
                await self._handle_events(connection, event.address)
        elif isinstance(event, Closed):
            pass

    async def send_all(self, connection: QuicConnection) -> None:
        for data, address in connection.datagrams_to_send(now=self.context.time()):
            await self.send(RawData(data=data, address=address))

    async def _handle_events(
        self, connection: QuicConnection, client: Optional[Tuple[str, int]] = None
    ) -> None:
        event = connection.next_event()
        while event is not None:
            if isinstance(event, ConnectionTerminated):
                pass
            elif isinstance(event, ProtocolNegotiated):
                self.http_connections[connection] = H3Protocol(
                    self.app,
                    self.config,
                    self.context,
                    self.task_group,
                    client,
                    self.server,
                    connection,
                    partial(self.send_all, connection),
                )
            elif isinstance(event, ConnectionIdIssued):
                self.connections[event.connection_id] = connection
            elif isinstance(event, ConnectionIdRetired):
                del self.connections[event.connection_id]

            if connection in self.http_connections:
                await self.http_connections[connection].handle(event)

            event = connection.next_event()

        await self.send_all(connection)

        timer = connection.get_timer()
        if timer is not None:
            self.task_group.spawn(self._handle_timer, timer, connection)

    async def _handle_timer(self, timer: float, connection: QuicConnection) -> None:
        wait = max(0, timer - self.context.time())
        await self.context.sleep(wait)
        if connection._close_at is not None:
            connection.handle_timer(now=self.context.time())
            await self._handle_events(connection, None)
