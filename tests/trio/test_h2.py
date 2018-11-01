import json
from typing import AsyncGenerator, Optional, Type

import h2
import h11
import pytest

import trio
from hypercorn.config import Config
from hypercorn.trio.h2 import H2Server
from hypercorn.typing import ASGIFramework
from ..helpers import ChunkedResponseFramework, EchoFramework, MockSocket, PushFramework

BASIC_HEADERS = [(":authority", "hypercorn"), (":scheme", "https")]
BASIC_DATA = "index"
FLOW_WINDOW_SIZE = 1


class MockConnection:
    def __init__(
        self,
        *,
        config: Config = Config(),
        framework: Type[ASGIFramework] = EchoFramework,
        upgrade_request: Optional[h11.Request] = None,
    ) -> None:
        self.client_stream, server_stream = trio.testing.memory_stream_pair()
        server_stream.socket = MockSocket()
        self.server = H2Server(  # type: ignore
            framework, config, server_stream, upgrade_request=upgrade_request
        )
        self.connection = h2.connection.H2Connection()
        if upgrade_request is not None:
            self.connection.initiate_upgrade_connection()
        else:
            self.connection.initiate_connection()

    async def send_request(self, headers: list, settings: dict) -> int:
        self.connection.update_settings(settings)
        await self.client_stream.send_all(self.connection.data_to_send())
        stream_id = self.connection.get_next_available_stream_id()
        self.connection.send_headers(stream_id, headers)
        await self.client_stream.send_all(self.connection.data_to_send())
        return stream_id

    async def send_data(self, stream_id: int, data: bytes) -> None:
        self.connection.send_data(stream_id, data)
        await self.client_stream.send_all(self.connection.data_to_send())
        await trio.sleep(0)  # Yield to allow the server to process

    async def end_stream(self, stream_id: int) -> None:
        self.connection.end_stream(stream_id)
        await self.client_stream.send_all(self.connection.data_to_send())
        await trio.sleep(0)  # Yield to allow the server to process

    async def close(self) -> None:
        self.connection.close_connection()
        await self.client_stream.send_all(self.connection.data_to_send())
        await self.client_stream.aclose()

    async def get_events(self) -> AsyncGenerator[h2.events.Event, None]:
        while True:
            try:
                events = self.connection.receive_data(
                    await self.client_stream.receive_some(2 ** 16)
                )
            except trio.ClosedResourceError:
                return

            for event in events:
                if isinstance(event, h2.events.ConnectionTerminated):
                    return
                elif isinstance(event, h2.events.DataReceived):
                    self.connection.acknowledge_received_data(
                        event.flow_controlled_length, event.stream_id
                    )
                    try:
                        await self.client_stream.send_all(self.connection.data_to_send())
                    except trio.ClosedResourceError:
                        return
                yield event


@pytest.mark.trio
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
async def test_request(headers: list, body: str, nursery: trio._core._run.Nursery) -> None:
    connection = MockConnection()
    nursery.start_soon(connection.server.handle_connection)
    stream_id = await connection.send_request(headers, {})
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
            await connection.close()
    data = json.loads(response_data.decode())
    assert data["request_body"] == body  # type: ignore


@pytest.mark.trio
async def test_protocol_error() -> None:
    connection = MockConnection()
    await connection.client_stream.send_all(b"broken nonsense\r\n\r\n")
    await connection.server.handle_connection()


@pytest.mark.trio
async def test_pipelining(nursery: trio._core._run.Nursery) -> None:
    connection = MockConnection()
    nursery.start_soon(connection.server.handle_connection)
    streams = [
        await connection.send_request(BASIC_HEADERS + [(":method", "GET"), (":path", "/1")], {}),
        await connection.send_request(BASIC_HEADERS + [(":method", "GET"), (":path", "/1")], {}),
    ]
    for stream_id in streams:
        await connection.end_stream(stream_id)
    responses = 0
    async for event in connection.get_events():
        if isinstance(event, h2.events.ResponseReceived):
            responses += 1
        elif isinstance(event, h2.events.StreamEnded) and responses == 2:
            await connection.close()
    assert responses == len(streams)


@pytest.mark.trio
async def test_server_sends_chunked(nursery: trio._core._run.Nursery) -> None:
    connection = MockConnection(framework=ChunkedResponseFramework)
    nursery.start_soon(connection.server.handle_connection)
    stream_id = await connection.send_request(
        BASIC_HEADERS + [(":method", "GET"), (":path", "/")], {}
    )
    await connection.end_stream(stream_id)
    response_data = b""
    async for event in connection.get_events():
        if isinstance(event, h2.events.DataReceived):
            response_data += event.data
        elif isinstance(event, h2.events.StreamEnded):
            await connection.close()
    assert response_data == b"chunked data"


@pytest.mark.trio
async def test_h2server_upgrade(nursery: trio._core._run.Nursery) -> None:
    upgrade_request = h11.Request(method="GET", target="/", headers=[("Host", "hypercorn")])
    connection = MockConnection(upgrade_request=upgrade_request)
    nursery.start_soon(connection.server.handle_connection)
    response_data = b""
    async for event in connection.get_events():
        if isinstance(event, h2.events.ResponseReceived):
            assert (b":status", b"200") in event.headers
            assert (b"server", b"hypercorn-h2") in event.headers
            assert b"date" in (header[0] for header in event.headers)
        elif isinstance(event, h2.events.DataReceived):
            response_data += event.data
        elif isinstance(event, h2.events.StreamEnded):
            await connection.close()


@pytest.mark.trio
async def test_h2_flow_control(nursery: trio._core._run.Nursery) -> None:
    connection = MockConnection()
    nursery.start_soon(connection.server.handle_connection)
    stream_id = await connection.send_request(
        BASIC_HEADERS + [(":method", "GET"), (":path", "/")],
        {h2.settings.SettingCodes.INITIAL_WINDOW_SIZE: FLOW_WINDOW_SIZE},
    )
    await connection.end_stream(stream_id)
    async for event in connection.get_events():
        if isinstance(event, h2.events.DataReceived):
            assert len(event.data) <= FLOW_WINDOW_SIZE
        elif isinstance(event, h2.events.StreamEnded):
            await connection.close()


@pytest.mark.trio
async def test_h2_push(nursery: trio._core._run.Nursery) -> None:
    connection = MockConnection(framework=PushFramework)
    nursery.start_soon(connection.server.handle_connection)
    stream_id = await connection.send_request(
        BASIC_HEADERS + [(":method", "GET"), (":path", "/")], {}
    )
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
                await connection.close()
    assert push_received
