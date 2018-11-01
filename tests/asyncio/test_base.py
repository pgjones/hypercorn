import asyncio

import pytest

from hypercorn.asyncio.base import HTTPServer
from hypercorn.config import Config
from .helpers import MockTransport


@pytest.mark.asyncio
async def test_keep_alive_timeout(event_loop: asyncio.AbstractEventLoop) -> None:
    transport = MockTransport()
    config = Config()
    config.keep_alive_timeout = 0.01
    server = HTTPServer(event_loop, config, transport, "")  # type: ignore
    server.start_keep_alive_timeout()
    assert not transport.closed.is_set()
    await asyncio.sleep(2 * config.keep_alive_timeout)
    assert transport.closed.is_set()


@pytest.mark.asyncio
async def test_http_server_drain(event_loop: asyncio.AbstractEventLoop) -> None:
    transport = MockTransport()
    server = HTTPServer(event_loop, Config(), transport, "")  # type: ignore
    server.pause_writing()

    async def write() -> None:
        server.write(b"Pre drain")
        await server.drain()
        server.write(b"Post drain")

    asyncio.ensure_future(write())
    await transport.updated.wait()
    assert transport.data == b"Pre drain"
    transport.clear()
    server.resume_writing()
    await transport.updated.wait()
    assert transport.data == b"Post drain"
