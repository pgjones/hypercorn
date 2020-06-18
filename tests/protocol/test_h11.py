import asyncio
from typing import Any
from unittest.mock import call, Mock

import h11
import pytest
from _pytest.monkeypatch import MonkeyPatch

import hypercorn.protocol.h11
from hypercorn.asyncio.tcp_server import EventWrapper
from hypercorn.config import Config
from hypercorn.events import Closed, RawData, Updated
from hypercorn.protocol.events import Body, Data, EndBody, EndData, Request, Response, StreamClosed
from hypercorn.protocol.h11 import H2CProtocolRequired, H2ProtocolAssumed, H11Protocol
from hypercorn.protocol.http_stream import HTTPStream
from hypercorn.typing import Event as IOEvent

try:
    from unittest.mock import AsyncMock
except ImportError:
    # Python < 3.8
    from mock import AsyncMock


BASIC_HEADERS = [("Host", "hypercorn"), ("Connection", "close")]


@pytest.fixture(name="protocol")
async def _protocol(monkeypatch: MonkeyPatch) -> H11Protocol:
    MockHTTPStream = Mock()  # noqa: N806
    MockHTTPStream.return_value = AsyncMock(spec=HTTPStream)
    monkeypatch.setattr(hypercorn.protocol.h11, "HTTPStream", MockHTTPStream)
    context = Mock()
    context.event_class.return_value = AsyncMock(spec=IOEvent)
    return H11Protocol(AsyncMock(), Config(), context, False, None, None, AsyncMock())


@pytest.mark.asyncio
async def test_protocol_send_response(protocol: H11Protocol) -> None:
    await protocol.stream_send(Response(stream_id=1, status_code=201, headers=[]))
    protocol.send.assert_called()
    assert protocol.send.call_args_list == [
        call(
            RawData(
                data=(
                    b"HTTP/1.1 201 \r\ndate: Thu, 01 Jan 1970 01:23:20 GMT\r\n"
                    b"server: hypercorn-h11\r\nconnection: close\r\n\r\n"
                )
            )
        )
    ]


@pytest.mark.asyncio
async def test_protocol_send_data(protocol: H11Protocol) -> None:
    await protocol.stream_send(Data(stream_id=1, data=b"hello"))
    protocol.send.assert_called()
    assert protocol.send.call_args_list == [call(RawData(data=b"hello"))]


@pytest.mark.asyncio
async def test_protocol_send_body(protocol: H11Protocol) -> None:
    await protocol.handle(
        RawData(data=b"GET / HTTP/1.1\r\nHost: hypercorn\r\nConnection: close\r\n\r\n")
    )
    await protocol.stream_send(
        Response(stream_id=1, status_code=200, headers=[(b"content-length", b"5")])
    )
    await protocol.stream_send(Body(stream_id=1, data=b"hello"))
    protocol.send.assert_called()
    assert protocol.send.call_args_list == [
        call(
            RawData(
                data=b"HTTP/1.1 200 \r\ncontent-length: 5\r\ndate: Thu, 01 Jan 1970 01:23:20 GMT\r\nserver: hypercorn-h11\r\nconnection: close\r\n\r\n"  # noqa: E501
            )
        ),
        call(RawData(data=b"hello")),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("keep_alive, expected", [(True, Updated()), (False, Closed())])
async def test_protocol_send_stream_closed(
    keep_alive: bool, expected: Any, protocol: H11Protocol
) -> None:
    data = b"GET / HTTP/1.1\r\nHost: hypercorn\r\n"
    if keep_alive:
        data += b"\r\n"
    else:
        data += b"Connection: close\r\n\r\n"
    await protocol.handle(RawData(data=data))
    await protocol.stream_send(Response(stream_id=1, status_code=200, headers=[]))
    await protocol.stream_send(EndBody(stream_id=1))
    await protocol.stream_send(StreamClosed(stream_id=1))
    protocol.send.assert_called()
    assert protocol.send.call_args_list[2] == call(expected)


@pytest.mark.asyncio
async def test_protocol_instant_recycle(protocol: H11Protocol) -> None:
    data = b"GET / HTTP/1.1\r\nHost: hypercorn\r\n\r\n"
    # This test requires a real event as the handling should pause on
    # the instant receipt
    protocol.can_read = EventWrapper()
    await protocol.handle(RawData(data=data))
    assert protocol.stream is not None
    await protocol.stream_send(Response(stream_id=1, status_code=200, headers=[]))
    await protocol.stream_send(EndBody(stream_id=1))
    task = asyncio.ensure_future(protocol.handle(RawData(data=data)))
    await asyncio.sleep(0)  # Switch to task
    await protocol.stream_send(StreamClosed(stream_id=1))
    # Should have recycled, i.e. a stream should exist
    assert protocol.stream is not None
    await asyncio.sleep(0)  # Switch to task
    assert task.done()


@pytest.mark.asyncio
async def test_protocol_send_end_data(protocol: H11Protocol) -> None:
    protocol.stream = AsyncMock()
    await protocol.stream_send(EndData(stream_id=1))
    assert protocol.stream is not None


@pytest.mark.asyncio
async def test_protocol_handle_closed(protocol: H11Protocol) -> None:
    await protocol.handle(
        RawData(data=b"GET / HTTP/1.1\r\nHost: hypercorn\r\nConnection: close\r\n\r\n")
    )
    stream = protocol.stream
    await protocol.handle(Closed())
    stream.handle.assert_called()
    assert stream.handle.call_args_list == [
        call(
            Request(
                stream_id=1,
                headers=[(b"host", b"hypercorn"), (b"connection", b"close")],
                http_version="1.1",
                method="GET",
                raw_path=b"/",
            )
        ),
        call(EndBody(stream_id=1)),
        call(StreamClosed(stream_id=1)),
    ]


@pytest.mark.asyncio
async def test_protocol_handle_request(protocol: H11Protocol) -> None:
    client = h11.Connection(h11.CLIENT)
    await protocol.handle(
        RawData(data=client.send(h11.Request(method="GET", target="/?a=b", headers=BASIC_HEADERS)))
    )
    protocol.stream.handle.assert_called()
    assert protocol.stream.handle.call_args_list == [
        call(
            Request(
                stream_id=1,
                headers=[(b"host", b"hypercorn"), (b"connection", b"close")],
                http_version="1.1",
                method="GET",
                raw_path=b"/?a=b",
            )
        ),
        call(EndBody(stream_id=1)),
    ]


@pytest.mark.asyncio
async def test_protocol_handle_protocol_error(protocol: H11Protocol) -> None:
    await protocol.handle(RawData(data=b"broken nonsense\r\n\r\n"))
    protocol.send.assert_called()
    assert protocol.send.call_args_list == [
        call(
            RawData(
                data=b"HTTP/1.1 400 \r\ncontent-length: 0\r\nconnection: close\r\n"
                b"date: Thu, 01 Jan 1970 01:23:20 GMT\r\nserver: hypercorn-h11\r\n\r\n"
            )
        ),
        call(RawData(data=b"")),
        call(Closed()),
    ]


@pytest.mark.asyncio
async def test_protocol_handle_send_client_error(protocol: H11Protocol) -> None:
    client = h11.Connection(h11.CLIENT)
    await protocol.handle(
        RawData(data=client.send(h11.Request(method="GET", target="/?a=b", headers=BASIC_HEADERS)))
    )
    await protocol.handle(RawData(data=b"some body"))
    # This next line should not cause an error
    await protocol.stream_send(Response(stream_id=1, status_code=200, headers=[]))


@pytest.mark.asyncio
async def test_protocol_handle_pipelining(protocol: H11Protocol) -> None:
    protocol.can_read.wait.side_effect = Exception()
    with pytest.raises(Exception):
        await protocol.handle(
            RawData(
                data=b"GET / HTTP/1.1\r\nHost: hypercorn\r\nConnection: keep-alive\r\n\r\n"
                b"GET / HTTP/1.1\r\nHost: hypercorn\r\nConnection: close\r\n\r\n"
            )
        )
    protocol.can_read.clear.assert_called()
    protocol.can_read.wait.assert_called()


@pytest.mark.asyncio
async def test_protocol_handle_continue_request(protocol: H11Protocol) -> None:
    client = h11.Connection(h11.CLIENT)
    await protocol.handle(
        RawData(
            data=client.send(
                h11.Request(
                    method="POST",
                    target="/?a=b",
                    headers=BASIC_HEADERS
                    + [("transfer-encoding", "chunked"), ("expect", "100-continue")],
                )
            )
        )
    )
    assert protocol.send.call_args[0][0] == RawData(
        data=b"HTTP/1.1 100 \r\ndate: Thu, 01 Jan 1970 01:23:20 GMT\r\nserver: hypercorn-h11\r\n\r\n"  # noqa: E501
    )


@pytest.mark.asyncio
async def test_protocol_handle_max_incomplete(monkeypatch: MonkeyPatch) -> None:
    config = Config()
    config.h11_max_incomplete_size = 5
    MockHTTPStream = AsyncMock()  # noqa: N806
    MockHTTPStream.return_value = AsyncMock(spec=HTTPStream)
    monkeypatch.setattr(hypercorn.protocol.h11, "HTTPStream", MockHTTPStream)
    context = Mock()
    context.event_class.return_value = AsyncMock(spec=IOEvent)
    protocol = H11Protocol(AsyncMock(), config, context, False, None, None, AsyncMock())
    await protocol.handle(RawData(data=b"GET / HTTP/1.1\r\nHost: hypercorn\r\n"))
    protocol.send.assert_called()
    assert protocol.send.call_args_list == [
        call(
            RawData(
                data=b"HTTP/1.1 400 \r\ncontent-length: 0\r\nconnection: close\r\n"
                b"date: Thu, 01 Jan 1970 01:23:20 GMT\r\nserver: hypercorn-h11\r\n\r\n"
            )
        ),
        call(RawData(data=b"")),
        call(Closed()),
    ]


@pytest.mark.asyncio
async def test_protocol_handle_h2c_upgrade(protocol: H11Protocol) -> None:
    with pytest.raises(H2CProtocolRequired) as exc_info:
        await protocol.handle(
            RawData(
                data=(
                    b"GET / HTTP/1.1\r\nHost: hypercorn\r\n"
                    b"upgrade: h2c\r\nhttp2-settings: abcd\r\n\r\nbbb"
                )
            )
        )
    assert protocol.send.call_args_list == [
        call(
            RawData(
                b"HTTP/1.1 101 \r\n"
                b"date: Thu, 01 Jan 1970 01:23:20 GMT\r\n"
                b"server: hypercorn-h11\r\n"
                b"connection: upgrade\r\n"
                b"upgrade: h2c\r\n"
                b"\r\n"
            )
        )
    ]
    assert exc_info.value.data == b"bbb"
    assert exc_info.value.headers == [
        (b":method", b"GET"),
        (b":path", b"/"),
        (b":authority", b"hypercorn"),
        (b"host", b"hypercorn"),
        (b"upgrade", b"h2c"),
        (b"http2-settings", b"abcd"),
    ]
    assert exc_info.value.settings == "abcd"


@pytest.mark.asyncio
async def test_protocol_handle_h2_prior(protocol: H11Protocol) -> None:
    with pytest.raises(H2ProtocolAssumed) as exc_info:
        await protocol.handle(RawData(data=b"PRI * HTTP/2.0\r\n\r\nbbb"))

    assert exc_info.value.data == b"PRI * HTTP/2.0\r\n\r\nbbb"


@pytest.mark.asyncio
async def test_protocol_handle_data_post_response(protocol: H11Protocol) -> None:
    await protocol.handle(
        RawData(data=b"POST / HTTP/1.1\r\nHost: hypercorn\r\nContent-Length: 4\r\n\r\n")
    )
    await protocol.stream_send(Response(stream_id=1, status_code=201, headers=[]))
    await protocol.stream_send(EndBody(stream_id=1))
    await protocol.handle(RawData(data=b"abcd"))


@pytest.mark.asyncio
async def test_protocol_handle_data_post_end(protocol: H11Protocol) -> None:
    await protocol.handle(
        RawData(data=b"POST / HTTP/1.1\r\nHost: hypercorn\r\nContent-Length: 10\r\n\r\n")
    )
    await protocol.stream_send(Response(stream_id=1, status_code=201, headers=[]))
    await protocol.stream_send(EndBody(stream_id=1))
    # Key is that this doesn't error
    await protocol.handle(RawData(data=b"abcdefghij"))


@pytest.mark.asyncio
async def test_protocol_handle_data_post_close(protocol: H11Protocol) -> None:
    await protocol.handle(
        RawData(data=b"POST / HTTP/1.1\r\nHost: hypercorn\r\nContent-Length: 10\r\n\r\n")
    )
    await protocol.stream_send(StreamClosed(stream_id=1))
    assert protocol.stream is None
    # Key is that this doesn't error
    await protocol.handle(RawData(data=b"abcdefghij"))
