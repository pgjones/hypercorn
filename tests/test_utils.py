from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import pytest

from hypercorn.typing import Scope
from hypercorn.utils import (
    build_and_validate_headers,
    filter_pseudo_headers,
    is_asgi,
    reduce_target_to_origin_form,
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
    "target, expected",
    [
        (b"/?a=b", b"/?a=b"),  # origin-form is unchanged
        (b"/a://b", b"/a://b"),  # origin-form may contain "://"
        (b"*", b"*"),  # asterisk-form is unchanged
        (b"example.org:443", b"example.org:443"),  # authority-form is unchanged
        (b"http://example.org/?a=b", b"/?a=b"),
        (b"http://example.org", b"/"),  # an empty path is equivalent to "/"
        (b"http://example.org?a=b", b"/?a=b"),
        (b"https://user@example.org:8080/path%20x?q=1", b"/path%20x?q=1"),
        (b"HTTP://EXAMPLE.ORG/Path", b"/Path"),  # the scheme is case-insensitive
    ],
)
def test_reduce_target_to_origin_form(target: bytes, expected: bytes) -> None:
    assert reduce_target_to_origin_form(target) == expected
