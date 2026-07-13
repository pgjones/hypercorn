from __future__ import annotations

import asyncio
import os
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from types import TracebackType

from h2.connection import H2Connection
from h2.events import DataReceived, StreamEnded

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CERTFILE = PROJECT_ROOT / "tests" / "assets" / "cert.pem"
KEYFILE = PROJECT_ROOT / "tests" / "assets" / "key.pem"


class ServerProcess:
    def __init__(self, server_repo: Path, *, tls: bool = True) -> None:
        self.port = reserve_port()
        self.server_repo = server_repo
        self.tls = tls
        self.process: subprocess.Popen[str] | None = None

    async def __aenter__(self) -> ServerProcess:
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([str(self.server_repo / "src"), str(PROJECT_ROOT)])
        command = [
            sys.executable,
            "-m",
            "hypercorn",
            "--bind",
            f"127.0.0.1:{self.port}",
            "--workers",
            "0",
            "--worker-class",
            "asyncio",
        ]
        if self.tls:
            command.extend(["--certfile", str(CERTFILE), "--keyfile", str(KEYFILE)])
        command.append("benchmarks.app:app")
        self.process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        await wait_for_ready(self.port, tls=self.tls)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(asyncio.to_thread(self.process.wait), timeout=5)
        except asyncio.TimeoutError:
            self.process.kill()
            await asyncio.to_thread(self.process.wait)


async def wait_for_ready(port: int, *, tls: bool) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not tls:
            if await _wait_for_ready_http11(port):
                return
            await asyncio.sleep(0.05)
            continue

        try:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1",
                port,
                ssl=build_ssl_context(),
                server_hostname="localhost",
            )
        except OSError:
            await asyncio.sleep(0.05)
            continue

        conn = H2Connection()
        conn.initiate_connection()
        writer.write(conn.data_to_send())
        writer.write(build_ready_request(conn))
        await writer.drain()

        while True:
            data = await asyncio.wait_for(reader.read(65535), timeout=1)
            if not data:
                break
            for event in conn.receive_data(data):
                if isinstance(event, DataReceived):
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                elif isinstance(event, StreamEnded) and event.stream_id == 1:
                    writer.close()
                    await writer.wait_closed()
                    return
            pending = conn.data_to_send()
            if pending:
                writer.write(pending)
                await writer.drain()
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.05)
    raise RuntimeError(f"Timed out waiting for benchmark server on port {port}")


async def _wait_for_ready_http11(port: int) -> bool:
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
    except OSError:
        return False

    try:
        writer.write(b"GET /ready HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        await writer.drain()
        status_line = await asyncio.wait_for(reader.readline(), timeout=1)
        return status_line.startswith(b"HTTP/1.1 200")
    except (OSError, asyncio.TimeoutError):
        return False
    finally:
        writer.close()
        await writer.wait_closed()


def build_ready_request(conn: H2Connection) -> bytes:
    conn.send_headers(
        1,
        [
            (":method", "GET"),
            (":scheme", "https"),
            (":authority", "localhost"),
            (":path", "/ready"),
        ],
        end_stream=True,
    )
    return conn.data_to_send()


def build_ssl_context(*, alpn_protocols: list[str] | None = None) -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.set_alpn_protocols(alpn_protocols or ["h2"])
    return context


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * ratio)))
    return ordered[index]


def reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def reserve_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
