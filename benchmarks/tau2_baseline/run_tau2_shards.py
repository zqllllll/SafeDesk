from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run tau2 manifest shards with skip/resume behavior."
    )
    parser.add_argument("run_dir", help="Directory created by make_tau2_manifest.py.")
    parser.add_argument("--domains", default=None)
    parser.add_argument("--shards", default=None, help="Comma-separated shard ids.")
    parser.add_argument("--max-shards", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--auto-resume", action="store_true", default=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--max-concurrency", type=int, default=None)
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    manifest_path = run_dir / "manifest.csv"
    run_config = _load_json(run_dir / "config.json")
    benchmarks_dir = Path(__file__).resolve().parents[1]
    tau2_dir = benchmarks_dir / "tau2-bench"
    runner_path = benchmarks_dir / "tau2_deerflow_adapter" / "run_tau2_deerflow.py"

    rows = _read_manifest(manifest_path)
    rows_by_shard = _group_by(rows, "shard_id")
    selected_shards = _select_shards(rows_by_shard, args.domains, args.shards)
    if args.max_shards is not None:
        selected_shards = selected_shards[: args.max_shards]

    planned = 0
    for shard_id in selected_shards:
        shard_rows = rows_by_shard[shard_id]
        status = _shard_status(shard_rows)
        _mark_rows(shard_rows, status)
        if status == "completed" and not args.force:
            print(f"skip {shard_id}: completed")
            continue

        planned += 1
        command = _build_command(
            python=args.python,
            runner_path=runner_path,
            run_dir=run_dir,
            shard_id=shard_id,
            shard_rows=shard_rows,
            run_config=run_config,
            args=args,
        )
        _write_command_file(run_dir, shard_id, command)
        print("run " + " ".join(command))
        if args.dry_run:
            continue

        _increment_attempts(shard_rows)
        _write_manifest(manifest_path, rows)
        completed = subprocess.run(
            command,
            cwd=str(tau2_dir),
            env=_child_env(),
            check=False,
        )
        status = _shard_status(shard_rows)
        _mark_rows(shard_rows, status)
        if completed.returncode != 0 and status == "pending":
            _mark_rows(shard_rows, "infra_error", "runner_returncode")
        _write_manifest(manifest_path, rows)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)

    _write_manifest(manifest_path, rows)
    print(f"planned {planned} shard(s)")


def _read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _group_by(rows: list[dict[str, str]], key: str) -> dict[str, list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        groups.setdefault(row[key], []).append(row)
    return dict(sorted(groups.items()))


def _select_shards(
    rows_by_shard: dict[str, list[dict[str, str]]],
    domains_arg: str | None,
    shards_arg: str | None,
) -> list[str]:
    selected = list(rows_by_shard.keys())
    if domains_arg:
        domains = set(_split_csv(domains_arg))
        selected = [
            shard_id
            for shard_id in selected
            if rows_by_shard[shard_id][0]["domain"] in domains
        ]
    if shards_arg:
        shards = set(_split_csv(shards_arg))
        selected = [shard_id for shard_id in selected if shard_id in shards]
    return selected


def _shard_status(shard_rows: list[dict[str, str]]) -> str:
    result_path = Path(shard_rows[0]["result_path"])
    if not result_path.exists():
        return "pending"

    try:
        data = _load_json(result_path)
    except (OSError, json.JSONDecodeError):
        return "incomplete"

    expected = {row["task_id"] for row in shard_rows}
    simulations = data.get("simulations") or []
    finished = {
        str(sim.get("task_id"))
        for sim in simulations
        if sim.get("termination_reason") != "infrastructure_error"
    }
    infra = {
        str(sim.get("task_id"))
        for sim in simulations
        if sim.get("termination_reason") == "infrastructure_error"
    }
    if expected <= finished and not infra:
        return "completed"
    if finished or infra:
        return "incomplete"
    return "pending"


def _mark_rows(
    shard_rows: list[dict[str, str]],
    status: str,
    failure_type: str = "",
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    result_path = Path(shard_rows[0]["result_path"])
    simulations_by_task = _load_simulations_by_task(result_path)
    for row in shard_rows:
        sim = simulations_by_task.get(row["task_id"])
        if sim and sim.get("termination_reason") == "infrastructure_error":
            row["status"] = "infra_error"
            row["failure_type"] = "infra_error"
        elif sim and sim.get("reward_info"):
            reward = (sim.get("reward_info") or {}).get("reward")
            row["status"] = "passed" if reward == 1.0 else "failed_model"
            row["failure_type"] = "" if reward == 1.0 else "model_failure"
        else:
            row["status"] = status
            row["failure_type"] = failure_type
        row["updated_at"] = now


def _load_simulations_by_task(result_path: Path) -> dict[str, dict[str, Any]]:
    if not result_path.exists():
        return {}
    try:
        data = _load_json(result_path)
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(sim.get("task_id")): sim
        for sim in data.get("simulations") or []
        if sim.get("task_id") is not None
    }


def _build_command(
    *,
    python: str,
    runner_path: Path,
    run_dir: Path,
    shard_id: str,
    shard_rows: list[dict[str, str]],
    run_config: dict[str, Any],
    args: argparse.Namespace,
) -> list[str]:
    first = shard_rows[0]
    shard_dir = run_dir / "shards" / shard_id
    command = [
        python,
        str(runner_path),
        "--domain",
        first["domain"],
        "--task-set-name",
        first["task_set_name"],
        "--task-split-name",
        first["task_split_name"],
        "--task-ids-file",
        str(shard_dir / "task_ids.txt"),
        "--backend",
        "deerflow",
        "--model",
        run_config.get("agent_model", "deepseek-v4-flash"),
        "--user-llm",
        run_config.get("user_llm", "deepseek/deepseek-v4-flash"),
        "--nl-assertion-model",
        run_config.get("judge_llm", "deepseek/deepseek-v4-flash"),
        "--user-llm-args",
        json.dumps(
            run_config.get(
                "user_llm_args",
                {
                    "temperature": 0.0,
                    "extra_body": {"thinking": {"type": "disabled"}},
                },
            )
        ),
        "--nl-assertion-llm-args",
        json.dumps(
            run_config.get(
                "judge_llm_args",
                {
                    "temperature": 0.0,
                    "extra_body": {"thinking": {"type": "disabled"}},
                },
            )
        ),
        "--save-to",
        first["save_to"],
        "--log-level",
        args.log_level,
    ]
    if args.auto_resume:
        command.append("--auto-resume")
    max_concurrency = args.max_concurrency or run_config.get("max_concurrency")
    if max_concurrency:
        command.extend(["--max-concurrency", str(max_concurrency)])
    max_retries = args.max_retries
    if max_retries is None:
        max_retries = run_config.get("max_retries")
    if max_retries is not None:
        command.extend(["--max-retries", str(max_retries)])
    timeout = args.timeout
    if timeout is None:
        timeout = run_config.get("timeout")
    if timeout is not None:
        command.extend(["--timeout", str(timeout)])
    return command


def _write_command_file(run_dir: Path, shard_id: str, command: list[str]) -> None:
    path = run_dir / "shards" / shard_id / "command.txt"
    path.write_text(" ".join(command) + "\n", encoding="utf-8")


def _increment_attempts(shard_rows: list[dict[str, str]]) -> None:
    for row in shard_rows:
        try:
            row["attempts"] = str(int(row.get("attempts") or 0) + 1)
        except ValueError:
            row["attempts"] = "1"


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env_path = Path(__file__).resolve().parents[2] / "deer-flow-main" / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            env.setdefault(name.strip(), value.strip().strip('"').strip("'"))
    env.setdefault("PYTHONUTF8", "1")
    return env


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


if __name__ == "__main__":
    main()
