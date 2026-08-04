from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECTION_VERSION = "safedesk_target_v1"
DATA_STATUS = "synthetic_projection_not_observed"
EXPECTED_TASKS = 417
EXPECTED_SCENARIOS = 139
TARGET_SUCCESSES = 235
TARGET_MAX_TURN_TASKS = 54
TARGET_GATE_BLOCKS = 15
TARGET_NO_COMPLETION_TASKS = 88
TARGET_TOKEN_REDUCTION = 0.38
TARGET_INVALID_CALL_REDUCTION = 0.60
TARGET_OUT_OF_SCHEMA_REDUCTION = 0.75
TARGET_DUPLICATE_CALL_REDUCTION = 0.60
TARGET_DUPLICATE_WRITE_REDUCTION = 0.75
MAX_TURNS = 50

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "benchmarks" / "results" / "appworld_deepseek_v4_flash_no_thinking_test_challenge_aligned"
DEFAULT_OUTPUT = ROOT / "benchmarks" / "projections" / "appworld_test_challenge_safedesk_target_v1"

OUTPUT_COLUMNS = [
    "data_status",
    "projection_version",
    "source_experiment_id",
    "source_run_id",
    "task_id",
    "scenario_id",
    "model",
    "primary_intervention",
    "projection_reason",
    "baseline_success",
    "projected_success",
    "baseline_passed_tests",
    "projected_passed_tests",
    "num_total_tests",
    "baseline_completion_called",
    "projected_completion_called",
    "projected_completion_allowed",
    "projected_completion_gate_blocked",
    "baseline_turns",
    "projected_turns",
    "baseline_model_tool_calls",
    "projected_model_tool_calls",
    "baseline_tool_calls",
    "projected_tool_calls",
    "baseline_read_tool_calls",
    "projected_read_tool_calls",
    "baseline_write_tool_calls",
    "projected_write_tool_calls",
    "baseline_other_tool_calls",
    "projected_other_tool_calls",
    "baseline_invalid_tool_calls",
    "projected_invalid_tool_calls",
    "baseline_out_of_schema_tool_calls",
    "projected_out_of_schema_tool_calls",
    "baseline_duplicate_tool_calls",
    "projected_duplicate_tool_calls",
    "baseline_duplicate_write_actions",
    "projected_duplicate_write_actions",
    "baseline_total_tokens",
    "projected_total_tokens",
    "baseline_input_tokens",
    "baseline_output_tokens",
    "projected_input_tokens",
    "projected_output_tokens",
    "baseline_duration_seconds",
    "projected_duration_seconds",
    "projected_num_verification_checks",
    "projected_num_verification_failures",
    "projected_num_recovery_attempts",
    "projected_recovery_success",
    "projected_unintended_side_effects",
]


def _int(row: dict[str, str], key: str) -> int:
    value = row.get(key, "").strip()
    return int(float(value)) if value else 0


def _float(row: dict[str, str], key: str) -> float | None:
    value = row.get(key, "").strip()
    return float(value) if value else None


def _bool(row: dict[str, str], key: str) -> bool:
    return row.get(key, "").strip().lower() == "true"


def _completion_called(row: dict[str, str]) -> bool:
    return bool(row.get("completion_step", "").strip())


def _scenario_id(task_id: str) -> str:
    return task_id.rsplit("_", 1)[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows.sort(key=lambda row: row["task_id"])
    return rows


def _recoverability_score(row: dict[str, str], scenario_successes: dict[str, int]) -> float:
    total_tests = max(_int(row, "num_total_tests"), 1)
    passed_ratio = _int(row, "num_passed_tests") / total_tests
    score = passed_ratio * 100.0
    score += 18.0 if _completion_called(row) else 0.0
    score += 14.0 if _int(row, "num_out_of_schema_tool_calls") else 0.0
    score += 10.0 if _int(row, "num_duplicate_tool_calls") else 0.0
    score += 12.0 if _int(row, "num_duplicate_write_actions") else 0.0
    score += 8.0 if _int(row, "num_turns") >= MAX_TURNS else 0.0
    score += 12.0 if scenario_successes[_scenario_id(row["task_id"])] == 2 else 0.0
    score += 4.0 if scenario_successes[_scenario_id(row["task_id"])] == 1 else 0.0
    return score


def _primary_intervention(row: dict[str, str]) -> str:
    if _int(row, "num_duplicate_write_actions") > 0:
        return "tool_execution_guard"
    if _completion_called(row):
        return "state_and_verification"
    if _int(row, "num_turns") >= MAX_TURNS or _int(row, "num_duplicate_tool_calls") > 0:
        return "recovery_controller"
    if _int(row, "num_out_of_schema_tool_calls") > 0 or _int(row, "num_invalid_tool_calls") > 0:
        return "tool_execution_guard"
    return "context_manager"


def _projection_reason(row: dict[str, str], converted: bool, gate_blocked: bool) -> str:
    if converted:
        intervention = _primary_intervention(row)
        reasons = {
            "state_and_verification": "failed completion is repaired after evidence-based verification",
            "tool_execution_guard": "schema or duplicate-write failure is repaired before execution",
            "recovery_controller": "stalled execution is re-planned within a bounded recovery budget",
            "context_manager": "context control prevents long-trajectory degradation",
        }
        return reasons[intervention]
    if gate_blocked:
        return "unsafe completion is blocked; task remains failed in this projection"
    if _bool(row, "success"):
        return "baseline success is preserved without regression"
    return "failure remains unresolved in the conservative point projection"


def _allocate_proportional(total: int, weights: list[float], caps: list[int] | None = None) -> list[int]:
    if total < 0:
        raise ValueError("allocation total must be non-negative")
    if caps is not None and total > sum(caps):
        raise ValueError("allocation total exceeds capacity")
    if not weights:
        return []
    weight_sum = sum(weights)
    if weight_sum <= 0:
        if total != 0:
            raise ValueError("cannot allocate a positive total with zero weights")
        return [0] * len(weights)

    raw = [total * weight / weight_sum for weight in weights]
    values = [math.floor(value) for value in raw]
    if caps is not None:
        values = [min(value, cap) for value, cap in zip(values, caps, strict=True)]

    remaining = total - sum(values)
    order = sorted(
        range(len(weights)),
        key=lambda index: (raw[index] - math.floor(raw[index]), weights[index], -index),
        reverse=True,
    )
    while remaining:
        progressed = False
        for index in order:
            if caps is not None and values[index] >= caps[index]:
                continue
            values[index] += 1
            remaining -= 1
            progressed = True
            if remaining == 0:
                break
        if not progressed:
            raise ValueError("allocation could not satisfy the requested total")
    return values


def _target_count(rows: list[dict[str, str]], field: str, reduction: float) -> tuple[int, list[int]]:
    baseline = [_int(row, field) for row in rows]
    target = round(sum(baseline) * (1.0 - reduction))
    return target, _allocate_proportional(target, [float(value) for value in baseline], baseline)


def _project_tool_counts(
    row: dict[str, str], converted: bool, projected_success: bool, kept_at_max_turns: bool
) -> tuple[int, int, int]:
    if _bool(row, "success"):
        factor = 0.90
    elif converted:
        factor = 0.58
    elif kept_at_max_turns:
        factor = 0.82
    elif _int(row, "num_turns") >= MAX_TURNS:
        factor = 0.52
    else:
        factor = 0.74

    read_calls = round(_int(row, "num_read_tool_calls") * factor)
    write_factor = 0.95 if projected_success else 0.78
    write_calls = round(_int(row, "num_write_tool_calls") * write_factor)
    other_calls = round(_int(row, "num_other_tool_calls") * factor)
    return max(read_calls, 0), max(write_calls, 0), max(other_calls, 0)


def _build_projection(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    baseline_successes = [row for row in rows if _bool(row, "success")]
    failures = [row for row in rows if not _bool(row, "success")]
    conversion_count = TARGET_SUCCESSES - len(baseline_successes)
    if conversion_count <= 0 or conversion_count > len(failures):
        raise ValueError("TARGET_SUCCESSES is incompatible with the baseline")

    scenario_successes: dict[str, int] = defaultdict(int)
    for row in baseline_successes:
        scenario_successes[_scenario_id(row["task_id"])] += 1
    ranked_failures = sorted(
        failures,
        key=lambda row: (-_recoverability_score(row, scenario_successes), row["task_id"]),
    )
    converted_ids = {row["task_id"] for row in ranked_failures[:conversion_count]}

    remaining_failures = [row for row in failures if row["task_id"] not in converted_ids]
    failed_completion_attempt_count = len(remaining_failures) - TARGET_NO_COMPLETION_TASKS
    if failed_completion_attempt_count < TARGET_GATE_BLOCKS:
        raise ValueError("completion attempt target cannot accommodate all gate blocks")
    completion_ranked = sorted(
        remaining_failures,
        key=lambda row: (
            not _completion_called(row),
            -_recoverability_score(row, scenario_successes),
            row["task_id"],
        ),
    )
    failed_completion_attempt_ids = {row["task_id"] for row in completion_ranked[:failed_completion_attempt_count]}
    gate_ranked = sorted(
        (row for row in remaining_failures if row["task_id"] in failed_completion_attempt_ids),
        key=lambda row: (
            _int(row, "num_passed_tests") / max(_int(row, "num_total_tests"), 1),
            -_int(row, "num_duplicate_write_actions"),
            -_int(row, "num_invalid_tool_calls"),
            row["task_id"],
        ),
    )
    gate_blocked_ids = {row["task_id"] for row in gate_ranked[:TARGET_GATE_BLOCKS]}

    remaining_max_turn_failures = [
        row for row in failures if row["task_id"] not in converted_ids and _int(row, "num_turns") >= MAX_TURNS
    ]
    if len(remaining_max_turn_failures) < TARGET_MAX_TURN_TASKS:
        raise ValueError("not enough remaining max-turn failures for the requested projection")
    kept_max_ids = {
        row["task_id"]
        for row in sorted(
            remaining_max_turn_failures,
            key=lambda row: (_recoverability_score(row, scenario_successes), row["task_id"]),
        )[:TARGET_MAX_TURN_TASKS]
    }

    target_invalid, projected_invalid = _target_count(rows, "num_invalid_tool_calls", TARGET_INVALID_CALL_REDUCTION)
    target_out_schema, projected_out_schema = _target_count(
        rows, "num_out_of_schema_tool_calls", TARGET_OUT_OF_SCHEMA_REDUCTION
    )
    target_duplicates, projected_duplicates = _target_count(
        rows, "num_duplicate_tool_calls", TARGET_DUPLICATE_CALL_REDUCTION
    )
    target_duplicate_writes, projected_duplicate_writes = _target_count(
        rows, "num_duplicate_write_actions", TARGET_DUPLICATE_WRITE_REDUCTION
    )

    preliminary_agent_weights: list[float] = []
    for row in rows:
        task_id = row["task_id"]
        if _bool(row, "success"):
            factor = 0.90
        elif task_id in converted_ids:
            factor = 0.42
        elif _int(row, "num_turns") >= MAX_TURNS and task_id not in kept_max_ids:
            factor = 0.44
        elif _int(row, "num_duplicate_tool_calls") > 0:
            factor = 0.58
        else:
            factor = 0.72
        preliminary_agent_weights.append(max(_int(row, "agent_tokens") * factor, 1.0))

    baseline_total_tokens = sum(_int(row, "total_tokens") for row in rows)
    predictor_total = sum(_int(row, "predictor_tokens") for row in rows)
    target_total_tokens = round(baseline_total_tokens * (1.0 - TARGET_TOKEN_REDUCTION))
    projected_agent_tokens = _allocate_proportional(target_total_tokens - predictor_total, preliminary_agent_weights)

    projected: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        task_id = row["task_id"]
        baseline_success = _bool(row, "success")
        converted = task_id in converted_ids
        projected_success = baseline_success or converted
        gate_blocked = task_id in gate_blocked_ids
        kept_at_max_turns = task_id in kept_max_ids

        baseline_turns = _int(row, "num_turns")
        if baseline_success:
            projected_turns = max(1, round(baseline_turns * 0.95))
        elif converted:
            projected_turns = max(3, min(24, round(baseline_turns * 0.48)))
        elif kept_at_max_turns:
            projected_turns = MAX_TURNS
        elif baseline_turns >= MAX_TURNS:
            projected_turns = 35
        else:
            projected_turns = max(1, round(baseline_turns * 0.85))

        read_calls, write_calls, other_calls = _project_tool_counts(
            row, converted, projected_success, kept_at_max_turns
        )
        tool_calls = read_calls + write_calls + other_calls
        out_schema_calls = min(projected_out_schema[index], projected_invalid[index])
        model_tool_calls = tool_calls + out_schema_calls
        invalid_calls = min(projected_invalid[index], model_tool_calls)
        duplicate_calls = min(projected_duplicates[index], tool_calls)
        duplicate_writes = min(projected_duplicate_writes[index], write_calls)

        predictor_tokens = _int(row, "predictor_tokens")
        total_tokens = predictor_tokens + projected_agent_tokens[index]
        baseline_total = max(_int(row, "total_tokens"), 1)
        baseline_input_ratio = _int(row, "input_tokens") / baseline_total
        input_tokens = min(round(total_tokens * baseline_input_ratio), total_tokens)
        output_tokens = total_tokens - input_tokens

        baseline_duration = _float(row, "duration_seconds")
        if baseline_duration is None or baseline_duration < 0:
            projected_duration: float | None = None
        else:
            projected_duration = round(baseline_duration * projected_turns / max(baseline_turns, 1), 4)

        completion_called = projected_success or task_id in failed_completion_attempt_ids
        completion_allowed = completion_called and not gate_blocked
        verification_checks = 1 + write_calls
        verification_failures = 0 if baseline_success else 1
        recovery_attempted = converted or (baseline_turns >= MAX_TURNS and not kept_at_max_turns)

        projected.append(
            {
                "data_status": DATA_STATUS,
                "projection_version": PROJECTION_VERSION,
                "source_experiment_id": row["experiment_id"],
                "source_run_id": row["run_id"],
                "task_id": task_id,
                "scenario_id": _scenario_id(task_id),
                "model": row["model"],
                "primary_intervention": _primary_intervention(row) if converted else "none",
                "projection_reason": _projection_reason(row, converted, gate_blocked),
                "baseline_success": baseline_success,
                "projected_success": projected_success,
                "baseline_passed_tests": _int(row, "num_passed_tests"),
                "projected_passed_tests": (
                    _int(row, "num_total_tests") if projected_success else _int(row, "num_passed_tests")
                ),
                "num_total_tests": _int(row, "num_total_tests"),
                "baseline_completion_called": _completion_called(row),
                "projected_completion_called": completion_called,
                "projected_completion_allowed": completion_allowed,
                "projected_completion_gate_blocked": gate_blocked,
                "baseline_turns": baseline_turns,
                "projected_turns": projected_turns,
                "baseline_model_tool_calls": _int(row, "num_model_tool_calls"),
                "projected_model_tool_calls": model_tool_calls,
                "baseline_tool_calls": _int(row, "num_tool_calls"),
                "projected_tool_calls": tool_calls,
                "baseline_read_tool_calls": _int(row, "num_read_tool_calls"),
                "projected_read_tool_calls": read_calls,
                "baseline_write_tool_calls": _int(row, "num_write_tool_calls"),
                "projected_write_tool_calls": write_calls,
                "baseline_other_tool_calls": _int(row, "num_other_tool_calls"),
                "projected_other_tool_calls": other_calls,
                "baseline_invalid_tool_calls": _int(row, "num_invalid_tool_calls"),
                "projected_invalid_tool_calls": invalid_calls,
                "baseline_out_of_schema_tool_calls": _int(row, "num_out_of_schema_tool_calls"),
                "projected_out_of_schema_tool_calls": out_schema_calls,
                "baseline_duplicate_tool_calls": _int(row, "num_duplicate_tool_calls"),
                "projected_duplicate_tool_calls": duplicate_calls,
                "baseline_duplicate_write_actions": _int(row, "num_duplicate_write_actions"),
                "projected_duplicate_write_actions": duplicate_writes,
                "baseline_total_tokens": _int(row, "total_tokens"),
                "projected_total_tokens": total_tokens,
                "baseline_input_tokens": _int(row, "input_tokens"),
                "baseline_output_tokens": _int(row, "output_tokens"),
                "projected_input_tokens": input_tokens,
                "projected_output_tokens": output_tokens,
                "baseline_duration_seconds": baseline_duration,
                "projected_duration_seconds": projected_duration,
                "projected_num_verification_checks": verification_checks,
                "projected_num_verification_failures": verification_failures,
                "projected_num_recovery_attempts": 1 if recovery_attempted else 0,
                "projected_recovery_success": True if converted else (False if recovery_attempted else None),
                "projected_unintended_side_effects": None,
            }
        )

    actual_targets = {
        "invalid": sum(item["projected_invalid_tool_calls"] for item in projected),
        "out_schema": sum(item["projected_out_of_schema_tool_calls"] for item in projected),
        "duplicates": sum(item["projected_duplicate_tool_calls"] for item in projected),
        "duplicate_writes": sum(item["projected_duplicate_write_actions"] for item in projected),
    }
    expected_targets = {
        "invalid": target_invalid,
        "out_schema": target_out_schema,
        "duplicates": target_duplicates,
        "duplicate_writes": target_duplicate_writes,
    }
    if actual_targets != expected_targets:
        raise AssertionError(f"projected event totals were clipped: {actual_targets} != {expected_targets}")
    return projected


def _metric_block(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    success_key = f"{prefix}_success"
    completion_key = f"{prefix}_completion_called"
    turns_key = f"{prefix}_turns"
    model_calls_key = f"{prefix}_model_tool_calls"
    tool_calls_key = f"{prefix}_tool_calls"
    read_calls_key = f"{prefix}_read_tool_calls"
    write_calls_key = f"{prefix}_write_tool_calls"
    other_calls_key = f"{prefix}_other_tool_calls"
    invalid_key = f"{prefix}_invalid_tool_calls"
    out_of_schema_key = f"{prefix}_out_of_schema_tool_calls"
    duplicate_key = f"{prefix}_duplicate_tool_calls"
    duplicate_write_key = f"{prefix}_duplicate_write_actions"
    tokens_key = f"{prefix}_total_tokens"
    input_tokens_key = f"{prefix}_input_tokens"
    output_tokens_key = f"{prefix}_output_tokens"
    passed_tests_key = f"{prefix}_passed_tests"

    successes = sum(bool(row[success_key]) for row in rows)
    scenarios: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        scenarios[str(row["scenario_id"])].append(row)
    successful_scenarios = sum(all(bool(row[success_key]) for row in items) for items in scenarios.values())
    completion_count = sum(bool(row[completion_key]) for row in rows)
    false_completions = sum(bool(row[completion_key]) and not bool(row[success_key]) for row in rows)
    model_calls = sum(int(row[model_calls_key]) for row in rows)
    tool_calls = sum(int(row[tool_calls_key]) for row in rows)
    read_calls = sum(int(row[read_calls_key]) for row in rows)
    write_calls = sum(int(row[write_calls_key]) for row in rows)
    other_calls = sum(int(row[other_calls_key]) for row in rows)
    invalid_calls = sum(int(row[invalid_key]) for row in rows)
    out_of_schema_calls = sum(int(row[out_of_schema_key]) for row in rows)
    duplicate_calls = sum(int(row[duplicate_key]) for row in rows)
    duplicate_writes = sum(int(row[duplicate_write_key]) for row in rows)
    total_tokens = sum(int(row[tokens_key]) for row in rows)
    input_tokens = sum(int(row[input_tokens_key]) for row in rows)
    output_tokens = sum(int(row[output_tokens_key]) for row in rows)
    passed_tests = sum(int(row[passed_tests_key]) for row in rows)
    total_tests = sum(int(row["num_total_tests"]) for row in rows)
    return {
        "num_tasks": len(rows),
        "num_success": successes,
        "tgc": successes / len(rows),
        "num_scenarios": len(scenarios),
        "num_successful_scenarios": successful_scenarios,
        "sgc": successful_scenarios / len(scenarios),
        "num_passed_tests": passed_tests,
        "num_total_tests": total_tests,
        "evaluator_test_pass_rate": passed_tests / total_tests,
        "num_completion_called": completion_count,
        "num_false_completion": false_completions,
        "false_completion_rate": false_completions / completion_count,
        "completion_precision": successes / completion_count,
        "num_max_turn_tasks": sum(int(row[turns_key]) >= MAX_TURNS for row in rows),
        "max_turn_rate": sum(int(row[turns_key]) >= MAX_TURNS for row in rows) / len(rows),
        "num_model_tool_calls": model_calls,
        "num_tool_calls": tool_calls,
        "num_read_tool_calls": read_calls,
        "num_write_tool_calls": write_calls,
        "num_other_tool_calls": other_calls,
        "num_invalid_tool_calls": invalid_calls,
        "invalid_call_rate": invalid_calls / model_calls,
        "num_tasks_with_invalid_calls": sum(int(row[invalid_key]) > 0 for row in rows),
        "invalid_task_rate": sum(int(row[invalid_key]) > 0 for row in rows) / len(rows),
        "num_out_of_schema_tool_calls": out_of_schema_calls,
        "out_of_schema_call_rate": out_of_schema_calls / model_calls,
        "num_tasks_with_out_of_schema_calls": sum(int(row[out_of_schema_key]) > 0 for row in rows),
        "out_of_schema_task_rate": sum(int(row[out_of_schema_key]) > 0 for row in rows) / len(rows),
        "num_duplicate_tool_calls": duplicate_calls,
        "duplicate_call_rate": duplicate_calls / model_calls,
        "num_tasks_with_duplicate_calls": sum(int(row[duplicate_key]) > 0 for row in rows),
        "duplicate_task_rate": sum(int(row[duplicate_key]) > 0 for row in rows) / len(rows),
        "num_duplicate_write_actions": duplicate_writes,
        "duplicate_write_rate": duplicate_writes / write_calls,
        "num_tasks_with_duplicate_writes": sum(int(row[duplicate_write_key]) > 0 for row in rows),
        "duplicate_write_task_rate": sum(int(row[duplicate_write_key]) > 0 for row in rows) / len(rows),
        "total_tokens": total_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "avg_tokens_per_task": total_tokens / len(rows),
    }


def _summarize(rows: list[dict[str, Any]], source_csv: Path, source_summary: Path) -> dict[str, Any]:
    baseline = _metric_block(rows, "baseline")
    projected = _metric_block(rows, "projected")
    projected["num_completion_gate_blocked"] = sum(bool(row["projected_completion_gate_blocked"]) for row in rows)
    projected["num_completion_attempts"] = projected["num_completion_called"]
    projected["num_completion_allowed"] = sum(bool(row["projected_completion_allowed"]) for row in rows)
    projected["num_allowed_false_completion"] = sum(
        bool(row["projected_completion_allowed"]) and not bool(row["projected_success"]) for row in rows
    )
    projected["allowed_false_completion_rate"] = (
        projected["num_allowed_false_completion"] / projected["num_completion_allowed"]
    )
    projected["allowed_completion_precision"] = projected["num_success"] / projected["num_completion_allowed"]
    projected["num_without_completion_attempt"] = projected["num_tasks"] - projected["num_completion_attempts"]
    projected["completion_gate_block_rate"] = (
        projected["num_completion_gate_blocked"] / projected["num_completion_attempts"]
    )
    projected["num_verification_checks"] = sum(int(row["projected_num_verification_checks"]) for row in rows)
    projected["num_verification_failures"] = sum(int(row["projected_num_verification_failures"]) for row in rows)
    projected["verification_failure_rate"] = (
        projected["num_verification_failures"] / projected["num_verification_checks"]
    )
    projected["num_recovery_attempts"] = sum(int(row["projected_num_recovery_attempts"]) for row in rows)
    projected["num_recovery_successes"] = sum(row["projected_recovery_success"] is True for row in rows)
    projected["recovery_success_rate"] = (
        projected["num_recovery_successes"] / projected["num_recovery_attempts"]
        if projected["num_recovery_attempts"]
        else None
    )
    projected["num_unintended_side_effects"] = None

    deltas = {
        "tgc_percentage_points": (projected["tgc"] - baseline["tgc"]) * 100,
        "sgc_percentage_points": (projected["sgc"] - baseline["sgc"]) * 100,
        "false_completion_relative_reduction": 1
        - projected["false_completion_rate"] / baseline["false_completion_rate"],
        "max_turn_relative_reduction": 1 - projected["max_turn_rate"] / baseline["max_turn_rate"],
        "invalid_call_rate_relative_reduction": 1 - projected["invalid_call_rate"] / baseline["invalid_call_rate"],
        "out_of_schema_call_rate_relative_reduction": 1
        - projected["out_of_schema_call_rate"] / baseline["out_of_schema_call_rate"],
        "duplicate_call_rate_relative_reduction": 1
        - projected["duplicate_call_rate"] / baseline["duplicate_call_rate"],
        "duplicate_write_rate_relative_reduction": 1
        - projected["duplicate_write_rate"] / baseline["duplicate_write_rate"],
        "avg_token_relative_reduction": 1 - projected["avg_tokens_per_task"] / baseline["avg_tokens_per_task"],
    }
    return {
        "data_status": DATA_STATUS,
        "warning": (
            "This is a deterministic development target projection, not an observed SafeDesk benchmark result; "
            "it must not be used for tuning or model selection."
        ),
        "projection_version": PROJECTION_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "experiment_id": rows[0]["source_experiment_id"],
            "dataset": "test_challenge",
            "model": rows[0]["model"],
            "thinking": "disabled",
            "results_csv": str(source_csv),
            "results_csv_sha256": _sha256(source_csv),
            "summary_json": str(source_summary),
            "summary_json_sha256": _sha256(source_summary),
        },
        "projection_assumptions": {
            "baseline_success_regressions": 0,
            "target_successes": TARGET_SUCCESSES,
            "target_max_turn_tasks": TARGET_MAX_TURN_TASKS,
            "target_completion_gate_blocks": TARGET_GATE_BLOCKS,
            "target_no_completion_tasks": TARGET_NO_COMPLETION_TASKS,
            "target_token_reduction": TARGET_TOKEN_REDUCTION,
            "target_invalid_call_count_reduction": TARGET_INVALID_CALL_REDUCTION,
            "target_out_of_schema_count_reduction": TARGET_OUT_OF_SCHEMA_REDUCTION,
            "target_duplicate_call_count_reduction": TARGET_DUPLICATE_CALL_REDUCTION,
            "target_duplicate_write_count_reduction": TARGET_DUPLICATE_WRITE_REDUCTION,
            "selection_uses_hidden_ground_truth": False,
        },
        "metric_definitions": {
            "tgc": {
                "scope": "task",
                "numerator": "num_success",
                "denominator": "num_tasks",
                "formula": "num_success / num_tasks",
            },
            "sgc": {
                "scope": "scenario",
                "numerator": "num_successful_scenarios",
                "denominator": "num_scenarios",
                "formula": "num_successful_scenarios / num_scenarios",
            },
            "evaluator_test_pass_rate": {
                "scope": "evaluator_test",
                "numerator": "num_passed_tests",
                "denominator": "num_total_tests",
                "formula": "num_passed_tests / num_total_tests",
            },
            "false_completion_rate": {
                "scope": "completion",
                "numerator": "num_false_completion",
                "denominator": "num_completion_called",
                "formula": "num_false_completion / num_completion_called",
                "meaning": "failed tasks where the model proposed complete_task",
            },
            "allowed_false_completion_rate": {
                "scope": "allowed_completion",
                "numerator": "num_allowed_false_completion",
                "denominator": "num_completion_allowed",
                "formula": "num_allowed_false_completion / num_completion_allowed",
                "availability": "projection_only",
            },
            "max_turn_rate": {
                "scope": "task",
                "numerator": "num_max_turn_tasks",
                "denominator": "num_tasks",
                "formula": "num_max_turn_tasks / num_tasks",
            },
            "invalid_call_rate": {
                "scope": "model_tool_call",
                "numerator": "num_invalid_tool_calls",
                "denominator": "num_model_tool_calls",
                "formula": "num_invalid_tool_calls / num_model_tool_calls",
            },
            "invalid_task_rate": {
                "scope": "task",
                "numerator": "num_tasks_with_invalid_calls",
                "denominator": "num_tasks",
                "formula": "num_tasks_with_invalid_calls / num_tasks",
            },
            "out_of_schema_call_rate": {
                "scope": "model_tool_call",
                "numerator": "num_out_of_schema_tool_calls",
                "denominator": "num_model_tool_calls",
                "formula": "num_out_of_schema_tool_calls / num_model_tool_calls",
            },
            "out_of_schema_task_rate": {
                "scope": "task",
                "numerator": "num_tasks_with_out_of_schema_calls",
                "denominator": "num_tasks",
                "formula": "num_tasks_with_out_of_schema_calls / num_tasks",
            },
            "duplicate_call_rate": {
                "scope": "model_tool_call",
                "numerator": "num_duplicate_tool_calls",
                "denominator": "num_model_tool_calls",
                "formula": "num_duplicate_tool_calls / num_model_tool_calls",
            },
            "duplicate_task_rate": {
                "scope": "task",
                "numerator": "num_tasks_with_duplicate_calls",
                "denominator": "num_tasks",
                "formula": "num_tasks_with_duplicate_calls / num_tasks",
            },
            "duplicate_write_rate": {
                "scope": "write_tool_call",
                "numerator": "num_duplicate_write_actions",
                "denominator": "num_write_tool_calls",
                "formula": "num_duplicate_write_actions / num_write_tool_calls",
            },
            "duplicate_write_task_rate": {
                "scope": "task",
                "numerator": "num_tasks_with_duplicate_writes",
                "denominator": "num_tasks",
                "formula": "num_tasks_with_duplicate_writes / num_tasks",
            },
            "completion_precision": {
                "scope": "completion",
                "numerator": "num_success",
                "denominator": "num_completion_called",
                "formula": "num_success / num_completion_called",
            },
            "completion_gate_block_rate": {
                "scope": "completion_attempt",
                "numerator": "num_completion_gate_blocked",
                "denominator": "num_completion_attempts",
                "formula": "num_completion_gate_blocked / num_completion_attempts",
                "availability": "projection_only",
            },
            "verification_failure_rate": {
                "scope": "runtime_verification",
                "numerator": "num_verification_failures",
                "denominator": "num_verification_checks",
                "formula": "num_verification_failures / num_verification_checks",
                "availability": "projection_only",
            },
            "recovery_success_rate": {
                "scope": "recovery",
                "numerator": "num_recovery_successes",
                "denominator": "num_recovery_attempts",
                "formula": "num_recovery_successes / num_recovery_attempts",
                "availability": "projection_only",
            },
            "avg_tokens_per_task": {
                "scope": "task",
                "numerator": "total_tokens",
                "denominator": "num_tasks",
                "formula": "total_tokens / num_tasks",
            },
            "unintended_side_effect_rate": {
                "scope": "task",
                "availability": "unavailable",
                "reason": "baseline lacks trace-level state diffs",
            },
        },
        "baseline": baseline,
        "projected": projected,
        "delta": deltas,
        "converted_tasks_by_primary_intervention": dict(
            sorted(
                Counter(
                    row["primary_intervention"]
                    for row in rows
                    if row["projected_success"] and not row["baseline_success"]
                ).items()
            )
        ),
    }


def _validate(rows: list[dict[str, Any]], summary: dict[str, Any], observed_summary_path: Path) -> dict[str, Any]:
    observed = json.loads(observed_summary_path.read_text(encoding="utf-8"))
    scenarios = Counter(str(row["scenario_id"]) for row in rows)
    checks = {
        "task_count_is_417": len(rows) == EXPECTED_TASKS,
        "task_ids_are_unique": len({row["task_id"] for row in rows}) == EXPECTED_TASKS,
        "scenario_count_is_139": len(scenarios) == EXPECTED_SCENARIOS,
        "each_scenario_has_three_tasks": set(scenarios.values()) == {3},
        "source_success_total_matches_summary": (summary["baseline"]["num_success"] == observed["num_success"]),
        "source_tokens_match_summary": (summary["baseline"]["total_tokens"] == observed["total_tokens"]),
        "source_evaluator_tests_match_summary": (
            summary["baseline"]["num_passed_tests"] == observed["num_passed_tests"]
            and summary["baseline"]["num_total_tests"] == observed["num_total_tests"]
        ),
        "source_tool_calls_match_summary": (
            summary["baseline"]["num_tool_calls"] == observed["num_tool_calls"]
            and summary["baseline"]["num_read_tool_calls"] == observed["num_read_tool_calls"]
            and summary["baseline"]["num_write_tool_calls"] == observed["num_write_tool_calls"]
        ),
        "source_invalid_calls_match_summary": (
            summary["baseline"]["num_invalid_tool_calls"] == observed["num_invalid_tool_calls"]
        ),
        "source_duplicate_writes_match_summary": (
            summary["baseline"]["num_duplicate_write_actions"] == observed["num_duplicate_write_actions"]
        ),
        "projected_success_total_matches_target": (summary["projected"]["num_success"] == TARGET_SUCCESSES),
        "projected_invalid_count_matches_target": (
            summary["projected"]["num_invalid_tool_calls"]
            == round(summary["baseline"]["num_invalid_tool_calls"] * (1 - TARGET_INVALID_CALL_REDUCTION))
        ),
        "projected_out_of_schema_count_matches_target": (
            summary["projected"]["num_out_of_schema_tool_calls"]
            == round(summary["baseline"]["num_out_of_schema_tool_calls"] * (1 - TARGET_OUT_OF_SCHEMA_REDUCTION))
        ),
        "projected_duplicate_count_matches_target": (
            summary["projected"]["num_duplicate_tool_calls"]
            == round(summary["baseline"]["num_duplicate_tool_calls"] * (1 - TARGET_DUPLICATE_CALL_REDUCTION))
        ),
        "projected_duplicate_write_count_matches_target": (
            summary["projected"]["num_duplicate_write_actions"]
            == round(summary["baseline"]["num_duplicate_write_actions"] * (1 - TARGET_DUPLICATE_WRITE_REDUCTION))
        ),
        "projected_token_total_matches_target": (
            summary["projected"]["total_tokens"]
            == round(summary["baseline"]["total_tokens"] * (1 - TARGET_TOKEN_REDUCTION))
        ),
        "no_baseline_success_regresses": all(not row["baseline_success"] or row["projected_success"] for row in rows),
        "projected_successes_pass_all_tests": all(
            not row["projected_success"] or row["projected_passed_tests"] == row["num_total_tests"] for row in rows
        ),
        "projected_tool_subtypes_sum_to_total": all(
            row["projected_tool_calls"]
            == row["projected_read_tool_calls"] + row["projected_write_tool_calls"] + row["projected_other_tool_calls"]
            for row in rows
        ),
        "baseline_token_parts_sum_to_total": all(
            row["baseline_input_tokens"] + row["baseline_output_tokens"] == row["baseline_total_tokens"] for row in rows
        ),
        "projected_token_parts_sum_to_total": all(
            row["projected_input_tokens"] + row["projected_output_tokens"] == row["projected_total_tokens"]
            for row in rows
        ),
        "projected_model_calls_equal_executed_plus_blocked": all(
            row["projected_model_tool_calls"] == row["projected_tool_calls"] + row["projected_out_of_schema_tool_calls"]
            for row in rows
        ),
        "summary_rates_match_exact_counts": all(
            (
                math.isclose(
                    summary["projected"][rate],
                    summary["projected"][numerator] / summary["projected"][denominator],
                )
            )
            for rate, numerator, denominator in (
                ("tgc", "num_success", "num_tasks"),
                ("sgc", "num_successful_scenarios", "num_scenarios"),
                ("evaluator_test_pass_rate", "num_passed_tests", "num_total_tests"),
                ("false_completion_rate", "num_false_completion", "num_completion_called"),
                (
                    "allowed_false_completion_rate",
                    "num_allowed_false_completion",
                    "num_completion_allowed",
                ),
                ("completion_gate_block_rate", "num_completion_gate_blocked", "num_completion_attempts"),
                ("max_turn_rate", "num_max_turn_tasks", "num_tasks"),
                ("invalid_call_rate", "num_invalid_tool_calls", "num_model_tool_calls"),
                ("out_of_schema_call_rate", "num_out_of_schema_tool_calls", "num_model_tool_calls"),
                ("duplicate_call_rate", "num_duplicate_tool_calls", "num_model_tool_calls"),
                ("duplicate_write_rate", "num_duplicate_write_actions", "num_write_tool_calls"),
                ("recovery_success_rate", "num_recovery_successes", "num_recovery_attempts"),
            )
        ),
        "max_turn_target_matches": (summary["projected"]["num_max_turn_tasks"] == TARGET_MAX_TURN_TASKS),
        "no_completion_target_matches": (
            summary["projected"]["num_without_completion_attempt"] == TARGET_NO_COMPLETION_TASKS
        ),
        "gate_block_target_matches": (summary["projected"]["num_completion_gate_blocked"] == TARGET_GATE_BLOCKS),
        "completion_attempts_partition_tasks": (
            summary["projected"]["num_completion_attempts"] + summary["projected"]["num_without_completion_attempt"]
            == summary["projected"]["num_tasks"]
        ),
        "completion_attempts_partition_gate_decisions": (
            summary["projected"]["num_completion_allowed"] + summary["projected"]["num_completion_gate_blocked"]
            == summary["projected"]["num_completion_attempts"]
        ),
        "task_completion_flags_are_consistent": all(
            (
                (not row["projected_completion_allowed"] or row["projected_completion_called"])
                and (not row["projected_completion_gate_blocked"] or row["projected_completion_called"])
                and not (row["projected_completion_allowed"] and row["projected_completion_gate_blocked"])
                and (not row["projected_success"] or row["projected_completion_allowed"])
            )
            for row in rows
        ),
        "token_reduction_target_matches": math.isclose(
            summary["delta"]["avg_token_relative_reduction"], TARGET_TOKEN_REDUCTION, abs_tol=1e-8
        ),
        "false_completion_reduction_meets_plan": (summary["delta"]["false_completion_relative_reduction"] >= 0.30),
        "max_turn_reduction_meets_plan": summary["delta"]["max_turn_relative_reduction"] >= 0.30,
        "invalid_rate_reduction_meets_plan": (summary["delta"]["invalid_call_rate_relative_reduction"] >= 0.30),
        "duplicate_write_rate_reduction_meets_plan": (
            summary["delta"]["duplicate_write_rate_relative_reduction"] >= 0.50
        ),
        "token_reduction_meets_plan": summary["delta"]["avg_token_relative_reduction"] >= 0.35,
        "unmeasured_side_effects_remain_null": all(row["projected_unintended_side_effects"] is None for row in rows),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise AssertionError(f"projection validation failed: {failed}")
    return {"all_checks_passed": True, "checks": checks}


def _write_methodology(path: Path, summary: dict[str, Any]) -> None:
    baseline = summary["baseline"]
    projected = summary["projected"]
    delta = summary["delta"]
    converted_count = projected["num_success"] - baseline["num_success"]
    token_reduction = summary["projection_assumptions"]["target_token_reduction"]
    selection_rule = (
        f"3. 从 {baseline['num_tasks'] - baseline['num_success']} 个失败任务中确定性选取 "
        f"{converted_count} 个“较可恢复任务”，目标 TGC 为 "
        f"{projected['num_success']}/{projected['num_tasks']}。"
    )

    def ratio_text(numerator: int, denominator: int) -> str:
        return f"{numerator:,}/{denominator:,} ({numerator / denominator:.2%})"

    def metric_row(metric: str, definition: str, baseline_value: str, projected_value: str) -> str:
        return f"| {metric} | {definition} | {baseline_value} | {projected_value} |"

    detailed_rows = "\n".join(
        (
            metric_row(
                "TGC",
                "成功任务/全部任务",
                ratio_text(baseline["num_success"], baseline["num_tasks"]),
                ratio_text(projected["num_success"], projected["num_tasks"]),
            ),
            metric_row(
                "SGC",
                "全成功场景/完整场景",
                ratio_text(baseline["num_successful_scenarios"], baseline["num_scenarios"]),
                ratio_text(projected["num_successful_scenarios"], projected["num_scenarios"]),
            ),
            metric_row(
                "Evaluator Test Pass Rate",
                "通过测试/全部测试",
                ratio_text(baseline["num_passed_tests"], baseline["num_total_tests"]),
                ratio_text(projected["num_passed_tests"], projected["num_total_tests"]),
            ),
            metric_row(
                "假完成率",
                "失败完成声明/模型提出complete_task",
                ratio_text(baseline["num_false_completion"], baseline["num_completion_called"]),
                ratio_text(projected["num_false_completion"], projected["num_completion_called"]),
            ),
            metric_row(
                "Gate放行后假完成率",
                "失败且被放行/全部被放行完成",
                "N/A（Baseline无Gate）",
                ratio_text(projected["num_allowed_false_completion"], projected["num_completion_allowed"]),
            ),
            metric_row(
                "Completion Precision",
                "成功任务/模型提出complete_task",
                ratio_text(baseline["num_success"], baseline["num_completion_called"]),
                ratio_text(projected["num_success"], projected["num_completion_called"]),
            ),
            metric_row(
                "Gate放行完成精度",
                "成功任务/全部被放行完成",
                "N/A（Baseline无Gate）",
                ratio_text(projected["num_success"], projected["num_completion_allowed"]),
            ),
            metric_row(
                "Max-Turn Rate",
                "达到50轮任务/全部任务",
                ratio_text(baseline["num_max_turn_tasks"], baseline["num_tasks"]),
                ratio_text(projected["num_max_turn_tasks"], projected["num_tasks"]),
            ),
            metric_row(
                "Invalid Call Rate",
                "Invalid事件/模型工具调用",
                ratio_text(baseline["num_invalid_tool_calls"], baseline["num_model_tool_calls"]),
                ratio_text(projected["num_invalid_tool_calls"], projected["num_model_tool_calls"]),
            ),
            metric_row(
                "Invalid任务发生率",
                "出现Invalid的任务/全部任务",
                ratio_text(baseline["num_tasks_with_invalid_calls"], baseline["num_tasks"]),
                ratio_text(projected["num_tasks_with_invalid_calls"], projected["num_tasks"]),
            ),
            metric_row(
                "Out-of-Schema Rate",
                "越界调用/模型工具调用",
                ratio_text(baseline["num_out_of_schema_tool_calls"], baseline["num_model_tool_calls"]),
                ratio_text(projected["num_out_of_schema_tool_calls"], projected["num_model_tool_calls"]),
            ),
            metric_row(
                "Out-of-Schema任务发生率",
                "发生越界调用任务/全部任务",
                ratio_text(baseline["num_tasks_with_out_of_schema_calls"], baseline["num_tasks"]),
                ratio_text(projected["num_tasks_with_out_of_schema_calls"], projected["num_tasks"]),
            ),
            metric_row(
                "重复调用率",
                "重复调用事件/模型工具调用",
                ratio_text(baseline["num_duplicate_tool_calls"], baseline["num_model_tool_calls"]),
                ratio_text(projected["num_duplicate_tool_calls"], projected["num_model_tool_calls"]),
            ),
            metric_row(
                "重复调用任务发生率",
                "发生重复调用任务/全部任务",
                ratio_text(baseline["num_tasks_with_duplicate_calls"], baseline["num_tasks"]),
                ratio_text(projected["num_tasks_with_duplicate_calls"], projected["num_tasks"]),
            ),
            metric_row(
                "重复写操作率",
                "重复写事件/全部写调用",
                ratio_text(baseline["num_duplicate_write_actions"], baseline["num_write_tool_calls"]),
                ratio_text(projected["num_duplicate_write_actions"], projected["num_write_tool_calls"]),
            ),
            metric_row(
                "重复写任务发生率",
                "发生重复写任务/全部任务",
                ratio_text(baseline["num_tasks_with_duplicate_writes"], baseline["num_tasks"]),
                ratio_text(projected["num_tasks_with_duplicate_writes"], projected["num_tasks"]),
            ),
            metric_row(
                "平均Token",
                "总Token/全部任务",
                f"{baseline['total_tokens']:,}/{baseline['num_tasks']} = {baseline['avg_tokens_per_task']:,.2f}",
                f"{projected['total_tokens']:,}/{projected['num_tasks']} = {projected['avg_tokens_per_task']:,.2f}",
            ),
            metric_row(
                "Token结构",
                "输入Token+输出Token=总Token",
                (f"{baseline['input_tokens']:,}+{baseline['output_tokens']:,}={baseline['total_tokens']:,}"),
                (f"{projected['input_tokens']:,}+{projected['output_tokens']:,}={projected['total_tokens']:,}"),
            ),
            metric_row(
                "工具调用结构",
                "读+写+其他=实际执行调用",
                (
                    f"{baseline['num_read_tool_calls']:,}+{baseline['num_write_tool_calls']:,}+"
                    f"{baseline['num_other_tool_calls']:,}={baseline['num_tool_calls']:,}"
                ),
                (
                    f"{projected['num_read_tool_calls']:,}+{projected['num_write_tool_calls']:,}+"
                    f"{projected['num_other_tool_calls']:,}={projected['num_tool_calls']:,}"
                ),
            ),
            metric_row(
                "Completion Gate Block Rate",
                "被阻止完成/全部完成尝试",
                "N/A（Baseline无Gate）",
                ratio_text(projected["num_completion_gate_blocked"], projected["num_completion_attempts"]),
            ),
            metric_row(
                "Verification Failure Rate",
                "失败验证/运行时验证",
                "N/A（Baseline未记录）",
                ratio_text(projected["num_verification_failures"], projected["num_verification_checks"]),
            ),
            metric_row(
                "Recovery Success Rate",
                "成功恢复/恢复尝试",
                "N/A（Baseline未记录）",
                ratio_text(projected["num_recovery_successes"], projected["num_recovery_attempts"]),
            ),
            metric_row(
                "Unintended Side Effects",
                "有状态差分证据的非预期副作用",
                "N/A（证据不足）",
                "N/A（保持空值）",
            ),
        )
    )
    table_rows = "\n".join(
        (
            (
                f"| TGC | {baseline['num_success']}/{baseline['num_tasks']} ({baseline['tgc']:.2%}) | "
                f"{projected['num_success']}/{projected['num_tasks']} ({projected['tgc']:.2%}) | "
                f"{delta['tgc_percentage_points']:+.2f} pp |"
            ),
            (
                "| SGC | "
                f"{baseline['num_successful_scenarios']}/{baseline['num_scenarios']} ({baseline['sgc']:.2%}) | "
                f"{projected['num_successful_scenarios']}/{projected['num_scenarios']} ({projected['sgc']:.2%}) | "
                f"{delta['sgc_percentage_points']:+.2f} pp |"
            ),
            (
                "| 假完成率 | "
                f"{baseline['num_false_completion']}/{baseline['num_completion_called']} "
                f"({baseline['false_completion_rate']:.2%}) | "
                f"{projected['num_false_completion']}/{projected['num_completion_called']} "
                f"({projected['false_completion_rate']:.2%}) | "
                f"相对下降 {delta['false_completion_relative_reduction']:.2%} |"
            ),
            (
                "| Max-Turn Rate | "
                f"{baseline['num_max_turn_tasks']}/{baseline['num_tasks']} ({baseline['max_turn_rate']:.2%}) | "
                f"{projected['num_max_turn_tasks']}/{projected['num_tasks']} "
                f"({projected['max_turn_rate']:.2%}) | "
                f"相对下降 {delta['max_turn_relative_reduction']:.2%} |"
            ),
            (
                "| Invalid Call Rate | "
                f"{baseline['num_invalid_tool_calls']}/{baseline['num_model_tool_calls']} "
                f"({baseline['invalid_call_rate']:.2%}) | "
                f"{projected['num_invalid_tool_calls']}/{projected['num_model_tool_calls']} "
                f"({projected['invalid_call_rate']:.2%}) | "
                f"相对下降 {delta['invalid_call_rate_relative_reduction']:.2%} |"
            ),
            (
                "| 重复写操作率 | "
                f"{baseline['num_duplicate_write_actions']}/{baseline['num_write_tool_calls']} "
                f"({baseline['duplicate_write_rate']:.2%}) | "
                f"{projected['num_duplicate_write_actions']}/{projected['num_write_tool_calls']} "
                f"({projected['duplicate_write_rate']:.2%}) | "
                f"相对下降 {delta['duplicate_write_rate_relative_reduction']:.2%} |"
            ),
            (
                f"| 平均 Token | {baseline['avg_tokens_per_task']:,.0f} | "
                f"{projected['avg_tokens_per_task']:,.0f} | "
                f"相对下降 {delta['avg_token_relative_reduction']:.2%} |"
            ),
        )
    )
    content = f"""# AppWorld Test Challenge SafeDesk 合成目标数据说明

## 数据性质

本目录是 **SafeDesk 开发目标投影**，不是实际 benchmark 跑分。所有记录均带有
`data_status={DATA_STATUS}`，不得用于论文、榜单或实验报告中的“实测结果”。

由于任务选择使用了既有 `test_challenge` 结果，这份投影也不得用于训练、调参、阈值选择或
模块取舍；否则会造成测试集泄漏。它只适合作为开发目标、数据管道样例和可视化演示数据。

投影以现有 DeepSeek V4 Flash、关闭 Thinking 的 417 条真实 baseline 任务结果为唯一输入。
源文件及 SHA-256 记录在 `summary.json`，生成过程不读取 AppWorld Ground Truth、隐藏 API
或 Evaluator 实现。

## 点目标

| 指标 | Baseline | SafeDesk 投影 | 变化 |
| --- | ---: | ---: | ---: |
{table_rows}

## 完整统计口径与数量

| 指标 | 公式/口径 | Baseline数量 | SafeDesk投影数量 |
| --- | --- | ---: | ---: |
{detailed_rows}

口径注意事项：

- 任务发生率按 417 个任务计数；调用事件率按模型工具调用或写调用计数。
- 模型工具调用包含被 Schema Guard 拦截的调用；实际执行调用不包含这些调用。
- Out-of-Schema 是 Invalid 的组成部分，两者可能重叠，不能相加得到“总错误数”。
- 重复调用和重复写的分子是重复事件数，不是发生问题的任务数；任务发生率单独列出。
- Token 满足输入加输出等于总量；平均 Token 的分母固定为 417 个任务。
- Verification 与 Recovery 是 SafeDesk 运行时投影字段，Baseline 没有对应观测值。

投影字段的具体构造关系：

- 模型工具调用为 {projected["num_model_tool_calls"]:,} 次，其中实际执行
  {projected["num_tool_calls"]:,} 次，被拦截的 Out-of-Schema 调用
  {projected["num_out_of_schema_tool_calls"]:,} 次；三者满足
  {projected["num_tool_calls"]:,}+{projected["num_out_of_schema_tool_calls"]:,}=
  {projected["num_model_tool_calls"]:,}。
- 模型提出 `complete_task` 共 {projected["num_completion_attempts"]:,} 次，其中 Gate 允许
  {projected["num_completion_allowed"]:,} 次、阻止 {projected["num_completion_gate_blocked"]:,} 次；
  未提出完成的任务为 {projected["num_without_completion_attempt"]:,} 个。
- 模型提出的完成中有 {projected["num_success"]:,} 个最终成功、
  {projected["num_false_completion"]:,} 个最终失败；Gate 放行的完成中有
  {projected["num_success"]:,} 个成功和 {projected["num_allowed_false_completion"]:,} 个假完成。
- Runtime Verification 共 {projected["num_verification_checks"]:,} 次，定义为每个任务一次目标检查，
  再加每次 projected 写调用一次写后验证，即
  {projected["num_tasks"]:,}+{projected["num_write_tool_calls"]:,}=
  {projected["num_verification_checks"]:,}；其中投影失败验证
  {projected["num_verification_failures"]:,} 次。该失败数按
  {baseline["num_tasks"] - baseline["num_success"]:,} 个 baseline 失败任务各发现一次
  不一致构造，其中部分随后被 Recovery 修复；它是明确投影假设，不是 baseline 观测值。
- Recovery 尝试 {projected["num_recovery_attempts"]:,} 次，其中成功恢复
  {projected["num_recovery_successes"]:,} 次、未恢复
  {projected["num_recovery_attempts"] - projected["num_recovery_successes"]:,} 次。成功的
  {projected["num_recovery_successes"]:,} 次对应新增成功任务；未恢复的
  {projected["num_recovery_attempts"] - projected["num_recovery_successes"]:,} 次对应被
  Stagnation Detector 提前终止、但最终任务仍失败的轨迹。

## 任务级构造规则

1. 保留全部 {baseline["num_tasks"]} 个真实 `task_id`、{baseline["num_scenarios"]} 个场景和 baseline 指标，不生成新任务。
2. 所有 {baseline["num_success"]} 个 baseline 成功任务保持成功，明确假设没有回归。
{selection_rule}
4. 可恢复性只使用 baseline 的 Evaluator 通过比例、是否假完成、Max-Turn、Invalid、
   Out-of-Schema、重复调用和重复写等可观测字段；同分时按 `task_id` 排序。
5. 任务转为成功后，Evaluator 通过数才投影为全部通过；其余失败任务不凭空增加通过测试。
6. SGC 不单独指定，由任务级 projected success 按三任务场景重新计算。
7. Token 总量按固定 {token_reduction:.4%} 降幅分配；Predictor Token 保持不变，削减来自 Agent 轨迹。
8. `num_unintended_side_effects` 保留为空，因为当前 baseline 没有足够的状态差分证据，
   不用失败结果反推副作用。

## 准确性边界

这里的“准确”是指源数据、公式、总量和任务级数据完全一致且可复现，不表示未来实测必然达到
该数值。真实 SafeDesk 结果必须使用相同模型、Thinking、Temperature、最大轮数、数据顺序、
Tool Catalog 和 Evaluator 配置重新运行，并存入独立的 `benchmarks/results` 实验目录。

`validation.json` 中所有检查通过，才允许重新生成本目录。
"""
    path.write_text(content, encoding="utf-8", newline="\n")


def generate(source_directory: Path, output_directory: Path) -> dict[str, Any]:
    source_csv = source_directory / "results.csv"
    source_summary = source_directory / "summary.json"
    if not source_csv.exists() or not source_summary.exists():
        raise FileNotFoundError("source results.csv and summary.json are required")

    source_rows = _load_rows(source_csv)
    if len(source_rows) != EXPECTED_TASKS:
        raise ValueError(f"expected {EXPECTED_TASKS} source tasks, found {len(source_rows)}")
    projected_rows = _build_projection(source_rows)
    summary = _summarize(projected_rows, source_csv.resolve(), source_summary.resolve())
    validation = _validate(projected_rows, summary, source_summary)

    output_directory.mkdir(parents=True, exist_ok=True)
    with (output_directory / "task_projection.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(projected_rows)
    with (output_directory / "task_projection.jsonl").open("w", encoding="utf-8") as handle:
        for row in projected_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    (output_directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (output_directory / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    _write_methodology(output_directory / "README.md", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a deterministic, clearly labeled SafeDesk target projection."
    )
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    summary = generate(args.source_dir.resolve(), args.output_dir.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
