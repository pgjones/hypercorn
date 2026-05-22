from __future__ import annotations

import asyncio
import multiprocessing
import threading
from collections.abc import Callable, Iterable
from typing import Any

import pytest

from hypercorn.typing import Scope
from hypercorn.utils import (
    build_and_validate_headers,
    check_multiprocess_shutdown_event,
    filter_pseudo_headers,
    is_asgi,
    suppress_body,
)


@pytest.mark.parametrize(
    "method, status, expected", [("HEAD", 200, True), ("GET", 200, False), ("GET", 101, True)]
)
def test_suppress_body(method: str, status: int, expected: bool) -> None:
    assert suppress_body(method, status) is expected


class ASGIClassInstance:
    def __init__(self) -> None:
        pass

    async def __call__(self, scope: Scope, receive: Callable, send: Callable) -> None:
        pass


async def asgi_callable(scope: Scope, receive: Callable, send: Callable) -> None:
    pass


class WSGIClassInstance:
    def __init__(self) -> None:
        pass

    def __call__(self, environ: dict, start_response: Callable) -> Iterable[bytes]:
        pass


def wsgi_callable(environ: dict, start_response: Callable) -> Iterable[bytes]:
    pass


@pytest.mark.parametrize(
    "app, expected",
    [
        (WSGIClassInstance(), False),
        (ASGIClassInstance(), True),
        (wsgi_callable, False),
        (asgi_callable, True),
    ],
)
def test_is_asgi(app: Any, expected: bool) -> None:
    assert is_asgi(app) == expected


def test_build_and_validate_headers_validate() -> None:
    with pytest.raises(TypeError):
        build_and_validate_headers([("string", "string")])  # type: ignore


def test_build_and_validate_headers_pseudo() -> None:
    with pytest.raises(ValueError):
        build_and_validate_headers([(b":authority", b"quart")])


def test_filter_pseudo_headers() -> None:
    result = filter_pseudo_headers(
        [(b":authority", b"quart"), (b":path", b"/"), (b"user-agent", b"something")]
    )
    assert result == [(b"host", b"quart"), (b"user-agent", b"something")]


def test_filter_pseudo_headers_no_authority() -> None:
    result = filter_pseudo_headers(
        [(b"host", b"quart"), (b":path", b"/"), (b"user-agent", b"something")]
    )
    assert result == [(b"host", b"quart"), (b"user-agent", b"something")]


@pytest.mark.parametrize(
    "make_event",
    [
        # multiprocessing.Event() — the originally hinted concrete type.
        pytest.param(lambda: multiprocessing.Event(), id="multiprocessing.Event"),
        # Manager().Event() — a proxy wrapping threading.Event; valid per
        # https://docs.python.org/3/library/multiprocessing.html#multiprocessing.managers.SyncManager.Event
        pytest.param(lambda: multiprocessing.Manager().Event(), id="multiprocessing.Manager.Event"),
        # threading.Event — equally usable in single-process tests.
        pytest.param(lambda: threading.Event(), id="threading.Event"),
    ],
)
def test_check_multiprocess_shutdown_event_accepts_is_set_protocol(make_event: Callable) -> None:
    """Regression test for https://github.com/pgjones/hypercorn/issues/247.

    ``check_multiprocess_shutdown_event`` should accept anything that exposes a
    synchronous ``.is_set()`` method, not only ``multiprocessing.synchronize.Event``.
    """
    event = make_event()
    event.set()

    async def call() -> None:
        await check_multiprocess_shutdown_event(event, asyncio.sleep)

    # Should return immediately because the event is already set.
    asyncio.run(asyncio.wait_for(call(), timeout=1.0))
