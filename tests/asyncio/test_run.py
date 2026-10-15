from __future__ import annotations

import asyncio
import socket
import ssl
import threading
import time
from collections.abc import Awaitable, Callable

import pytest

from hypercorn.app_wrappers import ASGIWrapper
from hypercorn.asyncio.run import worker_serve
from hypercorn.config import Config
from ..helpers import empty_framework


def _hold_connection(port: int, answered: threading.Event) -> None:
    # A client that sends a request, reads the response and then keeps the
    # connection without reading, so it never answers the server's close.
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with context.wrap_socket(socket.create_connection(("127.0.0.1", port))) as sock:
        sock.sendall(b"GET / HTTP/1.1\r\nHost: hypercorn\r\n\r\n")
        sock.recv(1024)
        answered.set()
        time.sleep(30)


def _tls_config() -> Config:
    config = Config()
    config.bind = ["127.0.0.1:0"]
    config.certfile = "tests/assets/cert.pem"
    config.keyfile = "tests/assets/key.pem"
    config.graceful_timeout = 0.5
    return config


async def _shutdown_time(config: Config, hold: Callable[[int], Awaitable[None]]) -> float:
    sockets = config.create_sockets()
    port = sockets.secure_sockets[0].getsockname()[1]
    shutdown = asyncio.Event()
    serve = asyncio.create_task(
        worker_serve(
            ASGIWrapper(empty_framework), config, sockets=sockets, shutdown_trigger=shutdown.wait
        )
    )
    await asyncio.sleep(0.1)
    await hold(port)
    shutdown.set()
    started = time.monotonic()
    await asyncio.wait_for(serve, 10)
    elapsed = time.monotonic() - started
    await asyncio.sleep(0.1)
    connections = [
        task
        for task in asyncio.all_tasks()
        if getattr(task.get_coro(), "__qualname__", "").endswith("_server_callback")
    ]
    assert connections == []
    return elapsed


@pytest.mark.asyncio
async def test_shutdown_is_bounded_with_a_held_connection() -> None:
    answered = threading.Event()

    async def hold(port: int) -> None:
        threading.Thread(target=_hold_connection, args=(port, answered), daemon=True).start()
        assert await asyncio.to_thread(answered.wait, 5)

    assert await _shutdown_time(_tls_config(), hold) < 2


@pytest.mark.asyncio
async def test_shutdown_is_bounded_with_an_unfinished_handshake() -> None:
    held: list[socket.socket] = []

    async def hold(port: int) -> None:
        held.append(socket.create_connection(("127.0.0.1", port)))
        await asyncio.sleep(0.1)

    try:
        assert await _shutdown_time(_tls_config(), hold) < 2
    finally:
        held[0].close()
