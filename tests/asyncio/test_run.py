import asyncio

import h2
import h11
import pytest

from hypercorn.asyncio.run import _cancel_all_tasks, Server
from hypercorn.config import Config
from .helpers import MockTransport
from ..helpers import EchoFramework


def test__cancel_all_tasks() -> None:
    async def _noop() -> None:
        pass

    loop = asyncio.get_event_loop()
    tasks = [loop.create_task(_noop()), loop.create_task(_noop())]
    _cancel_all_tasks(loop)
    assert all(task.cancelled() for task in tasks)


async def _make_upgrade_request(
    request: h11.Request, event_loop: asyncio.AbstractEventLoop
) -> h11.InformationalResponse:
    client = h11.Connection(h11.CLIENT)
    server = Server(EchoFramework, event_loop, Config())
    transport = MockTransport()
    server.connection_made(transport)  # type: ignore
    server.data_received(client.send(request))
    await transport.updated.wait()
    client.receive_data(transport.data)
    return client.next_event()


@pytest.mark.asyncio
async def test_websocket_upgrade(event_loop: asyncio.AbstractEventLoop) -> None:
    event = await _make_upgrade_request(
        h11.Request(
            method="GET",
            target="/",
            headers=[
                ("Host", "Hypercorn"),
                ("Upgrade", "WebSocket"),
                ("Connection", "Upgrade"),
                ("Sec-WebSocket-Version", "13"),
                ("Sec-WebSocket-Key", "121312"),
            ],
        ),
        event_loop,
    )
    assert isinstance(event, h11.InformationalResponse)
    assert event.status_code == 101


@pytest.mark.asyncio
async def test_h2c_upgrade(event_loop: asyncio.AbstractEventLoop) -> None:
    event = await _make_upgrade_request(
        h11.Request(
            method="GET",
            target="/",
            headers=[("Host", "Hypercorn"), ("Upgrade", "h2c"), ("Connection", "Upgrade")],
        ),
        event_loop,
    )
    assert isinstance(event, h11.InformationalResponse)
    assert event.status_code == 101
    has_h2c = False
    for name, value in event.headers:
        if name == b"upgrade":
            assert value == b"h2c"
            has_h2c = True
    assert has_h2c


@pytest.mark.asyncio
async def test_h2_prior_knowledge(event_loop: asyncio.AbstractEventLoop) -> None:
    client = h2.connection.H2Connection()
    client.initiate_connection()
    client.ping(b"12345678")
    server = Server(EchoFramework, event_loop, Config())
    transport = MockTransport()
    server.connection_made(transport)  # type: ignore
    server.data_received(client.data_to_send())
    await transport.updated.wait()
    events = client.receive_data(transport.data)
    client.close_connection()
    server.data_received(client.data_to_send())
    assert isinstance(events[-1], h2.events.PingAcknowledged)
