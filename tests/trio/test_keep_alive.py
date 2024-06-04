from __future__ import annotations

from typing import Awaitable, Callable, cast, Generator

import h11
import pytest
import trio

from hypercorn.app_wrappers import ASGIWrapper
from hypercorn.config import Config
from hypercorn.trio.tcp_server import TCPServer
from hypercorn.trio.worker_context import WorkerContext
from hypercorn.typing import ASGIReceiveEvent, ASGISendEvent, Scope
from ..helpers import MockSocket

try:
    from typing import TypeAlias
except ImportError:
    from typing_extensions import TypeAlias


KEEP_ALIVE_TIMEOUT = 0.01
REQUEST = h11.Request(method="GET", target="/", headers=[(b"host", b"hypercorn")])

ClientStream: TypeAlias = trio.StapledStream[
    trio.testing.MemorySendStream, trio.testing.MemoryReceiveStream
]


async def slow_framework(
    scope: Scope,
    receive: Callable[[], Awaitable[ASGIReceiveEvent]],
    send: Callable[[ASGISendEvent], Awaitable[None]],
) -> None:
    while True:
        event = await receive()
        if event["type"] == "http.disconnect":
            break
        elif event["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif event["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
        elif event["type"] == "http.request" and not event.get("more_body", False):
            await trio.sleep(2 * KEEP_ALIVE_TIMEOUT)
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-length", b"0")],
                }
            )
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            break


@pytest.fixture(name="client_stream", scope="function")
def _client_stream(
    nursery: trio.Nursery,
) -> Generator[ClientStream, None, None]:
    config = Config()
    config.keep_alive_timeout = KEEP_ALIVE_TIMEOUT
    client_stream, server_stream = trio.testing.memory_stream_pair()
    server_stream = cast("trio.SSLStream[trio.SocketStream]", server_stream)
    server_stream.socket = MockSocket()
    server = TCPServer(ASGIWrapper(slow_framework), config, WorkerContext(None), {}, server_stream)
    nursery.start_soon(server.run)
    yield client_stream


@pytest.mark.trio
async def test_http1_keep_alive_pre_request(client_stream: ClientStream) -> None:
    await client_stream.send_all(b"GET")
    await trio.sleep(2 * KEEP_ALIVE_TIMEOUT)
    # Only way to confirm closure is to invoke an error
    with pytest.raises(trio.BrokenResourceError):
        await client_stream.send_all(b"a")


@pytest.mark.trio
async def test_http1_keep_alive_during(
    client_stream: ClientStream,
) -> None:
    client = h11.Connection(h11.CLIENT)
    # client.send(h11.Request) and client.send(h11.EndOfMessage) only returns bytes.
    # Fixed on master/ in the h11 repo, once released the ignore's can be removed.
    # See https://github.com/python-hyper/h11/issues/175
    await client_stream.send_all(client.send(REQUEST))  # type: ignore[arg-type]
    await trio.sleep(2 * KEEP_ALIVE_TIMEOUT)
    # Key is that this doesn't error
    await client_stream.send_all(client.send(h11.EndOfMessage()))  # type: ignore[arg-type]


@pytest.mark.trio
async def test_http1_keep_alive(
    client_stream: ClientStream,
) -> None:
    client = h11.Connection(h11.CLIENT)
    await client_stream.send_all(client.send(REQUEST))  # type: ignore[arg-type]
    await trio.sleep(2 * KEEP_ALIVE_TIMEOUT)
    await client_stream.send_all(client.send(h11.EndOfMessage()))  # type: ignore[arg-type]
    while True:
        event = client.next_event()
        if event == h11.NEED_DATA:
            data = await client_stream.receive_some(2**16)
            client.receive_data(data)
        elif isinstance(event, h11.EndOfMessage):
            break
    client.start_next_cycle()
    await client_stream.send_all(client.send(REQUEST))  # type: ignore[arg-type]
    await trio.sleep(2 * KEEP_ALIVE_TIMEOUT)
    # Key is that this doesn't error
    await client_stream.send_all(client.send(h11.EndOfMessage()))  # type: ignore[arg-type]


@pytest.mark.trio
async def test_http1_keep_alive_pipelining(
    client_stream: ClientStream,
) -> None:
    await client_stream.send_all(
        b"GET / HTTP/1.1\r\nHost: hypercorn\r\n\r\nGET / HTTP/1.1\r\nHost: hypercorn\r\n\r\n"
    )
    await client_stream.receive_some(2**16)
    await trio.sleep(2 * KEEP_ALIVE_TIMEOUT)
    await client_stream.send_all(b"")
