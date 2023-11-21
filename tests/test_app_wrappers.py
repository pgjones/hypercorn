from __future__ import annotations

import asyncio
from functools import partial
from typing import Any, Callable, Iterable, List

import pytest
import trio

from hypercorn.app_wrappers import _build_environ, InvalidPathError, WSGIWrapper
from hypercorn.typing import ASGISendEvent, HTTPScope, WSGIFramework


def echo_body(environ: dict, start_response: Callable) -> List[bytes]:
    status = "200 OK"
    output = environ["wsgi.input"].read()
    headers = [
        ("Content-Type", "text/plain; charset=utf-8"),
    ]
    start_response(status, headers)
    return [output]


class ResponseHelperBase:
    close_called: bool = False

    def __init__(self, body_parts: list[bytes] = []):
        self.body_parts = iter(body_parts)

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        return next(self.body_parts)

    def close(self) -> None:
        self.close_called = True


class ResponseHelper(ResponseHelperBase):
    def __init__(self, body_parts: list[bytes] = []):
        self.length = len(body_parts)
        super().__init__(body_parts)

    def __len__(self) -> int:
        return self.length


def simple_wsgi_app(response: ResponseHelperBase) -> WSGIFramework:
    def app(environ: dict, start_response: Callable) -> Iterable[bytes]:
        status = "200 OK"
        headers = [
            ("Content-Type", "text/plain; charset=utf-8"),
        ]
        start_response(status, headers)
        return response

    return app


async def wsgi_helper(wsgi_app: WSGIFramework, event_loop: asyncio.AbstractEventLoop) -> list[dict]:
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
    }
    queue: asyncio.Queue = asyncio.Queue()
    await queue.put({"type": "http.request"})

    messages = []

    async def _send(message: ASGISendEvent) -> None:
        nonlocal messages
        messages.append(message)

    def _call_soon(func: Callable, *args: Any) -> Any:
        future = asyncio.run_coroutine_threadsafe(func(*args), event_loop)
        return future.result()

    await app(scope, queue.get, _send, partial(event_loop.run_in_executor, None), _call_soon)
    return messages


@pytest.mark.asyncio
async def test_wsgi_close(event_loop: asyncio.AbstractEventLoop) -> None:
    response = ResponseHelper([b"testing close"])
    await wsgi_helper(simple_wsgi_app(response), event_loop)
    assert response.close_called


@pytest.mark.asyncio
async def test_implicit_content_type(event_loop: asyncio.AbstractEventLoop) -> None:
    response = ResponseHelper([b"testing len"])
    messages = await wsgi_helper(simple_wsgi_app(response), event_loop)
    assert messages == [
        {
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", b"11"),
            ],
            "status": 200,
            "type": "http.response.start",
        },
        {"body": bytearray(b"testing len"), "type": "http.response.body", "more_body": False},
    ]


@pytest.mark.asyncio
async def test_no_content_type(event_loop: asyncio.AbstractEventLoop) -> None:
    response = ResponseHelperBase([b"testing len"])
    messages = await wsgi_helper(simple_wsgi_app(response), event_loop)
    assert messages == [
        {
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
            ],
            "status": 200,
            "type": "http.response.start",
        },
        {"body": bytearray(b"testing len"), "type": "http.response.body", "more_body": True},
        {"body": bytearray(b""), "type": "http.response.body", "more_body": False},
    ]


@pytest.mark.asyncio
async def test_multiple_parts(event_loop: asyncio.AbstractEventLoop) -> None:
    response = ResponseHelperBase([b"testing", b" ", b"parts"])
    messages = await wsgi_helper(simple_wsgi_app(response), event_loop)
    assert messages == [
        {
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
            ],
            "status": 200,
            "type": "http.response.start",
        },
        {"body": bytearray(b"testing"), "type": "http.response.body", "more_body": True},
        {"body": bytearray(b" "), "type": "http.response.body", "more_body": True},
        {"body": bytearray(b"parts"), "type": "http.response.body", "more_body": True},
        {"body": bytearray(b""), "type": "http.response.body", "more_body": False},
    ]


@pytest.mark.asyncio
async def test_multiple_parts_with_length(event_loop: asyncio.AbstractEventLoop) -> None:
    response = ResponseHelper([b"testing", b" ", b"parts"])
    messages = await wsgi_helper(simple_wsgi_app(response), event_loop)
    assert messages == [
        {
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
            ],
            "status": 200,
            "type": "http.response.start",
        },
        {"body": bytearray(b"testing"), "type": "http.response.body", "more_body": True},
        {"body": bytearray(b" "), "type": "http.response.body", "more_body": True},
        {"body": bytearray(b"parts"), "type": "http.response.body", "more_body": False},
    ]


@pytest.mark.trio
async def test_wsgi_trio() -> None:
    app = WSGIWrapper(echo_body, 2**16)
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

    async def _send(message: ASGISendEvent) -> None:
        nonlocal messages
        messages.append(message)

    await app(scope, receive_channel.receive, _send, trio.to_thread.run_sync, trio.from_thread.run)
    assert messages == [
        {
            "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", b"0")],
            "status": 200,
            "type": "http.response.start",
        },
        {"body": bytearray(b""), "type": "http.response.body", "more_body": False},
    ]


@pytest.mark.asyncio
async def test_wsgi_asyncio(event_loop: asyncio.AbstractEventLoop) -> None:
    app = WSGIWrapper(echo_body, 2**16)
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

    async def _send(message: ASGISendEvent) -> None:
        nonlocal messages
        messages.append(message)

    def _call_soon(func: Callable, *args: Any) -> Any:
        future = asyncio.run_coroutine_threadsafe(func(*args), event_loop)
        return future.result()

    await app(scope, queue.get, _send, partial(event_loop.run_in_executor, None), _call_soon)
    assert messages == [
        {
            "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", b"0")],
            "status": 200,
            "type": "http.response.start",
        },
        {"body": bytearray(b""), "type": "http.response.body", "more_body": False},
    ]


@pytest.mark.asyncio
async def test_max_body_size(event_loop: asyncio.AbstractEventLoop) -> None:
    app = WSGIWrapper(echo_body, 4)
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

    async def _send(message: ASGISendEvent) -> None:
        nonlocal messages
        messages.append(message)

    def _call_soon(func: Callable, *args: Any) -> Any:
        future = asyncio.run_coroutine_threadsafe(func(*args), event_loop)
        return future.result()

    await app(scope, queue.get, _send, partial(event_loop.run_in_executor, None), _call_soon)
    assert messages == [
        {"headers": [], "status": 400, "type": "http.response.start"},
        {"body": bytearray(b""), "type": "http.response.body", "more_body": False},
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
    with pytest.raises(InvalidPathError):
        _build_environ(scope, b"")
