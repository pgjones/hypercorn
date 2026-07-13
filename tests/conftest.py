from __future__ import annotations

import sys
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = PROJECT_ROOT / "src"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

import hypercorn.config  # noqa: E402
from hypercorn.typing import ConnectionState, HTTPScope  # noqa: E402


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
        "state": ConnectionState({}),
    }
