import h2
import h11
import pytest
import trio

from hypercorn.config import Config
from hypercorn.trio.run import serve_stream
from ..helpers import EchoFramework, MockSocket


async def _make_upgrade_request(request: h11.Request) -> h11.InformationalResponse:
    client_stream, server_stream = trio.testing.memory_stream_pair()
    server_stream.socket = MockSocket()
    async with trio.open_nursery() as nursery:
        nursery.start_soon(serve_stream, EchoFramework, Config(), server_stream)
        client = h11.Connection(h11.CLIENT)
        await client_stream.send_all(client.send(request))
        client.receive_data(await client_stream.receive_some(2 ** 16))
        await client_stream.aclose()
    return client.next_event()


@pytest.mark.trio
async def test_websocket_upgrade() -> None:
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
        )
    )
    assert isinstance(event, h11.InformationalResponse)
    assert event.status_code == 101


@pytest.mark.trio
async def test_h2c_upgrade() -> None:
    event = await _make_upgrade_request(
        h11.Request(
            method="GET",
            target="/",
            headers=[("Host", "Hypercorn"), ("Upgrade", "h2c"), ("Connection", "Upgrade")],
        )
    )
    assert isinstance(event, h11.InformationalResponse)
    assert event.status_code == 101
    has_h2c = False
    for name, value in event.headers:
        if name == b"upgrade":
            assert value == b"h2c"
            has_h2c = True
    assert has_h2c


@pytest.mark.trio
async def test_h2_prior_knowledge() -> None:
    client_stream, server_stream = trio.testing.memory_stream_pair()
    server_stream.socket = MockSocket()
    async with trio.open_nursery() as nursery:
        nursery.start_soon(serve_stream, EchoFramework, Config(), server_stream)
        client = h2.connection.H2Connection()
        client.initiate_connection()
        client.ping(b"12345678")
        await client_stream.send_all(client.data_to_send())
        events = client.receive_data(await client_stream.receive_some(2 ** 16))
        client.close_connection()
        await client_stream.send_all(client.data_to_send())
    assert isinstance(events[-1], h2.events.PingAcknowledged)
