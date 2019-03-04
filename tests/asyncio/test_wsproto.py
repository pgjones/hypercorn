import asyncio
from typing import AnyStr, List, Type

import h11
import pytest
from wsproto import ConnectionType, WSConnection
from wsproto.events import AcceptConnection, CloseConnection, Message, Request

from hypercorn.asyncio.wsproto import WebsocketServer
from hypercorn.config import Config
from hypercorn.typing import ASGIFramework
from .helpers import MockTransport
from ..helpers import BadFramework, EchoFramework


class MockHTTPConnection:
    def __init__(
        self,
        path: str,
        event_loop: asyncio.AbstractEventLoop,
        *,
        framework: Type[ASGIFramework] = EchoFramework,
    ) -> None:
        self.transport = MockTransport()
        self.client = h11.Connection(h11.CLIENT)
        self.server = WebsocketServer(  # type: ignore
            framework, event_loop, Config(), self.transport
        )
        self.server.data_received(
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

    def get_events(self) -> list:
        events = []
        self.client.receive_data(self.transport.data)
        while True:
            event = self.client.next_event()
            if event in (h11.NEED_DATA, h11.PAUSED):
                break
            events.append(event)
            if isinstance(event, h11.ConnectionClosed):
                break
        return events


class MockWebsocketConnection:
    def __init__(
        self,
        path: str,
        event_loop: asyncio.AbstractEventLoop,
        *,
        framework: Type[ASGIFramework] = EchoFramework,
    ) -> None:
        self.transport = MockTransport()
        self.server = WebsocketServer(  # type: ignore
            framework, event_loop, Config(), self.transport
        )
        self.connection = WSConnection(ConnectionType.CLIENT)
        self.server.data_received(self.connection.send(Request(target=path, host="hypercorn")))

    async def send(self, data: AnyStr) -> None:
        self.server.data_received(self.connection.send(Message(data=data)))
        await asyncio.sleep(0)  # Allow the server to respond

    async def receive(self) -> List[Message]:
        await self.transport.updated.wait()
        self.connection.receive_data(self.transport.data)
        self.transport.clear()
        return [event for event in self.connection.events()]

    def close(self) -> None:
        self.server.data_received(self.connection.send(CloseConnection(code=1000)))


@pytest.mark.asyncio
async def test_websocket_server(event_loop: asyncio.AbstractEventLoop) -> None:
    connection = MockWebsocketConnection("/ws", event_loop)
    events = await connection.receive()
    assert isinstance(events[0], AcceptConnection)
    await connection.send("data")
    events = await connection.receive()
    assert events[0].data == "data"
    connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/no_response", "/call"])
async def test_bad_framework_http(path: str, event_loop: asyncio.AbstractEventLoop) -> None:
    connection = MockHTTPConnection(path, event_loop, framework=BadFramework)
    await asyncio.sleep(0)  # Yield to allow the server to process
    await connection.transport.closed.wait()
    response, *_ = connection.get_events()
    assert isinstance(response, h11.Response)
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_bad_framework_websocket(event_loop: asyncio.AbstractEventLoop) -> None:
    connection = MockWebsocketConnection("/accept", event_loop, framework=BadFramework)
    await asyncio.sleep(0)  # Yield to allow the server to process
    *_, close = await connection.receive()
    assert isinstance(close, CloseConnection)
    assert close.code == 1000
