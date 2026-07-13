from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from hypercorn.app_wrappers import ASGIWrapper
from hypercorn.asyncio.tcp_server import TCPServer
from hypercorn.asyncio.worker_context import WorkerContext
from hypercorn.config import Config
from hypercorn.events import Closed, RawData
from .helpers import MemoryReader, MemoryWriter
from ..helpers import echo_framework


class TimeoutWriter(MemoryWriter):
    def __init__(self) -> None:
        super().__init__()
        self.transport = Mock()

    async def wait_closed(self) -> None:
        raise TimeoutError()


@pytest.mark.asyncio
async def test_completes_on_closed() -> None:
    event_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

    server = TCPServer(
        ASGIWrapper(echo_framework),
        event_loop,
        Config(),
        WorkerContext(None),
        {},
        MemoryReader(),  # type: ignore
        MemoryWriter(),  # type: ignore
    )
    server.reader.close()  # type: ignore
    await server.run()
    # Key is that this line is reached, rather than the above line
    # hanging.


@pytest.mark.asyncio
async def test_complets_on_half_close() -> None:
    event_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

    server = TCPServer(
        ASGIWrapper(echo_framework),
        event_loop,
        Config(),
        WorkerContext(None),
        {},
        MemoryReader(),  # type: ignore
        MemoryWriter(),  # type: ignore
    )
    task = event_loop.create_task(server.run())
    await server.reader.send(b"GET / HTTP/1.1\r\nHost: hypercorn\r\n\r\n")  # type: ignore
    server.reader.close()  # type: ignore
    await task
    data = await server.writer.receive()  # type: ignore
    assert (
        data
        == b"HTTP/1.1 200 \r\ncontent-length: 348\r\ndate: Thu, 01 Jan 1970 01:23:20 GMT\r\nserver: hypercorn-h11\r\n\r\n"  # noqa: E501
    )


@pytest.mark.asyncio
async def test_close_aborts_transport_on_wait_closed_timeout() -> None:
    event_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    writer = TimeoutWriter()

    server = TCPServer(
        ASGIWrapper(echo_framework),
        event_loop,
        Config(),
        WorkerContext(None),
        {},
        MemoryReader(),  # type: ignore
        writer,  # type: ignore
    )

    await server._close()

    writer.transport.abort.assert_called_once_with()


@pytest.mark.asyncio
async def test_read_data_without_timeout_does_not_use_wait_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    config = Config()
    config.read_timeout = None
    reader = MemoryReader()

    server = TCPServer(
        ASGIWrapper(echo_framework),
        event_loop,
        config,
        WorkerContext(None),
        {},
        reader,  # type: ignore[arg-type]
        MemoryWriter(),  # type: ignore[arg-type]
    )
    server.protocol = Mock()
    server.protocol.handle = AsyncMock()

    async def forbidden_wait_for(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("asyncio.wait_for should not be used when read_timeout is None")

    monkeypatch.setattr(asyncio, "wait_for", forbidden_wait_for)

    await reader.send(b"body")
    reader.close()
    await server._read_data()

    assert server.protocol.handle.await_args_list == [
        ((RawData(data=b"body"),),),
        ((RawData(data=b""),),),
        ((Closed(),),),
    ]
