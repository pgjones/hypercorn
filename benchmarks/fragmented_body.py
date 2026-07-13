from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from h2.connection import H2Connection
from h2.events import DataReceived, ResponseReceived, StreamEnded

from benchmarks._runtime import build_ssl_context, percentile, PROJECT_ROOT, ServerProcess


@dataclass
class FragmentedBodyBenchmarkConfig:
    warmup_iterations: int
    measured_iterations: int
    chunks: int
    chunk_size: int
    delay_ms: int


@dataclass
class FragmentedBodyBenchmarkResult:
    target_label: str
    server_repo: str
    warmup_iterations: int
    measured_iterations: int
    chunks: int
    chunk_size: int
    delay_ms: int
    samples_ms: list[float]
    mean_ms: float
    median_ms: float
    p95_ms: float
    minimum_ms: float
    maximum_ms: float


async def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    result = await run_fragmented_body_benchmark(
        server_repo=Path(args.server_repo).resolve(),
        label=args.label,
        config=FragmentedBodyBenchmarkConfig(
            warmup_iterations=args.warmup_iterations,
            measured_iterations=args.measured_iterations,
            chunks=args.chunks,
            chunk_size=args.chunk_size,
            delay_ms=args.delay_ms,
        ),
    )
    payload = asdict(result)
    if args.output_json is not None:
        Path(args.output_json).write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an HTTP/2 fragmented request-body benchmark against Hypercorn."
    )
    parser.add_argument("--server-repo", default=str(PROJECT_ROOT))
    parser.add_argument("--label", default="local")
    parser.add_argument("--warmup-iterations", type=int, default=5)
    parser.add_argument("--measured-iterations", type=int, default=50)
    parser.add_argument("--chunks", type=int, default=256)
    parser.add_argument("--chunk-size", type=int, default=128)
    parser.add_argument("--delay-ms", type=int, default=1)
    parser.add_argument("--output-json")
    return parser


async def run_fragmented_body_benchmark(
    server_repo: Path, label: str, config: FragmentedBodyBenchmarkConfig
) -> FragmentedBodyBenchmarkResult:
    samples_ms: list[float] = []
    async with ServerProcess(server_repo) as server:
        for _ in range(config.warmup_iterations):
            await run_fragmented_body_iteration(server.port, config)
        for _ in range(config.measured_iterations):
            samples_ms.append(await run_fragmented_body_iteration(server.port, config))

    return FragmentedBodyBenchmarkResult(
        target_label=label,
        server_repo=str(server_repo),
        warmup_iterations=config.warmup_iterations,
        measured_iterations=config.measured_iterations,
        chunks=config.chunks,
        chunk_size=config.chunk_size,
        delay_ms=config.delay_ms,
        samples_ms=samples_ms,
        mean_ms=statistics.fmean(samples_ms),
        median_ms=statistics.median(samples_ms),
        p95_ms=percentile(samples_ms, 0.95),
        minimum_ms=min(samples_ms),
        maximum_ms=max(samples_ms),
    )


async def run_fragmented_body_iteration(port: int, config: FragmentedBodyBenchmarkConfig) -> float:
    reader, writer = await asyncio.open_connection(
        "127.0.0.1",
        port,
        ssl=build_ssl_context(),
        server_hostname="localhost",
    )
    conn = H2Connection()
    conn.initiate_connection()
    writer.write(conn.data_to_send())
    await writer.drain()

    total_bytes = config.chunks * config.chunk_size
    stream_id = 1
    conn.send_headers(
        stream_id,
        [
            (":method", "POST"),
            (":scheme", "https"),
            (":authority", "localhost"),
            (":path", f"/slow-read?delay_ms={config.delay_ms}"),
            ("content-length", str(total_bytes)),
        ],
        end_stream=False,
    )
    chunk = b"x" * config.chunk_size
    for index in range(config.chunks):
        conn.send_data(stream_id, chunk, end_stream=index == (config.chunks - 1))

    start = time.perf_counter()
    writer.write(conn.data_to_send())
    await writer.drain()

    while True:
        data = await asyncio.wait_for(reader.read(65535), timeout=10)
        if data == b"":
            raise RuntimeError("Fragmented body benchmark connection closed unexpectedly")
        for event in conn.receive_data(data):
            if isinstance(event, DataReceived):
                conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
            elif isinstance(event, ResponseReceived):
                continue
            elif isinstance(event, StreamEnded) and event.stream_id == stream_id:
                writer.close()
                await writer.wait_closed()
                return (time.perf_counter() - start) * 1000

        pending = conn.data_to_send()
        if pending:
            writer.write(pending)
            await writer.drain()
