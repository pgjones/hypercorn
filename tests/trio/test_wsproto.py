from typing import AnyStr, List, Type

import pytest
import trio
import wsproto

from hypercorn.config import Config
from hypercorn.trio.wsproto import WebsocketServer
from hypercorn.typing import ASGIFramework
from ..helpers import EchoFramework, MockSocket


class MockConnection:

    def __init__(self, path: str, *, framework: Type[ASGIFramework]=EchoFramework) -> None:
        self.client_stream, server_stream = trio.testing.memory_stream_pair()
        server_stream.socket = MockSocket()
        self.server = WebsocketServer(framework, Config(), server_stream)
        self.connection = wsproto.connection.WSConnection(
            wsproto.connection.CLIENT, host='hypercorn.com', resource=path,
        )
        self.server.connection.receive_bytes(self.connection.bytes_to_send())

    async def send(self, data: AnyStr) -> None:
        self.connection.send_data(data)
        await self.client_stream.send_all(self.connection.bytes_to_send())
        await trio.sleep(0)  # Allow the server to respond

    async def receive(self) -> List[wsproto.events.DataReceived]:
        data = await self.client_stream.receive_some(2**16)
        self.connection.receive_bytes(data)
        return [event for event in self.connection.events()]

    async def close(self) -> None:
        self.connection.close()
        await self.client_stream.send_all(self.connection.bytes_to_send())


@pytest.mark.trio
async def test_websocket_server() -> None:
    connection = MockConnection('/')
    async with trio.open_nursery() as nursery:
        nursery.start_soon(connection.server.handle_connection)
        events = await connection.receive()
        assert isinstance(events[0], wsproto.events.ConnectionEstablished)
        await connection.send('data')
        events = await connection.receive()
        assert events[0].data == 'data'
        await connection.close()
