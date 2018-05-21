import asyncio
from typing import AnyStr, List

import pytest
import wsproto.connection
import wsproto.events

from hypercorn.config import Config
from hypercorn.websocket import WebsocketServer
from .helpers import MockTransport, WebsocketFramework


class MockWebsocketConnection:

    def __init__(self, event_loop: asyncio.AbstractEventLoop) -> None:
        self.transport = MockTransport()
        self.server = WebsocketServer(WebsocketFramework, event_loop, Config(), self.transport)  # type: ignore # noqa: E501
        self.connection = wsproto.connection.WSConnection(
            wsproto.connection.CLIENT, host='hypercorn.com', resource='/ws',
        )
        self.server.data_received(self.connection.bytes_to_send())

    async def send(self, data: AnyStr) -> None:
        self.connection.send_data(data)
        self.server.data_received(self.connection.bytes_to_send())
        await asyncio.sleep(0)  # Allow the server to respond

    async def receive(self) -> List[wsproto.events.DataReceived]:
        await self.transport.updated.wait()
        self.connection.receive_bytes(self.transport.data)
        self.transport.clear()
        return [event for event in self.connection.events()]

    def close(self) -> None:
        self.connection.close()
        self.server.data_received(self.connection.bytes_to_send())


@pytest.mark.asyncio
async def test_websocket_server(event_loop: asyncio.AbstractEventLoop) -> None:
    connection = MockWebsocketConnection(event_loop)
    events = await connection.receive()
    assert isinstance(events[0], wsproto.events.ConnectionEstablished)
    await connection.send('data')
    events = await connection.receive()
    assert events[0].data == 'data'
    connection.close()
