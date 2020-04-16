import asyncio
from typing import Callable

import pytest

from hypercorn.asyncio.spawn_app import spawn_app
from hypercorn.asyncio.task_group import TaskGroup
from hypercorn.asyncio.tcp_server import TCPServer
from hypercorn.config import Config
from .helpers import MemoryReader, MemoryWriter
from ..helpers import echo_framework


@pytest.mark.asyncio
async def test_spawn_app(event_loop: asyncio.AbstractEventLoop) -> None:
    async def _echo_app(scope: dict, receive: Callable, send: Callable) -> None:
        while True:
            message = await receive()
            if message is None:
                return
            await send(message)

    app_queue: asyncio.Queue = asyncio.Queue()
    async with TaskGroup(event_loop) as task_group:
        put = await spawn_app(task_group, _echo_app, Config(), {"asgi": {}}, app_queue.put)
        await put({"type": "message"})
        assert (await app_queue.get()) == {"type": "message"}
        await put(None)


@pytest.mark.asyncio
async def test_spawn_app_error(event_loop: asyncio.AbstractEventLoop) -> None:
    async def _error_app(scope: dict, receive: Callable, send: Callable) -> None:
        raise Exception()

    app_queue: asyncio.Queue = asyncio.Queue()
    async with TaskGroup(event_loop) as task_group:
        await spawn_app(task_group, _error_app, Config(), {"asgi": {}}, app_queue.put)
    assert (await app_queue.get()) is None


@pytest.mark.asyncio
async def test_spawn_app_cancelled(event_loop: asyncio.AbstractEventLoop) -> None:
    async def _error_app(scope: dict, receive: Callable, send: Callable) -> None:
        raise asyncio.CancelledError()

    app_queue: asyncio.Queue = asyncio.Queue()
    with pytest.raises(asyncio.CancelledError):
        async with TaskGroup(event_loop) as task_group:
            await spawn_app(task_group, _error_app, Config(), {"asgi": {}}, app_queue.put)
    assert (await app_queue.get()) is None


@pytest.mark.asyncio
async def test_completes_on_closed(event_loop: asyncio.AbstractEventLoop) -> None:
    server = TCPServer(
        echo_framework, event_loop, Config(), MemoryReader(), MemoryWriter()  # type: ignore
    )
    server.reader.close()  # type: ignore
    await server.run()
    # Key is that this line is reached, rather than the above line
    # hanging.


@pytest.mark.asyncio
async def test_complets_on_half_close(event_loop: asyncio.AbstractEventLoop) -> None:
    server = TCPServer(
        echo_framework, event_loop, Config(), MemoryReader(), MemoryWriter()  # type: ignore
    )
    asyncio.ensure_future(server.run())
    await server.reader.send(b"GET / HTTP/1.1\r\nHost: hypercorn\r\n\r\n")  # type: ignore
    server.reader.close()  # type: ignore
    await asyncio.sleep(0)
    data = await server.writer.receive()  # type: ignore
    assert (
        data
        == b"HTTP/1.1 200 \r\ncontent-length: 317\r\ndate: Thu, 01 Jan 1970 01:23:20 GMT\r\nserver: hypercorn-h11\r\n\r\n"  # noqa: E501
    )
