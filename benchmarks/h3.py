from __future__ import annotations

import argparse
import asyncio
import json
import os
import ssl
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import TracebackType

from aioquic.asyncio.client import connect
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3_ALPN, H3Connection
from aioquic.h3.events import DataReceived, HeadersReceived
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import ConnectionTerminated, QuicEvent

from benchmarks._runtime import CERTFILE, KEYFILE, percentile, PROJECT_ROOT, reserve_udp_port


@dataclass
class H3BenchmarkConfig:
    path: str
    warmup_requests: int
    total_requests: int
    concurrency: int


@dataclass
class H3BenchmarkResult:
    target_label: str
    server_repo: str
    path: str
    warmup_requests: int
    total_requests: int
    concurrency: int
    total_time_s: float
    requests_per_second: float
    samples_ms: list[float]
    mean_ms: float
    median_ms: float
    p95_ms: float
    minimum_ms: float
    maximum_ms: float


async def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    result = await run_h3_benchmark(
        server_repo=Path(args.server_repo).resolve(),
        label=args.label,
        config=H3BenchmarkConfig(
            path=args.path,
            warmup_requests=args.warmup_requests,
            total_requests=args.total_requests,
            concurrency=args.concurrency,
        ),
    )
    payload = asdict(result)
    if args.output_json is not None:
        Path(args.output_json).write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a real HTTP/3 benchmark over QUIC.")
    parser.add_argument("--server-repo", default=str(PROJECT_ROOT))
    parser.add_argument("--label", default="local")
    parser.add_argument("--path", default="/fast")
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--total-requests", type=int, default=500)
    parser.add_argument("--warmup-requests", type=int, default=50)
    parser.add_argument("--output-json")
    return parser


async def run_h3_benchmark(
    server_repo: Path, label: str, config: H3BenchmarkConfig
) -> H3BenchmarkResult:
    async with QuicServerProcess(server_repo) as server:
        await run_requests(
            server.port,
            "/ready",
            min(config.warmup_requests, config.total_requests),
            max(1, min(config.concurrency, config.warmup_requests or config.total_requests)),
        )
        total_time_s, samples_ms = await run_requests(
            server.port, config.path, config.total_requests, config.concurrency
        )

    return H3BenchmarkResult(
        target_label=label,
        server_repo=str(server_repo),
        path=config.path,
        warmup_requests=config.warmup_requests,
        total_requests=config.total_requests,
        concurrency=config.concurrency,
        total_time_s=total_time_s,
        requests_per_second=(config.total_requests / total_time_s) if total_time_s > 0 else 0.0,
        samples_ms=samples_ms,
        mean_ms=statistics.fmean(samples_ms),
        median_ms=statistics.median(samples_ms),
        p95_ms=percentile(samples_ms, 0.95),
        minimum_ms=min(samples_ms),
        maximum_ms=max(samples_ms),
    )


class QuicServerProcess:
    def __init__(self, server_repo: Path) -> None:
        self.port = reserve_udp_port()
        self.server_repo = server_repo
        self.process: subprocess.Popen[str] | None = None

    async def __aenter__(self) -> QuicServerProcess:
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join([str(self.server_repo / "src"), str(PROJECT_ROOT)])
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "hypercorn",
                "--bind",
                "127.0.0.1:0",
                "--quic-bind",
                f"127.0.0.1:{self.port}",
                "--workers",
                "0",
                "--worker-class",
                "asyncio",
                "--certfile",
                str(CERTFILE),
                "--keyfile",
                str(KEYFILE),
                "benchmarks.app:app",
            ],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        await wait_for_ready(self.port)
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


class H3ClientProtocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._http = H3Connection(self._quic)
        self._responses: dict[int, bytearray] = {}
        self._waiters: dict[int, asyncio.Future[tuple[list[tuple[bytes, bytes]], bytes]]] = {}
        self._headers: dict[int, list[tuple[bytes, bytes]]] = {}

    async def get(self, path: str) -> tuple[list[tuple[bytes, bytes]], bytes]:
        stream_id = self._quic.get_next_available_stream_id()
        waiter = asyncio.get_running_loop().create_future()
        self._responses[stream_id] = bytearray()
        self._waiters[stream_id] = waiter
        self._headers[stream_id] = []
        self._http.send_headers(
            stream_id,
            [
                (b":method", b"GET"),
                (b":scheme", b"https"),
                (b":authority", b"localhost"),
                (b":path", path.encode("ascii")),
            ],
            end_stream=True,
        )
        self.transmit()
        return await waiter

    def quic_event_received(self, event: QuicEvent) -> None:
        if isinstance(event, ConnectionTerminated):
            error = RuntimeError("HTTP/3 connection terminated")
            for waiter in list(self._waiters.values()):
                if not waiter.done():
                    waiter.set_exception(error)
            self._waiters.clear()
            return

        for http_event in self._http.handle_event(event):
            if isinstance(http_event, HeadersReceived):
                self._headers[http_event.stream_id] = http_event.headers
                if http_event.stream_ended:
                    self._finish(http_event.stream_id)
            elif isinstance(http_event, DataReceived):
                self._responses[http_event.stream_id].extend(http_event.data)
                if http_event.stream_ended:
                    self._finish(http_event.stream_id)

    def _finish(self, stream_id: int) -> None:
        waiter = self._waiters.pop(stream_id)
        headers = self._headers.pop(stream_id)
        body = bytes(self._responses.pop(stream_id))
        if not waiter.done():
            waiter.set_result((headers, body))


async def wait_for_ready(port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            headers, _ = await request_once(port, "/ready")
            if _status_code(headers) == 200:
                return
        except Exception:
            await asyncio.sleep(0.05)
            continue
    raise RuntimeError(f"Timed out waiting for HTTP/3 benchmark server on port {port}")


async def run_requests(
    port: int, path: str, total_requests: int, concurrency: int
) -> tuple[float, list[float]]:
    if total_requests <= 0:
        return 0.0, []

    queue: asyncio.Queue[int | None] = asyncio.Queue()
    for index in range(total_requests):
        queue.put_nowait(index)
    for _ in range(concurrency):
        queue.put_nowait(None)

    latencies_ms: list[float] = []
    lock = asyncio.Lock()
    async with connect(
        "127.0.0.1",
        port,
        configuration=_build_quic_config(),
        create_protocol=H3ClientProtocol,
        wait_connected=True,
    ) as client:
        client = client  # typing hint

        async def worker() -> None:
            while True:
                item = await queue.get()
                if item is None:
                    return
                start = time.perf_counter()
                headers, _ = await client.get(path)  # type: ignore[attr-defined]
                if _status_code(headers) != 200:
                    raise RuntimeError(
                        f"Unexpected HTTP/3 status for {path}: {_status_code(headers)}"
                    )
                elapsed_ms = (time.perf_counter() - start) * 1000
                async with lock:
                    latencies_ms.append(elapsed_ms)

        start = time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(concurrency)))
        total_time_s = time.perf_counter() - start
    return total_time_s, latencies_ms


async def request_once(port: int, path: str) -> tuple[list[tuple[bytes, bytes]], bytes]:
    async with connect(
        "127.0.0.1",
        port,
        configuration=_build_quic_config(),
        create_protocol=H3ClientProtocol,
        wait_connected=True,
    ) as client:
        return await client.get(path)  # type: ignore[attr-defined]


def _build_quic_config() -> QuicConfiguration:
    config = QuicConfiguration(alpn_protocols=H3_ALPN, is_client=True, server_name="localhost")
    config.verify_mode = ssl.CERT_NONE
    return config


def _status_code(headers: list[tuple[bytes, bytes]]) -> int:
    for name, value in headers:
        if name == b":status":
            return int(value)
    raise RuntimeError("Missing :status header")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
