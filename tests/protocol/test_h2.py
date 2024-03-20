from __future__ import annotations

import asyncio
from unittest.mock import call, Mock

import pytest
from h2.connection import H2Connection
from h2.events import ConnectionTerminated

from hypercorn.asyncio.worker_context import EventWrapper, WorkerContext
from hypercorn.config import Config
from hypercorn.events import Closed, RawData
from hypercorn.protocol.h2 import BUFFER_HIGH_WATER, BufferCompleteError, H2Protocol, StreamBuffer

try:
    from unittest.mock import AsyncMock
except ImportError:
    # Python < 3.8
    from mock import AsyncMock  # type: ignore


@pytest.mark.asyncio
async def test_stream_buffer_push_and_pop() -> None:
    event_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

    stream_buffer = StreamBuffer(EventWrapper)

    async def _push_over_limit() -> bool:
        await stream_buffer.push(b"a" * (BUFFER_HIGH_WATER + 1))
        return True

    task = event_loop.create_task(_push_over_limit())
    assert not task.done()  # Blocked as over high water
    await stream_buffer.pop(BUFFER_HIGH_WATER // 4)
    assert not task.done()  # Blocked as over low water
    await stream_buffer.pop(BUFFER_HIGH_WATER // 4)
    assert (await task) is True


@pytest.mark.asyncio
async def test_stream_buffer_drain() -> None:
    event_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

    stream_buffer = StreamBuffer(EventWrapper)
    await stream_buffer.push(b"a" * 10)

    async def _drain() -> bool:
        await stream_buffer.drain()
        return True

    task = event_loop.create_task(_drain())
    assert not task.done()  # Blocked
    await stream_buffer.pop(20)
    assert (await task) is True


@pytest.mark.asyncio
async def test_stream_buffer_closed() -> None:
    stream_buffer = StreamBuffer(EventWrapper)
    await stream_buffer.close()
    await stream_buffer._is_empty.wait()
    await stream_buffer._paused.wait()
    assert True
    with pytest.raises(BufferCompleteError):
        await stream_buffer.push(b"a")


@pytest.mark.asyncio
async def test_stream_buffer_complete() -> None:
    stream_buffer = StreamBuffer(EventWrapper)
    await stream_buffer.push(b"a" * 10)
    assert not stream_buffer.complete
    stream_buffer.set_complete()
    assert not stream_buffer.complete
    await stream_buffer.pop(20)
    assert stream_buffer.complete


@pytest.mark.asyncio
async def test_protocol_handle_protocol_error() -> None:
    protocol = H2Protocol(
        Mock(), Config(), WorkerContext(None), AsyncMock(), False, None, None, AsyncMock()
    )
    await protocol.handle(RawData(data=b"broken nonsense\r\n\r\n"))
    protocol.send.assert_awaited()  # type: ignore
    assert protocol.send.call_args_list == [call(Closed())]  # type: ignore


@pytest.mark.asyncio
async def test_protocol_keep_alive_max_requests() -> None:
    protocol = H2Protocol(
        Mock(), Config(), WorkerContext(None), AsyncMock(), False, None, None, AsyncMock()
    )
    protocol.config.keep_alive_max_requests = 0
    client = H2Connection()
    client.initiate_connection()
    headers = [
        (":method", "GET"),
        (":path", "/reqinfo"),
        (":authority", "hypercorn"),
        (":scheme", "https"),
    ]
    client.send_headers(1, headers, end_stream=True)
    await protocol.handle(RawData(data=client.data_to_send()))
    protocol.send.assert_awaited()  # type: ignore
    events = client.receive_data(protocol.send.call_args_list[1].args[0].data)  # type: ignore
    assert isinstance(events[-1], ConnectionTerminated)
