from typing import Any, List, Tuple
from unittest.mock import call, Mock

import pytest
from wsproto.events import BytesMessage, TextMessage

from asynctest.mock import CoroutineMock, Mock as AsyncMock
from hypercorn.config import Config
from hypercorn.logging import Logger
from hypercorn.protocol.events import Body, Data, EndBody, EndData, Request, Response, StreamClosed
from hypercorn.protocol.ws_stream import (
    ASGIWebsocketState,
    FrameTooLarge,
    Handshake,
    WebsocketBuffer,
    WSStream,
)
from hypercorn.utils import UnexpectedMessage


def test_buffer() -> None:
    buffer_ = WebsocketBuffer(10)
    buffer_.extend(TextMessage(data="abc", frame_finished=False, message_finished=True))
    assert buffer_.to_message() == {"type": "websocket.receive", "bytes": None, "text": "abc"}
    buffer_.clear()
    buffer_.extend(BytesMessage(data=b"abc", frame_finished=False, message_finished=True))
    assert buffer_.to_message() == {"type": "websocket.receive", "bytes": b"abc", "text": None}


def test_buffer_frame_too_large() -> None:
    buffer_ = WebsocketBuffer(2)
    with pytest.raises(FrameTooLarge):
        buffer_.extend(TextMessage(data="abc", frame_finished=False, message_finished=True))


@pytest.mark.parametrize(
    "data",
    [
        (
            TextMessage(data="abc", frame_finished=False, message_finished=True),
            BytesMessage(data=b"abc", frame_finished=False, message_finished=True),
        ),
        (
            BytesMessage(data=b"abc", frame_finished=False, message_finished=True),
            TextMessage(data="abc", frame_finished=False, message_finished=True),
        ),
    ],
)
def test_buffer_mixed_types(data: list) -> None:
    buffer_ = WebsocketBuffer(10)
    buffer_.extend(data[0])
    with pytest.raises(TypeError):
        buffer_.extend(data[1])


@pytest.mark.parametrize(
    "headers, http_version, valid",
    [
        ([], "1.0", False),
        (
            [
                (b"connection", b"upgrade, keep-alive"),
                (b"sec-websocket-version", b"13"),
                (b"upgrade", b"websocket"),
                (b"sec-websocket-key", b"UnQ3lpJAH6j2PslA993iKQ=="),
            ],
            "1.1",
            True,
        ),
        (
            [
                (b"connection", b"keep-alive"),
                (b"sec-websocket-version", b"13"),
                (b"upgrade", b"websocket"),
                (b"sec-websocket-key", b"UnQ3lpJAH6j2PslA993iKQ=="),
            ],
            "1.1",
            False,
        ),
        (
            [
                (b"connection", b"upgrade, keep-alive"),
                (b"sec-websocket-version", b"13"),
                (b"upgrade", b"h2c"),
                (b"sec-websocket-key", b"UnQ3lpJAH6j2PslA993iKQ=="),
            ],
            "1.1",
            False,
        ),
        ([(b"sec-websocket-version", b"13")], "2", True),
        ([(b"sec-websocket-version", b"12")], "2", False),
    ],
)
def test_handshake_validity(
    headers: List[Tuple[bytes, bytes]], http_version: str, valid: bool
) -> None:
    handshake = Handshake(headers, http_version)
    assert handshake.is_valid() is valid


def test_handshake_accept_http1() -> None:
    handshake = Handshake(
        [
            (b"connection", b"upgrade, keep-alive"),
            (b"sec-websocket-version", b"13"),
            (b"upgrade", b"websocket"),
            (b"sec-websocket-key", b"UnQ3lpJAH6j2PslA993iKQ=="),
        ],
        "1.1",
    )
    status_code, headers, _ = handshake.accept(None)
    assert status_code == 101
    assert headers == [
        (b"sec-websocket-accept", b"1BpNk/3ah1huDGgcuMJBcjcMbEA="),
        (b"upgrade", b"WebSocket"),
        (b"connection", b"Upgrade"),
    ]


def test_handshake_accept_http2() -> None:
    handshake = Handshake([(b"sec-websocket-version", b"13")], "2")
    status_code, headers, _ = handshake.accept(None)
    assert status_code == 200
    assert headers == []


@pytest.fixture(name="stream")
async def _stream() -> WSStream:
    stream = WSStream(Config(), False, None, None, CoroutineMock(), CoroutineMock(), 1)
    stream.spawn_app.return_value = CoroutineMock()
    stream.app_put = CoroutineMock()
    stream.config._log = AsyncMock(spec=Logger)
    return stream


@pytest.mark.asyncio
async def test_handle_request(stream: WSStream) -> None:
    await stream.handle(
        Request(
            stream_id=1,
            http_version="2",
            headers=[(b"sec-websocket-version", b"13")],
            raw_path=b"/?a=b",
            method="GET",
        )
    )
    stream.spawn_app.assert_called()
    scope = stream.spawn_app.call_args[0][0]
    assert scope == {
        "type": "websocket",
        "asgi": {"spec_version": "2.1"},
        "scheme": "ws",
        "http_version": "2",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"a=b",
        "root_path": "",
        "headers": [(b"sec-websocket-version", b"13")],
        "client": None,
        "server": None,
        "subprotocols": [],
        "extensions": {"websocket.http.response": {}},
    }


@pytest.mark.asyncio
async def test_handle_connection(stream: WSStream) -> None:
    await stream.handle(
        Request(
            stream_id=1,
            http_version="2",
            headers=[(b"sec-websocket-version", b"13")],
            raw_path=b"/?a=b",
            method="GET",
        )
    )
    await stream.app_send({"type": "websocket.accept"})
    stream.app_put = CoroutineMock()
    await stream.handle(Data(stream_id=1, data=b"\x81\x85&`\x13\x0eN\x05\x7fbI"))
    stream.app_put.assert_called()
    assert stream.app_put.call_args_list == [
        call({"type": "websocket.receive", "bytes": None, "text": "hello"})
    ]


@pytest.mark.asyncio
async def test_handle_closed(stream: WSStream) -> None:
    await stream.handle(StreamClosed(stream_id=1))
    stream.app_put.assert_called()
    assert stream.app_put.call_args_list == [call({"type": "websocket.disconnect", "code": 1006})]


@pytest.mark.asyncio
async def test_send_accept(stream: WSStream) -> None:
    await stream.handle(
        Request(
            stream_id=1,
            http_version="2",
            headers=[(b"sec-websocket-version", b"13")],
            raw_path=b"/",
            method="GET",
        )
    )
    await stream.app_send({"type": "websocket.accept"})
    assert stream.state == ASGIWebsocketState.CONNECTED
    stream.send.assert_called()
    assert stream.send.call_args_list == [call(Response(stream_id=1, headers=[], status_code=200))]


@pytest.mark.asyncio
async def test_send_reject(stream: WSStream) -> None:
    await stream.handle(
        Request(
            stream_id=1,
            http_version="2",
            headers=[(b"sec-websocket-version", b"13")],
            raw_path=b"/",
            method="GET",
        )
    )
    await stream.app_send({"type": "websocket.http.response.start", "status": 200, "headers": []})
    assert stream.state == ASGIWebsocketState.HANDSHAKE
    # Must wait for response before sending anything
    stream.send.assert_not_called()
    await stream.app_send({"type": "websocket.http.response.body", "body": b"Body"})
    assert stream.state == ASGIWebsocketState.HTTPCLOSED
    stream.send.assert_called()
    assert stream.send.call_args_list == [
        call(Response(stream_id=1, headers=[], status_code=200)),
        call(Body(stream_id=1, data=b"Body")),
        call(EndBody(stream_id=1)),
    ]
    stream.config._log.access.assert_called()


@pytest.mark.asyncio
async def test_send_app_error_handshake(stream: WSStream) -> None:
    await stream.handle(
        Request(
            stream_id=1,
            http_version="2",
            headers=[(b"sec-websocket-version", b"13")],
            raw_path=b"/",
            method="GET",
        )
    )
    await stream.app_send(None)
    stream.send.assert_called()
    assert stream.send.call_args_list == [
        call(
            Response(
                stream_id=1,
                headers=[(b"content-length", b"0"), (b"connection", b"close")],
                status_code=500,
            )
        ),
        call(EndBody(stream_id=1)),
        call(StreamClosed(stream_id=1)),
    ]
    stream.config._log.access.assert_called()


@pytest.mark.asyncio
async def test_send_app_error_connected(stream: WSStream) -> None:
    await stream.handle(
        Request(
            stream_id=1,
            http_version="2",
            headers=[(b"sec-websocket-version", b"13")],
            raw_path=b"/",
            method="GET",
        )
    )
    await stream.app_send({"type": "websocket.accept"})
    await stream.app_send(None)
    stream.send.assert_called()
    assert stream.send.call_args_list == [
        call(Response(stream_id=1, headers=[], status_code=200)),
        call(Data(stream_id=1, data=b"\x88\x02\x03\xe8")),
        call(StreamClosed(stream_id=1)),
    ]
    stream.config._log.access.assert_called()


@pytest.mark.asyncio
async def test_send_connection(stream: WSStream) -> None:
    await stream.handle(
        Request(
            stream_id=1,
            http_version="2",
            headers=[(b"sec-websocket-version", b"13")],
            raw_path=b"/",
            method="GET",
        )
    )
    await stream.app_send({"type": "websocket.accept"})
    await stream.app_send({"type": "websocket.send", "text": "hello"})
    await stream.app_send({"type": "websocket.close"})
    stream.send.assert_called()
    assert stream.send.call_args_list == [
        call(Response(stream_id=1, headers=[], status_code=200)),
        call(Data(stream_id=1, data=b"\x81\x05hello")),
        call(Data(stream_id=1, data=b"\x88\x02\x03\xe8")),
        call(EndData(stream_id=1)),
    ]


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
async def test_send_invalid_message_given_state(
    stream: WSStream, state: ASGIWebsocketState, message_type: str
) -> None:
    stream.state = state
    with pytest.raises(UnexpectedMessage):
        await stream.app_send({"type": message_type})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, headers, body",
    [
        ("201 NO CONTENT", [], b""),  # Status should be int
        (200, [("X-Foo", "foo")], b""),  # Headers should be bytes
        (200, [], "Body"),  # Body should be bytes
    ],
)
async def test_send_invalid_http_message(
    stream: WSStream, status: Any, headers: Any, body: Any
) -> None:
    stream.connection = Mock()
    stream.state = ASGIWebsocketState.HANDSHAKE
    stream.scope = {"method": "GET"}
    with pytest.raises((TypeError, ValueError)):
        await stream.app_send(
            {"type": "websocket.http.response.start", "headers": headers, "status": status}
        )
        await stream.app_send({"type": "websocket.http.response.body", "body": body})


@pytest.mark.parametrize(
    "state, idle",
    [
        (state, False)
        for state in ASGIWebsocketState
        if state not in {ASGIWebsocketState.CLOSED, ASGIWebsocketState.HTTPCLOSED}
    ]
    + [(ASGIWebsocketState.CLOSED, True), (ASGIWebsocketState.HTTPCLOSED, True)],
)
def test_stream_idle(stream: WSStream, state: ASGIWebsocketState, idle: bool) -> None:
    stream.state = state
    assert stream.idle is idle


@pytest.mark.asyncio
async def test_closure(stream: WSStream) -> None:
    assert not stream.closed
    await stream.handle(StreamClosed(stream_id=1))
    assert stream.closed
    await stream.handle(StreamClosed(stream_id=1))
    assert stream.closed
    # It is important that the disconnect message has only been sent
    # once.
    assert stream.app_put.call_args_list == [call({"type": "websocket.disconnect", "code": 1006})]


@pytest.mark.asyncio
async def test_closed_app_send_noop(stream: WSStream) -> None:
    stream.closed = True
    await stream.app_send({"type": "websocket.accept"})
    stream.send.assert_not_called()
