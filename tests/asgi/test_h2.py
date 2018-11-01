from typing import Any, Dict

import h2
import pytest

from hypercorn.asgi.h2 import (
    ASGIH2State,
    Data,
    EndStream,
    H2Event,
    H2Mixin,
    H2StreamBase,
    Response,
    UnexpectedMessage,
)
from hypercorn.config import Config
from ..helpers import BadFramework, EmptyFramework


class MockH2(H2Mixin):
    def __init__(self) -> None:
        self.app = EmptyFramework  # type: ignore
        self.client = ("127.0.0.1", 5000)
        self.config = Config()
        self.server = ("remote", 5000)
        self.streams: Dict[int, H2StreamBase] = {}  # type: ignore
        self.sent_events: list = []

    @property
    def scheme(self) -> str:
        return "https"

    def response_headers(self) -> list:
        return [(b"server", b"hypercorn")]

    async def asend(self, event: H2Event) -> None:
        self.sent_events.append(event)


@pytest.mark.asyncio
async def test_asgi_scope() -> None:
    server = MockH2()
    request = h2.events.RequestReceived()
    request.stream_id = 0
    request.headers = [
        (b":method", b"GET"),
        (b":path", b"/path?a=b"),
        (b":authority", b"hypercorn"),
        (b":scheme", b"https"),
    ]
    server.streams[request.stream_id] = H2StreamBase()
    await server.handle_request(request)
    scope = server.streams[request.stream_id].scope

    assert scope == {
        "type": "http",
        "http_version": "2",
        "asgi": {"version": "2.0"},
        "method": "GET",
        "scheme": "https",
        "path": "/path",
        "query_string": b"a=b",
        "root_path": "",
        "headers": [
            (b":method", b"GET"),
            (b":path", b"/path?a=b"),
            (b":authority", b"hypercorn"),
            (b":scheme", b"https"),
        ],
        "client": ("127.0.0.1", 5000),
        "server": ("remote", 5000),
        "extensions": {"http.response.push": {}},
    }


@pytest.mark.asyncio
async def test_asgi_send() -> None:
    server = MockH2()
    server.streams[0] = H2StreamBase()
    server.streams[0].scope = {"method": "GET"}
    await server.asgi_send(
        0, {"type": "http.response.start", "headers": [(b"X-Header", b"Value")], "status": 200}
    )
    # Server must not send a response till the receipt of the first
    # body chunk.
    assert server.sent_events == []
    await server.asgi_send(0, {"type": "http.response.body", "body": b"a", "more_body": True})
    await server.asgi_send(0, {"type": "http.response.body", "more_body": False})
    assert server.sent_events == [
        Response(0, [(b":status", b"200"), (b"X-Header", b"Value"), (b"server", b"hypercorn")]),
        Data(0, b"a"),
        EndStream(0),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state, message_type",
    [
        (ASGIH2State.REQUEST, "not_a_real_type"),
        (ASGIH2State.RESPONSE, "http.response.start"),
        (ASGIH2State.CLOSED, "http.response.start"),
        (ASGIH2State.CLOSED, "http.response.body"),
    ],
)
async def test_asgi_send_invalid_message_given_state(state: ASGIH2State, message_type: str) -> None:
    server = MockH2()
    server.streams[1] = H2StreamBase()
    server.streams[1].state = state
    with pytest.raises(UnexpectedMessage):
        await server.asgi_send(1, {"type": message_type})


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
    server = MockH2()
    server.streams[0] = H2StreamBase()
    server.streams[0].scope = {"method": "GET"}
    with pytest.raises((TypeError, ValueError)):
        await server.asgi_send(
            0, {"type": "http.response.start", "headers": headers, "status": status}
        )
        await server.asgi_send(0, {"type": "http.response.body", "body": body})


@pytest.mark.asyncio
@pytest.mark.parametrize("path, headers", [(b"/", []), ("/", [("X-Foo", "foo")])])
async def test_asgi_send_invalid_server_push_message(path: str, headers: Any) -> None:
    server = MockH2()
    server.streams[0] = H2StreamBase()
    with pytest.raises((TypeError, ValueError)):
        await server.asgi_send(0, {"type": "http.response.push", "headers": headers, "path": path})


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/no_response", "/call"])
async def test_bad_framework(path: str) -> None:
    server = MockH2()
    server.app = BadFramework  # type: ignore
    request = h2.events.RequestReceived()
    request.stream_id = 0
    request.headers = [
        (b":method", b"GET"),
        (b":path", b"/path?a=b"),
        (b":authority", b"hypercorn"),
        (b":scheme", b"https"),
    ]
    server.streams[0] = H2StreamBase()
    await server.handle_request(request)
    assert server.sent_events == [
        Response(0, [(b":status", b"500"), (b"server", b"hypercorn")]),
        EndStream(0),
    ]
