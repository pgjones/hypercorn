from typing import Callable

import pytest

import hypercorn.utils
from hypercorn.typing import ASGIFramework


@pytest.mark.parametrize(
    "method, status, expected", [("HEAD", 200, True), ("GET", 200, False), ("GET", 101, True)]
)
def test_suppress_body(method: str, status: int, expected: bool) -> None:
    assert hypercorn.utils.suppress_body(method, status) is expected


@pytest.mark.asyncio
async def test_invoke_asgi_3() -> None:
    result: dict = {}

    async def asgi3_callable(scope: dict, receive: Callable, send: Callable) -> None:
        nonlocal result
        result = scope

    await hypercorn.utils.invoke_asgi(asgi3_callable, {"asgi": {}}, None, None)
    assert result["asgi"]["version"] == "3.0"


@pytest.mark.asyncio
async def test_invoke_asgi_2() -> None:
    result: dict = {}

    def asgi2_callable(scope: dict) -> Callable:
        nonlocal result
        result = scope

        async def inner(receive: Callable, send: Callable) -> None:
            pass

        return inner

    await hypercorn.utils.invoke_asgi(asgi2_callable, {"asgi": {}}, None, None)  # type: ignore
    assert result["asgi"]["version"] == "2.0"


class ASGI2Class:
    def __init__(self, scope: dict) -> None:
        pass

    async def __call__(self, receive: Callable, send: Callable) -> None:
        pass


class ASGI3ClassInstance:
    def __init__(self) -> None:
        pass

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        pass


def asgi2_callable(scope: dict) -> Callable:
    async def inner(receive: Callable, send: Callable) -> None:
        pass

    return inner


async def asgi3_callable(scope: dict, receive: Callable, send: Callable) -> None:
    pass


@pytest.mark.parametrize(
    "app, is_asgi_2",
    [
        (ASGI2Class, True),
        (ASGI3ClassInstance(), False),
        (asgi2_callable, True),
        (asgi3_callable, False),
    ],
)
def test__is_asgi_2(app: ASGIFramework, is_asgi_2: bool) -> None:
    assert hypercorn.utils._is_asgi_2(app) == is_asgi_2


def test_build_and_validate_headers_validate() -> None:
    with pytest.raises(TypeError):
        hypercorn.utils.build_and_validate_headers([("string", "string")])  # type: ignore


def test_build_and_validate_headers_pseudo() -> None:
    with pytest.raises(ValueError):
        hypercorn.utils.build_and_validate_headers([(b":authority", b"quart")])


def test_filter_pseudo_headers() -> None:
    result = hypercorn.utils.filter_pseudo_headers(
        [(b":authority", b"quart"), (b":path", b"/"), (b"user-agent", b"something")]
    )
    assert result == [(b"host", b"quart"), (b"user-agent", b"something")]
