from __future__ import annotations

import asyncio

from hypercorn.asyncio.udp_server import UDPServer
from hypercorn.asyncio.worker_context import WorkerContext
from hypercorn.config import Config
from hypercorn.events import RawData


def test_udp_server_uses_configured_queue_size() -> None:
    config = Config()
    config.quic_receive_queue_size = 64

    loop = asyncio.new_event_loop()
    try:
        server = UDPServer(None, loop, config, WorkerContext(None), {})

        assert server.protocol_queue.maxsize == 64
    finally:
        loop.close()


def test_udp_server_drops_datagrams_when_queue_is_full() -> None:
    config = Config()
    config.quic_receive_queue_size = 1

    loop = asyncio.new_event_loop()
    try:
        server = UDPServer(None, loop, config, WorkerContext(None), {})

        server.datagram_received(b"one", ("127.0.0.1", 4433))  # type: ignore[arg-type]
        server.datagram_received(b"two", ("127.0.0.1", 4433))  # type: ignore[arg-type]

        assert server.protocol_queue.qsize() == 1
        queued = server.protocol_queue.get_nowait()
        assert queued == RawData(data=b"one", address=("127.0.0.1", 4433))
    finally:
        loop.close()
