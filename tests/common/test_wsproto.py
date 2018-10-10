from typing import Any
from unittest.mock import Mock

import pytest

from hypercorn.common.wsproto import WebsocketMixin
from hypercorn.utils import WebsocketState


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'data_bytes, data_text',
    [
        (None, b'data'),  # Text should be str
        ('data', None),  # Bytes should be bytes
    ],
)
async def test_asgi_send_invalid_message(data_bytes: Any, data_text: Any) -> None:
    server = WebsocketMixin()
    server.connection = Mock()
    server.state = WebsocketState.CONNECTED
    with pytest.raises((TypeError, ValueError)):
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
        ('201 NO CONTENT', [], b''),  # Status should be int
        (200, [('X-Foo', 'foo')], b''),  # Headers should be bytes
        (200, [], 'Body'),  # Body should be bytes
    ],
)
async def test_asgi_send_invalid_http_message(status: Any, headers: Any, body: Any) -> None:
    server = WebsocketMixin()
    server.connection = Mock()
    server.state = WebsocketState.HANDSHAKE
    server.scope = {'method': 'GET'}
    with pytest.raises((TypeError, ValueError)):
        await server.asgi_send(
            {'method': 'GET'},
            {
                'type': 'websocket.http.response.start',
                'headers': headers,
                'status': status,
            },
        )
        await server.asgi_send(
            {'method': 'GET'},
            {
                'type': 'websocket.http.response.body',
                'body': body,
            },
        )
