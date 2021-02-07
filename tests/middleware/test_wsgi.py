from __future__ import annotations

import asyncio
from typing import Callable, List

import pytest
import trio

from hypercorn.middleware import AsyncioWSGIMiddleware, TrioWSGIMiddleware
from hypercorn.middleware.wsgi import _build_environ, InvalidPath
from hypercorn.typing import HTTPScope


def echo_body(environ: dict, start_response: Callable) -> List[bytes]:
    status = "200 OK"
    output = environ["wsgi.input"].read()
    headers = [
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Content-Length", str(len(output))),
    ]
    start_response(status, headers)
    return [output]


@pytest.mark.trio
async def test_wsgi_trio() -> None:
    middleware = TrioWSGIMiddleware(echo_body)
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
    }
    send_channel, receive_channel = trio.open_memory_channel(1)
    await send_channel.send({"type": "http.request"})

    messages = []

    async def _send(message: dict) -> None:
        nonlocal messages
        messages.append(message)

    await middleware(scope, receive_channel.receive, _send)
    assert messages == [
        {
            "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", b"0")],
            "status": 200,
            "type": "http.response.start",
        },
        {"body": bytearray(b""), "type": "http.response.body"},
    ]


@pytest.mark.asyncio
async def test_wsgi_asyncio() -> None:
    middleware = AsyncioWSGIMiddleware(echo_body)
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
    }
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put({"type": "http.request"})

    messages = []

    async def _send(message: dict) -> None:
        nonlocal messages
        messages.append(message)

    await middleware(scope, queue.get, _send)
    assert messages == [
        {
            "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", b"0")],
            "status": 200,
            "type": "http.response.start",
        },
        {"body": bytearray(b""), "type": "http.response.body"},
    ]


@pytest.mark.asyncio
async def test_max_body_size() -> None:
    middleware = AsyncioWSGIMiddleware(echo_body, max_body_size=4)
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
    }
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put({"type": "http.request", "body": b"abcde"})
    messages = []

    async def _send(message: dict) -> None:
        nonlocal messages
        messages.append(message)

    await middleware(scope, queue.get, _send)
    assert messages == [
        {"headers": [], "status": 400, "type": "http.response.start"},
        {"body": bytearray(b""), "type": "http.response.body"},
    ]


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
    }
    environ = _build_environ(scope, b"")
    assert environ["SCRIPT_NAME"] == "/中".encode("utf8").decode("latin-1")
    assert environ["PATH_INFO"] == "/文".encode("utf8").decode("latin-1")


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
    }
    with pytest.raises(InvalidPath):
        _build_environ(scope, b"")
