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
from benchmarks.h3 import H3BenchmarkConfig, run_h3_benchmark


async def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    baseline_repo: Path
    cleanup = None
    if args.baseline_path is not None:
        baseline_repo = Path(args.baseline_path).resolve()
    else:
        cleanup, baseline_repo = create_worktree(args.baseline_ref, fetch=not args.no_fetch)

    config = H3BenchmarkConfig(
        path=args.path,
        concurrency=args.concurrency,
        total_requests=args.total_requests,
        warmup_requests=args.warmup_requests,
    )

    try:
        current_runs, baseline_runs = await run_interleaved_async(
            args.runs,
            lambda index: run_h3_benchmark(PROJECT_ROOT, f"current-h3-run-{index + 1}", config),
            lambda index: run_h3_benchmark(
                baseline_repo,
                f"baseline-{args.baseline_ref}-h3-run-{index + 1}",
                config,
            ),
            interleave=not args.sequential,
        )
    finally:
        if cleanup is not None:
            cleanup()

    current = summarize_dataclass_runs(
        "current-h3",
        current_runs,
        extra_fields={
            "total_time_s": lambda runs: statistics.median(run.total_time_s for run in runs),
            "requests_per_second": lambda runs: statistics.median(
                run.requests_per_second for run in runs
            ),
        },
    )
    baseline = summarize_dataclass_runs(
        f"baseline-{args.baseline_ref}-h3",
        baseline_runs,
        extra_fields={
            "total_time_s": lambda runs: statistics.median(run.total_time_s for run in runs),
            "requests_per_second": lambda runs: statistics.median(
                run.requests_per_second for run in runs
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
            throughput_field="requests_per_second",
            throughput_delta_field="delta_rps",
            throughput_improvement_field="improvement_rps_percent",
        ),
    }
    write_json_output(payload, args.output_json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare local Hypercorn against upstream in a real HTTP/3 benchmark."
    )
    parser.add_argument("--baseline-ref", default="upstream/main")
    parser.add_argument("--baseline-path")
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--path", default="/fast")
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--total-requests", type=int, default=500)
    parser.add_argument("--warmup-requests", type=int, default=50)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--sequential", action="store_true", help="Run all current runs and then all baseline runs."
    )
    parser.add_argument("--output-json")
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
