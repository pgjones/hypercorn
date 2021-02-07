from __future__ import annotations

import pytest

from hypercorn.middleware import HTTPToHTTPSRedirectMiddleware
from hypercorn.typing import HTTPScope, WebsocketScope
from ..helpers import empty_framework


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_path", [b"/abc", b"/abc%3C"])
async def test_http_to_https_redirect_middleware_http(raw_path: bytes) -> None:
    app = HTTPToHTTPSRedirectMiddleware(empty_framework, "localhost")
    sent_events = []

    async def send(message: dict) -> None:
        nonlocal sent_events
        sent_events.append(message)

    scope: HTTPScope = {
        "type": "http",
        "asgi": {},
        "http_version": "2",
        "method": "GET",
        "scheme": "http",
        "path": raw_path.decode(),
        "raw_path": raw_path,
        "query_string": b"a=b",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 80),
        "server": None,
        "extensions": {},
    }

    await app(scope, None, send)

    assert sent_events == [
        {
            "type": "http.response.start",
            "status": 307,
            "headers": [(b"location", b"https://localhost%s?a=b" % raw_path)],
        },
        {"type": "http.response.body"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_path", [b"/abc", b"/abc%3C"])
async def test_http_to_https_redirect_middleware_websocket(raw_path: bytes) -> None:
    app = HTTPToHTTPSRedirectMiddleware(empty_framework, "localhost")
    sent_events = []

    async def send(message: dict) -> None:
        nonlocal sent_events
        sent_events.append(message)

    scope: WebsocketScope = {
        "type": "websocket",
        "asgi": {},
        "http_version": "1.1",
        "scheme": "ws",
        "path": raw_path.decode(),
        "raw_path": raw_path,
        "query_string": b"a=b",
        "root_path": "",
        "headers": [],
        "client": None,
        "server": None,
        "subprotocols": [],
        "extensions": {"websocket.http.response": {}},
    }
    await app(scope, None, send)

    assert sent_events == [
        {
            "type": "websocket.http.response.start",
            "status": 307,
            "headers": [(b"location", b"wss://localhost%s?a=b" % raw_path)],
        },
        {"type": "websocket.http.response.body"},
    ]


@pytest.mark.asyncio
async def test_http_to_https_redirect_middleware_websocket_http2() -> None:
    app = HTTPToHTTPSRedirectMiddleware(empty_framework, "localhost")
    sent_events = []

    async def send(message: dict) -> None:
        nonlocal sent_events
        sent_events.append(message)

    scope: WebsocketScope = {
        "type": "websocket",
        "asgi": {},
        "http_version": "2",
        "scheme": "ws",
        "path": "/abc",
        "raw_path": b"/abc",
        "query_string": b"a=b",
        "root_path": "",
        "headers": [],
        "client": None,
        "server": None,
        "subprotocols": [],
        "extensions": {"websocket.http.response": {}},
    }
    await app(scope, None, send)

    assert sent_events == [
        {
            "type": "websocket.http.response.start",
            "status": 307,
            "headers": [(b"location", b"https://localhost/abc?a=b")],
        },
        {"type": "websocket.http.response.body"},
    ]


@pytest.mark.asyncio
async def test_http_to_https_redirect_middleware_websocket_no_rejection() -> None:
    app = HTTPToHTTPSRedirectMiddleware(empty_framework, "localhost")
    sent_events = []

    async def send(message: dict) -> None:
        nonlocal sent_events
        sent_events.append(message)

    scope: WebsocketScope = {
        "type": "websocket",
        "asgi": {},
        "http_version": "2",
        "scheme": "ws",
        "path": "/abc",
        "raw_path": b"/abc",
        "query_string": b"a=b",
        "root_path": "",
        "headers": [],
        "client": None,
        "server": None,
        "subprotocols": [],
        "extensions": {},
    }
    await app(scope, None, send)

    assert sent_events == [{"type": "websocket.close"}]


def test_http_to_https_redirect_new_url_header() -> None:
    app = HTTPToHTTPSRedirectMiddleware(empty_framework, None)
    new_url = app._new_url(
        "https",
        {
            "http_version": "1.1",
            "asgi": {},
            "method": "GET",
            "headers": [(b"host", b"localhost")],
            "path": "/",
            "root_path": "",
            "query_string": b"",
            "raw_path": b"/",
            "scheme": "http",
            "type": "http",
            "client": None,
            "server": None,
            "extensions": {},
        },
    )
    assert new_url == "https://localhost/"
