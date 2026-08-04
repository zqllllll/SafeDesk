from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


FIELDNAMES = [
    "experiment_id",
    "run_id",
    "timestamp",
    "benchmark",
    "domain",
    "task_id",
    "trial_id",
    "agent_name",
    "agent_backend",
    "model",
    "reward",
    "pass",
    "db_match",
    "termination_reason",
    "infra_error",
    "duration_seconds",
    "total_tokens",
    "num_turns",
    "num_tool_calls",
    "num_read_tool_calls",
    "num_write_tool_calls",
    "num_other_tool_calls",
    "num_failed_tool_calls",
    "partial_completion",
    "premature_finish",
    "num_invalid_tool_calls",
    "num_duplicate_tool_calls",
    "num_duplicate_write_actions",
    "num_evaluator_checks",
    "num_evaluator_failures",
    "num_recovery_attempts",
    "recovery_success",
    "num_unintended_side_effects",
    "infra_error_type",
    "trace_path",
    "failure_type",
    "failure_note",
    "results_path",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract compact per-task metrics from tau2 results.json files."
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="Path(s) to tau2 results.json files or simulation directories.",
    )
    parser.add_argument(
        "--manifest",
        action="append",
        default=[],
        help="Baseline manifest.csv file(s); existing result_path entries are collected.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for metrics_summary.csv and run_summary.json.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional run id for the combined extracted metrics.",
    )
    args = parser.parse_args()

    if not args.inputs and not args.manifest:
        parser.error("provide at least one input path or --manifest")

    result_paths: list[Path] = []
    for value in args.inputs:
        result_paths.extend(_resolve_results_paths(Path(value)))
    for value in args.manifest:
        result_paths.extend(_resolve_manifest_results(Path(value)))
    result_paths = _unique_paths(result_paths)
    rows: list[dict[str, Any]] = []
    source_summaries: list[dict[str, Any]] = []
    combined_run_id = args.run_id or _default_run_id(result_paths)

    for result_path in result_paths:
        data = _load_json(result_path)
        source_rows = _extract_rows(data, result_path, combined_run_id)
        rows.extend(source_rows)
        source_summaries.append(_summarize_rows(source_rows, result_path.parent.name))

    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir(combined_run_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    metrics_csv = output_dir / "metrics_summary.csv"
    with metrics_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    run_summary = _summarize_rows(rows, combined_run_id)
    run_summary["sources"] = source_summaries
    run_summary["token_scope"] = (
        "Sum of usage.prompt_tokens + usage.completion_tokens on messages stored "
        "in tau2 simulation trajectories. Evaluator/judge calls are included only "
        "if tau2 stored them as trajectory messages."
    )
    summary_json = output_dir / "run_summary.json"
    with summary_json.open("w", encoding="utf-8") as f:
        json.dump(run_summary, f, ensure_ascii=False, indent=2)

    print(f"wrote {metrics_csv}")
    print(f"wrote {summary_json}")


def _resolve_results_paths(path: Path) -> list[Path]:
    if path.is_dir():
        candidate = path / "results.json"
        if candidate.exists():
            return [candidate]
        matches = sorted(
            child
            for child in path.rglob("results.json")
            if child.parent.name != "results"
        )
        if matches:
            return matches
    if path.name != "results.json" and not path.exists():
        simulation_candidate = (
            Path("data") / "simulations" / str(path) / "results.json"
        )
        if simulation_candidate.exists():
            return [simulation_candidate]
    if not path.exists():
        raise FileNotFoundError(f"Results path not found: {path}")
    return [path]


def _resolve_manifest_results(path: Path) -> list[Path]:
    if not path.exists():
        raise FileNotFoundError(f"Manifest path not found: {path}")
    paths: list[Path] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            result_path = row.get("result_path")
            if not result_path:
                continue
            candidate = Path(result_path)
            if candidate.exists():
                paths.append(candidate)
    return paths


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen = set()
    unique = []
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _default_run_id(result_paths: list[Path]) -> str:
    if len(result_paths) == 1:
        return result_paths[0].parent.name
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"combined_tau2_{stamp}"


def _default_output_dir(run_id: str) -> Path:
    return Path("..") / "results" / run_id


def _extract_rows(
    data: dict[str, Any],
    result_path: Path,
    experiment_id: str,
) -> list[dict[str, Any]]:
    info = data.get("info") or {}
    agent_info = info.get("agent_info") or {}
    environment_info = info.get("environment_info") or {}
    domain = environment_info.get("domain_name") or _infer_domain(result_path)
    model = agent_info.get("llm") or ""
    agent_args = agent_info.get("llm_args") or {}
    agent_backend = agent_args.get("deerflow_backend") or agent_info.get("implementation") or ""
    agent_name = agent_info.get("implementation") or ""
    timestamp = data.get("timestamp") or ""

    rows = []
    for simulation in data.get("simulations") or []:
        reward_info = simulation.get("reward_info")
        reward = _as_float((reward_info or {}).get("reward"))
        infra_error = _is_infra_error(simulation)
        passed = (not infra_error) and reward == 1.0
        messages = simulation.get("messages") or []
        total_tokens = _sum_total_tokens(messages)
        tool_counts = _count_tools(simulation, reward_info)
        tool_diagnostics = _tool_diagnostics(simulation, reward_info)
        verification_counts = _verification_counts(reward_info)
        recovery_metrics = _recovery_metrics(simulation, reward_info)
        failure_type, failure_note = _classify_failure(simulation, reward_info)

        rows.append(
            {
                "experiment_id": experiment_id,
                "run_id": _task_run_id(experiment_id, domain, simulation),
                "timestamp": timestamp,
                "benchmark": "tau2",
                "domain": domain,
                "task_id": simulation.get("task_id", ""),
                "trial_id": simulation.get("trial", ""),
                "agent_name": agent_name,
                "agent_backend": agent_backend,
                "model": model,
                "reward": _format_float(reward),
                "pass": _bool_text(passed),
                "db_match": _nullable_bool_text(_db_match(reward_info)),
                "termination_reason": simulation.get("termination_reason", ""),
                "infra_error": _bool_text(infra_error),
                "duration_seconds": _format_float(_as_float(simulation.get("duration"))),
                "total_tokens": total_tokens,
                "num_turns": _count_turns(messages),
                "num_tool_calls": tool_counts["tool_calls"],
                "num_read_tool_calls": tool_counts["read_tool_calls"],
                "num_write_tool_calls": tool_counts["write_tool_calls"],
                "num_other_tool_calls": tool_counts["other_tool_calls"],
                "num_failed_tool_calls": tool_counts["failed_tool_calls"],
                "partial_completion": _nullable_bool_text(
                    _partial_completion(simulation, reward_info)
                ),
                "premature_finish": _nullable_bool_text(
                    _premature_finish(simulation, reward_info)
                ),
                "num_invalid_tool_calls": tool_diagnostics["invalid_tool_calls"],
                "num_duplicate_tool_calls": tool_diagnostics["duplicate_tool_calls"],
                "num_duplicate_write_actions": tool_diagnostics[
                    "duplicate_write_actions"
                ],
                "num_evaluator_checks": verification_counts["checks"],
                "num_evaluator_failures": verification_counts["failures"],
                "num_recovery_attempts": recovery_metrics["attempts"],
                "recovery_success": _nullable_bool_text(recovery_metrics["success"]),
                "num_unintended_side_effects": _nullable_int_text(
                    _num_unintended_side_effects(reward_info)
                ),
                "infra_error_type": _infra_error_type(simulation),
                "trace_path": _trace_path(result_path, simulation),
                "failure_type": failure_type,
                "failure_note": failure_note,
                "results_path": str(result_path),
            }
        )
    return rows


def _infer_domain(result_path: Path) -> str:
    name = result_path.parent.name
    for domain in ("airline", "retail", "telecom", "mock"):
        if domain in name:
            return domain
    return ""


def _task_run_id(
    experiment_id: str,
    domain: str,
    simulation: dict[str, Any],
) -> str:
    sim_id = simulation.get("id")
    if sim_id:
        return str(sim_id)
    task_id = _safe_id_text(simulation.get("task_id", ""))
    trial = _safe_id_text(simulation.get("trial", ""))
    return f"{experiment_id}:{domain}:{task_id}:{trial}"


def _safe_id_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in text)


def _sum_total_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        usage = message.get("usage") or {}
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if prompt is not None or completion is not None:
            total += int(prompt or 0) + int(completion or 0)
            continue
        raw_usage = ((message.get("raw_data") or {}).get("usage") or {})
        if raw_usage.get("total_tokens") is not None:
            total += int(raw_usage["total_tokens"])
    return total


def _count_turns(messages: list[dict[str, Any]]) -> int:
    return sum(1 for message in messages if message.get("role") in {"assistant", "user"})


def _count_tools(
    simulation: dict[str, Any],
    reward_info: dict[str, Any] | None,
) -> dict[str, int]:
    messages = simulation.get("messages") or []
    tool_calls = 0
    failed_tool_calls = 0
    for message in messages:
        if message.get("role") == "assistant":
            tool_calls += len(message.get("tool_calls") or [])
        if message.get("role") == "tool" and message.get("error"):
            failed_tool_calls += 1

    read_tool_calls = 0
    write_tool_calls = 0
    for check in (reward_info or {}).get("action_checks") or []:
        if check.get("tool_type") == "read":
            read_tool_calls += 1
        elif check.get("tool_type") == "write":
            write_tool_calls += 1

    return {
        "tool_calls": tool_calls,
        "read_tool_calls": read_tool_calls,
        "write_tool_calls": write_tool_calls,
        "other_tool_calls": max(tool_calls - read_tool_calls - write_tool_calls, 0),
        "failed_tool_calls": failed_tool_calls,
    }


def _tool_diagnostics(
    simulation: dict[str, Any],
    reward_info: dict[str, Any] | None,
) -> dict[str, int]:
    messages = simulation.get("messages") or []
    write_names = _write_tool_names(reward_info)
    tool_calls = _assistant_tool_calls(messages)
    read_like_signatures = [
        call["signature"] for call in tool_calls if call["name"] not in write_names
    ]
    write_signatures = [
        call["signature"] for call in tool_calls if call["name"] in write_names
    ]

    return {
        "invalid_tool_calls": _count_invalid_tool_calls(messages),
        "duplicate_tool_calls": _count_duplicates(read_like_signatures),
        "duplicate_write_actions": _count_duplicates(write_signatures),
    }


def _assistant_tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    calls = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            name = str(call.get("name") or "")
            calls.append(
                {
                    "name": name,
                    "signature": _tool_call_signature(call),
                }
            )
    return calls


def _tool_call_signature(call: dict[str, Any]) -> str:
    args = call.get("arguments")
    if args is None:
        args = call.get("args")
    try:
        args_text = json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
    except TypeError:
        args_text = str(args)
    return f"{call.get('name', '')}:{args_text}"


def _write_tool_names(reward_info: dict[str, Any] | None) -> set[str]:
    names = set()
    for check in (reward_info or {}).get("action_checks") or []:
        if check.get("tool_type") != "write":
            continue
        action = check.get("action") or {}
        if action.get("name"):
            names.add(str(action["name"]))
    return names


def _count_duplicates(signatures: list[str]) -> int:
    counts = Counter(signatures)
    return sum(count - 1 for count in counts.values() if count > 1)


def _count_invalid_tool_calls(messages: list[dict[str, Any]]) -> int:
    count = 0
    for message in messages:
        if message.get("role") != "tool":
            continue
        content = str(message.get("content") or "")
        if message.get("error") is True or content.lower().startswith("error:"):
            count += 1
    return count


def _verification_counts(
    reward_info: dict[str, Any] | None,
) -> dict[str, int]:
    if not reward_info:
        return {"checks": 0, "failures": 0}

    checks = 0
    failures = 0
    db_match = _db_match(reward_info)
    if db_match is not None:
        checks += 1
        if db_match is False:
            failures += 1

    for check in reward_info.get("env_assertions") or []:
        checks += 1
        if check.get("met") is False or check.get("reward") == 0:
            failures += 1

    return {"checks": checks, "failures": failures}


def _recovery_metrics(
    simulation: dict[str, Any],
    reward_info: dict[str, Any] | None,
) -> dict[str, bool | int | None]:
    messages = simulation.get("messages") or []
    attempts = 0
    pending_failure = False
    for message in messages:
        if message.get("role") == "tool" and _tool_message_failed(message):
            pending_failure = True
            continue
        if pending_failure and message.get("role") == "assistant":
            if message.get("tool_calls"):
                attempts += 1
                pending_failure = False
            elif message.get("content"):
                pending_failure = False

    if attempts == 0:
        success = None
    else:
        success = _as_float((reward_info or {}).get("reward")) == 1.0
    return {"attempts": attempts, "success": success}


def _tool_message_failed(message: dict[str, Any]) -> bool:
    content = str(message.get("content") or "")
    return message.get("error") is True or content.lower().startswith("error:")


def _num_unintended_side_effects(reward_info: dict[str, Any] | None) -> int | None:
    if not reward_info:
        return None
    reward = _as_float(reward_info.get("reward"))
    if reward == 1.0:
        return 0
    # A failed/missing expected write is not enough evidence for an unintended
    # side effect. Keep this blank until a trace-level diff identifies extra
    # or unrelated state mutations.
    return None


def _partial_completion(
    simulation: dict[str, Any],
    reward_info: dict[str, Any] | None,
) -> bool | None:
    if _is_infra_error(simulation) or not reward_info:
        return None
    reward = _as_float(reward_info.get("reward"))
    if reward is None:
        return None
    if reward == 1.0:
        return False

    # Some evaluator subchecks may pass even when the actual user goal was not
    # partially completed. Keep failures blank until we have a goal-level
    # partial-completion classifier.
    return None


def _premature_finish(
    simulation: dict[str, Any],
    reward_info: dict[str, Any] | None,
) -> bool | None:
    if _is_infra_error(simulation) or not reward_info:
        return None
    reward = _as_float(reward_info.get("reward"))
    if reward is None:
        return None
    if reward == 1.0:
        return False
    termination = simulation.get("termination_reason")
    if termination == "agent_stop":
        return True
    # `user_stop` can mean the user simulator ended after observing failure, so
    # do not infer premature agent finish from it.
    return None


def _component_success_values(reward_info: dict[str, Any]) -> list[bool]:
    values: list[bool] = []
    db_match = _db_match(reward_info)
    if db_match is not None:
        values.append(db_match)
    for check in reward_info.get("env_assertions") or []:
        values.append(not (check.get("met") is False or check.get("reward") == 0))
    for check in reward_info.get("nl_assertions") or []:
        values.append(check.get("met") is not False)
    for check in reward_info.get("communicate_checks") or []:
        values.append(check.get("met") is not False)
    for check in reward_info.get("action_checks") or []:
        values.append(
            not (
                check.get("action_match") is False
                or check.get("action_reward") == 0
            )
        )
    return values


def _infra_error_type(simulation: dict[str, Any]) -> str:
    if not _is_infra_error(simulation):
        return ""
    info = simulation.get("info") or {}
    raw = " ".join(
        str(value)
        for value in (
            info.get("error_type"),
            info.get("error"),
            info.get("exception"),
            simulation.get("termination_reason"),
        )
        if value
    ).lower()
    if "timeout" in raw or "timed out" in raw:
        return "timeout"
    if "rate" in raw or "429" in raw or "limit" in raw:
        return "rate_limit"
    if "sandbox" in raw:
        return "sandbox_error"
    if "model" in raw or "api" in raw or "llm" in raw or "service" in raw:
        return "model_service_error"
    return "unknown_infra_error"


def _trace_path(result_path: Path, simulation: dict[str, Any]) -> str:
    sims_dir = result_path.parent / "simulations"
    sim_id = simulation.get("id")
    if sim_id:
        candidate = sims_dir / f"{sim_id}.json"
        if candidate.exists():
            return str(candidate)
    return ""


def _classify_failure(
    simulation: dict[str, Any],
    reward_info: dict[str, Any] | None,
) -> tuple[str, str]:
    if _is_infra_error(simulation):
        info = simulation.get("info") or {}
        return "infra_error", _short_text(info.get("error") or info.get("error_type"))
    if reward_info is None:
        return "unknown_failure", "missing reward_info"
    if _as_float(reward_info.get("reward")) == 1.0:
        return "none", ""

    breakdown = reward_info.get("reward_breakdown") or {}
    if breakdown.get("DB") == 0 or _db_match(reward_info) is False:
        return "db_mismatch", "database state did not match expected state"
    if breakdown.get("NL_ASSERTION") == 0:
        return "nl_assertion_failed", _first_failed_nl_note(reward_info)
    if breakdown.get("ENV_ASSERTION") == 0:
        return "env_assertion_failed", _first_failed_env_note(reward_info)
    if breakdown.get("COMMUNICATE") == 0:
        return "communicate_failed", _first_failed_communicate_note(reward_info)
    if breakdown.get("ACTION") == 0:
        return "tool_action_mismatch", _first_failed_action_note(reward_info)

    termination = simulation.get("termination_reason")
    if termination in {"max_steps", "agent_stop", "user_stop"}:
        return termination or "unknown_failure", ""
    return "unknown_failure", _first_available_failure_note(reward_info)


def _is_infra_error(simulation: dict[str, Any]) -> bool:
    return simulation.get("termination_reason") == "infrastructure_error"


def _db_match(reward_info: dict[str, Any] | None) -> bool | None:
    if not reward_info:
        return None
    db_check = reward_info.get("db_check")
    if not isinstance(db_check, dict):
        return None
    return db_check.get("db_match")


def _first_failed_nl_note(reward_info: dict[str, Any]) -> str:
    for check in reward_info.get("nl_assertions") or []:
        if check.get("met") is False:
            return _short_text(check.get("justification") or check.get("nl_assertion"))
    return ""


def _first_failed_env_note(reward_info: dict[str, Any]) -> str:
    for check in reward_info.get("env_assertions") or []:
        if check.get("met") is False or check.get("reward") == 0:
            return _short_text(check.get("justification") or check.get("assertion"))
    return ""


def _first_failed_communicate_note(reward_info: dict[str, Any]) -> str:
    for check in reward_info.get("communicate_checks") or []:
        if check.get("met") is False:
            return _short_text(check.get("justification") or check.get("info"))
    return ""


def _first_failed_action_note(reward_info: dict[str, Any]) -> str:
    for check in reward_info.get("action_checks") or []:
        if check.get("action_match") is False or check.get("action_reward") == 0:
            action = check.get("action") or {}
            return _short_text(f"{action.get('name', 'action')} did not match")
    return ""


def _first_available_failure_note(reward_info: dict[str, Any]) -> str:
    for getter in (
        _first_failed_nl_note,
        _first_failed_env_note,
        _first_failed_communicate_note,
        _first_failed_action_note,
    ):
        note = getter(reward_info)
        if note:
            return note
    return ""


def _summarize_rows(rows: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    total_tasks = len(rows)
    rewards = [_as_float(row.get("reward")) or 0.0 for row in rows]
    pass_count = sum(1 for row in rows if row.get("pass") == "true")
    db_values = [row.get("db_match") for row in rows if row.get("db_match") != ""]
    db_match_count = sum(1 for value in db_values if value == "true")
    infra_error_count = sum(1 for row in rows if row.get("infra_error") == "true")
    total_tokens = sum(int(row.get("total_tokens") or 0) for row in rows)
    durations = [_as_float(row.get("duration_seconds")) or 0.0 for row in rows]
    recovery_values = [row.get("recovery_success") for row in rows if row.get("recovery_success")]
    recovery_success_count = sum(1 for value in recovery_values if value == "true")

    return {
        "experiment_id": run_id,
        "benchmark": "tau2",
        "domains": sorted({row.get("domain", "") for row in rows if row.get("domain")}),
        "models": sorted({row.get("model", "") for row in rows if row.get("model")}),
        "num_tasks": total_tasks,
        "average_reward": _round(sum(rewards) / total_tasks if total_tasks else 0.0),
        "pass_rate": _round(pass_count / total_tasks if total_tasks else 0.0),
        "db_match_rate": _round(db_match_count / len(db_values) if db_values else 0.0),
        "infra_error_count": infra_error_count,
        "total_tokens": total_tokens,
        "avg_tokens_per_task": _round(total_tokens / total_tasks if total_tasks else 0.0),
        "avg_duration_seconds": _round(sum(durations) / total_tasks if total_tasks else 0.0),
        "partial_completion_count": _count_true(rows, "partial_completion"),
        "premature_finish_count": _count_true(rows, "premature_finish"),
        "num_invalid_tool_calls": _sum_int(rows, "num_invalid_tool_calls"),
        "num_duplicate_tool_calls": _sum_int(rows, "num_duplicate_tool_calls"),
        "num_duplicate_write_actions": _sum_int(rows, "num_duplicate_write_actions"),
        "num_other_tool_calls": _sum_int(rows, "num_other_tool_calls"),
        "num_evaluator_checks": _sum_int(rows, "num_evaluator_checks"),
        "num_evaluator_failures": _sum_int(rows, "num_evaluator_failures"),
        "num_recovery_attempts": _sum_int(rows, "num_recovery_attempts"),
        "recovery_success_count": recovery_success_count,
        "recovery_success_rate": _round(
            recovery_success_count / len(recovery_values) if recovery_values else 0.0
        ),
        "num_unintended_side_effects": _sum_int(rows, "num_unintended_side_effects"),
        "infra_error_types": _value_counts(rows, "infra_error_type"),
        "failure_counts": _failure_counts(rows),
    }


def _sum_int(rows: list[dict[str, Any]], key: str) -> int:
    total = 0
    for row in rows:
        try:
            total += int(row.get(key) or 0)
        except (TypeError, ValueError):
            pass
    return total


def _count_true(rows: list[dict[str, Any]], key: str) -> int:
    return sum(1 for row in rows if row.get(key) == "true")


def _value_counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(key)
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _failure_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = row.get("failure_type") or "unknown_failure"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _as_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.4f}"


def _round(value: float) -> float:
    return round(value, 4)


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _nullable_bool_text(value: bool | None) -> str:
    if value is None:
        return ""
    return _bool_text(value)


def _nullable_int_text(value: int | None) -> str:
    if value is None:
        return ""
    return str(value)


def _short_text(value: Any, limit: int = 300) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


if __name__ == "__main__":
    main()
