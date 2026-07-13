from __future__ import annotations

import argparse
import asyncio
import statistics
from pathlib import Path

from benchmarks._compare import (
    build_comparison_result,
    create_worktree,
    methodology_name,
    PROJECT_ROOT,
    run_interleaved_async,
    summarize_dataclass_runs,
    write_json_output,
)
from benchmarks.ws import run_ws_benchmark, WebsocketBenchmarkConfig


async def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    baseline_repo: Path
    cleanup = None
    if args.baseline_path is not None:
        baseline_repo = Path(args.baseline_path).resolve()
    else:
        cleanup, baseline_repo = create_worktree(args.baseline_ref, fetch=not args.no_fetch)

    config = WebsocketBenchmarkConfig(
        tls=args.tls,
        path=args.path,
        warmup_messages=args.warmup_messages,
        measured_messages=args.measured_messages,
        payload_size=args.payload_size,
    )

    try:
        current_runs, baseline_runs = await run_interleaved_async(
            args.runs,
            lambda index: run_ws_benchmark(PROJECT_ROOT, f"current-ws-run-{index + 1}", config),
            lambda index: run_ws_benchmark(
                baseline_repo,
                f"baseline-{args.baseline_ref}-ws-run-{index + 1}",
                config,
            ),
            interleave=not args.sequential,
        )
    finally:
        if cleanup is not None:
            cleanup()

    current = summarize_dataclass_runs(
        "current-ws",
        current_runs,
        extra_fields={
            "total_time_s": lambda runs: statistics.median(run.total_time_s for run in runs),
            "messages_per_second": lambda runs: statistics.median(
                run.messages_per_second for run in runs
            ),
        },
    )
    baseline = summarize_dataclass_runs(
        f"baseline-{args.baseline_ref}-ws",
        baseline_runs,
        extra_fields={
            "total_time_s": lambda runs: statistics.median(run.total_time_s for run in runs),
            "messages_per_second": lambda runs: statistics.median(
                run.messages_per_second for run in runs
            ),
        },
    )
    payload = {
        "baseline_ref": args.baseline_ref,
        "runs": args.runs,
        "methodology": methodology_name(sequential=args.sequential),
        **build_comparison_result(
            current,
            baseline,
            throughput_field="messages_per_second",
            throughput_delta_field="delta_messages_per_second",
            throughput_improvement_field="improvement_messages_per_second_percent",
        ),
    }
    write_json_output(payload, args.output_json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare local Hypercorn against upstream in websocket echo benchmarks."
    )
    parser.add_argument("--baseline-ref", default="upstream/main")
    parser.add_argument("--baseline-path")
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--tls", action="store_true")
    parser.add_argument("--path", default="/ws")
    parser.add_argument("--warmup-messages", type=int, default=50)
    parser.add_argument("--measured-messages", type=int, default=300)
    parser.add_argument("--payload-size", type=int, default=65536)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument(
        "--sequential", action="store_true", help="Run all current runs and then all baseline runs."
    )
    parser.add_argument("--output-json")
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
