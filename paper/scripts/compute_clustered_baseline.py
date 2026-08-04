"""Compute scenario-clustered bootstrap intervals for measured AppWorld TGC."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np


PAPER = Path(__file__).resolve().parents[1]
INPUT = PAPER / "data" / "appworld_task_level_derived.csv"
OUTPUT = PAPER / "data" / "appworld_clustered_intervals.json"
SEED = 20260723
SAMPLES = 10_000


def scenario_id(task_id: str) -> str:
    base, variant = task_id.rsplit("_", 1)
    if variant not in {"1", "2", "3"}:
        raise ValueError(f"Unexpected AppWorld task id: {task_id}")
    return base


def as_binary(value: str) -> int:
    normalized = value.strip().lower()
    if normalized in {"1", "true"}:
        return 1
    if normalized in {"0", "false"}:
        return 0
    raise ValueError(f"Unexpected binary value: {value}")


def interval(rows: list[dict[str, str]], rng: np.random.Generator) -> dict[str, float | int]:
    groups: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        groups[scenario_id(row["task_id"])].append(as_binary(row["success"]))
    if any(len(values) != 3 for values in groups.values()):
        raise ValueError("Every included scenario must contain exactly three task variants")
    scenario_values = np.asarray(list(groups.values()), dtype=float)
    draws = rng.integers(0, len(scenario_values), size=(SAMPLES, len(scenario_values)))
    estimates = scenario_values[draws].mean(axis=(1, 2)) * 100.0
    point = scenario_values.mean() * 100.0
    low, high = np.percentile(estimates, [2.5, 97.5])
    return {
        "tasks": len(rows),
        "scenarios": len(groups),
        "successes": sum(as_binary(row["success"]) for row in rows),
        "tgc_percent": round(float(point), 2),
        "ci95_low": round(float(low), 2),
        "ci95_high": round(float(high), 2),
    }


def main() -> None:
    with INPUT.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rng = np.random.default_rng(SEED)
    result = {
        "method": "scenario-clustered percentile bootstrap",
        "samples": SAMPLES,
        "seed": SEED,
        "overall": interval(rows, rng),
        "difficulty": {
            level: interval([row for row in rows if row["difficulty"] == level], rng)
            for level in ("1", "2", "3")
        },
    }
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
