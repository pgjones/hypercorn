import asyncio
from typing import Callable

import pytest

from hypercorn.asyncio.server import Server, spawn_app
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
    put = await spawn_app(_echo_app, event_loop, Config(), {"asgi": {}}, app_queue.put)
    await put({"type": "message"})
    assert (await app_queue.get()) == {"type": "message"}
    await put(None)


@pytest.mark.asyncio
async def test_spawn_app_error(event_loop: asyncio.AbstractEventLoop) -> None:
    async def _error_app(scope: dict, receive: Callable, send: Callable) -> None:
        raise Exception()

    app_queue: asyncio.Queue = asyncio.Queue()
    await spawn_app(_error_app, event_loop, Config(), {"asgi": {}}, app_queue.put)
    assert (await app_queue.get()) is None


@pytest.mark.asyncio
async def test_spawn_app_cancelled(event_loop: asyncio.AbstractEventLoop) -> None:
    async def _error_app(scope: dict, receive: Callable, send: Callable) -> None:
        raise asyncio.CancelledError()

    app_queue: asyncio.Queue = asyncio.Queue()
    await spawn_app(_error_app, event_loop, Config(), {"asgi": {}}, app_queue.put)
    assert app_queue.empty()


@pytest.fixture(name="server")
def _server(event_loop: asyncio.AbstractEventLoop) -> Server:
    return Server(  # type: ignore
        echo_framework, event_loop, Config(), MemoryReader(), MemoryWriter()
    )


@pytest.mark.asyncio
async def test_initial_keep_alive_timeout(server: Server) -> None:
    server.config.keep_alive_timeout = 0.01
    asyncio.ensure_future(server.run())
    await asyncio.sleep(2 * server.config.keep_alive_timeout)
    assert server.writer.is_closed  # type: ignore


@pytest.mark.asyncio
async def test_post_request_keep_alive_timeout(server: Server) -> None:
    server.config.keep_alive_timeout = 0.01
    asyncio.ensure_future(server.run())
    await server.reader.send(  # type: ignore
        b"GET / HTTP/1.1\r\nHost: hypercorn\r\nconnection: keep-alive\r\n\r\n"
    )
    await asyncio.sleep(2 * server.config.keep_alive_timeout)
    assert server.writer.is_closed  # type: ignore


@pytest.mark.asyncio
async def test_completes_on_closed(server: Server) -> None:
    await server.reader.send(b"")  # type: ignore
    await server.run()
    # Key is that this line is reached, rather than the above line
    # hanging.
