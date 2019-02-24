from typing import Any, List

import pytest
from wsproto.events import (
    AcceptConnection,
    BytesMessage,
    CloseConnection,
    Event,
    RejectConnection,
    RejectData,
    Request,
)

from hypercorn.asgi.utils import ASGIWebsocketState, UnexpectedMessage
from hypercorn.asgi.wsproto import WebsocketMixin
from hypercorn.config import Config
from ..helpers import BadFramework, EmptyFramework


class MockWebsocket(WebsocketMixin):
    def __init__(self) -> None:
        self.app = EmptyFramework  # type: ignore
        self.client = ("127.0.0.1", 5000)
        self.config = Config()
        self.server = ("remote", 5000)
        self.scope = {"method": "GET"}
        self.start_time = 0.0
        self.state = ASGIWebsocketState.HANDSHAKE

        self.sent_events: List[Event] = []

    @property
    def scheme(self) -> str:
        return "ws"

    def response_headers(self) -> list:
        return [(b"server", b"hypercorn")]

    async def asend(self, event: Event) -> None:
        self.sent_events.append(event)


@pytest.mark.asyncio
async def test_asgi_scope() -> None:
    server = MockWebsocket()
    connection_request = Request(target="/path?a=b", host="hypercorn")
    await server.handle_websocket(connection_request)
    scope = server.scope
    assert scope == {
        "type": "websocket",
        "asgi": {"version": "2.0"},
        "http_version": "1.1",
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
    await server.asgi_send({"type": "websocket.accept"})
    await server.asgi_send({"type": "websocket.send", "bytes": b"abc"})
    await server.asgi_send({"type": "websocket.close", "code": 1000})
    assert isinstance(server.sent_events[0], AcceptConnection)
    assert server.sent_events[1:] == [BytesMessage(data=b"abc"), CloseConnection(code=1000)]


@pytest.mark.asyncio
async def test_asgi_send_http() -> None:
    server = MockWebsocket()
    await server.asgi_send(
        {
            "type": "websocket.http.response.start",
            "headers": [(b"X-Header", b"Value")],
            "status": 200,
        }
    )
    # Server must not send a response till the receipt of the first
    # body chunk.
    assert server.sent_events == []
    await server.asgi_send(
        {"type": "websocket.http.response.body", "body": b"a", "more_body": True}
    )
    await server.asgi_send({"type": "websocket.http.response.body", "more_body": False})
    server.sent_events[0].headers = list(server.sent_events[0].headers)  # To allow comparison
    assert server.sent_events == [
        RejectConnection(
            status_code=200,
            headers=[(b"x-header", b"Value"), (b"server", b"hypercorn")],
            has_body=True,
        ),
        RejectData(data=b"a", body_finished=False),
        RejectData(data=b"", body_finished=True),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data_bytes, data_text",
    [(None, b"data"), ("data", None)],  # Text should be str  # Bytes should be bytes
)
async def test_asgi_send_invalid_message(data_bytes: Any, data_text: Any) -> None:
    server = WebsocketMixin()
    server.config = Config()
    server.start_time = 0.0
    server.state = ASGIWebsocketState.CONNECTED
    with pytest.raises((TypeError, ValueError)):
        await server.asgi_send({"type": "websocket.send", "bytes": data_bytes, "text": data_text})


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
async def test_asgi_send_invalid_http_message(status: Any, headers: Any, body: Any) -> None:
    server = WebsocketMixin()
    server.config = Config()
    server.start_time = 0.0
    server.state = ASGIWebsocketState.HANDSHAKE
    server.scope = {"method": "GET"}
    with pytest.raises((TypeError, ValueError)):
        await server.asgi_send(
            {"type": "websocket.http.response.start", "headers": headers, "status": status}
        )
        await server.asgi_send({"type": "websocket.http.response.body", "body": body})


@pytest.mark.asyncio
async def test_bad_framework() -> None:
    server = MockWebsocket()
    server.app = BadFramework  # type: ignore
    headers = [
        (b"sec-websocket-key", b"ZdCqRHQRNflNt6o7yU48Pg=="),
        (b"sec-websocket-version", b"13"),
        (b"connection", b"upgrade"),
        (b"upgrade", b"connection"),
    ]
    request = Request(target="/accept", host="hypercorn", extra_headers=headers)
    await server.handle_websocket(request)
    assert isinstance(server.sent_events[0], AcceptConnection)
    assert server.sent_events[1:] == [CloseConnection(code=1006)]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/", "/no_response", "/call"])
async def test_bad_framework_http(path: str) -> None:
    server = MockWebsocket()
    server.app = BadFramework  # type: ignore
    headers = [
        (b"sec-websocket-key", b"ZdCqRHQRNflNt6o7yU48Pg=="),
        (b"sec-websocket-version", b"13"),
        (b"connection", b"upgrade"),
        (b"upgrade", b"connection"),
    ]
    request = Request(target=path, host="hypercorn", extra_headers=headers)
    await server.handle_websocket(request)
    assert server.sent_events == [
        RejectConnection(status_code=500, headers=[(b"server", b"hypercorn")], has_body=False)
    ]
