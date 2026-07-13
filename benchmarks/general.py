from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from benchmarks._runtime import percentile, PROJECT_ROOT, ServerProcess


@dataclass
class GeneralBenchmarkConfig:
    http_version: str
    tls: bool
    path: str
    concurrency: int
    total_requests: int
    warmup_requests: int


@dataclass
class GeneralBenchmarkResult:
    target_label: str
    server_repo: str
    http_version: str
    tls: bool
    path: str
    concurrency: int
    total_requests: int
    warmup_requests: int
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
    result = await run_general_benchmark(
        server_repo=Path(args.server_repo).resolve(),
        label=args.label,
        config=GeneralBenchmarkConfig(
            http_version=args.http_version,
            tls=(args.tls or args.http_version == "2"),
            path=args.path,
            concurrency=args.concurrency,
            total_requests=args.total_requests,
            warmup_requests=args.warmup_requests,
        ),
    )
    payload = asdict(result)
    if args.output_json is not None:
        Path(args.output_json).write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a general Hypercorn benchmark over /fast.")
    parser.add_argument("--server-repo", default=str(PROJECT_ROOT))
    parser.add_argument("--label", default="local")
    parser.add_argument("--http-version", choices=["1.1", "2"], default="1.1")
    parser.add_argument(
        "--tls", action="store_true", help="Use TLS for HTTP/1.1 runs. HTTP/2 always uses TLS."
    )
    parser.add_argument("--path", default="/fast")
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--total-requests", type=int, default=500)
    parser.add_argument("--warmup-requests", type=int, default=50)
    parser.add_argument("--output-json")
    return parser


async def run_general_benchmark(
    server_repo: Path, label: str, config: GeneralBenchmarkConfig
) -> GeneralBenchmarkResult:
    if config.http_version == "2" and not config.tls:
        raise ValueError("HTTP/2 benchmark requires TLS")

    async with ServerProcess(server_repo, tls=config.tls) as server:
        await run_requests(
            server.port,
            config.http_version,
            config.tls,
            config.path,
            min(config.warmup_requests, config.total_requests),
            max(1, min(config.concurrency, config.warmup_requests or config.total_requests)),
        )
        total_time_s, samples_ms = await run_requests(
            server.port,
            config.http_version,
            config.tls,
            config.path,
            config.total_requests,
            config.concurrency,
        )

    return GeneralBenchmarkResult(
        target_label=label,
        server_repo=str(server_repo),
        http_version=config.http_version,
        tls=config.tls,
        path=config.path,
        concurrency=config.concurrency,
        total_requests=config.total_requests,
        warmup_requests=config.warmup_requests,
        total_time_s=total_time_s,
        requests_per_second=(config.total_requests / total_time_s) if total_time_s > 0 else 0.0,
        samples_ms=samples_ms,
        mean_ms=statistics.fmean(samples_ms),
        median_ms=statistics.median(samples_ms),
        p95_ms=percentile(samples_ms, 0.95),
        minimum_ms=min(samples_ms),
        maximum_ms=max(samples_ms),
    )


async def run_requests(
    port: int, http_version: str, tls: bool, path: str, total_requests: int, concurrency: int
) -> tuple[float, list[float]]:
    if total_requests <= 0:
        return 0.0, []

    scheme = "https" if tls else "http"
    url = f"{scheme}://127.0.0.1:{port}{path}"
    queue: asyncio.Queue[int | None] = asyncio.Queue()
    for index in range(total_requests):
        queue.put_nowait(index)
    for _ in range(concurrency):
        queue.put_nowait(None)

    latencies_ms: list[float] = []
    lock = asyncio.Lock()
    limits = httpx.Limits(
        max_connections=1 if http_version == "2" else concurrency,
        max_keepalive_connections=1 if http_version == "2" else concurrency,
    )
    start = time.perf_counter()
    async with httpx.AsyncClient(
        http2=(http_version == "2"),
        verify=False if tls else True,
        timeout=10.0,
        limits=limits,
    ) as client:

        async def worker() -> None:
            while True:
                item = await queue.get()
                if item is None:
                    return
                req_start = time.perf_counter()
                response = await client.get(url)
                response.raise_for_status()
                elapsed_ms = (time.perf_counter() - req_start) * 1000
                async with lock:
                    latencies_ms.append(elapsed_ms)

        await asyncio.gather(*(worker() for _ in range(concurrency)))
    total_time_s = time.perf_counter() - start
    return total_time_s, latencies_ms


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
