"""Regression coverage for early HTTP/2 responses with unread request bodies (#393)."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import h2.config
import h2.connection
import h2.events
import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Requires POSIX process-group cleanup")

ServerFixture = tuple[int, subprocess.Popen[bytes]]

APP = """async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] == "http":
        if scope["path"] == "/consume":
            while True:
                message = await receive()
                if message["type"] == "http.disconnect" or not message.get("more_body", False):
                    break
        status = 413 if scope["path"] == "/early" else 200
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-length", b"2")]})
        await send({"type": "http.response.body", "body": b"ok"})
"""


@pytest.fixture
def server(tmp_path: Path) -> Iterator[ServerFixture]:
    (tmp_path / "app.py").write_text(APP)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "hypercorn",
            "--bind",
            f"127.0.0.1:{port}",
            "--workers",
            "1",
            "--graceful-timeout",
            "2",
            "app:app",
        ],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            assert process.poll() is None, "Hypercorn exited during startup"
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.02)
        else:
            pytest.fail("Hypercorn did not listen within five seconds")
        yield port, process
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)


def probe(
    port: int,
    data_frames: int,
    path: str = "/early",
    *,
    reset: bool = False,
    extra_streams: int = 1,
    before_close: Callable[[socket.socket, dict[int, str], set[int]], None] | None = None,
) -> tuple[dict[int, str], set[int]]:
    connection = h2.connection.H2Connection(
        config=h2.config.H2Configuration(client_side=True, header_encoding="utf-8")
    )
    connection.initiate_connection()
    connection.send_headers(
        1,
        [
            (":method", "POST"),
            (":scheme", "http"),
            (":authority", "localhost"),
            (":path", path),
        ],
        end_stream=False,
    )
    for _ in range(data_frames):
        connection.send_data(1, b"x" * 1024)
    if reset:
        connection.reset_stream(1)
    elif path == "/consume":
        connection.end_stream(1)
    for follow_stream_id in range(3, 2 * extra_streams + 3, 2):
        connection.send_headers(
            follow_stream_id,
            [
                (":method", "GET"),
                (":scheme", "http"),
                (":authority", "localhost"),
                (":path", "/next"),
            ],
            end_stream=True,
        )

    with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
        sock.sendall(connection.data_to_send())
        statuses: dict[int, str] = {}
        ended: set[int] = set()
        deadline = time.monotonic() + 3
        expected_end = set(range(3, 2 * extra_streams + 3, 2))
        if not reset:
            expected_end.add(1)
        while not expected_end.issubset(ended) and time.monotonic() < deadline:
            sock.settimeout(max(0.01, deadline - time.monotonic()))
            try:
                data = sock.recv(65536)
            except TimeoutError:
                break
            if not data:
                break
            for event in connection.receive_data(data):
                if isinstance(event, h2.events.ResponseReceived):
                    statuses[event.stream_id] = cast(dict[str, str], dict(event.headers))[":status"]
                elif isinstance(event, h2.events.DataReceived):
                    connection.acknowledge_received_data(
                        event.flow_controlled_length, event.stream_id
                    )
                elif isinstance(event, h2.events.StreamEnded):
                    ended.add(event.stream_id)
            sock.sendall(connection.data_to_send())
        if before_close is not None:
            before_close(sock, statuses, ended)
        return statuses, ended


@pytest.mark.parametrize("data_frames", [0, 1, 40, 60])
def test_later_http2_stream_not_blocked(server: ServerFixture, data_frames: int) -> None:
    statuses, ended = probe(server[0], data_frames)
    assert statuses == {1: "413", 3: "200"}
    assert ended == {1, 3}


def test_normal_request_body_consumption(server: ServerFixture) -> None:
    statuses, ended = probe(server[0], 40, path="/consume")
    assert statuses == {1: "200", 3: "200"}
    assert ended == {1, 3}


def test_client_reset_does_not_block_later_stream(server: ServerFixture) -> None:
    statuses, ended = probe(server[0], 40, reset=True)
    assert statuses[3] == "200"
    assert 3 in ended


def test_shutdown_after_early_response_with_client_connected(server: ServerFixture) -> None:
    _, process = server

    def stop_while_socket_open(
        sock: socket.socket, statuses: dict[int, str], ended: set[int]
    ) -> None:
        assert statuses == {1: "413", 3: "200"}
        assert ended == {1, 3}
        os.kill(process.pid, signal.SIGTERM)
        process.wait(timeout=4)

    probe(server[0], 40, before_close=stop_while_socket_open)


def test_http11_early_response_compatibility(server: ServerFixture) -> None:
    with socket.create_connection(("127.0.0.1", server[0]), timeout=3) as sock:
        sock.settimeout(3)
        sock.sendall(
            b"POST /early HTTP/1.1\r\nHost: localhost\r\nContent-Length: 40960\r\n\r\n"
            + b"x" * 40960
        )
        assert b"HTTP/1.1 413" in sock.recv(4096)


@pytest.mark.parametrize("data_frames", [40, 60])
def test_multiple_following_http2_streams_not_blocked(
    server: ServerFixture, data_frames: int
) -> None:
    statuses, ended = probe(server[0], data_frames, extra_streams=20)
    expected_statuses = {1: "413", **{index: "200" for index in range(3, 43, 2)}}
    assert statuses == expected_statuses
    assert ended == set(expected_statuses)
