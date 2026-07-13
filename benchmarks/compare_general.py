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
from benchmarks.general import GeneralBenchmarkConfig, run_general_benchmark


async def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    baseline_repo: Path
    cleanup = None
    if args.baseline_path is not None:
        baseline_repo = Path(args.baseline_path).resolve()
    else:
        cleanup, baseline_repo = create_worktree(args.baseline_ref, fetch=not args.no_fetch)

    try:
        results = {}
        scenarios = [
            ("http1", "1.1", False),
            ("http1_tls", "1.1", True),
            ("http2", "2", True),
        ]
        for scenario_name, http_version, tls in scenarios:
            config = GeneralBenchmarkConfig(
                http_version=http_version,
                tls=tls,
                path=args.path,
                concurrency=args.concurrency,
                total_requests=args.total_requests,
                warmup_requests=args.warmup_requests,
            )
            current_runs, baseline_runs = await run_interleaved_async(
                args.runs,
                lambda index: run_general_benchmark(
                    PROJECT_ROOT,
                    f"current-{scenario_name}-run-{index + 1}",
                    config,
                ),
                lambda index: run_general_benchmark(
                    baseline_repo,
                    f"baseline-{args.baseline_ref}-{scenario_name}-run-{index + 1}",
                    config,
                ),
                interleave=not args.sequential,
            )
            current = summarize_dataclass_runs(
                f"current-{scenario_name}",
                current_runs,
                extra_fields={
                    "total_time_s": lambda runs: statistics.median(
                        run.total_time_s for run in runs
                    ),
                    "requests_per_second": lambda runs: statistics.median(
                        run.requests_per_second for run in runs
                    ),
                },
            )
            baseline = summarize_dataclass_runs(
                f"baseline-{args.baseline_ref}-{scenario_name}",
                baseline_runs,
                extra_fields={
                    "total_time_s": lambda runs: statistics.median(
                        run.total_time_s for run in runs
                    ),
                    "requests_per_second": lambda runs: statistics.median(
                        run.requests_per_second for run in runs
                    ),
                },
            )
            results[scenario_name] = build_comparison_result(
                current,
                baseline,
                throughput_field="requests_per_second",
                throughput_delta_field="delta_rps",
                throughput_improvement_field="improvement_rps_percent",
            )
    finally:
        if cleanup is not None:
            cleanup()

    payload = {
        "baseline_ref": args.baseline_ref,
        "runs": args.runs,
        "methodology": methodology_name(sequential=args.sequential),
        "concurrency": args.concurrency,
        "total_requests": args.total_requests,
        "warmup_requests": args.warmup_requests,
        "results": results,
    }
    write_json_output(payload, args.output_json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare local Hypercorn against upstream in general HTTP benchmarks."
    )
    parser.add_argument("--baseline-ref", default="upstream/main")
    parser.add_argument("--baseline-path")
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--path", default="/fast")
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--total-requests", type=int, default=500)
    parser.add_argument("--warmup-requests", type=int, default=50)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument(
        "--sequential", action="store_true", help="Run all current runs and then all baseline runs."
    )
    parser.add_argument("--output-json")
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
