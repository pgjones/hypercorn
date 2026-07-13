from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Sequence, TypeVar

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INTERLEAVED_METHODOLOGY = "interleaved median-of-runs"
SEQUENTIAL_METHODOLOGY = "sequential median-of-runs"

T = TypeVar("T")


def create_worktree(ref: str, fetch: bool) -> tuple[Callable[[], None], Path]:
    if fetch:
        subprocess.run(["git", "fetch", "upstream"], cwd=PROJECT_ROOT, check=True)

    tempdir = Path(tempfile.mkdtemp(prefix="hypercorn-bench-"))
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(tempdir), ref],
        cwd=PROJECT_ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def cleanup() -> None:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(tempdir)],
            cwd=PROJECT_ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        shutil.rmtree(tempdir, ignore_errors=True)

    return cleanup, tempdir


def percentage_improvement(current: float, baseline: float) -> float:
    if baseline == 0:
        return 0.0
    return ((baseline - current) / baseline) * 100


def percentage_growth(current: float, baseline: float) -> float:
    if baseline == 0:
        return 0.0
    return ((current - baseline) / baseline) * 100


def methodology_name(*, sequential: bool) -> str:
    return SEQUENTIAL_METHODOLOGY if sequential else INTERLEAVED_METHODOLOGY


async def run_interleaved_async(
    runs: int,
    current_runner: Callable[[int], Any],
    baseline_runner: Callable[[int], Any],
    *,
    interleave: bool,
) -> tuple[list[Any], list[Any]]:
    current_runs: list[Any] = []
    baseline_runs: list[Any] = []
    for index in range(runs):
        if interleave and (index % 2 == 1):
            baseline_runs.append(await baseline_runner(index))
            current_runs.append(await current_runner(index))
        else:
            current_runs.append(await current_runner(index))
            baseline_runs.append(await baseline_runner(index))
    return current_runs, baseline_runs


def run_interleaved_sync(
    runs: int,
    current_runner: Callable[[int], T],
    baseline_runner: Callable[[int], T],
    *,
    interleave: bool,
) -> tuple[list[T], list[T]]:
    current_runs: list[T] = []
    baseline_runs: list[T] = []
    for index in range(runs):
        if interleave and (index % 2 == 1):
            baseline_runs.append(baseline_runner(index))
            current_runs.append(current_runner(index))
        else:
            current_runs.append(current_runner(index))
            baseline_runs.append(baseline_runner(index))
    return current_runs, baseline_runs


def summarize_dataclass_runs(
    label: str,
    runs: Sequence[T],
    *,
    extra_fields: dict[str, Callable[[Sequence[T]], Any]] | None = None,
) -> T:
    if not runs:
        raise ValueError("Expected at least one benchmark run")

    overrides: dict[str, Any] = {
        "target_label": label,
        "samples_ms": [sample for run in runs for sample in getattr(run, "samples_ms")],
        "mean_ms": _median(getattr(run, "mean_ms") for run in runs),
        "median_ms": _median(getattr(run, "median_ms") for run in runs),
        "p95_ms": _median(getattr(run, "p95_ms") for run in runs),
        "minimum_ms": _median(getattr(run, "minimum_ms") for run in runs),
        "maximum_ms": _median(getattr(run, "maximum_ms") for run in runs),
    }
    if extra_fields is not None:
        for field, aggregator in extra_fields.items():
            overrides[field] = aggregator(runs)
    return replace(runs[0], **overrides)


def build_comparison_result(
    current: Any,
    baseline: Any,
    *,
    throughput_field: str | None = None,
    throughput_delta_field: str | None = None,
    throughput_improvement_field: str | None = None,
) -> dict[str, Any]:
    payload = {
        "current": current.__dict__,
        "baseline": baseline.__dict__,
        "delta_mean_ms": current.mean_ms - baseline.mean_ms,
        "delta_median_ms": current.median_ms - baseline.median_ms,
        "delta_p95_ms": current.p95_ms - baseline.p95_ms,
        "improvement_mean_percent": percentage_improvement(current.mean_ms, baseline.mean_ms),
        "improvement_median_percent": percentage_improvement(current.median_ms, baseline.median_ms),
        "improvement_p95_percent": percentage_improvement(current.p95_ms, baseline.p95_ms),
    }
    if throughput_field is not None:
        current_value = getattr(current, throughput_field)
        baseline_value = getattr(baseline, throughput_field)
        payload[throughput_delta_field or f"delta_{throughput_field}"] = (
            current_value - baseline_value
        )
        payload[throughput_improvement_field or f"improvement_{throughput_field}_percent"] = (
            percentage_growth(current_value, baseline_value)
        )
    return payload


def write_json_output(payload: dict[str, Any], output_json: str | None) -> None:
    encoded = json.dumps(payload, indent=2) + "\n"
    print(encoded, end="")
    if output_json is not None:
        Path(output_json).write_text(encoded)


def _median(values) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2
