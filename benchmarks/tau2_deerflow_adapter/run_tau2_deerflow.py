from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _add_project_paths() -> None:
    here = Path(__file__).resolve()
    adapter_parent = here.parents[1]
    tau2_src = adapter_parent / "tau2-bench" / "src"
    for path in (adapter_parent, tau2_src):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


def _patch_nl_assertion_model(model: str, llm_args: dict) -> None:
    import tau2.config as config
    import tau2.evaluator.evaluator_nl_assertions as nl_eval

    config.DEFAULT_LLM_NL_ASSERTIONS = model
    config.DEFAULT_LLM_NL_ASSERTIONS_ARGS = llm_args
    nl_eval.DEFAULT_LLM_NL_ASSERTIONS = model
    nl_eval.DEFAULT_LLM_NL_ASSERTIONS_ARGS = llm_args


def main() -> None:
    _add_project_paths()

    parser = argparse.ArgumentParser(description="Run tau2 with DeerFlowTauAgent")
    parser.add_argument("--domain", default="mock")
    parser.add_argument("--task-set-name", default=None)
    parser.add_argument(
        "--task-split-name",
        default="none",
        help="Use 'none' for the full task file, or a named split such as base/test/train.",
    )
    parser.add_argument("--task-ids", default=None)
    parser.add_argument("--task-ids-file", default=None)
    parser.add_argument("--num-tasks", type=int, default=None)
    parser.add_argument("--num-trials", type=int, default=1)
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--max-errors", type=int, default=10)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--auto-resume", action="store_true")
    parser.add_argument("--save-to", default=None)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--backend", choices=["deerflow", "litellm"], default="deerflow")
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--user-llm", default="deepseek/deepseek-v4-flash")
    parser.add_argument("--nl-assertion-model", default="deepseek/deepseek-v4-flash")
    parser.add_argument(
        "--thinking",
        action="store_true",
        help="Enable model reasoning/thinking. Disabled by default to control cost.",
    )
    parser.add_argument(
        "--no-thinking",
        action="store_false",
        dest="thinking",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--deerflow-backend-path", default=None)
    parser.add_argument("--deerflow-config-path", default=None)
    parser.add_argument("--agent-llm-args", default="{}")
    parser.add_argument(
        "--user-llm-args",
        default='{"temperature": 0.0, "extra_body": {"thinking": {"type": "disabled"}}}',
    )
    parser.add_argument(
        "--nl-assertion-llm-args",
        default='{"temperature": 0.0, "extra_body": {"thinking": {"type": "disabled"}}}',
    )
    args = parser.parse_args()

    os.environ.setdefault("PYTHONUTF8", "1")

    from tau2.data_model.simulation import TextRunConfig
    from tau2.registry import registry
    from tau2.run import run_domain

    from tau2_deerflow_adapter import create_deerflow_tau_agent

    _patch_nl_assertion_model(
        args.nl_assertion_model,
        json.loads(args.nl_assertion_llm_args),
    )

    try:
        registry.register_agent_factory(create_deerflow_tau_agent, "deerflow_tau_agent")
    except ValueError as exc:
        if "already registered" not in str(exc):
            raise

    agent_llm_args = json.loads(args.agent_llm_args)
    agent_llm_args.update(
        {
            "deerflow_backend": args.backend,
            "deerflow_model": args.model,
            "thinking_enabled": args.thinking,
        }
    )
    if args.deerflow_backend_path:
        agent_llm_args["deerflow_backend_path"] = args.deerflow_backend_path
    if args.deerflow_config_path:
        agent_llm_args["deerflow_config_path"] = args.deerflow_config_path
    task_ids = _parse_task_ids(args.task_ids, args.task_ids_file)
    task_split_name = _parse_optional_split(args.task_split_name)
    num_tasks = args.num_tasks
    if num_tasks is None and task_ids is None:
        num_tasks = 1

    run_domain(
        TextRunConfig(
            domain=args.domain,
            task_set_name=args.task_set_name,
            task_split_name=task_split_name,
            task_ids=task_ids,
            agent="deerflow_tau_agent",
            user="user_simulator",
            llm_agent=args.model,
            llm_args_agent=agent_llm_args,
            llm_user=args.user_llm,
            llm_args_user=json.loads(args.user_llm_args),
            num_trials=args.num_trials,
            num_tasks=num_tasks,
            max_errors=args.max_errors,
            max_retries=args.max_retries,
            retry_delay=args.retry_delay,
            timeout=args.timeout,
            auto_resume=args.auto_resume,
            max_concurrency=args.max_concurrency,
            save_to=args.save_to,
            log_level=args.log_level,
        )
    )


def _parse_task_ids(task_ids: str | None, task_ids_file: str | None) -> list[str] | None:
    values: list[str] = []
    if task_ids:
        values.extend(item.strip() for item in task_ids.split(",") if item.strip())
    if task_ids_file:
        path = Path(task_ids_file)
        with path.open("r", encoding="utf-8") as f:
            values.extend(line.strip() for line in f if line.strip())
    return values or None


def _parse_optional_split(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if text.lower() in {"", "none", "null", "all"}:
        return None
    return text


if __name__ == "__main__":
    main()
