from __future__ import annotations

import asyncio
import ssl
from collections.abc import Coroutine
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aioquic.buffer import Buffer
from aioquic.h3.connection import H3_ALPN
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import QuicConnection
from aioquic.quic.events import HandshakeCompleted, PingAcknowledged, QuicEvent
from aioquic.quic.packet import encode_quic_version_negotiation, pull_quic_header, QuicPacketType

from hypercorn.asyncio.worker_context import WorkerContext
from hypercorn.config import Config
from hypercorn.events import RawData
from hypercorn.protocol.quic import QuicProtocol
from hypercorn.typing import ConnectionState

ASSETS = Path(__file__).parent.parent / "assets"
CLIENT_ADDRESS = ("127.0.0.1", 50000)
SERVER_ADDRESS = ("127.0.0.1", 4433)
UNSUPPORTED_VERSION = 0x1A2A3A4A


@pytest.fixture(name="protocol")
def _protocol() -> QuicProtocol:
    config = Config()
    config.certfile = str(ASSETS / "cert.pem")
    config.keyfile = str(ASSETS / "key.pem")
    return QuicProtocol(
        AsyncMock(),
        config,
        WorkerContext(None),
        MagicMock(),
        ConnectionState({}),
        None,
        AsyncMock(),
    )


@pytest.mark.asyncio
async def test_version_negotiation(protocol: QuicProtocol) -> None:
    # A long header packet with an unsupported version.
    data = (
        b"\xc0"
        + UNSUPPORTED_VERSION.to_bytes(4, "big")
        + b"\x08"
        + b"\x01" * 8
        + b"\x08"
        + b"\x02" * 8
        + b"\x00" * 1200
    )
    await protocol.handle(RawData(data=data, address=CLIENT_ADDRESS))

    protocol.send.assert_called_once()  # type: ignore
    event = protocol.send.call_args[0][0]  # type: ignore
    assert event.address == CLIENT_ADDRESS
    header = pull_quic_header(Buffer(data=event.data))
    assert header.packet_type == QuicPacketType.VERSION_NEGOTIATION
    assert header.destination_cid == b"\x02" * 8
    assert header.source_cid == b"\x01" * 8
    assert header.supported_versions == protocol.quic_config.supported_versions


@pytest.mark.asyncio
async def test_version_negotiation_not_answered(protocol: QuicProtocol) -> None:
    data = encode_quic_version_negotiation(
        source_cid=b"\x01" * 8,
        destination_cid=b"\x02" * 8,
        supported_versions=[UNSUPPORTED_VERSION],
    )
    await protocol.handle(RawData(data=data, address=CLIENT_ADDRESS))

    protocol.send.assert_not_called()  # type: ignore
    assert protocol.idle


@pytest.mark.asyncio
async def test_connection(protocol: QuicProtocol) -> None:
    # Connection IDs longer than the default must still be routed.
    protocol.quic_config.connection_id_length = 16
    client = QuicConnection(
        configuration=QuicConfiguration(
            alpn_protocols=H3_ALPN, is_client=True, verify_mode=ssl.CERT_NONE
        )
    )
    client.connect(SERVER_ADDRESS, now=protocol.context.time())

    # Run the timers as plain tasks, cancelled at the end of the test.
    tasks: list[asyncio.Task] = []

    def create_task(coroutine: Coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        tasks.append(task)
        return task

    protocol.task_group = MagicMock()
    protocol.task_group._task_group.create_task.side_effect = create_task

    async def exchange() -> list[QuicEvent]:
        for data, _ in client.datagrams_to_send(now=protocol.context.time()):
            await protocol.handle(RawData(data=data, address=CLIENT_ADDRESS))
        # Let the server's ACK timer fire.
        await asyncio.sleep(0.1)
        for call_args in protocol.send.call_args_list:  # type: ignore
            client.receive_datagram(
                call_args[0][0].data, SERVER_ADDRESS, now=protocol.context.time()
            )
        protocol.send.reset_mock()  # type: ignore
        events = []
        while (event := client.next_event()) is not None:
            events.append(event)
        return events

    try:
        # After two round trips the handshake is confirmed, and the client
        # only sends short header packets, which carry no connection ID length.
        events = await exchange() + await exchange()
        assert any(isinstance(event, HandshakeCompleted) for event in events)
        assert not protocol.idle

        client.send_ping(1)
        assert PingAcknowledged(uid=1) in await exchange()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
