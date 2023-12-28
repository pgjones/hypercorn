from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hypercorn.middleware import ProxyFixMiddleware
from hypercorn.typing import HTTPScope


@pytest.mark.asyncio
async def test_proxy_fix_legacy() -> None:
    mock = AsyncMock()
    app = ProxyFixMiddleware(mock)
    scope: HTTPScope = {
        "type": "http",
        "asgi": {},
        "http_version": "2",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"x-forwarded-for", b"127.0.0.1"),
            (b"x-forwarded-for", b"127.0.0.2"),
            (b"x-forwarded-proto", b"http,https"),
        ],
        "client": ("127.0.0.3", 80),
        "server": None,
        "extensions": {},
    }
    await app(scope, None, None)
    mock.assert_called()
    assert mock.call_args[0][0]["client"] == ("127.0.0.2", 0)
    assert mock.call_args[0][0]["scheme"] == "https"


@pytest.mark.asyncio
async def test_proxy_fix_modern() -> None:
    mock = AsyncMock()
    app = ProxyFixMiddleware(mock, mode="modern")
    scope: HTTPScope = {
        "type": "http",
        "asgi": {},
        "http_version": "2",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"forwarded", b"for=127.0.0.1;proto=http,for=127.0.0.2;proto=https"),
        ],
        "client": ("127.0.0.3", 80),
        "server": None,
        "extensions": {},
    }
    await app(scope, None, None)
    mock.assert_called()
    assert mock.call_args[0][0]["client"] == ("127.0.0.2", 0)
    assert mock.call_args[0][0]["scheme"] == "https"
