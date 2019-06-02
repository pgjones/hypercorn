import pytest
import trio

from hypercorn.config import Config
from hypercorn.trio.server import Server
from ..helpers import echo_framework, MockSocket


@pytest.mark.trio
async def test_initial_keep_alive_timeout() -> None:
    config = Config()
    config.keep_alive_timeout = 0.01
    client_stream, server_stream = trio.testing.memory_stream_pair()
    server_stream.socket = MockSocket()
    server = Server(echo_framework, config, server_stream)
    with trio.fail_after(2 * config.keep_alive_timeout):
        await server.run()
    # Only way to confirm closure is to invoke an error
    with pytest.raises(trio.BrokenResourceError):
        await client_stream.send_all(b"GET / HTTP/1.1\r\nHost: hypercorn\r\n")


@pytest.mark.trio
async def test_post_request_keep_alive_timeout() -> None:
    config = Config()
    config.keep_alive_timeout = 0.01
    client_stream, server_stream = trio.testing.memory_stream_pair()
    server_stream.socket = MockSocket()
    server = Server(echo_framework, config, server_stream)
    await client_stream.send_all(
        b"GET / HTTP/1.1\r\nHost: hypercorn\r\nconnection: keep-alive\r\n\r\n"
    )
    with trio.fail_after(2 * config.keep_alive_timeout):
        await server.run()
    data = await client_stream.receive_some(2 ** 16)
    assert data.startswith(b"HTTP/1.1 200")
    # Only way to confirm closure is to invoke an error
    with pytest.raises(trio.BrokenResourceError):
        await client_stream.send_all(b"GET / HTTP/1.1\r\nHost: hypercorn\r\n")
