from __future__ import annotations

import asyncio

import pytest

from hypercorn.app_wrappers import ASGIWrapper
from hypercorn.asyncio.tcp_server import TCPServer
from hypercorn.asyncio.worker_context import WorkerContext
from hypercorn.config import Config
from .helpers import MemoryReader, MemoryWriter
from ..helpers import echo_framework


@pytest.mark.asyncio
async def test_completes_on_closed(event_loop: asyncio.AbstractEventLoop) -> None:
    server = TCPServer(
        ASGIWrapper(echo_framework),
        event_loop,
        Config(),
        WorkerContext(),
        MemoryReader(),  # type: ignore
        MemoryWriter(),  # type: ignore
    )
    server.reader.close()  # type: ignore
    await server.run()
    # Key is that this line is reached, rather than the above line
    # hanging.


@pytest.mark.asyncio
async def test_complets_on_half_close(event_loop: asyncio.AbstractEventLoop) -> None:
    server = TCPServer(
        ASGIWrapper(echo_framework),
        event_loop,
        Config(),
        WorkerContext(),
        MemoryReader(),  # type: ignore
        MemoryWriter(),  # type: ignore
    )
    task = event_loop.create_task(server.run())
    await server.reader.send(b"GET / HTTP/1.1\r\nHost: hypercorn\r\n\r\n")  # type: ignore
    await asyncio.sleep(0.001)
    server.reader.close()  # type: ignore
    await task

    data = await server.writer.receive()  # type: ignore
    assert (
        data
        == b"HTTP/1.1 200 \r\ncontent-length: 335\r\ndate: Thu, 01 Jan 1970 01:23:20 GMT\r\nserver: hypercorn-h11\r\n\r\n"  # noqa: E501
    )
