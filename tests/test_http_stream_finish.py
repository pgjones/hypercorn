from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import pytest

from hypercorn.asyncio.worker_context import WorkerContext
from hypercorn.config import Config
from hypercorn.protocol.events import Body, Request, StreamClosed
from hypercorn.protocol.http_stream import HTTPStream
from hypercorn.typing import ConnectionState, HTTPResponseBodyEvent, HTTPResponseStartEvent


def make_stream() -> HTTPStream:
    task_group = AsyncMock()
    task_group.spawn_app.return_value = AsyncMock()
    stream = HTTPStream(
        AsyncMock(), Config(), WorkerContext(None), task_group, False, None, None, AsyncMock(), 1
    )
    stream.config._log = AsyncMock()
    return stream


async def start_request(stream: HTTPStream) -> None:
    await stream.handle(
        Request(
            stream_id=1,
            http_version="2",
            headers=[],
            raw_path=b"/",
            method="GET",
            state=ConnectionState({}),
        )
    )


@pytest.mark.asyncio
async def test_close_notifies_finished_queue() -> None:
    stream = make_stream()
    await start_request(stream)
    await stream.handle(StreamClosed(stream_id=1))
    app_put = cast(AsyncMock, stream.app_put)
    app_put.disconnect.assert_awaited_once()
    app_put.assert_not_awaited()


@pytest.mark.asyncio
async def test_response_finished_drops_late_body() -> None:
    stream = make_stream()
    await start_request(stream)
    start: HTTPResponseStartEvent = {"type": "http.response.start", "status": 200, "headers": []}
    await stream.app_send(start)
    body: HTTPResponseBodyEvent = {"type": "http.response.body", "body": b"ok", "more_body": False}
    await stream.app_send(body)
    app_put = cast(AsyncMock, stream.app_put)
    app_put.finish.assert_awaited_once()
    await stream.handle(Body(stream_id=1, data=b"late"))
    app_put.assert_not_awaited()
