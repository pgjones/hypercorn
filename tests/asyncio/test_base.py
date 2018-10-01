import asyncio

import pytest

from hypercorn.asyncio.base import HTTPServer
from hypercorn.config import Config
from .helpers import MockTransport


@pytest.mark.asyncio
async def test_http_server_drain(event_loop: asyncio.AbstractEventLoop) -> None:
    transport = MockTransport()
    server = HTTPServer(event_loop, Config(), transport, '')  # type: ignore
    server.pause_writing()

    async def write() -> None:
        server.write(b'Pre drain')
        await server.drain()
        server.write(b'Post drain')

    asyncio.ensure_future(write())
    await transport.updated.wait()
    assert transport.data == b'Pre drain'
    transport.clear()
    server.resume_writing()
    await transport.updated.wait()
    assert transport.data == b'Post drain'
