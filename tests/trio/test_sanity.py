from unittest.mock import Mock, PropertyMock

import h2
import h11
import pytest
import trio
import wsproto

from asynctest.mock import CoroutineMock
from hypercorn.config import Config
from hypercorn.trio.tcp_server import TCPServer
from ..helpers import MockSocket, SANITY_BODY, sanity_framework


@pytest.mark.trio
async def test_http1_request(nursery: trio._core._run.Nursery) -> None:
    client_stream, server_stream = trio.testing.memory_stream_pair()
    server_stream.socket = MockSocket()
    server = TCPServer(sanity_framework, Config(), server_stream)
    nursery.start_soon(server.run)
    client = h11.Connection(h11.CLIENT)
    await client_stream.send_all(
        client.send(
            h11.Request(
                method="GET",
                target="/",
                headers=[
                    (b"host", b"hypercorn"),
                    (b"connection", b"close"),
                    (b"content-length", b"%d" % len(SANITY_BODY)),
                ],
            )
        )
    )
    await client_stream.send_all(client.send(h11.Data(data=SANITY_BODY)))
    await client_stream.send_all(client.send(h11.EndOfMessage()))
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
    server_stream.socket = MockSocket()
    server = TCPServer(sanity_framework, Config(), server_stream)
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
    server_stream.transport_stream = Mock(return_value=PropertyMock(return_value=MockSocket()))
    server_stream.do_handshake = CoroutineMock()
    server_stream.selected_alpn_protocol = Mock(return_value="h2")
    server = TCPServer(sanity_framework, Config(), server_stream)
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
    server_stream.transport_stream = Mock(return_value=PropertyMock(return_value=MockSocket()))
    server_stream.do_handshake = CoroutineMock()
    server_stream.selected_alpn_protocol = Mock(return_value="h2")
    server = TCPServer(sanity_framework, Config(), server_stream)
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
