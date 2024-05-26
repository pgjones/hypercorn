from __future__ import annotations

from functools import partial
from secrets import token_bytes
from typing import Awaitable, Callable, Dict, Optional, Set, Tuple

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
    encode_quic_retry,
    PACKET_TYPE_INITIAL,
    pull_quic_header,
)
from aioquic.quic.retry import QuicRetryTokenHandler
from aioquic.tls import SessionTicket

from .h3 import H3Protocol
from ..config import Config
from ..events import Closed, Event, RawData
from ..typing import AppWrapper, TaskGroup, WorkerContext, Timer


class ConnectionState:
    def __init__(self, connection: QuicConnection):
        self.connection = connection
        self.timer: Optional[Timer] = None
        self.cids: Set[bytes] = set()
        self.h3_protocol: Optional[H3Protocol] = None

    def add_cid(self, cid: bytes) -> None:
        self.cids.add(cid)

    def remove_cid(self, cid: bytes) -> None:
        self.cids.remove(cid)


class QuicProtocol:
    def __init__(
        self,
        app: AppWrapper,
        config: Config,
        context: WorkerContext,
        task_group: TaskGroup,
        server: Optional[Tuple[str, int]],
        send: Callable[[Event], Awaitable[None]],
    ) -> None:
        self.app = app
        self.config = config
        self.context = context
        self.connections: Dict[bytes, ConnectionState] = {}
        self.send = send
        self.server = server
        self.task_group = task_group

        self.quic_config = QuicConfiguration(alpn_protocols=H3_ALPN, is_client=False)
        self.quic_config.load_cert_chain(certfile=config.certfile, keyfile=config.keyfile)
        self.retry: Optional[QuicRetryTokenHandler]
        if config.quic_retry:
            self.retry = QuicRetryTokenHandler()
        else:
            self.retry = None
        self.session_tickets: Dict[bytes, bytes] = {}

    @property
    def idle(self) -> bool:
        return len(self.connections) == 0

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

            state = self.connections.get(header.destination_cid)
            if state is not None:
                connection = state.connection
            else:
                connection = None
            if (
                state is None
                and len(event.data) >= 1200
                and header.packet_type == PACKET_TYPE_INITIAL
                and not self.context.terminated.is_set()
            ):
                cid = header.destination_cid
                retry_cid = None
                if self.retry is not None:
                    if not header.token:
                        if header.version is None:
                            return
                        source_cid = token_bytes(8)
                        wire = encode_quic_retry(
                            version=header.version,
                            source_cid=source_cid,
                            destination_cid=header.source_cid,
                            original_destination_cid=header.destination_cid,
                            retry_token=self.retry.create_token(
                                event.address, header.destination_cid, source_cid
                            ),
                        )
                        await self.send(RawData(data=wire, address=event.address))
                        return
                    else:
                        try:
                            (cid, retry_cid) = self.retry.validate_token(
                                event.address, header.token
                            )
                            if self.connections.get(cid) is not None:
                                # duplicate!
                                return
                        except ValueError:
                            return
                fetcher: Optional[Callable]
                handler: Optional[Callable]
                if self.config.quic_max_saved_sessions > 0:
                    fetcher = self._get_session_ticket
                    handler = self._store_session_ticket
                else:
                    fetcher = None
                    handler = None
                connection = QuicConnection(
                    configuration=self.quic_config,
                    original_destination_connection_id=cid,
                    retry_source_connection_id=retry_cid,
                    session_ticket_fetcher=fetcher,
                    session_ticket_handler=handler,
                )
                state = ConnectionState(connection)
                timer = self.task_group.create_timer(partial(self._timeout, state))
                state.timer = timer
                state.add_cid(header.destination_cid)
                self.connections[header.destination_cid] = state
                state.add_cid(connection.host_cid)
                self.connections[connection.host_cid] = state

            if connection is not None:
                connection.receive_datagram(event.data, event.address, now=self.context.time())
                await self._wake_up_timer(state)
        elif isinstance(event, Closed):
            pass

    async def send_all(self, connection: QuicConnection) -> None:
        for data, address in connection.datagrams_to_send(now=self.context.time()):
            await self.send(RawData(data=data, address=address))

    async def _handle_events(
        self, state: ConnectionState, client: Optional[Tuple[str, int]] = None
    ) -> None:
        connection = state.connection
        event = connection.next_event()
        while event is not None:
            if isinstance(event, ConnectionTerminated):
                await state.timer.stop()
                for cid in state.cids:
                    del self.connections[cid]
                state.cids = set()
            elif isinstance(event, ProtocolNegotiated):
                state.h3_protocol = H3Protocol(
                    self.app,
                    self.config,
                    self.context,
                    self.task_group,
                    client,
                    self.server,
                    connection,
                    partial(self._wake_up_timer, state),
                )
            elif isinstance(event, ConnectionIdIssued):
                state.add_cid(event.connection_id)
                self.connections[event.connection_id] = state
            elif isinstance(event, ConnectionIdRetired):
                state.remove_cid(event.connection_id)
                del self.connections[event.connection_id]

            elif state.h3_protocol is not None:
                await state.h3_protocol.handle(event)

            event = connection.next_event()

    async def _wake_up_timer(self, state: ConnectionState) -> None:
        # When new output is send, or new input is received, we
        # fire the timer right away so we update our state.
        await state.timer.schedule(0.0)

    async def _timeout(self, state: ConnectionState) -> None:
        connection = state.connection
        now = self.context.time()
        when = connection.get_timer()
        if when is not None and now > when:
            connection.handle_timer(now)
        await self._handle_events(state, None)
        await self.send_all(connection)
        await state.timer.schedule(connection.get_timer())

    def _get_session_ticket(self, ticket: bytes) -> None:
        try:
            self.session_tickets.pop(ticket)
        except KeyError:
            return None

    def _store_session_ticket(self, session_ticket: SessionTicket) -> None:
        self.session_tickets[session_ticket.ticket] = session_ticket
        # Implement a simple FIFO remembering the self.config.quic_max_saved_sessions
        # most recent sessions.
        while len(self.session_tickets) > self.config.quic_max_saved_sessions:
            # Grab the first key
            key = next(iter(self.session_tickets.keys()))
            del self.session_tickets[key]
