from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, call, Mock

import pytest
from h2.connection import H2Connection
from h2.events import ConnectionTerminated, RemoteSettingsChanged, StreamReset
from h2.settings import SettingCodes

from hypercorn.asyncio.worker_context import EventWrapper, WorkerContext
from hypercorn.config import Config
from hypercorn.events import Closed, RawData
from hypercorn.protocol.events import StreamClosed
from hypercorn.protocol.h2 import BUFFER_HIGH_WATER, BufferCompleteError, H2Protocol, StreamBuffer
from hypercorn.typing import ConnectionState


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
        Mock(),
        Config(),
        WorkerContext(None),
        AsyncMock(),
        ConnectionState({}),
        False,
        None,
        None,
        AsyncMock(),
    )
    await protocol.handle(RawData(data=b"broken nonsense\r\n\r\n"))
    protocol.send.assert_awaited()  # type: ignore
    assert protocol.send.call_args_list == [call(Closed())]  # type: ignore


@pytest.mark.asyncio
async def test_protocol_keep_alive_max_requests() -> None:
    protocol = H2Protocol(
        Mock(),
        Config(),
        WorkerContext(None),
        AsyncMock(),
        ConnectionState({}),
        False,
        None,
        None,
        AsyncMock(),
    )
    protocol.config.keep_alive_max_requests = 2
    client = H2Connection()
    client.initiate_connection()
    headers = [
        (":method", "GET"),
        (":path", "/reqinfo"),
        (":authority", "hypercorn"),
        (":scheme", "https"),
    ]
    # Three requests in flight when the limit is reached, the third one sent before
    # the client could learn that the connection is winding down.
    for stream_id in (1, 3, 5):
        client.send_headers(stream_id, headers, end_stream=True)
    await protocol.handle(RawData(data=client.data_to_send()))
    protocol.send.assert_awaited()  # type: ignore
    events = _received_events(client, protocol)
    settings = [event for event in events if isinstance(event, RemoteSettingsChanged)]
    assert settings[-1].changed_settings[SettingCodes.MAX_CONCURRENT_STREAMS].new_value == 0
    # Nothing is closed while requests are in flight...
    assert not any(isinstance(event, (ConnectionTerminated, StreamReset)) for event in events)
    assert set(protocol.streams) == {1, 3, 5}

    for stream_id in (1, 3):
        await protocol.stream_send(StreamClosed(stream_id=stream_id))
    assert not any(
        isinstance(event, ConnectionTerminated) for event in _received_events(client, protocol)
    )
    # ...the GOAWAY follows once the last one has completed.
    await protocol.stream_send(StreamClosed(stream_id=5))
    assert protocol.send.call_args_list[-1] == call(Closed())  # type: ignore
    assert isinstance(_received_events(client, protocol)[-1], ConnectionTerminated)


def _received_events(client: H2Connection, protocol: H2Protocol) -> list:
    events = []
    for call_ in protocol.send.call_args_list:  # type: ignore
        if isinstance(call_.args[0], RawData):
            events.extend(client.receive_data(call_.args[0].data))
    protocol.send.reset_mock()  # type: ignore
    return events
