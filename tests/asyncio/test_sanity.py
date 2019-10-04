import asyncio

import h2
import h11
import pytest
import wsproto

from hypercorn.asyncio.tcp_server import TCPServer
from hypercorn.config import Config
from .helpers import MemoryReader, MemoryWriter
from ..helpers import SANITY_BODY, sanity_framework


@pytest.mark.asyncio
async def test_http1_request(event_loop: asyncio.AbstractEventLoop) -> None:
    server = TCPServer(
        sanity_framework, event_loop, Config(), MemoryReader(), MemoryWriter()  # type: ignore
    )
    asyncio.ensure_future(server.run())
    client = h11.Connection(h11.CLIENT)
    await server.reader.send(  # type: ignore
        client.send(
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
    await server.reader.send(client.send(h11.Data(data=SANITY_BODY)))  # type: ignore
    await server.reader.send(client.send(h11.EndOfMessage()))  # type: ignore
    events = []
    while True:
        event = client.next_event()
        if event == h11.NEED_DATA:
            data = await server.writer.receive()  # type: ignore
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


@pytest.mark.asyncio
async def test_http1_websocket(event_loop: asyncio.AbstractEventLoop) -> None:
    server = TCPServer(
        sanity_framework, event_loop, Config(), MemoryReader(), MemoryWriter()  # type: ignore
    )
    asyncio.ensure_future(server.run())
    client = wsproto.WSConnection(wsproto.ConnectionType.CLIENT)
    await server.reader.send(  # type: ignore
        client.send(wsproto.events.Request(host="hypercorn", target="/"))
    )
    client.receive_data(await server.writer.receive())  # type: ignore
    assert list(client.events()) == [
        wsproto.events.AcceptConnection(
            extra_headers=[
                (b"date", b"Thu, 01 Jan 1970 01:23:20 GMT"),
                (b"server", b"hypercorn-h11"),
            ]
        )
    ]
    await server.reader.send(  # type: ignore
        client.send(wsproto.events.BytesMessage(data=SANITY_BODY))
    )
    client.receive_data(await server.writer.receive())  # type: ignore
    assert list(client.events()) == [wsproto.events.TextMessage(data="Hello & Goodbye")]
    await server.reader.send(  # type: ignore
        client.send(wsproto.events.CloseConnection(code=1000))
    )
    client.receive_data(await server.writer.receive())  # type: ignore
    assert list(client.events()) == [wsproto.events.CloseConnection(code=1000, reason="")]
    assert server.writer.is_closed  # type: ignore


@pytest.mark.asyncio
async def test_http2_request(event_loop: asyncio.AbstractEventLoop) -> None:
    server = TCPServer(
        sanity_framework,
        event_loop,
        Config(),
        MemoryReader(),  # type: ignore
        MemoryWriter(http2=True),  # type: ignore
    )
    asyncio.ensure_future(server.run())
    client = h2.connection.H2Connection()
    client.initiate_connection()
    await server.reader.send(client.data_to_send())  # type: ignore
    stream_id = client.get_next_available_stream_id()
    client.send_headers(
        stream_id,
        [
            (":method", "POST"),
            (":path", "/"),
            (":authority", "hypercorn"),
            (":scheme", "https"),
            ("content-length", "%d" % len(SANITY_BODY)),
        ],
    )
    client.send_data(stream_id, SANITY_BODY)
    client.end_stream(stream_id)
    await server.reader.send(client.data_to_send())  # type: ignore
    events = []
    open_ = True
    while open_:
        data = await server.writer.receive()  # type: ignore
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
        await server.reader.send(client.data_to_send())  # type: ignore
    assert isinstance(events[2], h2.events.ResponseReceived)
    assert events[2].headers == [
        (b":status", b"200"),
        (b"content-length", b"15"),
        (b"date", b"Thu, 01 Jan 1970 01:23:20 GMT"),
        (b"server", b"hypercorn-h2"),
    ]


@pytest.mark.asyncio
async def test_http2_websocket(event_loop: asyncio.AbstractEventLoop) -> None:
    server = TCPServer(
        sanity_framework,
        event_loop,
        Config(),
        MemoryReader(),  # type: ignore
        MemoryWriter(http2=True),  # type: ignore
    )
    asyncio.ensure_future(server.run())
    h2_client = h2.connection.H2Connection()
    h2_client.initiate_connection()
    await server.reader.send(h2_client.data_to_send())  # type: ignore
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
    await server.reader.send(h2_client.data_to_send())  # type: ignore
    events = h2_client.receive_data(await server.writer.receive())  # type: ignore
    await server.reader.send(h2_client.data_to_send())  # type: ignore
    events = h2_client.receive_data(await server.writer.receive())  # type: ignore
    events = h2_client.receive_data(await server.writer.receive())  # type: ignore
    assert isinstance(events[0], h2.events.ResponseReceived)
    assert events[0].headers == [
        (b":status", b"200"),
        (b"date", b"Thu, 01 Jan 1970 01:23:20 GMT"),
        (b"server", b"hypercorn-h2"),
    ]
    client = wsproto.connection.Connection(wsproto.ConnectionType.CLIENT)
    h2_client.send_data(stream_id, client.send(wsproto.events.BytesMessage(data=SANITY_BODY)))
    await server.reader.send(h2_client.data_to_send())  # type: ignore
    events = h2_client.receive_data(await server.writer.receive())  # type: ignore
    client.receive_data(events[0].data)
    assert list(client.events()) == [wsproto.events.TextMessage(data="Hello & Goodbye")]
    h2_client.send_data(stream_id, client.send(wsproto.events.CloseConnection(code=1000)))
    await server.reader.send(h2_client.data_to_send())  # type: ignore
    events = h2_client.receive_data(await server.writer.receive())  # type: ignore
    client.receive_data(events[0].data)
    assert list(client.events()) == [wsproto.events.CloseConnection(code=1000, reason="")]
    await server.reader.send(b"")  # type: ignore
