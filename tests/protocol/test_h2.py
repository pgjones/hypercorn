from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, call, Mock

import pytest
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import ConnectionTerminated, ResponseReceived
from hpack import Encoder

from hypercorn.asyncio.worker_context import EventWrapper, WorkerContext
from hypercorn.config import Config
from hypercorn.events import Closed, RawData
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
async def test_protocol_handle_malformed_request_sends_400() -> None:
    # A request header value with leading/trailing whitespace is malformed
    # per RFC 9113 8.2.1, and h2 raises a ProtocolError for it. Hypercorn
    # should respond with a 400 (Bad Request) for the offending stream rather
    # than closing the connection with no response. See issue #348.
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
    client = H2Connection(config=H2Configuration(client_side=True))
    client.initiate_connection()
    await protocol.handle(RawData(data=client.data_to_send()))
    for send_call in protocol.send.call_args_list:  # type: ignore
        if isinstance(send_call.args[0], RawData):
            client.receive_data(send_call.args[0].data)
    protocol.send.reset_mock()  # type: ignore

    # Open the stream client-side with valid headers (discarded) so the client
    # accepts the server's response for it; h2's client normalises outbound
    # headers, so the malformed HEADERS frame (a header value surrounded by
    # whitespace) that is actually sent to the server is crafted by hand.
    stream_id = client.get_next_available_stream_id()
    client.send_headers(
        stream_id,
        [
            (":method", "GET"),
            (":path", "/"),
            (":scheme", "https"),
            (":authority", "hypercorn"),
        ],
        end_stream=False,
    )
    client.data_to_send()  # discard the valid frame; the server never sees it
    header_block = Encoder().encode(
        [
            (b":method", b"GET"),
            (b":path", b"/"),
            (b":scheme", b"https"),
            (b":authority", b"hypercorn"),
            (b"x-bad", b" whitespace "),
        ]
    )
    frame = (
        len(header_block).to_bytes(3, "big")
        + b"\x01"  # HEADERS frame type
        + b"\x05"  # END_STREAM | END_HEADERS flags
        + stream_id.to_bytes(4, "big")
        + header_block
    )
    await protocol.handle(RawData(data=frame))

    statuses: list[bytes] = []
    for send_call in protocol.send.call_args_list:  # type: ignore
        if isinstance(send_call.args[0], RawData):
            for event in client.receive_data(send_call.args[0].data):
                if isinstance(event, ResponseReceived):
                    statuses.extend(value for name, value in event.headers if name == b":status")
    assert statuses == [b"400"]
    assert call(Closed()) in protocol.send.call_args_list  # type: ignore


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
