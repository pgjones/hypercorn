from typing import AnyStr, List, Type

import h11
import pytest
import wsproto

import trio
from hypercorn.config import Config
from hypercorn.trio.wsproto import WebsocketServer
from hypercorn.typing import ASGIFramework
from ..helpers import BadFramework, EchoFramework, MockSocket


class MockHTTPConnection:
    def __init__(self, path: str, *, framework: Type[ASGIFramework] = EchoFramework) -> None:
        self.client_stream, server_stream = trio.testing.memory_stream_pair()
        server_stream.socket = MockSocket()
        self.client = h11.Connection(h11.CLIENT)
        self.server = WebsocketServer(framework, Config(), server_stream)
        self.server.connection.receive_bytes(
            self.client.send(
                h11.Request(
                    method="GET",
                    target=path,
                    headers=[
                        ("Host", "Hypercorn"),
                        ("Upgrade", "WebSocket"),
                        ("Connection", "Upgrade"),
                        ("Sec-WebSocket-Version", "13"),
                        ("Sec-WebSocket-Key", "121312"),
                    ],
                )
            )
        )

    async def get_events(self) -> list:
        events = []
        self.client.receive_data(await self.client_stream.receive_some(2 ** 16))
        while True:
            event = self.client.next_event()
            if event in (h11.NEED_DATA, h11.PAUSED):
                break
            events.append(event)
            if isinstance(event, h11.ConnectionClosed):
                break
        return events


class MockWebsocketConnection:
    def __init__(self, path: str, *, framework: Type[ASGIFramework] = EchoFramework) -> None:
        self.client_stream, server_stream = trio.testing.memory_stream_pair()
        server_stream.socket = MockSocket()
        self.server = WebsocketServer(framework, Config(), server_stream)
        self.connection = wsproto.connection.WSConnection(
            wsproto.connection.CLIENT, host="hypercorn.com", resource=path
        )
        self.server.connection.receive_bytes(self.connection.bytes_to_send())

    async def send(self, data: AnyStr) -> None:
        self.connection.send_data(data)
        await self.client_stream.send_all(self.connection.bytes_to_send())
        await trio.sleep(0)  # Allow the server to respond

    async def receive(self) -> List[wsproto.events.DataReceived]:
        data = await self.client_stream.receive_some(2 ** 16)
        self.connection.receive_bytes(data)
        return [event for event in self.connection.events()]

    async def close(self) -> None:
        self.connection.close()
        await self.client_stream.send_all(self.connection.bytes_to_send())


@pytest.mark.trio
async def test_websocket_server() -> None:
    connection = MockWebsocketConnection("/")
    async with trio.open_nursery() as nursery:
        nursery.start_soon(connection.server.handle_connection)
        events = await connection.receive()
        assert isinstance(events[0], wsproto.events.ConnectionEstablished)
        await connection.send("data")
        events = await connection.receive()
        assert events[0].data == "data"
        await connection.close()


@pytest.mark.trio
@pytest.mark.parametrize("path", ["/", "/no_response", "/call"])
async def test_bad_framework_http(path: str) -> None:
    connection = MockHTTPConnection(path, framework=BadFramework)
    await connection.server.handle_connection()
    response, *_ = await connection.get_events()
    assert isinstance(response, h11.Response)
    assert response.status_code == 500


@pytest.mark.trio
async def test_bad_framework_websocket() -> None:
    connection = MockWebsocketConnection("/accept", framework=BadFramework)
    with trio.move_on_after(0.2):  # Temporary HACK, Fix close timeout issue
        await connection.server.handle_connection()
    *_, close = await connection.receive()
    assert isinstance(close, wsproto.events.ConnectionClosed)
    assert close.code == 1000
