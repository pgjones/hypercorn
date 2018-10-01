from typing import Any

import pytest

from hypercorn.common.h11 import H11Mixin
from hypercorn.utils import ASGIState


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'status, headers, body',
    [
        ('201 NO CONTENT', [], b''),  # Status should be int
        (200, [('X-Foo', 'foo')], b''),  # Headers should be bytes
        (200, [], 'Body'),  # Body should be bytes
    ],
)
async def test_asgi_send_invalid_message(status: Any, headers: Any, body: Any) -> None:
    server = H11Mixin()
    server.state = ASGIState.REQUEST
    server.scope = {'method': 'GET'}
    with pytest.raises((TypeError, ValueError)):
        await server.asgi_send({
            'type': 'http.response.start',
            'headers': headers,
            'status': status,
        })
        await server.asgi_send({
            'type': 'http.response.body',
            'body': body,
        })
