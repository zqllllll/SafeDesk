from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any


RESULT_COLUMNS = [
    "experiment_id",
    "run_id",
    "benchmark",
    "dataset",
    "task_id",
    "model",
    "success",
    "num_passed_tests",
    "num_failed_tests",
    "num_total_tests",
    "duration_seconds",
    "num_turns",
    "num_model_tool_calls",
    "num_tool_calls",
    "num_read_tool_calls",
    "num_write_tool_calls",
    "num_other_tool_calls",
    "num_invalid_tool_calls",
    "num_out_of_schema_tool_calls",
    "num_duplicate_tool_calls",
    "num_duplicate_write_actions",
    "num_non_executed_tool_calls",
    "completion_step",
    "state_save_count",
    "state_persisted_before_evaluation",
    "predicted_api_count",
    "predictor_tokens",
    "predictor_input_tokens",
    "predictor_output_tokens",
    "agent_tokens",
    "agent_input_tokens",
    "agent_output_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "infra_error_type",
    "trace_path",
    "api_calls_path",
    "evaluation_report_path",
    "result_path",
]


API_PREDICTOR_DEMOS = [
    {
        "instruction": "What is the title of the most-liked song in my Spotify playlists.",
        "apis": [
            "spotify.login",
            "spotify.show_playlist_library",
            "spotify.show_song",
            "supervisor.complete_task",
            "supervisor.show_account_passwords",
            "supervisor.show_profile",
        ],
    },
    {
        "instruction": (
            "Christopher has asked for my movie recommendations via phone text message. "
            "Reply to them with a list of comma-separated movie titles from my Simple Note "
            "account as per their request."
        ),
        "apis": [
            "phone.login",
            "phone.search_contacts",
            "phone.search_text_messages",
            "phone.send_text_message",
            "simple_note.login",
            "simple_note.search_notes",
            "simple_note.show_note",
            "supervisor.complete_task",
            "supervisor.show_account_passwords",
            "supervisor.show_profile",
        ],
    },
    {
        "instruction": "How much money have I sent to my roommates on venmo since 1st Jan of this year?",
        "apis": [
            "phone.login",
            "phone.search_contacts",
            "supervisor.complete_task",
            "supervisor.show_account_passwords",
            "supervisor.show_profile",
            "venmo.login",
            "venmo.show_transactions",
        ],
    },
]


def _add_paths() -> None:
    here = Path(__file__).resolve()
    benchmarks_dir = here.parents[1]
    text = str(benchmarks_dir)
    if text not in sys.path:
        sys.path.insert(0, text)
    os.environ.setdefault("APPWORLD_ROOT", str(benchmarks_dir / "appworld-root"))
    os.environ.setdefault("APPWORLD_CACHE", str(benchmarks_dir / "appworld-root" / ".cache"))
    os.environ.setdefault("PYTHONUTF8", "1")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def _monotonic_seconds() -> float:
    if os.name == "nt":
        import ctypes

        return ctypes.windll.kernel32.GetTickCount64() / 1000.0
    return time.monotonic()


def _load_local_env() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name:
            os.environ.setdefault(name, value)


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def _make_run_id(experiment_name: str, task_id: str) -> str:
    return f"{_safe_id(experiment_name)}__{_safe_id(task_id)}"


def _result_path(output_dir: Path, run_id: str) -> Path:
    return output_dir / "tasks" / f"{run_id}.json"


def _load_task_list(path: Path) -> list[str]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(item) for item in data]
        if isinstance(data, dict) and isinstance(data.get("task_ids"), list):
            return [str(item) for item in data["task_ids"]]
        raise ValueError(f"Unsupported task-list JSON structure in {path}")
    task_ids = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            task_ids.append(line)
    return task_ids


def _apply_shard(task_ids: list[str], shard_index: int | None, num_shards: int | None) -> list[str]:
    if shard_index is None and num_shards is None:
        return task_ids
    if shard_index is None or num_shards is None:
        raise ValueError("--shard-index and --num-shards must be provided together.")
    if num_shards <= 0:
        raise ValueError("--num-shards must be positive.")
    if shard_index < 0 or shard_index >= num_shards:
        raise ValueError("--shard-index must be in [0, num_shards).")
    return [task_id for index, task_id in enumerate(task_ids) if index % num_shards == shard_index]


def _load_existing_results(output_dir: Path) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    legacy_path = output_dir / "results.json"
    if legacy_path.exists():
        try:
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
            if isinstance(legacy, list):
                for item in legacy:
                    if isinstance(item, dict) and isinstance(item.get("task_id"), str):
                        results[item["task_id"]] = item
        except Exception:
            pass
    task_dir = output_dir / "tasks"
    if not task_dir.exists():
        return results
    for path in task_dir.glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        task_id = item.get("task_id")
        if isinstance(task_id, str):
            results[task_id] = item
    return results


def _scenario_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    scenarios: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        task_id = str(result.get("task_id", ""))
        scenario_id = task_id.rsplit("_", 1)[0] if "_" in task_id else task_id
        scenarios.setdefault(scenario_id, []).append(result)
    complete_scenarios = [items for items in scenarios.values() if len(items) == 3]
    successful_scenarios = [
        items for items in complete_scenarios if all(bool(item.get("success")) for item in items)
    ]
    return {
        "num_scenarios_observed": len(scenarios),
        "num_complete_scenarios": len(complete_scenarios),
        "num_successful_scenarios": len(successful_scenarios),
        "scenario_goal_completion": (
            len(successful_scenarios) / len(complete_scenarios) if complete_scenarios else None
        ),
    }


def _classify_infra_error(error: str | None) -> str | None:
    if not error:
        return None
    text = error.lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "rate limit" in text or "429" in text:
        return "rate_limit"
    if "deepseek api error" in text or "api error" in text or "connection" in text:
        return "model_service_error"
    if "sandbox" in text:
        return "sandbox_error"
    return "runtime_error"


def _tool_name(schema: dict[str, Any]) -> str:
    return str(schema.get("function", {}).get("name", ""))


def _load_app_tool_schemas(app: str) -> list[dict[str, Any]]:
    root = Path(os.environ["APPWORLD_ROOT"]) / "data" / "api_docs" / "function_calling"
    path = root / f"{app}.json"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        schemas = json.load(f)
    for schema in schemas:
        schema.pop("canary_string", None)
    return schemas


def _load_all_tool_schemas() -> list[dict[str, Any]]:
    root = Path(os.environ["APPWORLD_ROOT"]) / "data" / "api_docs" / "function_calling"
    schemas: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        schemas.extend(_load_app_tool_schemas(path.stem))
    return schemas


def _api_predictor_messages(task: Any) -> list[dict[str, Any]]:
    import yaml

    prompt_path = Path(__file__).resolve().parent / "official_api_predictor_prompt.txt"
    prompt_template = prompt_path.read_text(encoding="utf-8")
    api_descriptions = {
        app_name: {
            api_name: api_doc["description"]
            for api_name, api_doc in app_api_docs.items()
        }
        for app_name, app_api_docs in task.api_docs.items()
        if app_name != "api_docs"
    }
    header_template, body_template = prompt_template.split(
        "============================================================================\n",
        1,
    )
    header_content = header_template.replace(
        "{api_descriptions_string}",
        yaml.safe_dump(api_descriptions, allow_unicode=True, sort_keys=False).rstrip(),
    ).rstrip()
    user_template, _ = body_template.rsplit(
        "\n----------------------------------------------------------------------------\n",
        1,
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": header_content}]
    for demo in API_PREDICTOR_DEMOS:
        messages.append(
            {
                "role": "user",
                "content": user_template.replace("{instruction}", demo["instruction"]).rstrip(),
            }
        )
        messages.append({"role": "assistant", "content": "\n".join(demo["apis"])})
    messages.append(
        {
            "role": "user",
            "content": user_template.replace("{instruction}", task.instruction).rstrip(),
        }
    )
    return messages


def _parse_predicted_apis(
    content: str, all_schemas: list[dict[str, Any]], max_predicted_apis: int
) -> tuple[list[str], list[dict[str, Any]]]:
    schema_by_dot_name = {
        _tool_name(schema).replace("__", ".", 1): schema
        for schema in all_schemas
        if _tool_name(schema)
    }
    predicted: list[str] = []
    for raw_line in content.splitlines():
        name = raw_line.strip().lower()
        if name in schema_by_dot_name and name not in predicted:
            predicted.append(name)
    if "supervisor.complete_task" not in predicted:
        predicted.insert(0, "supervisor.complete_task")
    predicted = predicted[:max_predicted_apis]
    tools = [schema_by_dot_name[name] for name in predicted]
    return predicted, tools


def _out_of_schema_result(name: str) -> dict[str, Any]:
    return {
        "executed": False,
        "reason": "out_of_schema_tool_call",
        "message": (
            f"Tool {name!r} was not executed because it is not in the active tool schema. "
            "Re-plan using only the tools provided for this task."
        ),
    }


def _official_agent_messages(world: Any, max_turns: int) -> list[dict[str, Any]]:
    prompt_dir = Path(__file__).resolve().parent
    template = (prompt_dir / "official_function_calling_instructions.txt").read_text(encoding="utf-8")
    demos = json.loads(
        (prompt_dir / "official_function_calling_demos.json").read_text(encoding="utf-8")
    )
    supervisor = world.task.supervisor

    def supervisor_value(name: str) -> str:
        if isinstance(supervisor, dict):
            return str(supervisor.get(name, ""))
        return str(getattr(supervisor, name, ""))

    app_descriptions = dict(world.task.app_descriptions)
    app_descriptions.pop("api_docs", None)
    rendered = template
    replacements = {
        "{{ main_user.first_name }}": supervisor_value("first_name"),
        "{{ main_user.last_name }}": supervisor_value("last_name"),
        "{{ main_user.email }}": supervisor_value("email"),
        "{{ main_user.phone_number }}": supervisor_value("phone_number"),
        "{max_steps}": str(max_turns),
        "{app_descriptions}": json.dumps(app_descriptions, ensure_ascii=False, default=str),
        "{instruction}": world.task.instruction,
    }
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    marker = "============================================================================\n# Real Task Instruction"
    header = rendered.split(marker, 1)[0].rstrip()
    task_message = (
        f"# Real Task Instruction\n{world.task.instruction}\n\n"
        "Disclaimer: This is a real task. Do NOT reuse access tokens, passwords, names, or "
        "other values from the tutorial examples. Retrieve real values through APIs."
    )
    return [{"role": "system", "content": header}, *demos, {"role": "user", "content": task_message}]


def _usage(usage: dict[str, Any] | None) -> dict[str, int]:
    usage = usage or {}
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content or "")


def _estimate_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return (len(text) + 3) // 4


def _safe_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _compact_mapping(name: str, item: dict[str, Any]) -> dict[str, Any]:
    if name == "spotify__show_song":
        keys = [
            "song_id",
            "title",
            "like_count",
            "play_count",
            "rating",
            "genre",
            "artists",
            "release_date",
        ]
        return {key: item[key] for key in keys if key in item}
    if name == "spotify__show_playlist_library":
        keys = ["playlist_id", "title", "song_ids", "is_public", "owner"]
        return {key: item[key] for key in keys if key in item}
    return item


def _compact_tool_result(name: str, result: Any, max_chars: int) -> Any:
    if max_chars <= 0:
        return result
    compact: Any = result
    if isinstance(result, list):
        compact = [_compact_mapping(name, item) if isinstance(item, dict) else item for item in result]
    elif isinstance(result, dict):
        compact = _compact_mapping(name, result)
    text = _safe_json_dumps(compact)
    if len(text) <= max_chars:
        return compact
    if isinstance(compact, list):
        trimmed = []
        used = 2
        for item in compact:
            item_text = _safe_json_dumps(item)
            if used + len(item_text) + 2 > max_chars:
                break
            trimmed.append(item)
            used += len(item_text) + 2
        return {
            "items": trimmed,
            "truncated": True,
            "original_count": len(compact),
            "note": "Tool result was truncated before being sent back to the model.",
        }
    return {
        "value": text[:max_chars],
        "truncated": True,
        "note": "Tool result was truncated before being sent back to the model.",
    }


def _canonical_call(name: str, args: dict[str, Any]) -> str:
    return json.dumps({"name": name, "args": args}, ensure_ascii=False, sort_keys=True, default=str)


def _is_write_tool(name: str) -> bool:
    api = name.split("__", 1)[1] if "__" in name else name
    return api.startswith(
        (
            "complete_",
            "create_",
            "update_",
            "delete_",
            "add_",
            "remove_",
            "send_",
            "submit_",
            "review_",
            "like_",
            "unlike_",
            "download_",
            "reset_",
            "verify_",
            "signup",
            "logout",
        )
    )


def _execution_plan(tool_calls: list[dict[str, Any]]) -> tuple[set[int], dict[int, dict[str, Any]]]:
    return set(range(len(tool_calls))), {}


def _persist_world_state(world: Any) -> None:
    save_state = getattr(world, "_save_state", None)
    output_path = getattr(world, "output_db_home_path_on_disk", None)
    if not callable(save_state) or not output_path:
        raise RuntimeError("This AppWorld version does not expose the required state persistence API.")
    save_state(output_path)


class OpenAICompatibleFunctionClient:
    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str,
        enable_thinking: bool,
        max_retries: int = 8,
        timeout: float = 180.0,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.enable_thinking = enable_thinking
        self.max_retries = max_retries
        self.timeout = timeout

    def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        parallel_tool_calls: bool = False,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        import requests

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
        }
        if "dashscope.aliyuncs.com" in self.base_url:
            payload["enable_thinking"] = self.enable_thinking
        elif "api.deepseek.com" in self.base_url:
            payload["thinking"] = {"type": "enabled" if self.enable_thinking else "disabled"}
        if tools:
            payload.update(
                {
                    "tools": tools,
                    "tool_choice": "auto",
                    "parallel_tool_calls": parallel_tool_calls,
                }
            )
        if self.enable_thinking:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        response = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                    stream=self.enable_thinking,
                )
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError(f"Model API connection error: {exc}") from exc
                time.sleep(min(2**attempt, 30))
                continue
            if response.status_code < 400:
                break
            if response.status_code not in {408, 409, 429, 500, 502, 503, 504}:
                break
            if attempt >= self.max_retries:
                break
            retry_after = response.headers.get("Retry-After")
            try:
                wait_seconds = float(retry_after) if retry_after else min(2**attempt, 30)
            except ValueError:
                wait_seconds = min(2**attempt, 30)
            time.sleep(max(0.0, min(wait_seconds, 60.0)))
        assert response is not None
        if response.status_code >= 400:
            raise RuntimeError(f"Model API error {response.status_code}: {response.text[:800]}")
        if self.enable_thinking:
            return self._streamed_message(response)
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"Model API returned no choices: {data}")
        message = choices[0].get("message") or {}
        return message, _usage(data.get("usage"))

    def _streamed_message(self, response: Any) -> tuple[dict[str, Any], dict[str, int]]:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "",
            "reasoning_content": "",
        }
        tool_calls: dict[int, dict[str, Any]] = {}
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = str(raw_line).strip()
            if not line.startswith("data:"):
                continue
            data_text = line[5:].strip()
            if data_text == "[DONE]":
                break
            try:
                chunk = json.loads(data_text)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                usage = _usage(chunk["usage"])
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            if delta.get("content"):
                message["content"] += str(delta["content"])
            if delta.get("reasoning_content"):
                message["reasoning_content"] += str(delta["reasoning_content"])
            for call_delta in delta.get("tool_calls") or []:
                index = int(call_delta.get("index", len(tool_calls)))
                call = tool_calls.setdefault(
                    index,
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    },
                )
                if call_delta.get("id"):
                    call["id"] += str(call_delta["id"])
                if call_delta.get("type"):
                    call["type"] = call_delta["type"]
                function_delta = call_delta.get("function") or {}
                if function_delta.get("name"):
                    call["function"]["name"] += str(function_delta["name"])
                if function_delta.get("arguments"):
                    call["function"]["arguments"] += str(function_delta["arguments"])
        if tool_calls:
            message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
        return message, usage


def _split_tool_name(name: str) -> tuple[str, str]:
    if "__" not in name:
        raise ValueError(f"Invalid AppWorld tool name {name!r}; expected app__api.")
    app, api = name.split("__", 1)
    return app, api


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return json.loads(json.dumps(value, default=str))


def _evaluate_tracker(tracker: Any) -> dict[str, Any]:
    if hasattr(tracker, "get_metrics"):
        try:
            metrics = tracker.get_metrics(include_details=False, reset=False)
            if isinstance(metrics, dict):
                return metrics
        except Exception:
            pass
    return {
        "success": bool(getattr(tracker, "success", False)),
        "num_passed_tests": getattr(tracker, "num_passed_tests", None),
        "num_failed_tests": getattr(tracker, "num_failed_tests", None),
        "num_total_tests": getattr(tracker, "num_total_tests", None),
    }


def _merge_evaluation_report(evaluation: dict[str, Any], report_path: Path) -> dict[str, Any]:
    if not report_path.exists():
        return evaluation
    text = report_path.read_text(encoding="utf-8", errors="replace")
    fields = {
        "num_passed_tests": r"Num Passed Tests\s*:\s*(\d+)",
        "num_failed_tests": r"Num Failed Tests\s*:\s*(\d+)",
        "num_total_tests": r"Num Total\s+Tests\s*:\s*(\d+)",
    }
    for key, pattern in fields.items():
        match = re.search(pattern, text)
        if match and evaluation.get(key) is None:
            evaluation[key] = int(match.group(1))
    if evaluation.get("num_failed_tests") is not None and evaluation.get("num_total_tests") is not None:
        evaluation["success"] = bool(evaluation["num_total_tests"] and evaluation["num_failed_tests"] == 0)
    return evaluation


def _failed_result(
    *,
    experiment_name: str,
    run_id: str,
    dataset: str,
    task_id: str,
    model_name: str,
    error: str,
    output_dir: Path,
) -> dict[str, Any]:
    result_path = _result_path(output_dir, run_id)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "experiment_id": experiment_name,
        "run_id": run_id,
        "benchmark": "appworld",
        "dataset": dataset,
        "task_id": task_id,
        "model": model_name,
        "success": False,
        "evaluation": {"success": False},
        "num_passed_tests": None,
        "num_failed_tests": None,
        "num_total_tests": None,
        "duration_seconds": 0,
        "num_turns": 0,
        "num_model_tool_calls": 0,
        "num_tool_calls": 0,
        "num_read_tool_calls": 0,
        "num_write_tool_calls": 0,
        "num_other_tool_calls": 0,
        "num_invalid_tool_calls": 0,
        "num_out_of_schema_tool_calls": 0,
        "num_duplicate_tool_calls": 0,
        "num_duplicate_write_actions": 0,
        "num_non_executed_tool_calls": 0,
        "completion_step": None,
        "state_save_count": 0,
        "state_persisted_before_evaluation": False,
        "predicted_api_count": 0,
        "predictor_tokens": 0,
        "predictor_input_tokens": 0,
        "predictor_output_tokens": 0,
        "agent_tokens": 0,
        "agent_input_tokens": 0,
        "agent_output_tokens": 0,
        "total_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "error": error,
        "infra_error_type": _classify_infra_error(error),
        "trace_path": "",
        "api_calls_path": "",
        "evaluation_report_path": "",
        "result_path": str(result_path.resolve()),
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def run_task(
    *,
    task_id: str,
    dataset: str,
    experiment_name: str,
    run_id: str,
    model_name: str,
    max_turns: int,
    max_predicted_apis: int,
    max_tool_result_chars: int,
    parallel_tool_calls: bool,
    api_base: str,
    api_key_env: str,
    enable_thinking: bool,
    api_max_retries: int,
    output_dir: Path,
) -> dict[str, Any]:
    from appworld import AppWorld

    started = _monotonic_seconds()
    world = AppWorld(
        task_id=task_id,
        experiment_name=experiment_name,
        timeout_seconds=None,
        ground_truth_mode="minimal",
        raise_on_failure=False,
        raise_on_extra_parameters=True,
    )
    trace: list[dict[str, Any]] = []
    round_stats: list[dict[str, Any]] = []
    predictor_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    agent_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    invalid_tool_calls = 0
    out_of_schema_tool_calls = 0
    out_of_schema_tool_names: list[str] = []
    num_tool_calls = 0
    num_model_tool_calls = 0
    num_suppressed_parallel_tool_calls = 0
    num_non_executed_tool_calls = 0
    num_read_tool_calls = 0
    num_write_tool_calls = 0
    num_other_tool_calls = 0
    complete_task_turn: int | None = None
    state_save_count = 0
    state_persisted_before_evaluation = False
    call_counter: Counter[str] = Counter()
    write_counter: Counter[str] = Counter()
    all_schemas = _load_all_tool_schemas()
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(f"{api_key_env} is not set in the current environment.")
    client = OpenAICompatibleFunctionClient(
        model=model_name,
        base_url=api_base,
        api_key=api_key,
        enable_thinking=enable_thinking,
        max_retries=api_max_retries,
    )
    predictor_messages = _api_predictor_messages(world.task)
    predictor_response, predictor_usage = client.chat(messages=predictor_messages)
    predictor_content = _content_text(predictor_response.get("content", ""))
    predicted_apis, tools = _parse_predicted_apis(
        predictor_content,
        all_schemas,
        max_predicted_apis=max_predicted_apis,
    )
    if not tools:
        raise RuntimeError(f"API Predictor returned no valid APIs: {predictor_content[:800]}")
    tool_schema_chars = len(_safe_json_dumps(tools))
    tool_schema_tokens = _estimate_tokens(tools)
    available_tool_names = {_tool_name(tool) for tool in tools}
    evaluation_report_path = (
        Path(os.environ["APPWORLD_ROOT"])
        / "experiments"
        / "outputs"
        / experiment_name
        / "tasks"
        / task_id
        / "evaluation"
        / "report.md"
    )

    messages = _official_agent_messages(world, max_turns=max_turns)
    trace.append(
        {
            "stage": "api_predictor",
            "role": "assistant",
            "content": predictor_content,
            "reasoning_content": _content_text(predictor_response.get("reasoning_content", "")),
            "predicted_apis": predicted_apis,
            "usage": predictor_usage,
        }
    )

    error: str | None = None
    try:
        for turn in range(1, max_turns + 1):
            history_chars_before_call = len(_safe_json_dumps(messages))
            response, usage = client.chat(
                messages=messages,
                tools=tools,
                parallel_tool_calls=parallel_tool_calls,
            )
            for key in agent_usage:
                agent_usage[key] += usage[key]

            model_tool_calls = list(response.get("tool_calls") or [])
            num_model_tool_calls += len(model_tool_calls)
            execute_indexes, non_execution_results = _execution_plan(model_tool_calls)
            tool_calls = [
                tool_call
                for index, tool_call in enumerate(model_tool_calls)
                if index in execute_indexes
                and str((tool_call.get("function") or {}).get("name", ""))
                in available_tool_names
            ]
            suppressed_tool_calls = [
                tool_call
                for index, tool_call in enumerate(model_tool_calls)
                if index in non_execution_results
            ]
            num_non_executed_tool_calls += len(suppressed_tool_calls)
            num_suppressed_parallel_tool_calls += len(suppressed_tool_calls)
            content = _content_text(response.get("content", ""))
            round_record = {
                "turn": turn,
                "num_model_tool_calls": len(model_tool_calls),
                "num_tool_calls": len(tool_calls),
                "num_suppressed_parallel_tool_calls": len(suppressed_tool_calls),
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "total_tokens": usage["total_tokens"],
                "tool_schema_count": len(tools),
                "tool_schema_chars": tool_schema_chars,
                "estimated_tool_schema_tokens": tool_schema_tokens,
                "history_chars_before_call": history_chars_before_call,
                "estimated_history_tokens_before_call": _estimate_tokens(messages),
            }
            round_stats.append(round_record)
            trace.append(
                {
                    "turn": turn,
                    "role": "assistant",
                    "content": content,
                    "reasoning_content": _content_text(response.get("reasoning_content", "")),
                    "tool_calls": _json_safe(model_tool_calls),
                    "executed_tool_calls": _json_safe(tool_calls),
                    "suppressed_tool_calls": _json_safe(suppressed_tool_calls),
                    "usage": usage,
                    "request_stats": round_record,
                }
            )
            assistant_message: dict[str, Any] = {"role": "assistant", "content": content}
            if response.get("reasoning_content") is not None:
                assistant_message["reasoning_content"] = response.get("reasoning_content")
            if model_tool_calls:
                assistant_message["tool_calls"] = model_tool_calls
            messages.append(assistant_message)

            if not model_tool_calls:
                if world.task_completed():
                    break
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "No tool call was made. Continue by calling the next needed tool, "
                            "or call supervisor__complete_task if the task is complete."
                        ),
                    }
                )
                continue

            for index, tool_call in enumerate(model_tool_calls):
                function = tool_call.get("function") or {}
                name = function.get("name", "")
                raw_args = function.get("arguments") or "{}"
                if name not in available_tool_names:
                    out_of_schema_tool_calls += 1
                    invalid_tool_calls += 1
                    if name and name not in out_of_schema_tool_names:
                        out_of_schema_tool_names.append(name)
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except Exception:
                    args = {}
                call_id = tool_call.get("id") or f"call_{uuid.uuid4().hex[:12]}"
                schema_allowed = name in available_tool_names
                executed = index in execute_indexes and schema_allowed
                if executed:
                    num_tool_calls += 1
                    try:
                        app, api = _split_tool_name(name)
                        result = world.requester.request(app, api, **args)
                    except Exception as exc:
                        invalid_tool_calls += 1
                        result = {"error": str(exc)}
                elif not schema_allowed:
                    if index not in non_execution_results:
                        num_non_executed_tool_calls += 1
                    result = _out_of_schema_result(name)
                else:
                    result = non_execution_results[index]
                result = _json_safe(result)
                compact_result = _compact_tool_result(name, result, max_chars=max_tool_result_chars)
                compact_result = _json_safe(compact_result)
                call_key = _canonical_call(name, args)
                if executed:
                    call_counter[call_key] += 1
                    if _is_write_tool(name):
                        num_write_tool_calls += 1
                        write_counter[call_key] += 1
                    elif name:
                        num_read_tool_calls += 1
                    else:
                        num_other_tool_calls += 1
                if executed and name == "supervisor__complete_task" and complete_task_turn is None:
                    complete_task_turn = turn
                trace.append(
                    {
                        "turn": turn,
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "args": _json_safe(args),
                        "executed": executed,
                        "result": result,
                        "message_result": compact_result,
                        "result_chars": len(_safe_json_dumps(result)),
                        "message_result_chars": len(_safe_json_dumps(compact_result)),
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(compact_result, ensure_ascii=False, default=str),
                    }
                )

            _persist_world_state(world)
            state_save_count += 1
            world.save_logs()
            if world.task_completed():
                break

        _persist_world_state(world)
        state_save_count += 1
        state_persisted_before_evaluation = True
        tracker = world.evaluate(suppress_errors=True)
        evaluation = _evaluate_tracker(tracker)
        evaluation = _merge_evaluation_report(evaluation, evaluation_report_path)
    except Exception as exc:
        error = str(exc)
        evaluation = {"success": False}
    finally:
        world.close()

    duration = _monotonic_seconds() - started
    trace_path = output_dir / "traces" / f"{task_id}.json"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    result_path = _result_path(output_dir, run_id)
    result_path.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "experiment_id": experiment_name,
        "run_id": run_id,
        "benchmark": "appworld",
        "dataset": dataset,
        "task_id": task_id,
        "model": model_name,
        "success": bool(evaluation.get("success", False)),
        "evaluation": evaluation,
        "num_passed_tests": evaluation.get("num_passed_tests"),
        "num_failed_tests": evaluation.get("num_failed_tests"),
        "num_total_tests": evaluation.get("num_total_tests"),
        "duration_seconds": round(duration, 4),
        "num_turns": len(round_stats),
        "num_tool_calls": num_tool_calls,
        "num_model_tool_calls": num_model_tool_calls,
        "num_suppressed_parallel_tool_calls": num_suppressed_parallel_tool_calls,
        "num_non_executed_tool_calls": num_non_executed_tool_calls,
        "num_read_tool_calls": num_read_tool_calls,
        "num_write_tool_calls": num_write_tool_calls,
        "num_other_tool_calls": num_other_tool_calls,
        "num_invalid_tool_calls": invalid_tool_calls,
        "num_out_of_schema_tool_calls": out_of_schema_tool_calls,
        "out_of_schema_tool_names": out_of_schema_tool_names,
        "num_duplicate_tool_calls": sum(count - 1 for count in call_counter.values() if count > 1),
        "num_duplicate_write_actions": sum(count - 1 for count in write_counter.values() if count > 1),
        "parallel_tool_calls_requested": parallel_tool_calls,
        "parallel_tool_calls_observed": any(item["num_model_tool_calls"] > 1 for item in round_stats),
        "parallel_tool_calls_executed": any(item["num_tool_calls"] > 1 for item in round_stats),
        "multi_tool_calls_executed_serially": any(item["num_tool_calls"] > 1 for item in round_stats),
        "tool_schema_count": len(tools),
        "tool_schema_chars": tool_schema_chars,
        "estimated_tool_schema_tokens": tool_schema_tokens,
        "round_stats": round_stats,
        "completion_step": complete_task_turn,
        "state_save_count": state_save_count,
        "state_persisted_before_evaluation": state_persisted_before_evaluation,
        "predicted_apis": predicted_apis,
        "predicted_api_count": len(predicted_apis),
        "predictor_tokens": predictor_usage["total_tokens"],
        "predictor_input_tokens": predictor_usage["input_tokens"],
        "predictor_output_tokens": predictor_usage["output_tokens"],
        "agent_tokens": agent_usage["total_tokens"],
        "agent_input_tokens": agent_usage["input_tokens"],
        "agent_output_tokens": agent_usage["output_tokens"],
        "total_tokens": predictor_usage["total_tokens"] + agent_usage["total_tokens"],
        "input_tokens": predictor_usage["input_tokens"] + agent_usage["input_tokens"],
        "output_tokens": predictor_usage["output_tokens"] + agent_usage["output_tokens"],
        "error": error,
        "infra_error_type": _classify_infra_error(error),
        "trace_path": str(trace_path.resolve()),
        "api_calls_path": str(
            Path(os.environ["APPWORLD_ROOT"])
            / "experiments"
            / "outputs"
            / experiment_name
            / "tasks"
            / task_id
            / "logs"
            / "api_calls.jsonl"
        ),
        "evaluation_report_path": str(evaluation_report_path),
        "result_path": str(result_path.resolve()),
        "config": {
            "max_turns": max_turns,
            "max_predicted_apis": max_predicted_apis,
            "max_tool_result_chars": max_tool_result_chars,
            "parallel_tool_calls": parallel_tool_calls,
            "thinking": "enabled" if enable_thinking else "disabled",
            "temperature": 0,
            "tool_mode": "function_calling",
            "schema_mode": "official_api_predictor",
            "api_base": api_base,
            "api_key_env": api_key_env,
            "api_max_retries": api_max_retries,
        },
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    _add_paths()
    _load_local_env()

    parser = argparse.ArgumentParser(description="Run AppWorld with model function calling.")
    parser.add_argument("--dataset", default="train")
    parser.add_argument("--task-id", default=None)
    parser.add_argument("--task-list", default=None, help="Path to a text or JSON file with task ids.")
    parser.add_argument("--num-tasks", type=int, default=1)
    parser.add_argument("--max-tasks", type=int, default=None)
    parser.add_argument("--shard-index", type=int, default=None)
    parser.add_argument("--num-shards", type=int, default=None)
    parser.add_argument("--resume", action="store_true", help="Skip task ids that already have per-task results.")
    parser.add_argument("--skip-completed", action="store_true", help="Skip task ids with existing successful results.")
    parser.add_argument("--force", action="store_true", help="Run even when an existing result is present.")
    parser.add_argument("--dry-run", action="store_true", help="Only print selected task ids and write the plan.")
    parser.add_argument("--experiment-name", default="appworld_qwen3_14b_official_baseline_smoke")
    parser.add_argument("--model", default="qwen3-14b")
    parser.add_argument("--max-turns", type=int, default=50)
    parser.add_argument("--max-predicted-apis", type=int, default=20)
    parser.add_argument(
        "--max-tool-result-chars",
        type=int,
        default=0,
        help="0 keeps official full tool results; a positive value enables truncation.",
    )
    parser.add_argument(
        "--parallel-tool-calls",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--thinking",
        choices=("disabled",),
        default="disabled",
        help="Thinking is intentionally disabled for SafeDesk benchmark runs.",
    )
    parser.add_argument(
        "--api-base",
        default=os.getenv("DASHSCOPE_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    )
    parser.add_argument("--api-key-env", default="DASHSCOPE_API_KEY")
    parser.add_argument("--api-max-retries", type=int, default=8)
    parser.add_argument(
        "--output-dir",
        default=str(
            Path(__file__).resolve().parents[1]
            / "results"
            / "appworld_qwen3_14b_official_baseline_smoke"
        ),
    )
    args = parser.parse_args()

    from appworld import load_task_ids

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.task_id:
        all_task_ids = [args.task_id]
    elif args.task_list:
        all_task_ids = _load_task_list(Path(args.task_list))
    else:
        all_task_ids = load_task_ids(args.dataset)
    selected_task_ids = _apply_shard(all_task_ids, args.shard_index, args.num_shards)
    task_limit = args.max_tasks if args.max_tasks is not None else args.num_tasks
    if task_limit is not None:
        selected_task_ids = selected_task_ids[:task_limit]

    existing_results = _load_existing_results(output_dir)
    results: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    plan = {
        "experiment_id": args.experiment_name,
        "benchmark": "appworld",
        "dataset": args.dataset,
        "model": args.model,
        "task_source_count": len(all_task_ids),
        "selected_count": len(selected_task_ids),
        "task_ids": selected_task_ids,
        "shard_index": args.shard_index,
        "num_shards": args.num_shards,
        "resume": args.resume,
        "skip_completed": args.skip_completed,
        "force": args.force,
        "config": {
            "max_turns": args.max_turns,
            "max_predicted_apis": args.max_predicted_apis,
            "max_tool_result_chars": args.max_tool_result_chars,
            "parallel_tool_calls": args.parallel_tool_calls,
            "thinking": args.thinking,
            "temperature": 0,
            "tool_mode": "function_calling",
            "schema_mode": "official_api_predictor",
            "api_base": args.api_base,
            "api_key_env": args.api_key_env,
            "api_max_retries": args.api_max_retries,
        },
    }
    (output_dir / "run_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return

    for task_id in selected_task_ids:
        run_id = _make_run_id(args.experiment_name, task_id)
        existing = existing_results.get(task_id)
        if existing and not args.force:
            if args.resume or (args.skip_completed and existing.get("success")):
                skipped.append(
                    {
                        "task_id": task_id,
                        "run_id": existing.get("run_id", run_id),
                        "reason": "existing_success" if existing.get("success") else "existing_result",
                        "result_path": existing.get("result_path"),
                    }
                )
                results.append(existing)
                continue
        try:
            result = run_task(
                task_id=task_id,
                dataset=args.dataset,
                experiment_name=args.experiment_name,
                run_id=run_id,
                model_name=args.model,
                max_turns=args.max_turns,
                max_predicted_apis=args.max_predicted_apis,
                max_tool_result_chars=args.max_tool_result_chars,
                parallel_tool_calls=args.parallel_tool_calls,
                api_base=args.api_base,
                api_key_env=args.api_key_env,
                enable_thinking=args.thinking == "enabled",
                api_max_retries=args.api_max_retries,
                output_dir=output_dir,
            )
        except Exception as exc:
            result = _failed_result(
                experiment_name=args.experiment_name,
                run_id=run_id,
                dataset=args.dataset,
                task_id=task_id,
                model_name=args.model,
                error=str(exc),
                output_dir=output_dir,
            )
        results.append(result)

    results = sorted(results, key=lambda item: selected_task_ids.index(item["task_id"]) if item.get("task_id") in selected_task_ids else 10**9)
    num_success = sum(1 for item in results if item["success"])
    summary = {
        "experiment_id": args.experiment_name,
        "benchmark": "appworld",
        "dataset": args.dataset,
        "model": args.model,
        "num_selected_tasks": len(selected_task_ids),
        "num_result_tasks": len(results),
        "num_skipped_tasks": len(skipped),
        "num_ran_tasks": len(results) - len(skipped),
        "num_success": num_success,
        "pass_rate": num_success / len(results) if results else 0.0,
        "task_goal_completion": num_success / len(results) if results else 0.0,
        **_scenario_metrics(results),
        "predictor_tokens": sum(int(item.get("predictor_tokens") or 0) for item in results),
        "agent_tokens": sum(int(item.get("agent_tokens") or 0) for item in results),
        "total_tokens": sum(int(item["total_tokens"] or 0) for item in results),
        "input_tokens": sum(int(item["input_tokens"] or 0) for item in results),
        "output_tokens": sum(int(item["output_tokens"] or 0) for item in results),
        "duration_seconds": round(sum(float(item["duration_seconds"] or 0) for item in results), 4),
        "skipped": skipped,
        "result_columns": RESULT_COLUMNS,
        "config": plan["config"],
        "results": results,
    }
    (output_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with (output_dir / "results.jsonl").open("w", encoding="utf-8") as f:
        for item in results:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    with (output_dir / "results.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for item in results:
            writer.writerow(item)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
