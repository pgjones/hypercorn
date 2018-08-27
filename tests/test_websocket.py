import asyncio
from typing import Any, AnyStr, List, Type
from unittest.mock import Mock

import pytest
import wsproto.connection
import wsproto.events

from hypercorn.config import Config
from hypercorn.typing import ASGIFramework
from hypercorn.websocket import WebsocketServer
from .helpers import ErrorFramework, MockTransport, WebsocketFramework


class MockWebsocketConnection:

    def __init__(
            self,
            event_loop: asyncio.AbstractEventLoop,
            *,
            framework: Type[ASGIFramework]=WebsocketFramework,
            path: str='/ws',
    ) -> None:
        self.transport = MockTransport()
        self.server = WebsocketServer(framework, event_loop, Config(), self.transport)  # type: ignore # noqa: E501
        self.connection = wsproto.connection.WSConnection(
            wsproto.connection.CLIENT, host='hypercorn.com', resource=path,
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


@pytest.mark.asyncio
async def test_websocket_response(event_loop: asyncio.AbstractEventLoop) -> None:
    connection = MockWebsocketConnection(event_loop, path='/http')
    await connection.transport.closed.wait()
    assert connection.transport.data.startswith(b'HTTP/1.1 401')


@pytest.mark.asyncio
async def test_close_on_framework_error(event_loop: asyncio.AbstractEventLoop) -> None:
    connection = MockWebsocketConnection(event_loop, framework=ErrorFramework)
    await connection.transport.closed.wait()  # This is the key part, must close on error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'data_bytes, data_text',
    [
        (None, b'data'),
        ('data', None),
    ],
)
async def test_asgi_send_invalid_message(
        data_bytes: Any, data_text: Any, event_loop: asyncio.AbstractEventLoop,
) -> None:
    server = WebsocketServer(ASGIFramework, event_loop, Config(), Mock())  # type: ignore
    server.connection = Mock()
    with pytest.raises((TypeError, ValueError)):
        await server.asgi_send({}, {'type': 'websocket.accept'})
        await server.asgi_send(
            {},
            {
                'type': 'websocket.send',
                'bytes': data_bytes,
                'text': data_text,
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'status, headers, body',
    [
        ('201 NO CONTENT', [], b''),
        (200, [('X-Foo', 'foo')], b''),
        (200, [], 'Body'),
    ],
)
async def test_asgi_send_http_invalid_message(
        status: Any, headers: Any, body: Any, event_loop: asyncio.AbstractEventLoop,
) -> None:
    server = WebsocketServer(ASGIFramework, event_loop, Config(), Mock())  # type: ignore
    server.connection = Mock()
    server.scope = {'method': 'GET'}
    with pytest.raises((TypeError, ValueError)):
        await server.asgi_send(
            {},
            {
                'type': 'websocket.http.response.start',
                'headers': headers,
                'status': status,
            },
        )
        await server.asgi_send(
            {},
            {
                'type': 'websocket.http.response.body',
                'body': body,
            },
        )
