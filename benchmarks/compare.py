from __future__ import annotations

import argparse
import asyncio
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
from benchmarks.run_load import BenchmarkConfig, run_benchmark


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

    baseline_repo: Path
    cleanup = None
    if args.baseline_path is not None:
        baseline_repo = Path(args.baseline_path).resolve()
    else:
        cleanup, baseline_repo = create_worktree(args.baseline_ref, fetch=not args.no_fetch)

    try:
        current_runs, baseline_runs = await run_interleaved_async(
            args.runs,
            lambda index: run_benchmark(PROJECT_ROOT, f"current-run-{index + 1}", config),
            lambda index: run_benchmark(
                baseline_repo,
                f"baseline:{args.baseline_ref}-run-{index + 1}",
                config,
            ),
            interleave=not args.sequential,
        )
    finally:
        if cleanup is not None:
            cleanup()

    current = summarize_dataclass_runs("current", current_runs)
    baseline = summarize_dataclass_runs(f"baseline:{args.baseline_ref}", baseline_runs)
    payload = {
        "runs": args.runs,
        "methodology": methodology_name(sequential=args.sequential),
        **build_comparison_result(current, baseline),
    }
    write_json_output(payload, args.output_json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare local Hypercorn against upstream for the H2 HoL benchmark."
    )
    parser.add_argument("--baseline-ref", default="upstream/main")
    parser.add_argument(
        "--baseline-path",
        help="Optional existing repo path to use as baseline instead of creating a worktree.",
    )
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--warmup-iterations", type=int, default=2)
    parser.add_argument("--measured-iterations", type=int, default=10)
    parser.add_argument("--fast-streams", type=int, default=5)
    parser.add_argument("--slow-chunks", type=int, default=12)
    parser.add_argument("--slow-chunk-size", type=int, default=4096)
    parser.add_argument("--slow-delay-ms", type=int, default=25)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument(
        "--sequential", action="store_true", help="Run all current runs and then all baseline runs."
    )
    parser.add_argument("--output-json")
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
