from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from benchmarks._runtime import percentile, PROJECT_ROOT
from hypercorn.app_wrappers import ASGIWrapper, WSGIWrapper
from hypercorn.asyncio.task_group import TaskGroup
from hypercorn.config import Config
from tests.wsgi_applications import wsgi_app_simple

HTTP_SCOPE = {
    "type": "http",
    "asgi": {"spec_version": "2.1", "version": "3.0"},
    "http_version": "1.1",
    "method": "GET",
    "scheme": "http",
    "path": "/",
    "raw_path": b"/",
    "query_string": b"",
    "root_path": "",
    "headers": [(b"host", b"localhost")],
    "client": ("127.0.0.1", 1234),
    "server": ("127.0.0.1", 8000),
    "state": {},
}


@dataclass
class TaskGroupBenchmarkConfig:
    mode: str
    warmup_iterations: int
    measured_iterations: int


@dataclass
class TaskGroupBenchmarkResult:
    target_label: str
    server_repo: str
    mode: str
    warmup_iterations: int
    measured_iterations: int
    samples_ms: list[float]
    mean_ms: float
    median_ms: float
    p95_ms: float
    minimum_ms: float
    maximum_ms: float


async def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    result = await run_task_group_benchmark(
        Path(args.server_repo).resolve(),
        args.label,
        TaskGroupBenchmarkConfig(
            mode=args.mode,
            warmup_iterations=args.warmup_iterations,
            measured_iterations=args.measured_iterations,
        ),
    )
    payload = asdict(result)
    if args.output_json is not None:
        Path(args.output_json).write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Measure TaskGroup.spawn_app() overhead.")
    parser.add_argument("--server-repo", default=str(PROJECT_ROOT))
    parser.add_argument("--label", default="local")
    parser.add_argument("--mode", choices=["asgi", "wsgi"], default="asgi")
    parser.add_argument("--warmup-iterations", type=int, default=200)
    parser.add_argument("--measured-iterations", type=int, default=2000)
    parser.add_argument("--output-json")
    return parser


async def run_task_group_benchmark(
    server_repo: Path,
    label: str,
    config: TaskGroupBenchmarkConfig,
) -> TaskGroupBenchmarkResult:
    loop = asyncio.get_running_loop()
    benchmark = _TaskGroupBenchmark(config.mode)
    samples_ms = []
    async with TaskGroup(loop) as task_group:
        for _ in range(config.warmup_iterations):
            await benchmark.run_iteration(task_group)
        for _ in range(config.measured_iterations):
            start = time.perf_counter()
            await benchmark.run_iteration(task_group)
            samples_ms.append((time.perf_counter() - start) * 1000)

    return TaskGroupBenchmarkResult(
        target_label=label,
        server_repo=str(server_repo),
        mode=config.mode,
        warmup_iterations=config.warmup_iterations,
        measured_iterations=config.measured_iterations,
        samples_ms=samples_ms,
        mean_ms=statistics.fmean(samples_ms),
        median_ms=statistics.median(samples_ms),
        p95_ms=percentile(samples_ms, 0.95),
        minimum_ms=min(samples_ms),
        maximum_ms=max(samples_ms),
    )


class _TaskGroupBenchmark:
    def __init__(self, mode: str) -> None:
        self._config = Config()
        if mode == "asgi":
            self._app = ASGIWrapper(self._asgi_app)
            self._message = {"type": "http.disconnect"}
        else:
            self._app = WSGIWrapper(wsgi_app_simple, 2**16)
            self._message = {"type": "http.request", "body": b"", "more_body": False}

    async def run_iteration(self, task_group: TaskGroup) -> None:
        send_queue: asyncio.Queue = asyncio.Queue()
        app_put = await task_group.spawn_app(self._app, self._config, HTTP_SCOPE, send_queue.put)
        await app_put(self._message)
        while True:
            message = await send_queue.get()
            if message is None:
                return

    async def _asgi_app(self, scope, receive, send) -> None:
        await receive()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
