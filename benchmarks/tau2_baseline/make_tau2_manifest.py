from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


FIELDNAMES = [
    "run_id",
    "domain",
    "task_set_name",
    "task_split_name",
    "task_id",
    "shard_id",
    "shard_index",
    "task_index",
    "status",
    "attempts",
    "save_to",
    "result_path",
    "failure_type",
    "updated_at",
]


def main() -> None:
    _add_project_paths()

    parser = argparse.ArgumentParser(
        description="Create a tau2 baseline manifest and per-shard task id files."
    )
    parser.add_argument("--run-id", default="tau2_full_flash")
    parser.add_argument(
        "--domains",
        default="airline,retail,telecom",
        help="Comma-separated tau2 domains/task sets.",
    )
    parser.add_argument(
        "--task-set",
        action="append",
        default=[],
        help="Override task set for a domain, e.g. telecom=telecom_full.",
    )
    parser.add_argument(
        "--task-split-name",
        default="none",
        help="Use 'none' for the full task file, or a named split such as base/test/train.",
    )
    parser.add_argument(
        "--task-split",
        action="append",
        default=[],
        help="Override split per domain, e.g. telecom=base.",
    )
    parser.add_argument(
        "--shard-size",
        action="append",
        default=[],
        help="Shard size override, e.g. telecom=25. Use default=10 for fallback.",
    )
    parser.add_argument("--default-shard-size", type=int, default=10)
    parser.add_argument(
        "--num-tasks",
        type=int,
        default=None,
        help="Limit each selected domain to its first N tasks (useful for smoke runs).",
    )
    parser.add_argument(
        "--skip-first",
        type=int,
        default=0,
        help="Skip the first N tasks in each selected domain.",
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--agent-model", default="deepseek-v4-flash")
    parser.add_argument("--user-llm", default="deepseek/deepseek-v4-flash")
    parser.add_argument("--judge-llm", default="deepseek/deepseek-v4-flash")
    parser.add_argument(
        "--user-llm-args",
        default='{"temperature": 0.0, "extra_body": {"thinking": {"type": "disabled"}}}',
    )
    parser.add_argument(
        "--judge-llm-args",
        default='{"temperature": 0.0, "extra_body": {"thinking": {"type": "disabled"}}}',
    )
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=None)
    args = parser.parse_args()

    from tau2.run import get_tasks

    benchmarks_dir = Path(__file__).resolve().parents[1]
    run_dir = (
        Path(args.output_dir)
        if args.output_dir
        else benchmarks_dir / "runs" / args.run_id
    )
    shards_dir = run_dir / "shards"
    shards_dir.mkdir(parents=True, exist_ok=True)

    task_set_overrides = _parse_mapping(args.task_set, default_value=None)
    task_split_overrides = _parse_mapping(args.task_split, default_value=None)
    shard_sizes = _parse_mapping(args.shard_size, default_value=args.default_shard_size)
    rows: list[dict[str, Any]] = []
    now = datetime.now().isoformat(timespec="seconds")

    for domain in _split_csv(args.domains):
        task_set_name = task_set_overrides.get(domain) or domain
        task_split_label = task_split_overrides.get(domain, args.task_split_name)
        task_split_name = _parse_optional_split(task_split_label)
        tasks = get_tasks(
            task_set_name=task_set_name,
            task_split_name=task_split_name,
        )
        if args.skip_first:
            tasks = tasks[args.skip_first :]
        if args.num_tasks is not None:
            tasks = tasks[: args.num_tasks]
        shard_size = int(shard_sizes.get(domain, args.default_shard_size))
        for shard_index, task_chunk in enumerate(_chunks(tasks, shard_size)):
            shard_id = f"{domain}_{shard_index:04d}"
            save_to = f"{args.run_id}_{shard_id}"
            shard_dir = shards_dir / shard_id
            shard_dir.mkdir(parents=True, exist_ok=True)
            task_ids_path = shard_dir / "task_ids.txt"
            task_ids = [str(task.id) for task in task_chunk]
            task_ids_path.write_text("\n".join(task_ids) + "\n", encoding="utf-8")

            for task_index, task_id in enumerate(task_ids):
                rows.append(
                    {
                        "run_id": args.run_id,
                        "domain": domain,
                        "task_set_name": task_set_name,
                        "task_split_name": task_split_label,
                        "task_id": task_id,
                        "shard_id": shard_id,
                        "shard_index": shard_index,
                        "task_index": task_index,
                        "status": "pending",
                        "attempts": 0,
                        "save_to": save_to,
                        "result_path": str(
                            benchmarks_dir
                            / "tau2-bench"
                            / "data"
                            / "simulations"
                            / save_to
                            / "results.json"
                        ),
                        "failure_type": "",
                        "updated_at": now,
                    }
                )

            shard_config = {
                "run_id": args.run_id,
                "domain": domain,
                "task_set_name": task_set_name,
                "task_split_name": task_split_label,
                "shard_id": shard_id,
                "save_to": save_to,
                "task_ids_file": str(task_ids_path),
                "num_tasks": len(task_ids),
            }
            (shard_dir / "config.json").write_text(
                json.dumps(shard_config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    manifest_path = run_dir / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    run_config = {
        "run_id": args.run_id,
        "created_at": now,
        "domains": _split_csv(args.domains),
        "task_set_overrides": task_set_overrides,
        "task_split_name": args.task_split_name,
        "task_split_overrides": task_split_overrides,
        "skip_first": args.skip_first,
        "shard_sizes": shard_sizes,
        "agent_model": args.agent_model,
        "user_llm": args.user_llm,
        "judge_llm": args.judge_llm,
        "user_llm_args": json.loads(args.user_llm_args),
        "judge_llm_args": json.loads(args.judge_llm_args),
        "thinking": False,
        "max_concurrency": args.max_concurrency,
        "max_retries": args.max_retries,
        "timeout": args.timeout,
        "pricing_cny_per_million": None,
        "pricing_note": (
            "Mixed-model run: token usage is recorded, but cost is not computed until "
            "per-role Agent/User/Judge pricing is configured."
        ),
    }
    (run_dir / "config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"wrote {manifest_path}")
    print(f"wrote {len(rows)} tasks in {len({row['shard_id'] for row in rows})} shards")


def _add_project_paths() -> None:
    here = Path(__file__).resolve()
    benchmarks_dir = here.parents[1]
    tau2_src = benchmarks_dir / "tau2-bench" / "src"
    for path in (benchmarks_dir, tau2_src):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_optional_split(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if text.lower() in {"", "none", "null", "all"}:
        return None
    return text


def _parse_mapping(values: list[str], default_value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"Expected key=value mapping, got {value!r}")
        key, raw = value.split("=", 1)
        key = key.strip()
        raw = raw.strip()
        if key == "default":
            result[key] = int(raw)
        elif default_value is None:
            result[key] = raw
        else:
            result[key] = int(raw)
    if default_value is not None and "default" in result:
        result["default"] = int(result["default"])
    return result


def _chunks(values: list[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        raise ValueError("shard size must be positive")
    return [values[index : index + size] for index in range(0, len(values), size)]


if __name__ == "__main__":
    main()
