from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from benchmarks._compare import (
    build_comparison_result,
    create_worktree,
    methodology_name,
    PROJECT_ROOT,
    run_interleaved_sync,
    summarize_dataclass_runs,
    write_json_output,
)
from benchmarks.task_group import TaskGroupBenchmarkResult


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    baseline_repo: Path
    cleanup = None
    if args.baseline_path is not None:
        baseline_repo = Path(args.baseline_path).resolve()
    else:
        cleanup, baseline_repo = create_worktree(args.baseline_ref, fetch=not args.no_fetch)

    try:
        current_runs, baseline_runs = run_interleaved_sync(
            args.runs,
            lambda index: _run_for_repo(PROJECT_ROOT, f"current-{args.mode}-run-{index + 1}", args),
            lambda index: _run_for_repo(
                baseline_repo,
                f"baseline-{args.baseline_ref}-{args.mode}-run-{index + 1}",
                args,
            ),
            interleave=not args.sequential,
        )
    finally:
        if cleanup is not None:
            cleanup()

    current = summarize_dataclass_runs(f"current-{args.mode}", current_runs)
    baseline = summarize_dataclass_runs(f"baseline-{args.baseline_ref}-{args.mode}", baseline_runs)
    payload = {
        "baseline_ref": args.baseline_ref,
        "runs": args.runs,
        "methodology": methodology_name(sequential=args.sequential),
        "mode": args.mode,
        **build_comparison_result(current, baseline),
    }
    write_json_output(payload, args.output_json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare TaskGroup.spawn_app() overhead across repos."
    )
    parser.add_argument("--baseline-ref", default="upstream/main")
    parser.add_argument("--baseline-path")
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--mode", choices=["asgi", "wsgi"], default="asgi")
    parser.add_argument("--warmup-iterations", type=int, default=200)
    parser.add_argument("--measured-iterations", type=int, default=2000)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--sequential", action="store_true", help="Run all current runs and then all baseline runs."
    )
    parser.add_argument("--output-json")
    return parser


def _run_for_repo(
    server_repo: Path, label: str, args: argparse.Namespace
) -> TaskGroupBenchmarkResult:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(server_repo / "src"), str(PROJECT_ROOT)])
    output = subprocess.check_output(
        [
            sys.executable,
            "-m",
            "benchmarks.task_group",
            "--server-repo",
            str(server_repo),
            "--label",
            label,
            "--mode",
            args.mode,
            "--warmup-iterations",
            str(args.warmup_iterations),
            "--measured-iterations",
            str(args.measured_iterations),
        ],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
    )
    return TaskGroupBenchmarkResult(**json.loads(output))


if __name__ == "__main__":
    raise SystemExit(main())
