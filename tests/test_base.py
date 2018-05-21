import asyncio

import pytest
from _pytest.monkeypatch import MonkeyPatch

import hypercorn.base
from hypercorn.config import Config
from .helpers import MockTransport


@pytest.mark.asyncio
async def test_http_server_drain(event_loop: asyncio.AbstractEventLoop) -> None:
    transport = MockTransport()
    server = hypercorn.base.HTTPServer(event_loop, Config(), transport, '')  # type: ignore
    server.pause_writing()

    async def write() -> None:
        server.write(b'Pre drain')
        await server.drain()
        server.write(b'Post drain')

    asyncio.ensure_future(write())
    await transport.updated.wait()
    assert transport.data == b'Pre drain'
    transport.clear()
    server.resume_writing()
    await transport.updated.wait()
    assert transport.data == b'Post drain'


@pytest.mark.parametrize(
    'method, status, expected',
    [
        ('HEAD', 200, True), ('GET', 200, False), ('GET', 101, True),
    ],
)
def test_suppress_body(method: str, status: int, expected: bool) -> None:
    assert hypercorn.base.suppress_body(method, status) is expected


def test_response_headers(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(hypercorn.base, 'time', lambda: 1512229395)
    assert hypercorn.base.response_headers('test') == [
        (b'date', b'Sat, 02 Dec 2017 15:43:15 GMT'),
        (b'server', b'hypercorn-test'),
    ]
