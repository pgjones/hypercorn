from __future__ import annotations

import asyncio

import pytest

from hypercorn.app_wrappers import ASGIWrapper
from hypercorn.asyncio.run import worker_serve
from hypercorn.config import Config
from ..helpers import sanity_framework


@pytest.mark.asyncio
async def test_worker_serve_closes_datagram_transports() -> None:
    pytest.importorskip("aioquic")
    event_loop = asyncio.get_running_loop()
    config = Config()
    config.bind = []
    config.quic_bind = ["127.0.0.1:0"]
    config.certfile = "tests/assets/cert.pem"
    config.keyfile = "tests/assets/key.pem"
    config.graceful_timeout = 0.1
    sockets = config.create_sockets()

    transports: list[asyncio.BaseTransport] = []
    create_datagram_endpoint = event_loop.create_datagram_endpoint

    async def _capturing(*args: object, **kwargs: object) -> tuple:
        transport, protocol = await create_datagram_endpoint(*args, **kwargs)  # type: ignore
        transports.append(transport)
        return transport, protocol

    async def _shutdown() -> None:
        return None

    event_loop.create_datagram_endpoint = _capturing  # type: ignore
    try:
        await worker_serve(
            ASGIWrapper(sanity_framework), config, sockets=sockets, shutdown_trigger=_shutdown
        )
    finally:
        event_loop.create_datagram_endpoint = create_datagram_endpoint  # type: ignore

    assert transports != []
    assert all(transport.is_closing() for transport in transports)
