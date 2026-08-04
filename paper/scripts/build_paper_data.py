"""Build the SafeDesk paper data package from local benchmark artifacts.

This script never synthesizes benchmark outcomes. It derives measured values from
the checked local CSV/JSON files and emits explicit pending rows for experiments
that have not been run.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PAPER_DIR = ROOT / "paper"
DATA_DIR = PAPER_DIR / "data"
RESULTS_DIR = ROOT / "benchmarks" / "results"

APPWORLD_PRIMARY_DIR = RESULTS_DIR / "appworld_deepseek_v4_flash_no_thinking_test_challenge_aligned"
APPWORLD_PRIMARY_SUMMARY = APPWORLD_PRIMARY_DIR / "summary.json"
APPWORLD_PRIMARY_RESULTS = APPWORLD_PRIMARY_DIR / "results.csv"
APPWORLD_HISTORICAL_NORMAL = RESULTS_DIR / "appworld_deepseek_v4_flash_no_thinking_test_normal" / "summary.json"
APPWORLD_STATE_SMOKE = RESULTS_DIR / "appworld_deepseek_v4_flash_no_thinking_state_save_check_5" / "summary.json"
APPWORLD_QWEN_PILOT = RESULTS_DIR / "appworld_qwen3_14b_official_baseline_train5" / "summary.json"
APPWORLD_SHADOW = (
    RESULTS_DIR
    / "agentgate_state_verification_shadow"
    / "audits"
    / "appworld_deepseek_v4_flash_no_thinking_challenge_alignment_smoke_5"
    / "shadow_audit_summary.json"
)
APPWORLD_CONVERSION = (
    RESULTS_DIR
    / "appworld_deepseek_v4_flash_no_thinking_challenge_alignment_smoke_5"
    / "analysis"
    / "agentgate_offline"
    / "conversion_summary.json"
)
TAU2_AIRLINE_RETAIL = RESULTS_DIR / "tau2_airline_retail_full_glm5_no_thinking" / "metrics_summary.csv"
TAU2_TELECOM = RESULTS_DIR / "tau2_telecom_base_glm5_no_thinking_114" / "metrics_summary.csv"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: clean_value(row.get(key)) for key in fieldnames})


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float):
        return round(value, 6)
    return value


def as_bool(value: Any) -> bool | None:
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip().lower() in {"true", "1", "yes"}


def as_int(value: Any) -> int:
    if value is None or str(value).strip() == "":
        return 0
    return int(float(value))


def as_float(value: Any) -> float:
    if value is None or str(value).strip() == "":
        return 0.0
    return float(value)


def ratio(numerator: int | float, denominator: int | float) -> float | None:
    if not denominator:
        return None
    return float(numerator) / float(denominator)


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return center - margin, center + margin


def nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def collection_sha256(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(source_ref(path).encode("utf-8"))
        digest.update(bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def source_ref(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def build_appworld_data() -> dict[str, Any]:
    summary = read_json(APPWORLD_PRIMARY_SUMMARY)
    rows = read_csv(APPWORLD_PRIMARY_RESULTS)
    assert len(rows) == summary["num_result_tasks"] == 417

    successful = [row for row in rows if as_bool(row["success"])]
    failed = [row for row in rows if not as_bool(row["success"])]
    completed = [row for row in rows if row.get("completion_step", "").strip()]
    false_completion_candidates = [row for row in completed if not as_bool(row["success"])]
    proposed_calls = sum(as_int(row["num_model_tool_calls"]) for row in rows)
    executed_calls = sum(as_int(row["num_tool_calls"]) for row in rows)
    write_calls = sum(as_int(row["num_write_tool_calls"]) for row in rows)
    tgc_ci = wilson_interval(summary["num_success"], summary["num_result_tasks"])
    sgc_ci = wilson_interval(summary["num_successful_scenarios"], summary["num_complete_scenarios"])
    token_values = [as_int(row["total_tokens"]) for row in rows]

    primary = [
        {
            "benchmark": "AppWorld",
            "split": "test_challenge",
            "experiment_id": summary["experiment_id"],
            "model": summary["model"],
            "thinking": summary["config"]["thinking"],
            "status": "measured_primary",
            "tasks": summary["num_result_tasks"],
            "scenarios": summary["num_complete_scenarios"],
            "task_successes": summary["num_success"],
            "tgc": summary["task_goal_completion"],
            "tgc_ci95_low": tgc_ci[0],
            "tgc_ci95_high": tgc_ci[1],
            "scenario_successes": summary["num_successful_scenarios"],
            "sgc": summary["scenario_goal_completion"],
            "sgc_ci95_low": sgc_ci[0],
            "sgc_ci95_high": sgc_ci[1],
            "evaluator_tests_passed": summary["num_passed_tests"],
            "evaluator_tests_total": summary["num_total_tests"],
            "evaluator_test_pass_rate": summary["evaluator_test_pass_rate"],
            "completion_attempts": len(completed),
            "false_completion_candidates": len(false_completion_candidates),
            "false_completion_candidate_rate": ratio(len(false_completion_candidates), len(completed)),
            "without_completion": summary["num_without_completion"],
            "max_turn_tasks": summary["num_reached_max_turns"],
            "max_turn_rate": ratio(summary["num_reached_max_turns"], summary["num_result_tasks"]),
            "model_proposed_tool_calls": proposed_calls,
            "executed_tool_calls": executed_calls,
            "read_tool_calls": summary["num_read_tool_calls"],
            "write_tool_calls": write_calls,
            "invalid_tool_calls": summary["num_invalid_tool_calls"],
            "invalid_call_rate": ratio(summary["num_invalid_tool_calls"], proposed_calls),
            "out_of_schema_tool_calls": summary["num_out_of_schema_tool_calls"],
            "out_of_schema_call_rate": ratio(summary["num_out_of_schema_tool_calls"], proposed_calls),
            "duplicate_tool_calls": summary["num_duplicate_tool_calls"],
            "duplicate_call_rate": ratio(summary["num_duplicate_tool_calls"], executed_calls),
            "duplicate_write_actions": summary["num_duplicate_write_actions"],
            "duplicate_write_action_rate": ratio(summary["num_duplicate_write_actions"], write_calls),
            "total_tokens": summary["total_tokens"],
            "predictor_tokens": summary["predictor_tokens"],
            "agent_tokens": summary["agent_tokens"],
            "input_tokens": summary["input_tokens"],
            "output_tokens": summary["output_tokens"],
            "avg_tokens_per_task": summary["avg_tokens_per_task"],
            "median_tokens_per_task": median(token_values),
            "p95_tokens_per_task": nearest_rank([float(value) for value in token_values], 0.95),
            "max_tokens_per_task": max(token_values),
            "estimated_cost_cny_at_1_2_per_m": (
                summary["input_tokens"] / 1_000_000 + 2 * summary["output_tokens"] / 1_000_000
            ),
            "estimated_cost_cny_at_3_6_per_m": (
                3 * summary["input_tokens"] / 1_000_000 + 6 * summary["output_tokens"] / 1_000_000
            ),
            "avg_turns": summary["avg_turns"],
            "avg_tool_calls": summary["avg_tool_calls"],
            "avg_duration_seconds": summary["avg_duration_seconds"],
            "infra_errors": summary["infra_error_count"],
            "source": source_ref(APPWORLD_PRIMARY_SUMMARY),
        }
    ]
    write_csv(
        DATA_DIR / "appworld_primary_metrics.csv",
        primary,
        list(primary[0]),
    )

    difficulty_rows = []
    difficulty_metadata = {
        row["task_id"]: ROOT
        / "benchmarks"
        / "appworld-root"
        / "data"
        / "tasks"
        / row["task_id"]
        / "ground_truth"
        / "metadata.json"
        for row in rows
    }
    missing_metadata = [task_id for task_id, path in difficulty_metadata.items() if not path.exists()]
    if missing_metadata:
        raise FileNotFoundError(f"Missing AppWorld metadata for {len(missing_metadata)} tasks: {missing_metadata[:5]}")
    difficulty_by_task = {
        task_id: as_int(read_json(path)["difficulty"]) for task_id, path in difficulty_metadata.items()
    }
    for level in (1, 2, 3):
        selected = [row for row in rows if difficulty_by_task[row["task_id"]] == level]
        passed = sum(as_bool(row["success"]) is True for row in selected)
        ci = wilson_interval(passed, len(selected))
        difficulty_rows.append(
            {
                "difficulty": level,
                "tasks": len(selected),
                "passed": passed,
                "tgc": ratio(passed, len(selected)),
                "tgc_ci95_low": ci[0],
                "tgc_ci95_high": ci[1],
                "status": "measured_primary",
                "difficulty_source": "AppWorld ground_truth/metadata.json",
            }
        )
    write_csv(
        DATA_DIR / "appworld_difficulty.csv",
        difficulty_rows,
        list(difficulty_rows[0]),
    )

    task_rows = []
    for row in rows:
        task_rows.append(
            {
                "task_id": row["task_id"],
                "difficulty": difficulty_by_task[row["task_id"]],
                "success": as_bool(row["success"]),
                "passed_tests": as_int(row["num_passed_tests"]),
                "total_tests": as_int(row["num_total_tests"]),
                "turns": as_int(row["num_turns"]),
                "model_proposed_calls": as_int(row["num_model_tool_calls"]),
                "executed_calls": as_int(row["num_tool_calls"]),
                "read_calls": as_int(row["num_read_tool_calls"]),
                "write_calls": as_int(row["num_write_tool_calls"]),
                "invalid_calls": as_int(row["num_invalid_tool_calls"]),
                "out_of_schema_calls": as_int(row["num_out_of_schema_tool_calls"]),
                "duplicate_calls": as_int(row["num_duplicate_tool_calls"]),
                "duplicate_write_actions": as_int(row["num_duplicate_write_actions"]),
                "completion_step": as_int(row["completion_step"]) if row["completion_step"].strip() else "",
                "state_persisted_before_evaluation": as_bool(row["state_persisted_before_evaluation"]),
                "predictor_tokens": as_int(row["predictor_tokens"]),
                "agent_tokens": as_int(row["agent_tokens"]),
                "total_tokens": as_int(row["total_tokens"]),
                "duration_seconds": as_float(row["duration_seconds"]),
                "infra_error_type": row["infra_error_type"],
            }
        )
    write_csv(
        DATA_DIR / "appworld_task_level_derived.csv",
        task_rows,
        list(task_rows[0]),
    )

    efficiency_rows = []
    for label, selected in (("success", successful), ("failure", failed)):
        efficiency_rows.append(
            {
                "outcome": label,
                "tasks": len(selected),
                "mean_turns": mean(as_int(row["num_turns"]) for row in selected),
                "mean_tool_calls": mean(as_int(row["num_tool_calls"]) for row in selected),
                "mean_tokens": mean(as_int(row["total_tokens"]) for row in selected),
                "median_tokens": median(as_int(row["total_tokens"]) for row in selected),
            }
        )
    write_csv(
        DATA_DIR / "appworld_efficiency_by_outcome.csv",
        efficiency_rows,
        list(efficiency_rows[0]),
    )

    association_rows = []
    markers = (
        ("out_of_schema_attempt", "num_out_of_schema_tool_calls"),
        ("duplicate_call", "num_duplicate_tool_calls"),
        ("duplicate_write", "num_duplicate_write_actions"),
        ("max_turn", "num_turns"),
    )
    for marker, field in markers:
        if marker == "max_turn":
            affected = [row for row in rows if as_int(row[field]) >= 50]
        else:
            affected = [row for row in rows if as_int(row[field]) > 0]
        unaffected = [row for row in rows if row not in affected]
        for group, selected in (("present", affected), ("absent", unaffected)):
            passed = sum(as_bool(row["success"]) is True for row in selected)
            association_rows.append(
                {
                    "marker": marker,
                    "group": group,
                    "tasks": len(selected),
                    "passed": passed,
                    "pass_rate": ratio(passed, len(selected)),
                    "interpretation": "descriptive_association_not_causal",
                }
            )
    write_csv(
        DATA_DIR / "appworld_failure_associations.csv",
        association_rows,
        list(association_rows[0]),
    )

    return {
        "summary": summary,
        "rows": rows,
        "primary": primary[0],
        "difficulty": difficulty_rows,
        "efficiency": efficiency_rows,
        "associations": association_rows,
    }


def build_tau2_data() -> dict[str, Any]:
    rows = read_csv(TAU2_AIRLINE_RETAIL) + read_csv(TAU2_TELECOM)
    by_domain: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_domain[row["domain"]].append(row)
    assert {key: len(value) for key, value in by_domain.items()} == {
        "airline": 50,
        "retail": 114,
        "telecom": 114,
    }

    def summarize(domain: str, selected: list[dict[str, str]]) -> dict[str, Any]:
        tasks = len(selected)
        passed = sum(as_bool(row["pass"]) is True for row in selected)
        db_matches = sum(as_bool(row["db_match"]) is True for row in selected)
        tool_calls = sum(as_int(row["num_tool_calls"]) for row in selected)
        invalid_calls = sum(as_int(row["num_invalid_tool_calls"]) for row in selected)
        duplicate_calls = sum(as_int(row["num_duplicate_tool_calls"]) for row in selected)
        duplicate_writes = sum(as_int(row["num_duplicate_write_actions"]) for row in selected)
        total_tokens = sum(as_int(row["total_tokens"]) for row in selected)
        failed = [row for row in selected if as_bool(row["pass"]) is not True]
        durations = [as_float(row["duration_seconds"]) for row in selected]
        turns = [as_int(row["num_turns"]) for row in selected]
        recovery_attempts = sum(as_int(row["num_recovery_attempts"]) for row in selected)
        recovery_successes = sum(as_bool(row["recovery_success"]) is True for row in selected)
        pass_ci = wilson_interval(passed, tasks)
        return {
            "domain": domain,
            "status": "measured_baseline",
            "tasks": tasks,
            "passed": passed,
            "pass_rate": ratio(passed, tasks),
            "pass_rate_ci95_low": pass_ci[0],
            "pass_rate_ci95_high": pass_ci[1],
            "db_matches": db_matches,
            "db_match_rate": ratio(db_matches, tasks),
            "infra_errors": sum(as_bool(row["infra_error"]) is True for row in selected),
            "avg_duration_seconds": mean(durations),
            "avg_turns": mean(turns),
            "p95_turns": nearest_rank([float(value) for value in turns], 0.95),
            "max_turns": max(turns),
            "avg_tool_calls": ratio(tool_calls, tasks),
            "total_tool_calls": tool_calls,
            "total_tokens": total_tokens,
            "avg_tokens": ratio(total_tokens, tasks),
            "invalid_calls": invalid_calls,
            "invalid_call_rate": ratio(invalid_calls, tool_calls),
            "invalid_call_task_rate": ratio(sum(as_int(row["num_invalid_tool_calls"]) > 0 for row in selected), tasks),
            "duplicate_nonwrite_calls": duplicate_calls,
            "duplicate_call_rate": ratio(duplicate_calls, tool_calls),
            "duplicate_call_task_rate": ratio(
                sum(as_int(row["num_duplicate_tool_calls"]) > 0 for row in selected), tasks
            ),
            "duplicate_write_actions": duplicate_writes,
            "duplicate_write_task_rate": ratio(
                sum(as_int(row["num_duplicate_write_actions"]) > 0 for row in selected),
                tasks,
            ),
            "confirmed_partial_completion": sum(as_bool(row["partial_completion"]) is True for row in selected),
            "partial_completion_unknown": sum(row["partial_completion"].strip() == "" for row in selected),
            "confirmed_premature_finish": sum(as_bool(row["premature_finish"]) is True for row in selected),
            "premature_finish_unknown": sum(row["premature_finish"].strip() == "" for row in selected),
            "confirmed_unintended_side_effects": sum(as_int(row["num_unintended_side_effects"]) for row in selected),
            "side_effect_unknown": sum(row["num_unintended_side_effects"].strip() == "" for row in selected),
            "recovery_attempts": recovery_attempts,
            "successful_recoveries": recovery_successes,
            "failed_tasks": len(failed),
            "source": (source_ref(TAU2_TELECOM) if domain == "telecom" else source_ref(TAU2_AIRLINE_RETAIL)),
        }

    domain_rows = [summarize(domain, by_domain[domain]) for domain in ("airline", "retail", "telecom")]
    overall = summarize("overall", rows)
    overall["source"] = f"{source_ref(TAU2_AIRLINE_RETAIL)};{source_ref(TAU2_TELECOM)}"
    output_rows = domain_rows + [overall]
    write_csv(DATA_DIR / "tau2_baseline_metrics.csv", output_rows, list(output_rows[0]))

    task_rows = [
        {
            "domain": row["domain"],
            "task_id": row["task_id"],
            "trial_id": row["trial_id"],
            "passed": as_bool(row["pass"]),
            "db_match": as_bool(row["db_match"]),
            "termination_reason": row["termination_reason"],
            "duration_seconds": as_float(row["duration_seconds"]),
            "turns": as_int(row["num_turns"]),
            "tool_calls": as_int(row["num_tool_calls"]),
            "invalid_calls": as_int(row["num_invalid_tool_calls"]),
            "duplicate_calls": as_int(row["num_duplicate_tool_calls"]),
            "duplicate_write_actions": as_int(row["num_duplicate_write_actions"]),
            "recovery_attempts": as_int(row["num_recovery_attempts"]),
            "recovery_success": as_bool(row["recovery_success"]),
            "total_tokens": as_int(row["total_tokens"]),
            "failure_type": row["failure_type"],
            "partial_completion": as_bool(row["partial_completion"]),
            "premature_finish": as_bool(row["premature_finish"]),
            "unintended_side_effects": (
                as_int(row["num_unintended_side_effects"]) if row["num_unintended_side_effects"].strip() else ""
            ),
        }
        for row in rows
    ]
    write_csv(DATA_DIR / "tau2_task_level_derived.csv", task_rows, list(task_rows[0]))

    failure_counts = Counter(
        (row.get("failure_type") or "none").strip() or "none" for row in rows if as_bool(row["pass"]) is not True
    )
    write_csv(
        DATA_DIR / "tau2_failure_breakdown.csv",
        [
            {"failure_type": key, "count": value, "status": "measured_baseline"}
            for key, value in sorted(failure_counts.items())
        ],
        ["failure_type", "count", "status"],
    )
    return {"rows": rows, "domains": output_rows, "failures": dict(failure_counts)}


def inventory_tree(path: Path) -> dict[str, int]:
    python_files = list(path.rglob("*.py")) if path.exists() else []
    physical_lines = 0
    code_lines = 0
    for file_path in python_files:
        lines = file_path.read_text(encoding="utf-8").splitlines()
        physical_lines += len(lines)
        code_lines += sum(bool(line.strip()) and not line.lstrip().startswith("#") for line in lines)
    return {
        "python_files": len(python_files),
        "physical_lines": physical_lines,
        "nonblank_noncomment_lines": code_lines,
    }


def build_implementation_inventory() -> dict[str, Any]:
    core = ROOT / "agentgate-core"
    source = core / "src" / "agentgate_core"
    components = {}
    for name in (
        "contracts",
        "state_verification",
        "tool_execution_guard",
        "recovery_controller",
        "context_manager",
        "runtime",
        "tracing",
    ):
        components[name] = inventory_tree(source / name)
    test_files = list((core / "tests").glob("test_*.py"))
    test_functions = 0
    for path in test_files:
        test_functions += len(re.findall(r"^(?:async\s+)?def\s+test_", path.read_text(encoding="utf-8"), re.M))
    inventory = {
        "status": "implemented_not_benchmark_validated",
        "core": inventory_tree(source),
        "components": components,
        "json_schemas": len(list((source / "schemas" / "v1").glob("*.schema.json"))),
        "core_test_files": len(test_files),
        "core_test_functions": test_functions,
        "adapters": {
            "agentgate_deerflow": inventory_tree(ROOT / "agentgate-deerflow" / "src"),
            "agentgate_appworld": inventory_tree(ROOT / "benchmark-adapters" / "appworld" / "src"),
        },
        "verification_note": (
            "The final module edits have not been followed by a full unit-test or "
            "benchmark run. Counts describe implementation artifacts, not quality evidence."
        ),
    }
    write_json(DATA_DIR / "implementation_inventory.json", inventory)
    return inventory


def build_protocol_tables() -> None:
    module_rows = [
        {
            "module": "State & Verification",
            "components": (
                "Task State; Evidence Board; Effect Ledger; Post-action Verification; "
                "Completion Gate; Response Grounding"
            ),
            "failure_modes": "goal drift; omitted subgoals; false completion; state/result mismatch",
            "primary_metrics": "TGC; SGC; false-completion rate; verification coverage; verification failure rate",
            "status": "implemented_not_benchmark_validated",
        },
        {
            "module": "Tool Execution Guard",
            "components": "Tool Schema Guard; Effect Guard; Dependency Scheduler; Policy Gate; Dynamic Tool Resolver",
            "failure_modes": (
                "invalid calls; duplicate effects; unsafe parallel writes; missing tools; policy violations"
            ),
            "primary_metrics": (
                "invalid-call rate; out-of-schema rate; duplicate-write rate; blocked unsafe actions; resolver success"
            ),
            "status": "implemented_not_benchmark_validated",
        },
        {
            "module": "Recovery Controller",
            "components": "Failure Classifier; Typed Recovery; Progress Monitor; Stagnation Detector; Recovery Budget",
            "failure_modes": "blind retries; read loops; repeated errors; max-turn exhaustion",
            "primary_metrics": "recovery attempts; recovery success; stagnation rate; max-turn rate; wasted calls",
            "status": "implemented_not_benchmark_validated",
        },
        {
            "module": "Context Manager",
            "components": "Context Builder; Invariant Keeper; Result Projector; Summary; Retrieval; Token Budget",
            "failure_modes": "token growth; buried evidence; forgotten constraints; long-horizon degradation",
            "primary_metrics": "input tokens; tokens/task; context-budget violations; invariant retention; TGC delta",
            "status": "implemented_not_benchmark_validated",
        },
    ]
    write_csv(DATA_DIR / "module_traceability.csv", module_rows, list(module_rows[0]))

    metric_rows = [
        ("TGC", "task_goal_completion", "successful tasks / all valid tasks", "task", "primary"),
        (
            "SGC",
            "scenario_goal_completion",
            "scenarios whose three variants all pass / complete scenarios",
            "scenario",
            "primary",
        ),
        (
            "Evaluator test pass rate",
            "evaluator_test_pass_rate",
            "passed evaluator checks / all evaluator checks",
            "check",
            "diagnostic",
        ),
        (
            "False-completion candidate rate",
            "false_completion_candidate_rate",
            "failed tasks that invoked completion / tasks that invoked completion",
            "task",
            "diagnostic; trace review required for causal label",
        ),
        (
            "Max-Turn Rate",
            "max_turn_rate",
            "tasks reaching configured turn cap / valid tasks",
            "task",
            "primary reliability",
        ),
        (
            "Invalid Call Rate",
            "invalid_call_rate",
            "invalid proposed calls / all model-proposed calls",
            "call",
            "primary reliability",
        ),
        (
            "Out-of-Schema Rate",
            "out_of_schema_call_rate",
            "calls absent from active schema / all model-proposed calls",
            "call",
            "primary reliability",
        ),
        (
            "Duplicate Call Rate",
            "duplicate_call_rate",
            "semantic or exact duplicate calls / executed calls",
            "call",
            "primary efficiency",
        ),
        (
            "Duplicate Write Rate",
            "duplicate_write_action_rate",
            "duplicate write actions / executed write actions",
            "write call",
            "primary safety",
        ),
        (
            "Verification coverage",
            "verification_coverage",
            "verified required effects / required effects",
            "effect",
            "SafeDesk pending",
        ),
        (
            "Verification failure rate",
            "verification_failure_rate",
            "failed verification checks / executed verification checks",
            "check",
            "SafeDesk pending",
        ),
        (
            "Recovery success rate",
            "recovery_success_rate",
            "successful recoveries / triggered recoveries",
            "recovery",
            "SafeDesk pending",
        ),
        ("Average tokens", "avg_tokens_per_task", "total reported tokens / valid tasks", "task", "primary efficiency"),
        ("P95 tokens", "p95_tokens_per_task", "95th percentile of per-task total tokens", "task", "primary efficiency"),
        (
            "Average duration",
            "avg_duration_seconds",
            "mean monotonic wall-clock duration over valid values",
            "task",
            "secondary efficiency",
        ),
        (
            "Unintended side effects",
            "num_unintended_side_effects",
            "verified changes outside the task contract",
            "effect",
            "manual/evaluator-backed only",
        ),
    ]
    write_csv(
        DATA_DIR / "metrics_dictionary.csv",
        [
            {
                "display_name": item[0],
                "field": item[1],
                "definition": item[2],
                "unit": item[3],
                "reporting_role": item[4],
            }
            for item in metric_rows
        ],
        ["display_name", "field", "definition", "unit", "reporting_role"],
    )

    experiment_rows = [
        (
            "E0",
            "AppWorld",
            "test_challenge",
            "DeepSeek V4 Flash",
            "plain function calling",
            417,
            "measured_primary",
            "primary full baseline; state persistence repaired",
        ),
        (
            "E1",
            "AppWorld",
            "test_normal",
            "DeepSeek V4 Flash",
            "plain function calling",
            168,
            "pending_rerun",
            "historical full run predates state persistence fix",
        ),
        (
            "E2",
            "AppWorld",
            "test_normal",
            "Qwen3-14B",
            "plain function calling; no thinking",
            168,
            "pending",
            "matched baseline",
        ),
        (
            "E3",
            "AppWorld",
            "test_challenge",
            "Qwen3-14B",
            "plain function calling; no thinking",
            417,
            "pending",
            "matched baseline",
        ),
        ("E4", "AppWorld", "test_normal", "Qwen3-14B", "SafeDesk; no thinking", 168, "pending", "main comparison"),
        ("E5", "AppWorld", "test_challenge", "Qwen3-14B", "SafeDesk; no thinking", 417, "pending", "main comparison"),
        (
            "E6",
            "tau2-bench",
            "airline+retail+telecom-base",
            "GLM-5 agent; DeepSeek user/judge",
            "DeerFlow baseline; no thinking",
            278,
            "measured_diagnostic",
            "roles use different models; tokens not role-separated",
        ),
        (
            "E7",
            "tau2-bench",
            "frozen matched subset",
            "Qwen3-14B",
            "plain function calling; no thinking",
            "TBD",
            "pending",
            "matched conversational baseline",
        ),
        (
            "E8",
            "tau2-bench",
            "same as E7",
            "Qwen3-14B",
            "SafeDesk; no thinking",
            "TBD",
            "pending",
            "matched SafeDesk comparison",
        ),
    ]
    write_csv(
        DATA_DIR / "experiment_matrix.csv",
        [
            {
                "id": row[0],
                "benchmark": row[1],
                "split": row[2],
                "model": row[3],
                "runtime": row[4],
                "tasks": row[5],
                "status": row[6],
                "notes": row[7],
            }
            for row in experiment_rows
        ],
        ["id", "benchmark", "split", "model", "runtime", "tasks", "status", "notes"],
    )

    ablation_rows = [
        ("A0", "Baseline", "none", "isolates ungoverned runner"),
        ("A1", "+StateVerification", "State & Verification", "tests evidence-grounded completion"),
        ("A2", "+ToolGuard", "A1 + Tool Execution Guard", "tests schema/effect/dependency controls"),
        ("A3", "+Recovery", "A2 + Recovery Controller", "tests typed recovery and stagnation handling"),
        ("A4", "+Context", "all four modules", "full SafeDesk"),
        ("B1", "Full minus StateVerification", "all except State & Verification", "leave-one-module-out"),
        ("B2", "Full minus ToolGuard", "all except Tool Execution Guard", "leave-one-module-out"),
        ("B3", "Full minus Recovery", "all except Recovery Controller", "leave-one-module-out"),
        ("B4", "Full minus Context", "all except Context Manager", "leave-one-module-out"),
    ]
    write_csv(
        DATA_DIR / "ablation_matrix.csv",
        [
            {
                "id": row[0],
                "condition": row[1],
                "enabled_modules": row[2],
                "purpose": row[3],
                "status": "pending",
                "tgc": "",
                "sgc": "",
                "avg_tokens": "",
                "false_completion_rate": "",
                "max_turn_rate": "",
            }
            for row in ablation_rows
        ],
        [
            "id",
            "condition",
            "enabled_modules",
            "purpose",
            "status",
            "tgc",
            "sgc",
            "avg_tokens",
            "false_completion_rate",
            "max_turn_rate",
        ],
    )

    external_rows = [
        {
            "benchmark": "AppWorld",
            "method": "Alibaba Cloud ApsaraLab AgentRL",
            "model": "Qwen3-14B",
            "date": "2026-02-15",
            "test_normal_tgc": 0.869,
            "test_normal_sgc": 0.804,
            "test_challenge_tgc": 0.676,
            "test_challenge_sgc": 0.504,
            "comparison_status": "external_reference_not_directly_comparable",
            "source": "https://github.com/StonyBrookNLP/appworld-leaderboard/blob/main/experiments/outputs/_leaderboard.json",
        }
    ]
    write_csv(DATA_DIR / "external_benchmarks.csv", external_rows, list(external_rows[0]))


def build_status_tables() -> None:
    historical = read_json(APPWORLD_HISTORICAL_NORMAL)
    state_smoke = read_json(APPWORLD_STATE_SMOKE)
    qwen = read_json(APPWORLD_QWEN_PILOT)
    shadow = read_json(APPWORLD_SHADOW)
    conversion = read_json(APPWORLD_CONVERSION)
    rows = [
        {
            "artifact": historical["experiment_id"],
            "scope": "AppWorld test_normal; 168 tasks",
            "observed_result": (
                f"TGC={historical['task_goal_completion']:.6f}; SGC={historical['scenario_goal_completion']:.6f}"
            ),
            "validity": "invalid_for_primary_claim",
            "reason": "run predates the state-persistence correction; retained for audit only",
        },
        {
            "artifact": state_smoke["experiment_id"],
            "scope": "AppWorld test_normal; 5 selected tasks",
            "observed_result": f"TGC={state_smoke['task_goal_completion']:.6f}; 5/5 state persisted",
            "validity": "smoke_only",
            "reason": "selected sanity check is not representative of the split",
        },
        {
            "artifact": qwen["experiment_id"],
            "scope": "AppWorld train; 5 selected tasks",
            "observed_result": f"TGC={qwen['task_goal_completion']:.6f}",
            "validity": "invalid_for_target_baseline",
            "reason": "thinking was enabled and the sample is a non-representative train pilot",
        },
        {
            "artifact": "state_verification_shadow_audit",
            "scope": f"AppWorld challenge smoke; {shadow['files']} traces",
            "observed_result": (
                f"completion_attempts={shadow['counts']['completion_attempts']}; "
                f"would_block={shadow['counts']['would_block']}"
            ),
            "validity": "diagnostic_only",
            "reason": "task contracts and verified evidence were unavailable; not an outcome evaluation",
        },
        {
            "artifact": "appworld_trace_conversion",
            "scope": f"{conversion['trace_files']} traces; {conversion['catalog_tool_count']} catalog tools",
            "observed_result": (
                f"actions={conversion['aggregate']['actions']}; "
                f"effects={conversion['aggregate']['effects']}; "
                f"missing_results={conversion['aggregate']['missing_tool_results']}"
            ),
            "validity": "implementation_diagnostic",
            "reason": "demonstrates trace conversion coverage, not SafeDesk effectiveness",
        },
    ]
    write_csv(DATA_DIR / "artifact_validity.csv", rows, list(rows[0]))

    claims = [
        (
            "C1",
            "The repaired DeepSeek baseline completes 174/417 AppWorld test_challenge tasks (41.73% TGC).",
            "measured",
            source_ref(APPWORLD_PRIMARY_SUMMARY),
            "allowed",
        ),
        (
            "C2",
            "The same baseline reaches 41/139 complete scenarios (29.50% SGC).",
            "measured",
            source_ref(APPWORLD_PRIMARY_SUMMARY),
            "allowed",
        ),
        (
            "C3",
            "Failed AppWorld tasks use substantially more turns, calls, and tokens than successful tasks.",
            "measured_descriptive",
            "paper/data/appworld_efficiency_by_outcome.csv",
            "allowed",
        ),
        (
            "C4",
            "Out-of-schema and duplicate-call markers are associated with lower task success.",
            "measured_association",
            "paper/data/appworld_failure_associations.csv",
            "allowed_with_noncausal_caveat",
        ),
        ("C5", "SafeDesk improves AppWorld TGC/SGC.", "pending", "E4/E5 not run", "prohibited_until_measured"),
        ("C6", "SafeDesk reduces token use.", "pending", "A0-A4 not run", "prohibited_until_measured"),
        (
            "C7",
            "SafeDesk prevents false completion.",
            "pending",
            "requires matched completion audit",
            "prohibited_until_measured",
        ),
        (
            "C8",
            "The four core modules and unified runtime are implemented.",
            "implementation_fact",
            "paper/data/implementation_inventory.json",
            "allowed_with_test-status_caveat",
        ),
        (
            "C9",
            "The 5-trace shadow audit would block four completion attempts under conservative missing-contract rules.",
            "diagnostic",
            source_ref(APPWORLD_SHADOW),
            "allowed_as_diagnostic_only",
        ),
        (
            "C10",
            "The historical test_normal result is a valid final baseline.",
            "invalid",
            source_ref(APPWORLD_HISTORICAL_NORMAL),
            "prohibited",
        ),
    ]
    write_csv(
        DATA_DIR / "claims_registry.csv",
        [
            {
                "claim_id": row[0],
                "claim": row[1],
                "evidence_status": row[2],
                "evidence": row[3],
                "publication_rule": row[4],
            }
            for row in claims
        ],
        ["claim_id", "claim", "evidence_status", "evidence", "publication_rule"],
    )


def build_manifest(generated_at: str) -> None:
    source_paths = [
        Path(__file__).resolve(),
        APPWORLD_PRIMARY_SUMMARY,
        APPWORLD_PRIMARY_RESULTS,
        APPWORLD_HISTORICAL_NORMAL,
        APPWORLD_STATE_SMOKE,
        APPWORLD_QWEN_PILOT,
        APPWORLD_SHADOW,
        APPWORLD_CONVERSION,
        TAU2_AIRLINE_RETAIL,
        TAU2_TELECOM,
        ROOT / "SafeDesk_核心模块开发计划.md",
    ]
    generated_paths = sorted(path for path in DATA_DIR.iterdir() if path.name != "data_manifest.json")
    appworld_rows = read_csv(APPWORLD_PRIMARY_RESULTS)
    difficulty_paths = [
        ROOT / "benchmarks" / "appworld-root" / "data" / "tasks" / row["task_id"] / "ground_truth" / "metadata.json"
        for row in appworld_rows
    ]
    manifest = {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "generator": source_ref(Path(__file__).resolve()),
        "integrity_policy": (
            "Measured values are derived from local artifacts. Missing experiments "
            "remain pending; no benchmark outcome is synthesized."
        ),
        "sources": [
            {
                "path": source_ref(path),
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in source_paths
        ],
        "source_collections": [
            {
                "name": "appworld_primary_task_difficulty_metadata",
                "path_pattern": "benchmarks/appworld-root/data/tasks/<task_id>/ground_truth/metadata.json",
                "files": len(difficulty_paths),
                "sha256": collection_sha256(difficulty_paths),
            }
        ],
        "generated_files": [
            {
                "path": source_ref(path),
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in generated_paths
        ],
    }
    write_json(DATA_DIR / "data_manifest.json", manifest)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    appworld = build_appworld_data()
    tau2 = build_tau2_data()
    inventory = build_implementation_inventory()
    build_protocol_tables()
    build_status_tables()
    build_manifest(generated_at)
    print(
        json.dumps(
            {
                "generated_at": generated_at,
                "appworld_tasks": len(appworld["rows"]),
                "appworld_tgc": appworld["primary"]["tgc"],
                "tau2_tasks": len(tau2["rows"]),
                "core_python_files": inventory["core"]["python_files"],
                "json_schemas": inventory["json_schemas"],
                "output": str(DATA_DIR),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
