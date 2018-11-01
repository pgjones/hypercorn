from typing import Any, List, Union

import h11
import pytest
import wsproto

from hypercorn.asgi.wsproto import (
    AcceptConnection,
    ASGIWebsocketState,
    CloseConnection,
    Data,
    FrameTooLarge,
    UnexpectedMessage,
    WebsocketBuffer,
    WebsocketMixin,
    WsprotoEvent,
)
from hypercorn.config import Config
from hypercorn.typing import H11SendableEvent
from ..helpers import BadFramework, EmptyFramework


def test_buffer() -> None:
    buffer_ = WebsocketBuffer(10)
    buffer_.extend(wsproto.events.TextReceived("abc", False, True))
    assert buffer_.to_message() == {"type": "websocket.receive", "bytes": None, "text": "abc"}
    buffer_.clear()
    buffer_.extend(wsproto.events.BytesReceived(b"abc", False, True))
    assert buffer_.to_message() == {"type": "websocket.receive", "bytes": b"abc", "text": None}


def test_buffer_frame_too_large() -> None:
    buffer_ = WebsocketBuffer(2)
    with pytest.raises(FrameTooLarge):
        buffer_.extend(wsproto.events.TextReceived("abc", False, True))


@pytest.mark.parametrize(
    "data",
    [
        (
            wsproto.events.TextReceived("abc", False, True),
            wsproto.events.BytesReceived(b"abc", False, True),
        ),
        (
            wsproto.events.BytesReceived(b"abc", False, True),
            wsproto.events.TextReceived("abc", False, True),
        ),
    ],
)
def test_buffer_mixed_types(data: list) -> None:
    buffer_ = WebsocketBuffer(10)
    buffer_.extend(data[0])
    with pytest.raises(TypeError):
        buffer_.extend(data[1])


class MockWebsocket(WebsocketMixin):
    def __init__(self) -> None:
        self.app = EmptyFramework  # type: ignore
        self.client = ("127.0.0.1", 5000)
        self.config = Config()
        self.server = ("remote", 5000)
        self.state = ASGIWebsocketState.HANDSHAKE

        self.sent_events: List[Union[H11SendableEvent, WsprotoEvent]] = []

    @property
    def scheme(self) -> str:
        return "ws"

    def response_headers(self) -> list:
        return [(b"server", b"hypercorn")]

    async def asend(self, event: Union[H11SendableEvent, WsprotoEvent]) -> None:
        self.sent_events.append(event)


@pytest.mark.asyncio
async def test_asgi_scope() -> None:
    server = MockWebsocket()
    request = h11.Request(method="GET", target=b"/path?a=b", headers=[(b"host", b"hypercorn")])
    connection_request = wsproto.events.ConnectionRequested([], request)
    await server.handle_websocket(connection_request)
    scope = server.scope
    assert scope == {
        "type": "websocket",
        "asgi": {"version": "2.0"},
        "scheme": "ws",
        "path": "/path",
        "query_string": b"a=b",
        "root_path": "",
        "headers": [(b"host", b"hypercorn")],
        "client": ("127.0.0.1", 5000),
        "server": ("remote", 5000),
        "subprotocols": [],
        "extensions": {"websocket.http.response": {}},
    }


@pytest.mark.asyncio
async def test_asgi_send() -> None:
    server = MockWebsocket()
    server.app = BadFramework  # type: ignore
    request = h11.Request(
        method="GET",
        target=b"/accept",
        headers=[
            (b"host", b"hypercorn"),
            (b"sec-websocket-key", b"ZdCqRHQRNflNt6o7yU48Pg=="),
            (b"sec-websocket-version", b"13"),
            (b"connection", b"upgrade"),
            (b"upgrade", b"connection"),
        ],
    )
    connection_request = wsproto.events.ConnectionRequested([], request)
    await server.asgi_send(connection_request, {"type": "websocket.accept"})
    await server.asgi_send(connection_request, {"type": "websocket.send", "bytes": b"abc"})
    await server.asgi_send(connection_request, {"type": "websocket.close", "code": 1000})
    assert server.sent_events == [
        AcceptConnection(connection_request),
        Data(b"abc"),
        CloseConnection(1000),
    ]


@pytest.mark.asyncio
async def test_asgi_send_http() -> None:
    server = MockWebsocket()
    server.scope = {"method": "GET"}
    await server.asgi_send(
        None,
        {
            "type": "websocket.http.response.start",
            "headers": [(b"X-Header", b"Value")],
            "status": 200,
        },
    )
    # Server must not send a response till the receipt of the first
    # body chunk.
    assert server.sent_events == []
    await server.asgi_send(
        None, {"type": "websocket.http.response.body", "body": b"a", "more_body": True}
    )
    await server.asgi_send(None, {"type": "websocket.http.response.body", "more_body": False})
    assert server.sent_events == [
        h11.Response(status_code=200, headers=[(b"x-header", b"Value"), (b"server", b"hypercorn")]),
        h11.Data(data=b"a", chunk_start=False, chunk_end=False),
        h11.EndOfMessage(headers=[]),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data_bytes, data_text",
    [(None, b"data"), ("data", None)],  # Text should be str  # Bytes should be bytes
)
async def test_asgi_send_invalid_message(data_bytes: Any, data_text: Any) -> None:
    server = WebsocketMixin()
    server.state = ASGIWebsocketState.CONNECTED
    with pytest.raises((TypeError, ValueError)):
        await server.asgi_send(
            {}, {"type": "websocket.send", "bytes": data_bytes, "text": data_text}
        )


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
async def test_asgi_send_invalid_message_given_state(
    state: ASGIWebsocketState, message_type: str
) -> None:
    server = MockWebsocket()
    server.state = state
    with pytest.raises(UnexpectedMessage):
        await server.asgi_send({}, {"type": message_type})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, headers, body",
    [
        ("201 NO CONTENT", [], b""),  # Status should be int
        (200, [("X-Foo", "foo")], b""),  # Headers should be bytes
        (200, [], "Body"),  # Body should be bytes
    ],
)
async def test_asgi_send_invalid_http_message(status: Any, headers: Any, body: Any) -> None:
    server = WebsocketMixin()
    server.state = ASGIWebsocketState.HANDSHAKE
    server.scope = {"method": "GET"}
    with pytest.raises((TypeError, ValueError)):
        await server.asgi_send(
            {"method": "GET"},
            {"type": "websocket.http.response.start", "headers": headers, "status": status},
        )
        await server.asgi_send(
            {"method": "GET"}, {"type": "websocket.http.response.body", "body": body}
        )


@pytest.mark.asyncio
async def test_bad_framework() -> None:
    server = MockWebsocket()
    server.app = BadFramework  # type: ignore
    request = h11.Request(
        method="GET",
        target=b"/accept",
        headers=[
            (b"host", b"hypercorn"),
            (b"sec-websocket-key", b"ZdCqRHQRNflNt6o7yU48Pg=="),
            (b"sec-websocket-version", b"13"),
            (b"connection", b"upgrade"),
            (b"upgrade", b"connection"),
        ],
    )
    connection_request = wsproto.events.ConnectionRequested([], request)
    await server.handle_websocket(connection_request)
    assert server.sent_events == [AcceptConnection(connection_request), CloseConnection(1006)]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/no_response", "/call"])
async def test_bad_framework_http(path: str) -> None:
    server = MockWebsocket()
    server.app = BadFramework  # type: ignore
    request = h11.Request(method="GET", target=path.encode(), headers=[(b"host", b"hypercorn")])
    connection_request = wsproto.events.ConnectionRequested([], request)
    await server.handle_websocket(connection_request)
    assert server.sent_events == [
        h11.Response(status_code=500, headers=[(b"server", b"hypercorn")]),
        h11.EndOfMessage(headers=[]),
    ]
