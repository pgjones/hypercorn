import asyncio
from unittest.mock import call, Mock

import pytest
from _pytest.monkeypatch import MonkeyPatch

import hypercorn.protocol.h2
from hypercorn.asyncio.tcp_server import EventWrapper
from hypercorn.config import Config
from hypercorn.events import Closed, RawData
from hypercorn.protocol.h2 import BUFFER_HIGH_WATER, BufferCompleteError, H2Protocol, StreamBuffer
from hypercorn.protocol.http_stream import HTTPStream

try:
    from unittest.mock import AsyncMock
except ImportError:
    # Python < 3.8
    from mock import AsyncMock


@pytest.mark.asyncio
async def test_stream_buffer_push_and_pop(event_loop: asyncio.AbstractEventLoop) -> None:
    stream_buffer = StreamBuffer(EventWrapper)

    async def _push_over_limit() -> None:
        await stream_buffer.push(b"a" * (BUFFER_HIGH_WATER + 1))
        return True

    task = event_loop.create_task(_push_over_limit())
    assert not task.done()  # Blocked as over high water
    await stream_buffer.pop(BUFFER_HIGH_WATER / 4)
    assert not task.done()  # Blocked as over low water
    await stream_buffer.pop(BUFFER_HIGH_WATER / 4)
    assert (await task) is True


@pytest.mark.asyncio
async def test_stream_buffer_drain(event_loop: asyncio.AbstractEventLoop) -> None:
    stream_buffer = StreamBuffer(EventWrapper)
    await stream_buffer.push(b"a" * 10)

    async def _drain() -> None:
        await stream_buffer.drain()
        return True

    task = event_loop.create_task(_drain())
    assert not task.done()  # Blocked
    await stream_buffer.pop(20)
    assert (await task) is True


@pytest.mark.asyncio
async def test_stream_buffer_closed(event_loop: asyncio.AbstractEventLoop) -> None:
    stream_buffer = StreamBuffer(EventWrapper)
    await stream_buffer.close()
    await stream_buffer._is_empty.wait()
    await stream_buffer._paused.wait()
    assert True
    with pytest.raises(BufferCompleteError):
        await stream_buffer.push(b"a")


@pytest.mark.asyncio
async def test_stream_buffer_complete(event_loop: asyncio.AbstractEventLoop) -> None:
    stream_buffer = StreamBuffer(EventWrapper)
    await stream_buffer.push(b"a" * 10)
    assert not stream_buffer.complete
    stream_buffer.set_complete()
    assert not stream_buffer.complete
    await stream_buffer.pop(20)
    assert stream_buffer.complete


@pytest.fixture(name="protocol")
async def _protocol(monkeypatch: MonkeyPatch) -> H2Protocol:
    MockHTTPStream = Mock()  # noqa: N806
    MockHTTPStream.return_value = AsyncMock(spec=HTTPStream)
    monkeypatch.setattr(hypercorn.protocol.h11, "HTTPStream", MockHTTPStream)
    return H2Protocol(AsyncMock(), Config(), AsyncMock(), False, None, None, AsyncMock())


@pytest.mark.asyncio
async def test_protocol_handle_protocol_error(protocol: H2Protocol) -> None:
    await protocol.handle(RawData(data=b"broken nonsense\r\n\r\n"))
    protocol.send.assert_called()
    assert protocol.send.call_args_list == [call(Closed())]
