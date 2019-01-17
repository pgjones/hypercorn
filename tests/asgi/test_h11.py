from typing import Any, List

import h11
import pytest

from hypercorn.asgi.h11 import H11Mixin
from hypercorn.asgi.utils import ASGIHTTPState, UnexpectedMessage
from hypercorn.config import Config
from hypercorn.typing import H11SendableEvent
from ..helpers import BadFramework, EmptyFramework


class MockH11(H11Mixin):
    def __init__(self) -> None:
        self.app = EmptyFramework  # type: ignore
        self.client = ("127.0.0.1", 5000)
        self.config = Config()
        self.server = ("remote", 5000)
        self.state = ASGIHTTPState.REQUEST
        self.sent_events: List[H11SendableEvent] = []

    @property
    def scheme(self) -> str:
        return "http"

    def response_headers(self) -> list:
        return [(b"server", b"hypercorn")]

    async def asend(self, event: H11SendableEvent) -> None:
        self.sent_events.append(event)


def test_error_response() -> None:
    server = MockH11()

    response = server.error_response(500)
    assert response.headers == [
        (b"content-length", b"0"),
        (b"connection", b"close"),
        (b"server", b"hypercorn"),
    ]
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_asgi_scope() -> None:
    server = MockH11()
    request = h11.Request(method="GET", target=b"/path?a=b", headers=[(b"host", b"hypercorn")])
    await server.handle_request(request)
    scope = server.scope
    assert scope == {
        "type": "http",
        "http_version": "1.1",
        "asgi": {"version": "2.0"},
        "method": "GET",
        "scheme": "http",
        "path": "/path",
        "query_string": b"a=b",
        "root_path": "",
        "headers": [(b"host", b"hypercorn")],
        "client": ("127.0.0.1", 5000),
        "server": ("remote", 5000),
    }


@pytest.mark.asyncio
async def test_asgi_send() -> None:
    server = MockH11()
    server.scope = {"method": "GET"}
    await server.asgi_send(
        {"type": "http.response.start", "headers": [(b"X-Header", b"Value")], "status": 200}
    )
    # Server must not send a response till the receipt of the first
    # body chunk.
    assert server.sent_events == []
    await server.asgi_send({"type": "http.response.body", "body": b"a", "more_body": True})
    await server.asgi_send({"type": "http.response.body", "more_body": False})
    assert server.sent_events == [
        h11.Response(status_code=200, headers=[(b"x-header", b"Value"), (b"server", b"hypercorn")]),
        h11.Data(data=b"a"),
        h11.EndOfMessage(),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state, message_type",
    [
        (ASGIHTTPState.REQUEST, "not_a_real_type"),
        (ASGIHTTPState.RESPONSE, "http.response.start"),
        (ASGIHTTPState.CLOSED, "http.response.start"),
        (ASGIHTTPState.CLOSED, "http.response.body"),
    ],
)
async def test_asgi_send_invalid_message_given_state(
    state: ASGIHTTPState, message_type: str
) -> None:
    server = MockH11()
    server.state = state
    with pytest.raises(UnexpectedMessage):
        await server.asgi_send({"type": message_type})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, headers, body",
    [
        ("201 NO CONTENT", [], b""),  # Status should be int
        (200, [("X-Foo", "foo")], b""),  # Headers should be bytes
        (200, [], "Body"),  # Body should be bytes
    ],
)
async def test_asgi_send_invalid_message(status: Any, headers: Any, body: Any) -> None:
    server = MockH11()
    server.scope = {"method": "GET"}
    with pytest.raises((TypeError, ValueError)):
        await server.asgi_send(
            {"type": "http.response.start", "headers": headers, "status": status}
        )
        await server.asgi_send({"type": "http.response.body", "body": body})


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/no_response", "/call"])
async def test_bad_framework(path: str) -> None:
    server = MockH11()
    server.app = BadFramework  # type: ignore
    request = h11.Request(method="GET", target=path.encode(), headers=[(b"host", b"hypercorn")])
    await server.handle_request(request)
    assert server.sent_events == [
        h11.Response(
            status_code=500,
            headers=[
                (b"content-length", b"0"),
                (b"connection", b"close"),
                (b"server", b"hypercorn"),
            ],
        ),
        h11.EndOfMessage(),
    ]
