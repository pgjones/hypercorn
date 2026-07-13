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
class BenchmarkConfig:
    warmup_iterations: int
    measured_iterations: int
    fast_streams: int
    slow_chunks: int
    slow_chunk_size: int
    slow_delay_ms: int


@dataclass
class BenchmarkResult:
    target_label: str
    server_repo: str
    warmup_iterations: int
    measured_iterations: int
    fast_streams: int
    slow_chunks: int
    slow_chunk_size: int
    slow_delay_ms: int
    samples_ms: list[float]
    mean_ms: float
    median_ms: float
    p95_ms: float
    minimum_ms: float
    maximum_ms: float


async def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config = BenchmarkConfig(
        warmup_iterations=args.warmup_iterations,
        measured_iterations=args.measured_iterations,
        fast_streams=args.fast_streams,
        slow_chunks=args.slow_chunks,
        slow_chunk_size=args.slow_chunk_size,
        slow_delay_ms=args.slow_delay_ms,
    )
    result = await run_benchmark(
        server_repo=Path(args.server_repo).resolve(),
        label=args.label,
        config=config,
    )

    payload = asdict(result)
    if args.output_json is not None:
        Path(args.output_json).write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a small HTTP/2 HoL benchmark against Hypercorn."
    )
    parser.add_argument(
        "--server-repo", default=str(PROJECT_ROOT), help="Path to the Hypercorn repo to benchmark."
    )
    parser.add_argument("--label", default="local", help="Label to include in the output.")
    parser.add_argument("--warmup-iterations", type=int, default=2)
    parser.add_argument("--measured-iterations", type=int, default=10)
    parser.add_argument("--fast-streams", type=int, default=5)
    parser.add_argument("--slow-chunks", type=int, default=12)
    parser.add_argument("--slow-chunk-size", type=int, default=4096)
    parser.add_argument("--slow-delay-ms", type=int, default=25)
    parser.add_argument("--output-json", help="Optional path to write the result as JSON.")
    return parser


async def run_benchmark(server_repo: Path, label: str, config: BenchmarkConfig) -> BenchmarkResult:
    samples_ms: list[float] = []
    async with ServerProcess(server_repo) as server:
        for _ in range(config.warmup_iterations):
            await run_h2_hol_iteration(server.port, config)
        for _ in range(config.measured_iterations):
            samples_ms.extend(await run_h2_hol_iteration(server.port, config))

    return BenchmarkResult(
        target_label=label,
        server_repo=str(server_repo),
        warmup_iterations=config.warmup_iterations,
        measured_iterations=config.measured_iterations,
        fast_streams=config.fast_streams,
        slow_chunks=config.slow_chunks,
        slow_chunk_size=config.slow_chunk_size,
        slow_delay_ms=config.slow_delay_ms,
        samples_ms=samples_ms,
        mean_ms=statistics.fmean(samples_ms),
        median_ms=statistics.median(samples_ms),
        p95_ms=percentile(samples_ms, 0.95),
        minimum_ms=min(samples_ms),
        maximum_ms=max(samples_ms),
    )


async def run_h2_hol_iteration(port: int, config: BenchmarkConfig) -> list[float]:
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

    slow_stream_id = 1
    fast_stream_ids = [3 + (index * 2) for index in range(config.fast_streams)]
    send_hol_workload(conn, slow_stream_id, fast_stream_ids, config)
    start_times = {stream_id: time.perf_counter() for stream_id in fast_stream_ids}
    writer.write(conn.data_to_send())
    await writer.drain()

    latencies_ms: list[float] = []
    while len(latencies_ms) < len(fast_stream_ids):
        data = await asyncio.wait_for(reader.read(65535), timeout=5)
        if data == b"":
            raise RuntimeError("Benchmark connection closed unexpectedly")
        for event in conn.receive_data(data):
            if isinstance(event, DataReceived):
                conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
            elif isinstance(event, ResponseReceived):
                if event.stream_id not in start_times:
                    continue
            elif isinstance(event, StreamEnded) and event.stream_id in start_times:
                latencies_ms.append((time.perf_counter() - start_times[event.stream_id]) * 1000)
        pending = conn.data_to_send()
        if pending:
            writer.write(pending)
            await writer.drain()

    writer.close()
    await writer.wait_closed()
    return latencies_ms


def send_hol_workload(
    conn: H2Connection, slow_stream_id: int, fast_stream_ids: list[int], config: BenchmarkConfig
) -> None:
    total_slow_bytes = config.slow_chunks * config.slow_chunk_size
    conn.send_headers(
        slow_stream_id,
        [
            (":method", "POST"),
            (":scheme", "https"),
            (":authority", "localhost"),
            (":path", f"/slow-read?delay_ms={config.slow_delay_ms}"),
            ("content-length", str(total_slow_bytes)),
        ],
        end_stream=False,
    )
    for _ in range(config.slow_chunks):
        conn.send_data(slow_stream_id, b"x" * config.slow_chunk_size, end_stream=False)

    for stream_id in fast_stream_ids:
        conn.send_headers(
            stream_id,
            [
                (":method", "POST"),
                (":scheme", "https"),
                (":authority", "localhost"),
                (":path", "/fast"),
                ("content-length", "2"),
            ],
            end_stream=False,
        )
        conn.send_data(stream_id, b"ok", end_stream=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
