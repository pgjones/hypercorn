from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from typing import Any

import pytest
import trio

from hypercorn.app_wrappers import _build_environ, InvalidPathError, WSGIWrapper
from hypercorn.typing import ASGIReceiveEvent, ASGISendEvent, ConnectionState, HTTPScope
from .wsgi_applications import (
    wsgi_app_echo_body,
    wsgi_app_generator,
    wsgi_app_generator_delayed_start_response,
    wsgi_app_generator_multiple_start_response_after_body,
    wsgi_app_generator_no_body,
    wsgi_app_multiple_start_response_no_exc_info,
    wsgi_app_no_body,
    wsgi_app_no_start_response,
    wsgi_app_simple,
)


@pytest.mark.trio
async def test_wsgi_trio() -> None:
    app = WSGIWrapper(wsgi_app_echo_body, 2**16)
    scope: HTTPScope = {
        "http_version": "1.1",
        "asgi": {},
        "method": "GET",
        "headers": [],
        "path": "/",
        "root_path": "/",
        "query_string": b"a=b",
        "raw_path": b"/",
        "scheme": "http",
        "type": "http",
        "client": ("localhost", 80),
        "server": None,
        "extensions": {},
        "state": ConnectionState({}),
    }
    send_channel, receive_channel = trio.open_memory_channel[ASGIReceiveEvent](1)
    await send_channel.send({"type": "http.request"})  # type: ignore

    messages = []

    async def _send(message: ASGISendEvent) -> None:
        messages.append(message)

    await app(scope, receive_channel.receive, _send, trio.to_thread.run_sync, trio.from_thread.run)
    assert messages == [
        {"body": bytearray(b""), "type": "http.response.body", "more_body": True},
        {
            "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", b"0")],
            "status": 200,
            "type": "http.response.start",
        },
        {"body": bytearray(b""), "type": "http.response.body", "more_body": False},
    ]


async def _run_app(app: WSGIWrapper, scope: HTTPScope, body: bytes = b"") -> list[ASGISendEvent]:
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put({"type": "http.request", "body": body})

    messages = []

    async def _send(message: ASGISendEvent) -> None:
        messages.append(message)

    event_loop = asyncio.get_running_loop()

    def _call_soon(func: Callable, *args: Any) -> Any:
        future = asyncio.run_coroutine_threadsafe(func(*args), event_loop)
        return future.result()

    await app(scope, queue.get, _send, partial(event_loop.run_in_executor, None), _call_soon)
    return messages


@pytest.mark.asyncio
async def test_wsgi_asyncio() -> None:
    app = WSGIWrapper(wsgi_app_echo_body, 2**16)
    scope: HTTPScope = {
        "http_version": "1.1",
        "asgi": {},
        "method": "GET",
        "headers": [],
        "path": "/",
        "root_path": "/",
        "query_string": b"a=b",
        "raw_path": b"/",
        "scheme": "http",
        "type": "http",
        "client": ("localhost", 80),
        "server": None,
        "extensions": {},
        "state": ConnectionState({}),
    }
    messages = await _run_app(app, scope, b"Hello, world!")
    assert messages == [
        {
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", b"13"),
            ],
            "status": 200,
            "type": "http.response.start",
        },
        {"body": b"Hello, world!", "type": "http.response.body", "more_body": True},
        {"body": b"", "type": "http.response.body", "more_body": False},
    ]


@pytest.mark.asyncio
async def test_max_body_size() -> None:
    app = WSGIWrapper(wsgi_app_echo_body, 4)
    scope: HTTPScope = {
        "http_version": "1.1",
        "asgi": {},
        "method": "GET",
        "headers": [],
        "path": "/",
        "root_path": "/",
        "query_string": b"a=b",
        "raw_path": b"/",
        "scheme": "http",
        "type": "http",
        "client": ("localhost", 80),
        "server": None,
        "extensions": {},
        "state": ConnectionState({}),
    }
    messages = await _run_app(app, scope, b"abcde")
    assert messages == [
        {"headers": [], "status": 400, "type": "http.response.start"},
        {"body": bytearray(b""), "type": "http.response.body", "more_body": False},
    ]


@pytest.mark.asyncio
async def test_no_start_response() -> None:
    app = WSGIWrapper(wsgi_app_no_start_response, 2**16)
    scope: HTTPScope = {
        "http_version": "1.1",
        "asgi": {},
        "method": "GET",
        "headers": [],
        "path": "/",
        "root_path": "/",
        "query_string": b"a=b",
        "raw_path": b"/",
        "scheme": "http",
        "type": "http",
        "client": ("localhost", 80),
        "server": None,
        "extensions": {},
        "state": ConnectionState({}),
    }
    with pytest.raises(RuntimeError):
        await _run_app(app, scope)


def test_build_environ_encoding() -> None:
    scope: HTTPScope = {
        "http_version": "1.0",
        "asgi": {},
        "method": "GET",
        "headers": [],
        "path": "/中/文",
        "root_path": "/中",
        "query_string": b"bar=baz",
        "raw_path": "/中/文".encode(),
        "scheme": "http",
        "type": "http",
        "client": ("localhost", 80),
        "server": None,
        "extensions": {},
        "state": ConnectionState({}),
    }
    environ = _build_environ(scope, b"")
    assert environ["SCRIPT_NAME"] == "/中".encode().decode("latin-1")
    assert environ["PATH_INFO"] == "/文".encode().decode("latin-1")


def test_build_environ_root_path() -> None:
    scope: HTTPScope = {
        "http_version": "1.0",
        "asgi": {},
        "method": "GET",
        "headers": [],
        "path": "/中文",
        "root_path": "/中国",
        "query_string": b"bar=baz",
        "raw_path": "/中文".encode(),
        "scheme": "http",
        "type": "http",
        "client": ("localhost", 80),
        "server": None,
        "extensions": {},
        "state": ConnectionState({}),
    }
    with pytest.raises(InvalidPathError):
        _build_environ(scope, b"")


@pytest.mark.asyncio
@pytest.mark.parametrize("wsgi_app", [wsgi_app_simple, wsgi_app_generator])
async def test_wsgi_protocol(wsgi_app: Callable) -> None:
    app = WSGIWrapper(wsgi_app, 2**16)
    scope: HTTPScope = {
        "http_version": "1.1",
        "asgi": {},
        "method": "GET",
        "headers": [],
        "path": "/",
        "root_path": "/",
        "query_string": b"a=b",
        "raw_path": b"/",
        "scheme": "http",
        "type": "http",
        "client": ("localhost", 80),
        "server": None,
        "extensions": {},
        "state": ConnectionState({}),
    }

    messages = await _run_app(app, scope)
    assert messages == [
        {
            "headers": [(b"x-test-header", b"Test-Value")],
            "status": 200,
            "type": "http.response.start",
        },
        {"body": b"Hello, ", "type": "http.response.body", "more_body": True},
        {"body": b"world!", "type": "http.response.body", "more_body": True},
        {"body": b"", "type": "http.response.body", "more_body": False},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("wsgi_app", [wsgi_app_no_body, wsgi_app_generator_no_body])
async def test_wsgi_protocol_no_body(wsgi_app: Callable) -> None:
    app = WSGIWrapper(wsgi_app, 2**16)
    scope: HTTPScope = {
        "http_version": "1.1",
        "asgi": {},
        "method": "GET",
        "headers": [],
        "path": "/",
        "root_path": "/",
        "query_string": b"a=b",
        "raw_path": b"/",
        "scheme": "http",
        "type": "http",
        "client": ("localhost", 80),
        "server": None,
        "extensions": {},
        "state": ConnectionState({}),
    }

    messages = await _run_app(app, scope)
    assert messages == [
        {
            "headers": [(b"x-test-header", b"Test-Value")],
            "status": 200,
            "type": "http.response.start",
        },
        {"body": b"", "type": "http.response.body", "more_body": False},
    ]


@pytest.mark.asyncio
async def test_wsgi_protocol_overwrite_start_response() -> None:
    app = WSGIWrapper(wsgi_app_generator_delayed_start_response, 2**16)
    scope: HTTPScope = {
        "http_version": "1.1",
        "asgi": {},
        "method": "GET",
        "headers": [],
        "path": "/",
        "root_path": "/",
        "query_string": b"a=b",
        "raw_path": b"/",
        "scheme": "http",
        "type": "http",
        "client": ("localhost", 80),
        "server": None,
        "extensions": {},
        "state": ConnectionState({}),
    }

    messages = await _run_app(app, scope)
    assert messages == [
        {"body": b"", "type": "http.response.body", "more_body": True},
        {
            "headers": [(b"x-test-header", b"New-Value")],
            "status": 500,
            "type": "http.response.start",
        },
        {"body": b"Hello, ", "type": "http.response.body", "more_body": True},
        {"body": b"world!", "type": "http.response.body", "more_body": True},
        {"body": b"", "type": "http.response.body", "more_body": False},
    ]


@pytest.mark.asyncio
async def test_wsgi_protocol_multiple_start_response_no_exc_info() -> None:
    app = WSGIWrapper(wsgi_app_multiple_start_response_no_exc_info, 2**16)
    scope: HTTPScope = {
        "http_version": "1.1",
        "asgi": {},
        "method": "GET",
        "headers": [],
        "path": "/",
        "root_path": "/",
        "query_string": b"a=b",
        "raw_path": b"/",
        "scheme": "http",
        "type": "http",
        "client": ("localhost", 80),
        "server": None,
        "extensions": {},
        "state": ConnectionState({}),
    }

    with pytest.raises(RuntimeError):
        await _run_app(app, scope)


@pytest.mark.asyncio
async def test_wsgi_protocol_multiple_start_response_after_body() -> None:
    app = WSGIWrapper(wsgi_app_generator_multiple_start_response_after_body, 2**16)
    scope: HTTPScope = {
        "http_version": "1.1",
        "asgi": {},
        "method": "GET",
        "headers": [],
        "path": "/",
        "root_path": "/",
        "query_string": b"a=b",
        "raw_path": b"/",
        "scheme": "http",
        "type": "http",
        "client": ("localhost", 80),
        "server": None,
        "extensions": {},
        "state": ConnectionState({}),
    }

    with pytest.raises(ValueError):
        await _run_app(app, scope)
