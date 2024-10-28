from __future__ import annotations

from typing import cast
from unittest.mock import Mock, PropertyMock

import h2
import h11
import pytest
import trio
import wsproto

from hypercorn.app_wrappers import ASGIWrapper
from hypercorn.config import Config
from hypercorn.trio.tcp_server import TCPServer
from hypercorn.trio.worker_context import WorkerContext
from ..helpers import MockSocket, SANITY_BODY, sanity_framework
from hypercorn.config import Config
from hypercorn.trio.tcp_server import TCPServer
from hypercorn.trio.worker_context import WorkerContext
from hypercorn.app_wrappers import ASGIWrapper
from ..helpers import MockSocket, SANITY_BODY, sanity_framework


try:
    from unittest.mock import AsyncMock
except ImportError:
    # Python < 3.8
    from mock import AsyncMock  # type: ignore


@pytest.mark.trio
async def test_http1_request(nursery: trio._core._run.Nursery) -> None:
    client_stream, server_stream = trio.testing.memory_stream_pair()
    server_stream = cast("trio.SSLStream[trio.SocketStream]", server_stream)
    server_stream.socket = MockSocket()
    server = TCPServer(
        ASGIWrapper(sanity_framework), Config(), WorkerContext(None), {}, server_stream
    )
    nursery.start_soon(server.run)
    client = h11.Connection(h11.CLIENT)
    await client_stream.send_all(
        client.send(  # type: ignore[arg-type]
            h11.Request(
                method="POST",
                target="/",
                headers=[
                    (b"host", b"hypercorn"),
                    (b"connection", b"close"),
                    (b"content-length", b"%d" % len(SANITY_BODY)),
                ],
            )
        )
    )
    await client_stream.send_all(client.send(h11.Data(data=SANITY_BODY)))  # type: ignore[arg-type]
    await client_stream.send_all(client.send(h11.EndOfMessage()))  # type: ignore[arg-type]
    events = []
    while True:
        event = client.next_event()
        if event == h11.NEED_DATA:
            # bytes cast is key otherwise b"" is lost
            data = bytes(await client_stream.receive_some(1024))
            client.receive_data(data)
        elif isinstance(event, h11.ConnectionClosed):
            break
        else:
            events.append(event)

    assert events == [
        h11.Response(
            status_code=200,
            headers=[
                (b"content-length", b"15"),
                (b"date", b"Thu, 01 Jan 1970 01:23:20 GMT"),
                (b"server", b"hypercorn-h11"),
                (b"connection", b"close"),
            ],
            http_version=b"1.1",
            reason=b"",
        ),
        h11.Data(data=b"Hello & Goodbye"),
        h11.EndOfMessage(headers=[]),
    ]


@pytest.mark.trio
async def test_http1_websocket(nursery: trio._core._run.Nursery) -> None:
    client_stream, server_stream = trio.testing.memory_stream_pair()
    server_stream = cast("trio.SSLStream[trio.SocketStream]", server_stream)
    server_stream.socket = MockSocket()
    server = TCPServer(
        ASGIWrapper(sanity_framework), Config(), WorkerContext(None), {}, server_stream
    )
    nursery.start_soon(server.run)
    client = wsproto.WSConnection(wsproto.ConnectionType.CLIENT)
    await client_stream.send_all(client.send(wsproto.events.Request(host="hypercorn", target="/")))
    client.receive_data(await client_stream.receive_some(1024))
    assert list(client.events()) == [
        wsproto.events.AcceptConnection(
            extra_headers=[
                (b"date", b"Thu, 01 Jan 1970 01:23:20 GMT"),
                (b"server", b"hypercorn-h11"),
            ]
        )
    ]
    await client_stream.send_all(client.send(wsproto.events.BytesMessage(data=SANITY_BODY)))
    client.receive_data(await client_stream.receive_some(1024))
    assert list(client.events()) == [wsproto.events.TextMessage(data="Hello & Goodbye")]
    await client_stream.send_all(client.send(wsproto.events.CloseConnection(code=1000)))
    client.receive_data(await client_stream.receive_some(1024))
    assert list(client.events()) == [wsproto.events.CloseConnection(code=1000, reason="")]


@pytest.mark.trio
async def test_http2_request(nursery: trio._core._run.Nursery) -> None:
    client_stream, server_stream = trio.testing.memory_stream_pair()
    server_stream = cast("trio.SSLStream[trio.SocketStream]", server_stream)
    server_stream.transport_stream = Mock(return_value=PropertyMock(return_value=MockSocket()))
    server_stream.do_handshake = AsyncMock()  # type: ignore[method-assign]
    server_stream.selected_alpn_protocol = Mock(return_value="h2")
    server = TCPServer(
        ASGIWrapper(sanity_framework), Config(), WorkerContext(None), {}, server_stream
    )
    nursery.start_soon(server.run)
    client = h2.connection.H2Connection()
    client.initiate_connection()
    await client_stream.send_all(client.data_to_send())
    stream_id = client.get_next_available_stream_id()
    client.send_headers(
        stream_id,
        [
            (":method", "GET"),
            (":path", "/"),
            (":authority", "hypercorn"),
            (":scheme", "https"),
            ("content-length", "%d" % len(SANITY_BODY)),
        ],
    )
    client.send_data(stream_id, SANITY_BODY)
    client.end_stream(stream_id)
    await client_stream.send_all(client.data_to_send())
    events = []
    open_ = True
    while open_:
        # bytes cast is key otherwise b"" is lost
        data = bytes(await client_stream.receive_some(1024))
        if data == b"":
            open_ = False

        h2_events = client.receive_data(data)
        for event in h2_events:
            if isinstance(event, h2.events.DataReceived):
                client.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
            elif isinstance(
                event,
                (h2.events.ConnectionTerminated, h2.events.StreamEnded, h2.events.StreamReset),
            ):
                open_ = False
                break
            else:
                events.append(event)
        await client_stream.send_all(client.data_to_send())
    assert isinstance(events[2], h2.events.ResponseReceived)
    assert events[2].headers == [
        (b":status", b"200"),
        (b"content-length", b"15"),
        (b"date", b"Thu, 01 Jan 1970 01:23:20 GMT"),
        (b"server", b"hypercorn-h2"),
    ]


@pytest.mark.trio
async def test_http2_websocket(nursery: trio._core._run.Nursery) -> None:
    client_stream, server_stream = trio.testing.memory_stream_pair()
    server_stream = cast("trio.SSLStream[trio.SocketStream]", server_stream)
    server_stream.transport_stream = Mock(return_value=PropertyMock(return_value=MockSocket()))
    server_stream.do_handshake = AsyncMock()  # type: ignore[method-assign]
    server_stream.selected_alpn_protocol = Mock(return_value="h2")
    server = TCPServer(
        ASGIWrapper(sanity_framework), Config(), WorkerContext(None), {}, server_stream
    )
    nursery.start_soon(server.run)
    h2_client = h2.connection.H2Connection()
    h2_client.initiate_connection()
    await client_stream.send_all(h2_client.data_to_send())
    stream_id = h2_client.get_next_available_stream_id()
    h2_client.send_headers(
        stream_id,
        [
            (":method", "CONNECT"),
            (":path", "/"),
            (":authority", "hypercorn"),
            (":scheme", "https"),
            ("sec-websocket-version", "13"),
        ],
    )
    await client_stream.send_all(h2_client.data_to_send())
    events = h2_client.receive_data(await client_stream.receive_some(1024))
    await client_stream.send_all(h2_client.data_to_send())
    events = h2_client.receive_data(await client_stream.receive_some(1024))
    if not isinstance(events[-1], h2.events.ResponseReceived):
        events = h2_client.receive_data(await client_stream.receive_some(1024))
    assert events[-1].headers == [
        (b":status", b"200"),
        (b"date", b"Thu, 01 Jan 1970 01:23:20 GMT"),
        (b"server", b"hypercorn-h2"),
    ]
    client = wsproto.connection.Connection(wsproto.ConnectionType.CLIENT)
    h2_client.send_data(stream_id, client.send(wsproto.events.BytesMessage(data=SANITY_BODY)))
    await client_stream.send_all(h2_client.data_to_send())
    events = h2_client.receive_data(await client_stream.receive_some(1024))
    client.receive_data(events[0].data)
    assert list(client.events()) == [wsproto.events.TextMessage(data="Hello & Goodbye")]
    h2_client.send_data(stream_id, client.send(wsproto.events.CloseConnection(code=1000)))
    await client_stream.send_all(h2_client.data_to_send())
    events = h2_client.receive_data(await client_stream.receive_some(1024))
    client.receive_data(events[0].data)
    assert list(client.events()) == [wsproto.events.CloseConnection(code=1000, reason="")]
    await client_stream.send_all(b"")

class SlowStream:
    """A stream that blocks on sends to simulate a slow client"""
    def __init__(self, stream):
        self.stream = stream
        self.block_sends = False
        # Copy over required SSL attributes
        self.transport_stream = Mock(return_value=PropertyMock(return_value=MockSocket()))
        self.do_handshake = AsyncMock()
        self.socket = MockSocket()
    
    async def send_all(self, data):
        if self.block_sends:
            await trio.sleep_forever()
        await self.stream.send_all(data)
    
    def __getattr__(self, attr):
        return getattr(self.stream, attr)

@pytest.mark.trio
async def test_write_timeout(nursery: trio._core._run.Nursery) -> None:
    client_stream, server_stream = trio.testing.memory_stream_pair()
    wrapped_stream = SlowStream(server_stream)
    server_stream = cast("trio.SSLStream[trio.SocketStream]", wrapped_stream)

    config = Config()
    config.write_timeout = 0.5
    
    # Create an app that sends a large response in two parts
    large_body = b"x" * (2**16)  # Large enough to ensure chunking
    async def chunked_response(scope, receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-length", str(len(large_body) * 2).encode())],
        })
        await send({
            "type": "http.response.body",
            "body": large_body,
            "more_body": True,
        })
        
        # Block on the second chunk
        wrapped_stream.block_sends = True
        await send({
            "type": "http.response.body",
            "body": large_body,
            "more_body": False,
        })

    server = TCPServer(
        ASGIWrapper(chunked_response),
        config,
        WorkerContext(None),
        {},
        server_stream
    )

    nursery.start_soon(server.run)

    # Send request
    client = h11.Connection(h11.CLIENT)
    await client_stream.send_all(
        client.send(
            h11.Request(
                method="GET",
                target="/",
                headers=[
                    (b"host", b"hypercorn"),
                    (b"connection", b"close"),
                ],
            )
        )
    )
    await client_stream.send_all(client.send(h11.EndOfMessage()))

    # Get first chunk and expect connection close during second chunk
    try:
        while True:
            event = client.next_event()
            if event is h11.NEED_DATA:
                data = await client_stream.receive_some(2**16)
                if not data:  # Connection closed
                    break
                client.receive_data(data)
            elif event is h11.PAUSED:
                client.start_next_cycle()
            elif isinstance(event, h11.EndOfMessage):
                assert False, "Should not get EndOfMessage as connection should close mid-response"
    except h11.RemoteProtocolError as e:
        # We expect this error since the connection closes mid-response
        assert "peer closed connection without sending complete message body" in str(e)

    # Verify we can't read any more data
    data = await client_stream.receive_some(2**16)
    assert data == b""  # Connection should be fully closed
    
@pytest.mark.trio
async def test_chunked_write_success(nursery: trio._core._run.Nursery) -> None:
    """Test that large responses are properly chunked and sent"""
    client_stream, server_stream = trio.testing.memory_stream_pair()
    server_stream = cast("trio.SSLStream[trio.SocketStream]", server_stream)
    server_stream.socket = MockSocket()

    # Create an app that sends a large response
    large_body = b"x" * (2**16 * 2)  # Two MAX_SEND chunks
    
    async def large_response(scope, receive, send):
        try:
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", str(len(large_body)).encode())],
            })
            
            await send({
                "type": "http.response.body",
                "body": large_body,
                "more_body": False,
            })
        except Exception as e:
            raise

    server = TCPServer(
        ASGIWrapper(large_response),
        Config(),
        WorkerContext(None),
        {},
        server_stream
    )

    # Start server in nursery
    server_task = nursery.start_soon(server.run)

    # Send request
    client = h11.Connection(h11.CLIENT)
    request_bytes = client.send(
        h11.Request(
            method="GET",
            target="/",
            headers=[
                (b"host", b"hypercorn"),
                (b"connection", b"close"),
            ],
        )
    )
    await client_stream.send_all(request_bytes)
    
    end_bytes = client.send(h11.EndOfMessage())
    await client_stream.send_all(end_bytes)

    # Read the complete response with timeout
    received_data = b""
    with trio.move_on_after(10) as cancel_scope:  # 10 second timeout
        while True:
            chunk = await client_stream.receive_some(2**16)
            if not chunk:
                break
            received_data += chunk

    if cancel_scope.cancelled_caught:
        raise TimeoutError("Timed out waiting for response")

    # Verify we got all the data
    try:
        assert large_body in received_data, \
            f"Expected {len(large_body)} bytes, got {len(received_data)} bytes"
    finally:
        # Clean up
        await client_stream.aclose()
        await server_stream.aclose()