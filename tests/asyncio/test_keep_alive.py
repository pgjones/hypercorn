from __future__ import annotations

import asyncio
from typing import AsyncGenerator

import h11
import pytest
import pytest_asyncio

from hypercorn.app_wrappers import ASGIWrapper
from hypercorn.asyncio.tcp_server import TCPServer
from hypercorn.asyncio.worker_context import WorkerContext
from hypercorn.config import Config
from hypercorn.typing import ASGIReceiveCallable, ASGISendCallable, Scope
from .helpers import MemoryReader, MemoryWriter

KEEP_ALIVE_TIMEOUT = 0.01
REQUEST = h11.Request(method="GET", target="/", headers=[(b"host", b"hypercorn")])


async def slow_framework(
    scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
) -> None:
    while True:
        event = await receive()
        if event["type"] == "http.disconnect":
            break
        elif event["type"] == "lifespan.startup":
            await send({"type": "lifspan.startup.complete"})  # type: ignore
        elif event["type"] == "lifespan.shutdown":
            await send({"type": "lifspan.shutdown.complete"})  # type: ignore
        elif event["type"] == "http.request" and not event.get("more_body", False):
            await asyncio.sleep(2 * KEEP_ALIVE_TIMEOUT)
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-length", b"0")],
                }
            )
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            break


@pytest_asyncio.fixture(name="server", scope="function")  # type: ignore[misc]
async def _server() -> AsyncGenerator[TCPServer, None]:
    event_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

    config = Config()
    config.keep_alive_timeout = KEEP_ALIVE_TIMEOUT
    server = TCPServer(
        ASGIWrapper(slow_framework),
        event_loop,
        config,
        WorkerContext(None),
        {},
        MemoryReader(),  # type: ignore
        MemoryWriter(),  # type: ignore
    )
    task = event_loop.create_task(server.run())
    yield server
    server.reader.close()  # type: ignore
    await task


@pytest.mark.asyncio
async def test_http1_keep_alive_pre_request(server: TCPServer) -> None:
    await server.reader.send(b"GET")  # type: ignore
    await asyncio.sleep(2 * KEEP_ALIVE_TIMEOUT)
    assert server.writer.is_closed  # type: ignore


@pytest.mark.asyncio
async def test_http1_keep_alive_during(server: TCPServer) -> None:
    client = h11.Connection(h11.CLIENT)
    await server.reader.send(client.send(REQUEST))  # type: ignore
    await server.reader.send(client.send(h11.EndOfMessage()))  # type: ignore
    await asyncio.sleep(2 * KEEP_ALIVE_TIMEOUT)
    assert not server.writer.is_closed  # type: ignore


@pytest.mark.asyncio
async def test_http1_keep_alive(server: TCPServer) -> None:
    client = h11.Connection(h11.CLIENT)
    await server.reader.send(client.send(REQUEST))  # type: ignore
    await asyncio.sleep(2 * KEEP_ALIVE_TIMEOUT)
    assert not server.writer.is_closed  # type: ignore
    await server.reader.send(client.send(h11.EndOfMessage()))  # type: ignore
    while True:
        event = client.next_event()
        if event == h11.NEED_DATA:
            data = await server.writer.receive()  # type: ignore
            client.receive_data(data)
        elif isinstance(event, h11.EndOfMessage):
            break
    client.start_next_cycle()
    await server.reader.send(client.send(REQUEST))  # type: ignore
    await asyncio.sleep(2 * KEEP_ALIVE_TIMEOUT)
    assert not server.writer.is_closed  # type: ignore


@pytest.mark.asyncio
async def test_http1_keep_alive_pipelining(server: TCPServer) -> None:
    await server.reader.send(  # type: ignore
        b"GET / HTTP/1.1\r\nHost: hypercorn\r\n\r\nGET / HTTP/1.1\r\nHost: hypercorn\r\n\r\n"
    )
    await server.writer.receive()  # type: ignore
    await asyncio.sleep(2 * KEEP_ALIVE_TIMEOUT)
    assert not server.writer.is_closed  # type: ignore
