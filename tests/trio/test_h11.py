import json
from typing import Type, Union

import h11
import pytest

import trio
import trio.testing
from hypercorn.config import Config
from hypercorn.trio.h11 import H11Server
from hypercorn.typing import ASGIFramework
from ..helpers import ChunkedResponseFramework, EchoFramework, MockSocket

BASIC_HEADERS = [("Host", "hypercorn"), ("Connection", "close")]
BASIC_DATA = "index"


class MockConnection:
    def __init__(self, *, framework: Type[ASGIFramework] = EchoFramework) -> None:
        self.client_stream, server_stream = trio.testing.memory_stream_pair()
        server_stream.socket = MockSocket()
        self.client = h11.Connection(h11.CLIENT)
        self.server = H11Server(framework, Config(), server_stream)

    async def send(self, event: Union[h11.Request, h11.Data, h11.EndOfMessage]) -> None:
        await self.send_raw(self.client.send(event))

    async def send_raw(self, data: bytes) -> None:
        await self.client_stream.send_all(data)
        await trio.sleep(0)

    async def get_events(self) -> list:
        events = []
        self.client.receive_data(await self.client_stream.receive_some(2 ** 16))
        while True:
            event = self.client.next_event()
            if event in (h11.NEED_DATA, h11.PAUSED):
                break
            events.append(event)
            if isinstance(event, h11.ConnectionClosed):
                break
        return events


@pytest.mark.trio
@pytest.mark.parametrize(
    "method, headers, body",
    [
        ("GET", BASIC_HEADERS, ""),
        ("POST", BASIC_HEADERS + [("content-length", str(len(BASIC_DATA.encode())))], BASIC_DATA),
    ],
)
async def test_requests(method: str, headers: list, body: str) -> None:
    connection = MockConnection()
    await connection.send(h11.Request(method=method, target="/", headers=headers))
    await connection.send(h11.Data(data=body.encode()))
    await connection.send(h11.EndOfMessage())
    await connection.server.handle_connection()
    response, *data, end = await connection.get_events()
    assert isinstance(response, h11.Response)
    assert response.status_code == 200
    assert (b"server", b"hypercorn-h11") in response.headers
    assert b"date" in (header[0] for header in response.headers)
    assert all(isinstance(datum, h11.Data) for datum in data)
    data = json.loads(b"".join(datum.data for datum in data).decode())
    assert data["request_body"] == body  # type: ignore
    assert isinstance(end, h11.EndOfMessage)


@pytest.mark.trio
async def test_protocol_error() -> None:
    connection = MockConnection()
    await connection.send_raw(b"broken nonsense\r\n\r\n")
    await connection.server.handle_connection()
    response, *_ = await connection.get_events()
    assert isinstance(response, h11.Response)
    assert response.status_code == 400
    assert (b"connection", b"close") in response.headers


@pytest.mark.trio
async def test_pipelining() -> None:
    connection = MockConnection()
    # Note that h11 does not support client pipelining, so this is all raw checks
    await connection.send_raw(
        b"GET / HTTP/1.1\r\nHost: hypercorn\r\nConnection: keep-alive\r\n\r\n"
        b"GET / HTTP/1.1\r\nHost: hypercorn\r\nConnection: close\r\n\r\n"
    )
    await connection.server.handle_connection()
    assert (await connection.client_stream.receive_some(2 ** 16)).decode().count("HTTP/1.1") == 2


@pytest.mark.trio
async def test_client_sends_chunked() -> None:
    connection = MockConnection()
    chunked_headers = [("transfer-encoding", "chunked"), ("expect", "100-continue")]
    await connection.send(
        h11.Request(method="POST", target="/echo", headers=BASIC_HEADERS + chunked_headers)
    )
    async with trio.open_nursery() as nursery:
        nursery.start_soon(connection.server.handle_connection)
        informational_response, *_ = await connection.get_events()
        assert isinstance(informational_response, h11.InformationalResponse)
        assert informational_response.status_code == 100
        for chunk in [b"chunked ", b"data"]:
            await connection.send(h11.Data(data=chunk, chunk_start=True, chunk_end=True))
        await connection.send(h11.EndOfMessage())
    response, *data, end = await connection.get_events()
    assert isinstance(response, h11.Response)
    assert response.status_code == 200
    assert all(isinstance(datum, h11.Data) for datum in data)
    data = json.loads(b"".join(datum.data for datum in data).decode())
    assert data["request_body"] == "chunked data"  # type: ignore
    assert isinstance(end, h11.EndOfMessage)


@pytest.mark.trio
async def test_server_sends_chunked() -> None:
    connection = MockConnection(framework=ChunkedResponseFramework)
    await connection.send(h11.Request(method="GET", target="/", headers=BASIC_HEADERS))
    await connection.send(h11.EndOfMessage())
    await connection.server.handle_connection()
    response, *data, end = await connection.get_events()
    assert isinstance(response, h11.Response)
    assert all(isinstance(datum, h11.Data) for datum in data)
    assert b"".join(datum.data for datum in data) == b"chunked data"
    assert isinstance(end, h11.EndOfMessage)


@pytest.mark.trio
async def test_max_incomplete_size() -> None:
    client_stream, server_stream = trio.testing.memory_stream_pair()
    server_stream.socket = MockSocket()
    config = Config()
    config.h11_max_incomplete_size = 5
    server = H11Server(EchoFramework, config, server_stream)
    await client_stream.send_all(b"GET / HTTP/1.1\r\nHost: hypercorn\r\n")  # Longer than 5 bytes
    await server.handle_connection()
    data = await client_stream.receive_some(2 ** 16)
    assert data.startswith(b"HTTP/1.1 400")


@pytest.mark.trio
async def test_initial_keep_alive_timeout() -> None:
    client_stream, server_stream = trio.testing.memory_stream_pair()
    server_stream.socket = MockSocket()
    config = Config()
    config.keep_alive_timeout = 0.01
    server = H11Server(EchoFramework, config, server_stream)
    with trio.fail_after(2 * config.keep_alive_timeout):
        await server.handle_connection()
    # Only way to confirm closure is to invoke an error
    with pytest.raises(trio.BrokenResourceError):
        await client_stream.send_all(b"GET / HTTP/1.1\r\nHost: hypercorn\r\n")


@pytest.mark.trio
async def test_post_request_keep_alive_timeout() -> None:
    client_stream, server_stream = trio.testing.memory_stream_pair()
    server_stream.socket = MockSocket()
    config = Config()
    config.keep_alive_timeout = 0.01
    server = H11Server(EchoFramework, config, server_stream)
    await client_stream.send_all(b"GET / HTTP/1.1\r\nHost: hypercorn\r\n\r\n")
    with trio.fail_after(2 * config.keep_alive_timeout):
        await server.handle_connection()
    data = await client_stream.receive_some(2 ** 16)
    assert data.startswith(b"HTTP/1.1 200")
