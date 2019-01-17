from typing import Any

import h2
import pytest

from hypercorn.asgi.h2 import (
    Data,
    EndStream,
    H2Event,
    H2HTTPStreamMixin,
    H2WebsocketStreamMixin,
    Response,
)
from hypercorn.asgi.utils import ASGIHTTPState, ASGIWebsocketState, UnexpectedMessage
from hypercorn.config import Config
from ..helpers import BadFramework, EmptyFramework


class MockH2HTTPStream(H2HTTPStreamMixin):
    def __init__(self) -> None:
        self.app = EmptyFramework  # type: ignore
        self.config = Config()
        self.sent_events: list = []
        self.state = ASGIHTTPState.REQUEST

    async def asend(self, event: H2Event) -> None:
        self.sent_events.append(event)


@pytest.mark.asyncio
async def test_http_asgi_scope() -> None:
    stream = MockH2HTTPStream()
    request = h2.events.RequestReceived()
    request.stream_id = 0
    request.headers = [
        (b":method", b"GET"),
        (b":path", b"/path?a=b"),
        (b":authority", b"hypercorn"),
        (b":scheme", b"https"),
    ]
    await stream.handle_request(request, "https", ("127.0.0.1", 5000), ("remote", 5000))
    scope = stream.scope

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
async def test_http_asgi_send() -> None:
    stream = MockH2HTTPStream()
    stream.scope = {"method": "GET"}
    await stream.asgi_send(
        {"type": "http.response.start", "headers": [(b"X-Header", b"Value")], "status": 200}
    )
    # Server must not send a response till the receipt of the first
    # body chunk.
    assert stream.sent_events == []
    await stream.asgi_send({"type": "http.response.body", "body": b"a", "more_body": True})
    await stream.asgi_send({"type": "http.response.body", "more_body": False})
    assert stream.sent_events == [
        Response([(b":status", b"200"), (b"X-Header", b"Value")]),
        Data(b"a"),
        EndStream(),
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
async def test_http_asgi_send_invalid_message_given_state(
    state: ASGIHTTPState, message_type: str
) -> None:
    stream = MockH2HTTPStream()
    stream.state = state
    with pytest.raises(UnexpectedMessage):
        await stream.asgi_send({"type": message_type})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, headers, body",
    [
        ("201 NO CONTENT", [], b""),  # Status should be int
        (200, [("X-Foo", "foo")], b""),  # Headers should be bytes
        (200, [], "Body"),  # Body should be bytes
    ],
)
async def test_http_asgi_send_invalid_message(status: Any, headers: Any, body: Any) -> None:
    stream = MockH2HTTPStream()
    stream.scope = {"method": "GET"}
    with pytest.raises((TypeError, ValueError)):
        await stream.asgi_send(
            {"type": "http.response.start", "headers": headers, "status": status}
        )
        await stream.asgi_send({"type": "http.response.body", "body": body})


@pytest.mark.asyncio
@pytest.mark.parametrize("path, headers", [(b"/", []), ("/", [("X-Foo", "foo")])])
async def test_http_asgi_send_invalid_server_push_message(path: str, headers: Any) -> None:
    stream = MockH2HTTPStream()
    with pytest.raises((TypeError, ValueError)):
        await stream.asgi_send({"type": "http.response.push", "headers": headers, "path": path})


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/no_response", "/call"])
async def test_http_bad_framework(path: str) -> None:
    stream = MockH2HTTPStream()
    stream.app = BadFramework  # type: ignore
    request = h2.events.RequestReceived()
    request.headers = [
        (b":method", b"GET"),
        (b":path", b"/path?a=b"),
        (b":authority", b"hypercorn"),
        (b":scheme", b"https"),
    ]
    await stream.handle_request(request, "https", ("127.0.0.1", 5000), ("remote", 5000))
    assert stream.sent_events == [Response([(b":status", b"500")]), EndStream()]


class MockH2WebsocketStream(H2WebsocketStreamMixin):
    def __init__(self) -> None:
        self.app = EmptyFramework  # type: ignore
        self.config = Config()
        self.scope = {"headers": []}
        self.sent_events: list = []
        self.start_time = 0.0
        self.state = ASGIWebsocketState.HANDSHAKE

    async def asend(self, event: H2Event) -> None:
        self.sent_events.append(event)

    def send(self, event: H2Event) -> None:
        self.sent_events.append(event)


@pytest.mark.asyncio
async def test_websocket_asgi_scope() -> None:
    stream = MockH2WebsocketStream()
    request = h2.events.RequestReceived()
    request.headers = [
        (b":method", b"GET"),
        (b":path", b"/path?a=b"),
        (b":authority", b"hypercorn"),
        (b":scheme", b"https"),
    ]
    await stream.handle_request(request, "https", ("127.0.0.1", 5000), ("remote", 5000))
    scope = stream.scope
    assert scope == {
        "type": "websocket",
        "asgi": {"version": "2.0"},
        "http_version": "2",
        "scheme": "wss",
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
        "subprotocols": [],
        "extensions": {"websocket.http.response": {}},
    }


@pytest.mark.asyncio
async def test_websocket_asgi_send() -> None:
    stream = MockH2WebsocketStream()
    stream.app = BadFramework  # type: ignore
    await stream.asgi_send({"type": "websocket.accept"})
    await stream.asgi_send({"type": "websocket.send", "bytes": b"abc"})
    await stream.asgi_send({"type": "websocket.close", "code": 1000})
    assert stream.sent_events == [
        Response(headers=[(b":status", b"200")]),
        Data(data=b"\x82\x03abc"),
        Data(data=b"\x88\x02\x03\xe8"),
    ]


@pytest.mark.asyncio
async def test_websocket_asgi_send_http() -> None:
    stream = MockH2WebsocketStream()
    await stream.asgi_send(
        {
            "type": "websocket.http.response.start",
            "headers": [(b"X-Header", b"Value")],
            "status": 200,
        }
    )
    # Stream must not send a response till the receipt of the first
    # body chunk.
    assert stream.sent_events == []
    await stream.asgi_send(
        {"type": "websocket.http.response.body", "body": b"a", "more_body": True}
    )
    await stream.asgi_send({"type": "websocket.http.response.body", "more_body": False})
    assert stream.sent_events == [
        Response(headers=[(b":status", b"200"), (b"X-Header", b"Value")]),
        Data(data=b"a"),
        EndStream(),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data_bytes, data_text",
    [(None, b"data"), ("data", None)],  # Text should be str  # Bytes should be bytes
)
async def test_websocket_asgi_send_invalid_message(data_bytes: Any, data_text: Any) -> None:
    stream = MockH2WebsocketStream()
    stream.config = Config()
    stream.state = ASGIWebsocketState.CONNECTED
    with pytest.raises((TypeError, ValueError)):
        await stream.asgi_send({"type": "websocket.send", "bytes": data_bytes, "text": data_text})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state, message_type",
    [
        (ASGIWebsocketState.HANDSHAKE, "websocket.send"),
        (ASGIWebsocketState.RESPONSE, "websocket.accept"),
        (ASGIWebsocketState.RESPONSE, "websocket.send"),
        (ASGIWebsocketState.CONNECTED, "websocket.http.response.start"),
        (ASGIWebsocketState.CONNECTED, "websocket.http.response.body"),
        (ASGIWebsocketState.CLOSED, "websocket.send"),
        (ASGIWebsocketState.CLOSED, "websocket.http.response.start"),
        (ASGIWebsocketState.CLOSED, "websocket.http.response.body"),
    ],
)
async def test_websocket_asgi_send_invalid_message_given_state(
    state: ASGIWebsocketState, message_type: str
) -> None:
    stream = MockH2WebsocketStream()
    stream.state = state
    with pytest.raises(UnexpectedMessage):
        await stream.asgi_send({"type": message_type})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, headers, body",
    [
        ("201 NO CONTENT", [], b""),  # Status should be int
        (200, [("X-Foo", "foo")], b""),  # Headers should be bytes
        (200, [], "Body"),  # Body should be bytes
    ],
)
async def test_websocket_asgi_send_invalid_http_message(
    status: Any, headers: Any, body: Any
) -> None:
    stream = MockH2WebsocketStream()
    stream.state = ASGIWebsocketState.HANDSHAKE
    with pytest.raises((TypeError, ValueError)):
        await stream.asgi_send(
            {"type": "websocket.http.response.start", "headers": headers, "status": status}
        )
        await stream.asgi_send({"type": "websocket.http.response.body", "body": body})


@pytest.mark.asyncio
async def test_websocket_bad_framework() -> None:
    stream = MockH2WebsocketStream()
    stream.app = BadFramework  # type: ignore
    request = h2.events.RequestReceived()
    request.headers = [
        (b":method", b"GET"),
        (b":protocol", "websocket"),
        (b":path", b"/accept"),
        (b":authority", b"hypercorn"),
        (b":scheme", b"https"),
        (b"sec-websocket-version", b"13"),
    ]
    await stream.handle_request(request, "https", ("127.0.0.1", 5000), ("remote", 5000))
    assert stream.sent_events == [
        Response(headers=[(b":status", b"200")]),
        Data(data=b"\x88\x02\x03\xe8"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/no_response", "/call"])
async def test_bad_framework_http(path: str) -> None:
    stream = MockH2WebsocketStream()
    stream.app = BadFramework  # type: ignore
    request = h2.events.RequestReceived()
    request.headers = [
        (b":method", b"GET"),
        (b":protocol", "websocket"),
        (b":path", path.encode()),
        (b":authority", b"hypercorn"),
        (b":scheme", b"https"),
        (b"sec-websocket-version", b"13"),
    ]
    await stream.handle_request(request, "https", ("127.0.0.1", 5000), ("remote", 5000))
    assert stream.sent_events == [Response(headers=[(b":status", b"500")]), EndStream()]
