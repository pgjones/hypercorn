from __future__ import annotations

import pytest
from _pytest.monkeypatch import MonkeyPatch

import hypercorn.config
from hypercorn.typing import HTTPScope


@pytest.fixture(autouse=True)
def _time(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(hypercorn.config, "time", lambda: 5000)


@pytest.fixture(name="http_scope")
def _http_scope() -> HTTPScope:
    return {
        "type": "http",
        "asgi": {},
        "http_version": "2",
        "method": "GET",
        "scheme": "https",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"a=b",
        "root_path": "",
        "headers": [
            (b"User-Agent", b"Hypercorn"),
            (b"X-Hypercorn", b"Hypercorn"),
            (b"Referer", b"hypercorn"),
        ],
        "client": ("127.0.0.1", 80),
        "server": None,
        "extensions": {},
    }
