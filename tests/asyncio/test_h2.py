import asyncio
import json
from typing import AsyncGenerator, Optional, Type
from unittest.mock import Mock

import h2
import h11
import pytest

from hypercorn.asyncio.h2 import H2Server
from hypercorn.config import Config
from hypercorn.typing import ASGIFramework
from ..helpers import ChunkedResponseFramework, EchoFramework, PushFramework
from .helpers import MockTransport

BASIC_HEADERS = [(":authority", "hypercorn"), (":scheme", "https")]
BASIC_DATA = "index"
FLOW_WINDOW_SIZE = 1


class MockConnection:
    def __init__(
        self,
        event_loop: asyncio.AbstractEventLoop,
        *,
        config: Config = Config(),
        framework: Type[ASGIFramework] = EchoFramework,
        upgrade_request: Optional[h11.Request] = None,
    ) -> None:
        self.transport = MockTransport()
        self.server = H2Server(  # type: ignore
            framework, event_loop, config, self.transport, upgrade_request=upgrade_request
        )
        self.connection = h2.connection.H2Connection()
        if upgrade_request is not None:
            self.connection.initiate_upgrade_connection()
        else:
            self.connection.initiate_connection()

    def send_request(self, headers: list, settings: dict) -> int:
        self.connection.update_settings(settings)
        self.server.data_received(self.connection.data_to_send())
        stream_id = self.connection.get_next_available_stream_id()
        self.connection.send_headers(stream_id, headers)
        self.server.data_received(self.connection.data_to_send())
        return stream_id

    async def send_data(self, stream_id: int, data: bytes) -> None:
        self.connection.send_data(stream_id, data)
        self.server.data_received(self.connection.data_to_send())
        await asyncio.sleep(0)  # Yield to allow the server to process

    async def end_stream(self, stream_id: int) -> None:
        self.connection.end_stream(stream_id)
        self.server.data_received(self.connection.data_to_send())
        await asyncio.sleep(0)  # Yield to allow the server to process

    def close(self) -> None:
        self.connection.close_connection()
        self.server.data_received(self.connection.data_to_send())

    async def get_events(self) -> AsyncGenerator[h2.events.Event, None]:
        while True:
            await self.transport.updated.wait()
            events = self.connection.receive_data(self.transport.data)
            self.transport.clear()
            for event in events:
                if isinstance(event, h2.events.ConnectionTerminated):
                    self.transport.close()
                elif isinstance(event, h2.events.DataReceived):
                    self.connection.acknowledge_received_data(
                        event.flow_controlled_length, event.stream_id
                    )
                    self.server.data_received(self.connection.data_to_send())
                yield event
            if self.transport.closed.is_set():
                break


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers, body",
    [
        (BASIC_HEADERS + [(":method", "GET"), (":path", "/")], ""),
        (
            BASIC_HEADERS
            + [
                (":method", "POST"),
                (":path", "/"),
                ("content-length", str(len(BASIC_DATA.encode()))),
            ],
            BASIC_DATA,
        ),
    ],
)
async def test_request(headers: list, body: str, event_loop: asyncio.AbstractEventLoop) -> None:
    connection = MockConnection(event_loop)
    stream_id = connection.send_request(headers, {})
    if body != "":
        await connection.send_data(stream_id, body.encode())
    await connection.end_stream(stream_id)
    response_data = b""
    async for event in connection.get_events():
        if isinstance(event, h2.events.ResponseReceived):
            assert (b":status", b"200") in event.headers
            assert (b"server", b"hypercorn-h2") in event.headers
            assert b"date" in (header[0] for header in event.headers)
        elif isinstance(event, h2.events.DataReceived):
            response_data += event.data
        elif isinstance(event, h2.events.StreamEnded):
            connection.close()
    data = json.loads(response_data.decode())
    assert data["request_body"] == body  # type: ignore


@pytest.mark.asyncio
async def test_protocol_error(event_loop: asyncio.AbstractEventLoop) -> None:
    connection = MockConnection(event_loop)
    connection.server.data_received(b"broken nonsense\r\n\r\n")
    assert connection.transport.closed.is_set()  # H2 just closes on error


@pytest.mark.asyncio
async def test_pipelining(event_loop: asyncio.AbstractEventLoop) -> None:
    connection = MockConnection(event_loop)
    streams = [
        connection.send_request(BASIC_HEADERS + [(":method", "GET"), (":path", "/1")], {}),
        connection.send_request(BASIC_HEADERS + [(":method", "GET"), (":path", "/1")], {}),
    ]
    for stream_id in streams:
        await connection.end_stream(stream_id)
    responses = 0
    async for event in connection.get_events():
        if isinstance(event, h2.events.ResponseReceived):
            responses += 1
        elif isinstance(event, h2.events.StreamEnded) and responses == 2:
            connection.close()
    assert responses == len(streams)


@pytest.mark.asyncio
async def test_server_sends_chunked(event_loop: asyncio.AbstractEventLoop) -> None:
    connection = MockConnection(event_loop, framework=ChunkedResponseFramework)
    stream_id = connection.send_request(BASIC_HEADERS + [(":method", "GET"), (":path", "/")], {})
    await connection.end_stream(stream_id)
    response_data = b""
    async for event in connection.get_events():
        if isinstance(event, h2.events.DataReceived):
            response_data += event.data
        elif isinstance(event, h2.events.StreamEnded):
            connection.close()
    assert response_data == b"chunked data"


@pytest.mark.asyncio
async def test_initial_keep_alive_timeout(event_loop: asyncio.AbstractEventLoop) -> None:
    config = Config()
    config.keep_alive_timeout = 0.01
    server = H2Server(EchoFramework, event_loop, config, Mock())
    await asyncio.sleep(2 * config.keep_alive_timeout)
    server.transport.close.assert_called()  # type: ignore


@pytest.mark.asyncio
async def test_post_response_keep_alive_timeout(event_loop: asyncio.AbstractEventLoop) -> None:
    config = Config()
    config.keep_alive_timeout = 0.01
    connection = MockConnection(event_loop, config=config)
    stream_id = connection.send_request(BASIC_HEADERS + [(":method", "GET"), (":path", "/1")], {})
    connection.server.pause_writing()
    await connection.end_stream(stream_id)
    await asyncio.sleep(2 * config.keep_alive_timeout)
    assert not connection.transport.closed.is_set()
    connection.server.resume_writing()
    await asyncio.sleep(2 * config.keep_alive_timeout)
    assert connection.transport.closed.is_set()


@pytest.mark.asyncio
async def test_h2server_upgrade(event_loop: asyncio.AbstractEventLoop) -> None:
    upgrade_request = h11.Request(method="GET", target="/", headers=[("Host", "hypercorn")])
    connection = MockConnection(event_loop, upgrade_request=upgrade_request)
    response_data = b""
    async for event in connection.get_events():
        if isinstance(event, h2.events.ResponseReceived):
            assert (b":status", b"200") in event.headers
            assert (b"server", b"hypercorn-h2") in event.headers
            assert b"date" in (header[0] for header in event.headers)
        elif isinstance(event, h2.events.DataReceived):
            response_data += event.data
        elif isinstance(event, h2.events.StreamEnded):
            connection.close()


@pytest.mark.asyncio
async def test_h2_flow_control(event_loop: asyncio.AbstractEventLoop) -> None:
    connection = MockConnection(event_loop)
    stream_id = connection.send_request(
        BASIC_HEADERS + [(":method", "GET"), (":path", "/")],
        {h2.settings.SettingCodes.INITIAL_WINDOW_SIZE: FLOW_WINDOW_SIZE},
    )
    await connection.end_stream(stream_id)
    async for event in connection.get_events():
        if isinstance(event, h2.events.DataReceived):
            assert len(event.data) <= FLOW_WINDOW_SIZE
        elif isinstance(event, h2.events.StreamEnded):
            connection.close()


@pytest.mark.asyncio
async def test_h2_push(event_loop: asyncio.AbstractEventLoop) -> None:
    connection = MockConnection(event_loop, framework=PushFramework)
    stream_id = connection.send_request(BASIC_HEADERS + [(":method", "GET"), (":path", "/")], {})
    await connection.end_stream(stream_id)
    push_received = False
    streams_received = 0
    async for event in connection.get_events():
        if isinstance(event, h2.events.PushedStreamReceived):
            assert (b":path", b"/") in event.headers
            assert (b":method", b"GET") in event.headers
            assert (b":scheme", b"http") in event.headers
            assert (b":authority", b"hypercorn") in event.headers
            push_received = True
        elif isinstance(event, h2.events.StreamEnded):
            streams_received += 1
            if streams_received == 2:
                connection.close()
    assert push_received
