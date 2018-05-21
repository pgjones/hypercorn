import asyncio
from typing import Callable, Optional
from unittest.mock import Mock

import pytest

from hypercorn.config import Config
from hypercorn.http import HTTPRequestResponseServer, Stream
from .helpers import MockTransport


def test_stream(event_loop: asyncio.AbstractEventLoop) -> None:
    stream = Stream(event_loop, {})
    stream.append(b'data')
    assert stream.queue.get_nowait() == {
        'type': 'http.request',
        'body': b'data',
        'more_body': True,
    }
    stream.complete()
    assert stream.queue.get_nowait() == {
        'type': 'http.request',
        'body': b'',
        'more_body': False,
    }
    stream.task = Mock()
    stream.close()
    assert stream.queue.get_nowait() == {
        'type': 'http.disconnect',
    }


class TrivialFramework:

    def __init__(self, scope: dict) -> None:
        self.scope = scope

    async def __call__(self, receive: Callable, send: Callable) -> None:
        pass


@pytest.mark.asyncio
async def test_handle_request(event_loop: asyncio.AbstractEventLoop) -> None:
    transport = MockTransport()
    server = HTTPRequestResponseServer(TrivialFramework, event_loop, Config(), transport, 'test')  # type: ignore  # noqa: E501
    server.handle_request(0, b'get', b'/?a=b', '1.0', [])
    assert server.streams[0].request == {
        'type': 'http',
        'http_version': '1.0',
        'method': 'GET',
        'scheme': 'http',
        'path': '/',
        'query_string': b'a=b',
        'root_path': '',
        'headers': [],
        'client': None,
        'server': ('127.0.0.1', ),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('attribute', [None, 'data_received', 'write'])
async def test_keep_alive_timeout(
        event_loop: asyncio.AbstractEventLoop,
        attribute: Optional[str],
) -> None:
    config = Config()
    config.keep_alive_timeout = 0.01
    server = HTTPRequestResponseServer(TrivialFramework, event_loop, config, Mock(), 'test')
    await asyncio.sleep(0.75 * config.keep_alive_timeout)
    server.transport.close.assert_not_called()  # type: ignore
    if attribute is not None and hasattr(server, attribute):
        getattr(server, attribute)(b'')
        await asyncio.sleep(0.75 * config.keep_alive_timeout)
        server.transport.close.assert_not_called()  # type: ignore
    await asyncio.sleep(2 * config.keep_alive_timeout)
    server.transport.close.assert_called_once()  # type: ignore


@pytest.mark.asyncio
async def test_keep_alive_timeout_suspended(event_loop: asyncio.AbstractEventLoop) -> None:
    config = Config()
    config.keep_alive_timeout = 0.01
    server = HTTPRequestResponseServer(TrivialFramework, event_loop, config, Mock(), 'test')
    await asyncio.sleep(0.75 * config.keep_alive_timeout)
    server.handle_request(0, b'get', b'/?a=b', '1.0', [])
    assert server._keep_alive_timeout_handle._cancelled
    await asyncio.sleep(2 * config.keep_alive_timeout)
    server.transport.close.assert_called_once()  # type: ignore
