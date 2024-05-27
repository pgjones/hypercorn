from __future__ import annotations

from typing import Any, cast
from unittest.mock import call

import pytest
import pytest_asyncio

from hypercorn.asyncio.statsd import StatsdLogger
from hypercorn.asyncio.worker_context import WorkerContext
from hypercorn.config import Config
from hypercorn.logging import Logger
from hypercorn.protocol.events import (
    Body,
    EndBody,
    InformationalResponse,
    Request,
    Response,
    StreamClosed,
    Trailers,
)
from hypercorn.protocol.http_stream import ASGIHTTPState, HTTPStream
from hypercorn.typing import (
    ConnectionState,
    HTTPResponseBodyEvent,
    HTTPResponseStartEvent,
    HTTPScope,
)
from hypercorn.utils import UnexpectedMessageError

try:
    from unittest.mock import AsyncMock
except ImportError:
    # Python < 3.8
    from mock import AsyncMock  # type: ignore


@pytest_asyncio.fixture(name="stream")  # type: ignore[misc]
async def _stream() -> HTTPStream:
    stream = HTTPStream(
        AsyncMock(), Config(), WorkerContext(None), AsyncMock(), False, None, None, AsyncMock(), 1
    )
    stream.app_put = AsyncMock()
    stream.config._log = AsyncMock(spec=Logger)
    return stream


@pytest.mark.parametrize("http_version", ["1.0", "1.1"])
@pytest.mark.asyncio
async def test_handle_request_http_1(stream: HTTPStream, http_version: str) -> None:
    await stream.handle(
        Request(
            stream_id=1,
            http_version=http_version,
            headers=[],
            raw_path=b"/?a=b",
            method="GET",
            state=ConnectionState({}),
        )
    )
    stream.task_group.spawn_app.assert_called()  # type: ignore
    scope = stream.task_group.spawn_app.call_args[0][2]  # type: ignore
    assert scope == {
        "type": "http",
        "http_version": http_version,
        "asgi": {"spec_version": "2.1", "version": "3.0"},
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"a=b",
        "root_path": stream.config.root_path,
        "headers": [],
        "client": None,
        "server": None,
        "extensions": {},
        "state": ConnectionState({}),
    }


@pytest.mark.asyncio
async def test_handle_request_http_2(stream: HTTPStream) -> None:
    await stream.handle(
        Request(
            stream_id=1,
            http_version="2",
            headers=[],
            raw_path=b"/?a=b",
            method="GET",
            state=ConnectionState({}),
        )
    )
    stream.task_group.spawn_app.assert_called()  # type: ignore
    scope = stream.task_group.spawn_app.call_args[0][2]  # type: ignore
    assert scope == {
        "type": "http",
        "http_version": "2",
        "asgi": {"spec_version": "2.1", "version": "3.0"},
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"a=b",
        "root_path": stream.config.root_path,
        "headers": [],
        "client": None,
        "server": None,
        "extensions": {
            "http.response.trailers": {},
            "http.response.early_hint": {},
            "http.response.push": {},
        },
        "state": ConnectionState({}),
    }


@pytest.mark.asyncio
async def test_handle_body(stream: HTTPStream) -> None:
    await stream.handle(Body(stream_id=1, data=b"data"))
    stream.app_put.assert_called()  # type: ignore
    assert stream.app_put.call_args_list == [  # type: ignore
        call({"type": "http.request", "body": b"data", "more_body": True})
    ]


@pytest.mark.asyncio
async def test_handle_end_body(stream: HTTPStream) -> None:
    stream.app_put = AsyncMock()
    await stream.handle(EndBody(stream_id=1))
    stream.app_put.assert_called()
    assert stream.app_put.call_args_list == [
        call({"type": "http.request", "body": b"", "more_body": False})
    ]


@pytest.mark.asyncio
async def test_handle_closed(stream: HTTPStream) -> None:
    await stream.handle(
        Request(
            stream_id=1,
            http_version="2",
            headers=[],
            raw_path=b"/?a=b",
            method="GET",
            state=ConnectionState({}),
        )
    )
    await stream.handle(StreamClosed(stream_id=1))
    stream.app_put.assert_called()  # type: ignore
    assert stream.app_put.call_args_list == [call({"type": "http.disconnect"})]  # type: ignore


@pytest.mark.asyncio
async def test_send_response(stream: HTTPStream) -> None:
    await stream.handle(
        Request(
            stream_id=1,
            http_version="2",
            headers=[],
            raw_path=b"/?a=b",
            method="GET",
            state=ConnectionState({}),
        )
    )
    await stream.app_send(
        cast(HTTPResponseStartEvent, {"type": "http.response.start", "status": 200, "headers": []})
    )
    assert stream.state == ASGIHTTPState.RESPONSE
    await stream.app_send(
        cast(HTTPResponseBodyEvent, {"type": "http.response.body", "body": b"Body"})
    )
    assert stream.state == ASGIHTTPState.CLOSED  # type: ignore
    stream.send.assert_called()
    assert stream.send.call_args_list == [
        call(Response(stream_id=1, headers=[], status_code=200)),
        call(Body(stream_id=1, data=b"Body")),
        call(EndBody(stream_id=1)),
        call(StreamClosed(stream_id=1)),
    ]
    stream.config._log.access.assert_called()


@pytest.mark.asyncio
async def test_invalid_server_name(stream: HTTPStream) -> None:
    stream.config.server_names = ["hypercorn"]
    await stream.handle(
        Request(
            stream_id=1,
            http_version="2",
            headers=[(b"host", b"example.com")],
            raw_path=b"/",
            method="GET",
            state=ConnectionState({}),
        )
    )
    assert stream.send.call_args_list == [  # type: ignore
        call(
            Response(
                stream_id=1,
                headers=[(b"content-length", b"0"), (b"connection", b"close")],
                status_code=404,
            )
        ),
        call(EndBody(stream_id=1)),
    ]
    # This shouldn't error
    await stream.handle(Body(stream_id=1, data=b"Body"))


@pytest.mark.asyncio
async def test_send_push(stream: HTTPStream, http_scope: HTTPScope) -> None:
    stream.scope = http_scope
    stream.stream_id = 1
    await stream.app_send({"type": "http.response.push", "path": "/push", "headers": []})
    assert stream.send.call_args_list == [  # type: ignore
        call(
            Request(
                stream_id=1,
                headers=[(b":scheme", b"https")],
                http_version="2",
                method="GET",
                raw_path=b"/push",
                state=ConnectionState({}),
            )
        )
    ]


@pytest.mark.asyncio
async def test_send_early_hint(stream: HTTPStream, http_scope: HTTPScope) -> None:
    stream.scope = http_scope
    stream.stream_id = 1
    await stream.app_send(
        {"type": "http.response.early_hint", "links": [b'</style.css>; rel="preload"; as="style"']}
    )
    assert stream.send.call_args_list == [  # type: ignore
        call(
            InformationalResponse(
                stream_id=1,
                headers=[(b"link", b'</style.css>; rel="preload"; as="style"')],
                status_code=103,
            )
        )
    ]


@pytest.mark.asyncio
async def test_send_trailers(stream: HTTPStream) -> None:
    await stream.handle(
        Request(
            stream_id=1,
            http_version="2",
            headers=[(b"te", b"trailers")],
            raw_path=b"/?a=b",
            method="GET",
            state=ConnectionState({}),
        )
    )
    await stream.app_send(
        cast(
            HTTPResponseStartEvent,
            {"type": "http.response.start", "status": 200, "trailers": True},
        )
    )
    await stream.app_send(
        cast(HTTPResponseBodyEvent, {"type": "http.response.body", "body": b"Body"})
    )
    await stream.app_send({"type": "http.response.trailers", "headers": [(b"X", b"V")]})
    assert stream.send.call_args_list == [  # type: ignore
        call(Response(stream_id=1, headers=[], status_code=200)),
        call(Body(stream_id=1, data=b"Body")),
        call(Trailers(stream_id=1, headers=[(b"X", b"V")])),
        call(EndBody(stream_id=1)),
        call(StreamClosed(stream_id=1)),
    ]


@pytest.mark.asyncio
async def test_send_trailers_ignored(stream: HTTPStream) -> None:
    await stream.handle(
        Request(
            stream_id=1,
            http_version="2",
            headers=[],  # no TE: trailers header
            raw_path=b"/?a=b",
            method="GET",
            state=ConnectionState({}),
        )
    )
    await stream.app_send(
        cast(
            HTTPResponseStartEvent,
            {"type": "http.response.start", "status": 200, "trailers": True},
        )
    )
    await stream.app_send(
        cast(HTTPResponseBodyEvent, {"type": "http.response.body", "body": b"Body"})
    )
    await stream.app_send({"type": "http.response.trailers", "headers": [(b"X", b"V")]})
    assert stream.send.call_args_list == [  # type: ignore
        call(Response(stream_id=1, headers=[], status_code=200)),
        call(Body(stream_id=1, data=b"Body")),
        call(EndBody(stream_id=1)),
        call(StreamClosed(stream_id=1)),
    ]


@pytest.mark.asyncio
async def test_send_app_error(stream: HTTPStream) -> None:
    await stream.handle(
        Request(
            stream_id=1,
            http_version="2",
            headers=[],
            raw_path=b"/?a=b",
            method="GET",
            state=ConnectionState({}),
        )
    )
    await stream.app_send(None)
    stream.send.assert_called()  # type: ignore
    assert stream.send.call_args_list == [  # type: ignore
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
    stream.config._log.access.assert_called()  # type: ignore


@pytest.mark.parametrize(
    "state, message_type",
    [
        (ASGIHTTPState.REQUEST, "not_a_real_type"),
        (ASGIHTTPState.RESPONSE, "http.response.start"),
        (ASGIHTTPState.TRAILERS, "http.response.start"),
        (ASGIHTTPState.CLOSED, "http.response.start"),
        (ASGIHTTPState.CLOSED, "http.response.body"),
        (ASGIHTTPState.CLOSED, "http.response.trailers"),
    ],
)
@pytest.mark.asyncio
async def test_send_invalid_message_given_state(
    stream: HTTPStream, state: ASGIHTTPState, http_scope: HTTPScope, message_type: str
) -> None:
    stream.state = state
    stream.scope = http_scope
    with pytest.raises(UnexpectedMessageError):
        await stream.app_send({"type": message_type})  # type: ignore


@pytest.mark.parametrize(
    "status, headers, body",
    [
        ("201 NO CONTENT", [], b""),  # Status should be int
        (200, [("X-Foo", "foo")], b""),  # Headers should be bytes
        (200, [], "Body"),  # Body should be bytes
    ],
)
@pytest.mark.asyncio
async def test_send_invalid_message(
    stream: HTTPStream,
    http_scope: HTTPScope,
    status: Any,
    headers: Any,
    body: Any,
) -> None:
    stream.scope = http_scope
    stream.state = ASGIHTTPState.REQUEST
    with pytest.raises((TypeError, ValueError)):
        await stream.app_send(
            cast(
                HTTPResponseStartEvent,
                {"type": "http.response.start", "headers": headers, "status": status},
            )
        )
        await stream.app_send(
            cast(HTTPResponseBodyEvent, {"type": "http.response.body", "body": body})
        )


def test_stream_idle(stream: HTTPStream) -> None:
    assert stream.idle is False


@pytest.mark.asyncio
async def test_closure(stream: HTTPStream) -> None:
    await stream.handle(
        Request(
            stream_id=1,
            http_version="2",
            headers=[],
            raw_path=b"/?a=b",
            method="GET",
            state=ConnectionState({}),
        )
    )
    assert not stream.closed
    await stream.handle(StreamClosed(stream_id=1))
    assert stream.closed
    await stream.handle(StreamClosed(stream_id=1))
    assert stream.closed
    # It is important that the disconnect message has only been sent
    # once.
    assert stream.app_put.call_args_list == [call({"type": "http.disconnect"})]


@pytest.mark.asyncio
async def test_abnormal_close_logging() -> None:
    config = Config()
    config.accesslog = "-"
    config.statsd_host = "localhost:9125"
    # This exercises an issue where `HTTPStream` at one point called the statsd logger
    # with `response=None` when the statsd logger failed to handle it.
    config.set_statsd_logger_class(StatsdLogger)
    stream = HTTPStream(
        AsyncMock(), config, WorkerContext(None), AsyncMock(), False, None, None, AsyncMock(), 1
    )

    await stream.handle(
        Request(
            stream_id=1,
            http_version="2",
            headers=[],
            raw_path=b"/?a=b",
            method="GET",
            state=ConnectionState({}),
        )
    )
    await stream.handle(StreamClosed(stream_id=1))
