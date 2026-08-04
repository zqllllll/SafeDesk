from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_appworld_function_calling import RESULT_COLUMNS


def _sum(results: list[dict[str, Any]], field: str) -> int:
    return sum(int(result.get(field) or 0) for result in results)


def _average(results: list[dict[str, Any]], field: str) -> float:
    if not results:
        return 0.0
    return round(sum(float(result.get(field) or 0) for result in results) / len(results), 4)


def _scenario_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    scenarios: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        task_id = str(result.get("task_id") or "")
        scenario_id = task_id.rsplit("_", 1)[0] if "_" in task_id else task_id
        scenarios.setdefault(scenario_id, []).append(result)
    complete = [items for items in scenarios.values() if len(items) == 3]
    successful = [items for items in complete if all(bool(item.get("success")) for item in items)]
    return {
        "num_scenarios_observed": len(scenarios),
        "num_complete_scenarios": len(complete),
        "num_successful_scenarios": len(successful),
        "scenario_goal_completion": len(successful) / len(complete) if complete else None,
    }


def _load_results(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    task_files = list((root / "shards").glob("shard_*/tasks/*.json"))
    task_files.extend((root / "repairs").glob("*/tasks/*.json"))
    task_files.sort(key=lambda path: path.stat().st_mtime_ns)
    by_run_id: dict[str, dict[str, Any]] = {}
    for path in task_files:
        result = json.loads(path.read_text(encoding="utf-8"))
        key = str(result.get("run_id") or result.get("task_id") or path.stem)
        by_run_id[key] = result
    results = sorted(by_run_id.values(), key=lambda item: str(item.get("task_id") or ""))
    shards = sorted(
        {
            path.parents[1].name
            for path in task_files
            if len(path.parents) > 2 and path.parents[2].name == "shards"
        }
    )
    return results, shards


def aggregate(root: Path) -> dict[str, Any]:
    results, shards = _load_results(root)
    if not results:
        raise RuntimeError(f"No shard task results found under {root}")

    first = results[0]
    successful = [result for result in results if bool(result.get("success"))]
    infra_errors = [result for result in results if result.get("infra_error_type")]
    total_tests = _sum(results, "num_total_tests")
    passed_tests = _sum(results, "num_passed_tests")
    configs = [result.get("config") or {} for result in results]
    max_turns = int(configs[0].get("max_turns") or 0)
    infra_error_types = Counter(str(result["infra_error_type"]) for result in infra_errors)

    valid_durations = [
        float(result.get("duration_seconds") or 0)
        for result in results
        if float(result.get("duration_seconds") or 0) >= 0
    ]
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment_id": first.get("experiment_id"),
        "benchmark": first.get("benchmark"),
        "dataset": first.get("dataset"),
        "model": first.get("model"),
        "source_shards": shards,
        "num_result_tasks": len(results),
        "num_success": len(successful),
        "pass_rate": len(successful) / len(results),
        "task_goal_completion": len(successful) / len(results),
        **_scenario_metrics(results),
        "num_passed_tests": passed_tests,
        "num_total_tests": total_tests,
        "evaluator_test_pass_rate": passed_tests / total_tests if total_tests else None,
        "infra_error_count": len(infra_errors),
        "infra_error_types": dict(sorted(infra_error_types.items())),
        "predictor_tokens": _sum(results, "predictor_tokens"),
        "agent_tokens": _sum(results, "agent_tokens"),
        "total_tokens": _sum(results, "total_tokens"),
        "input_tokens": _sum(results, "input_tokens"),
        "output_tokens": _sum(results, "output_tokens"),
        "avg_tokens_per_task": _average(results, "total_tokens"),
        "total_duration_seconds": round(sum(valid_durations), 4),
        "avg_duration_seconds": (
            round(sum(valid_durations) / len(valid_durations), 4) if valid_durations else None
        ),
        "num_invalid_duration_values": len(results) - len(valid_durations),
        "avg_turns": _average(results, "num_turns"),
        "avg_tool_calls": _average(results, "num_tool_calls"),
        "num_tool_calls": _sum(results, "num_tool_calls"),
        "num_read_tool_calls": _sum(results, "num_read_tool_calls"),
        "num_write_tool_calls": _sum(results, "num_write_tool_calls"),
        "num_invalid_tool_calls": _sum(results, "num_invalid_tool_calls"),
        "num_out_of_schema_tool_calls": _sum(results, "num_out_of_schema_tool_calls"),
        "num_duplicate_tool_calls": _sum(results, "num_duplicate_tool_calls"),
        "num_duplicate_write_actions": _sum(results, "num_duplicate_write_actions"),
        "num_non_executed_tool_calls": _sum(results, "num_non_executed_tool_calls"),
        "num_without_completion": sum(result.get("completion_step") is None for result in results),
        "num_reached_max_turns": sum(
            max_turns > 0 and int(result.get("num_turns") or 0) >= max_turns for result in results
        ),
        "avg_predicted_api_count": _average(results, "predicted_api_count"),
        "config": configs[0] if all(config == configs[0] for config in configs) else None,
        "config_consistent": all(config == configs[0] for config in configs),
        "result_columns": RESULT_COLUMNS,
    }

    (root / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (root / "results.jsonl").open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    with (root / "results.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for result in results:
            writer.writerow({column: result.get(column) for column in RESULT_COLUMNS})
    (root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate sharded AppWorld result files.")
    parser.add_argument("result_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(aggregate(args.result_dir.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
