from __future__ import annotations

import json
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path

from agentgate_appworld.catalog_adapter import AppWorldToolCatalog
from agentgate_appworld.cli import run
from agentgate_appworld.trace_converter import REDACTED_VALUE, AppWorldTraceConverter
from agentgate_core.contracts import ActionKind, EffectStatus, EvidenceSourceType, EvidenceStatus

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
REAL_API_DOCS = WORKSPACE_ROOT / "benchmarks" / "appworld-root" / "data" / "api_docs" / "function_calling"
REAL_TRACE = (
    WORKSPACE_ROOT / "benchmarks" / "results" / "appworld_function_calling_flash_smoke_4" / "traces" / "82e2fac_1.json"
)
FIXED_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def _schema(name: str, description: str, properties: dict | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties or {}},
        },
    }


def _catalog(tmp_path: Path) -> AppWorldToolCatalog:
    payload = [
        _schema("mail__login", "Log in.", {"password": {"type": "string"}}),
        _schema(
            "mail__send_message",
            "Send a message.",
            {"access_token": {"type": "string"}, "message": {"type": "string"}},
        ),
        _schema("mail__show_message", "Show a message.", {"message_id": {"type": "integer"}}),
        _schema("mail__delete_message", "Delete a message.", {"message_id": {"type": "integer"}}),
    ]
    api_docs = tmp_path / "api_docs"
    api_docs.mkdir()
    (api_docs / "mail.json").write_text(json.dumps(payload), encoding="utf-8")
    return AppWorldToolCatalog.from_api_docs(api_docs)


def _call(call_id: str, name: str, arguments: str) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def _result(call_id: str, name: str, args: dict, result: object, *, executed: bool = True) -> dict:
    return {
        "turn": 1,
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "args": args,
        "executed": executed,
        "result": result,
    }


def test_converter_preserves_calls_and_uses_conservative_states(tmp_path: Path) -> None:
    converter = AppWorldTraceConverter(_catalog(tmp_path))
    trace = [
        {
            "turn": 1,
            "role": "assistant",
            "tool_calls": [
                _call("login", "mail__login", '{"password":"raw-password"}'),
                _call("send", "mail__send_message", '{"access_token":"raw-token","message":"hello"}'),
                _call("read", "mail__show_message", '{"message_id":7}'),
                _call("delete", "mail__delete_message", '{"message_id":7}'),
                _call("missing", "mail__unknown_mutation", "{}"),
            ],
        },
        _result("login", "mail__login", {"password": "raw-password"}, {"access_token": "raw-token"}),
        _result(
            "send",
            "mail__send_message",
            {"access_token": "raw-token", "message": "hello"},
            {"message": "sent"},
        ),
        _result("read", "mail__show_message", {"message_id": 7}, {"message_id": 7, "body": "hello"}),
        _result(
            "delete",
            "mail__delete_message",
            {"message_id": 7},
            {"executed": False, "reason": "suppressed_pending_state_refresh"},
            executed=False,
        ),
    ]

    bundle = converter.convert(
        trace,
        task_id="task-1",
        run_id="run-1",
        converted_at=FIXED_TIME,
    )

    assert bundle.summary.actions == 5
    assert bundle.summary.executed_actions == 3
    assert bundle.summary.non_executed_actions == 2
    assert bundle.summary.read_actions == 1
    assert bundle.summary.write_actions == 4
    assert bundle.summary.evidence_items == 4
    assert bundle.summary.missing_tool_results == 1
    assert bundle.summary.unknown_tools == 1
    assert {effect.status for effect in bundle.effects} == {
        EffectStatus.APPLIED_UNVERIFIED,
        EffectStatus.PLANNED,
    }
    assert all(item.status is EvidenceStatus.OBSERVED for item in bundle.evidence)
    assert bundle.evidence[-1].source_type is EvidenceSourceType.RUNTIME

    send_action = next(action for action in bundle.actions if action.tool_name == "mail__send_message")
    login_action = next(action for action in bundle.actions if action.tool_name == "mail__login")
    login_evidence = next(item for item in bundle.evidence if item.subject == f"tool_call:{login_action.action_id}")
    assert send_action.dependency_action_ids == (login_action.action_id,)
    assert send_action.required_evidence_ids == (login_evidence.evidence_id,)
    assert send_action.arguments["access_token"] == REDACTED_VALUE

    serialized = bundle.model_dump_json()
    assert "raw-password" not in serialized
    assert "raw-token" not in serialized


def test_explicit_write_error_becomes_failed_not_verified(tmp_path: Path) -> None:
    converter = AppWorldTraceConverter(_catalog(tmp_path))
    trace = [
        {
            "turn": 2,
            "role": "assistant",
            "tool_calls": [_call("delete", "mail__delete_message", '{"message_id":9}')],
        },
        {
            **_result("delete", "mail__delete_message", {"message_id": 9}, {"error": "not found"}),
            "turn": 2,
        },
    ]

    bundle = converter.convert(trace, task_id="task-2", run_id="run-2", converted_at=FIXED_TIME)

    assert bundle.effects[0].status is EffectStatus.FAILED
    assert bundle.effects[0].verification_id is None
    assert bundle.evidence[0].status is EvidenceStatus.OBSERVED


def test_legacy_trace_without_execution_flags_is_detected_as_a_whole_format(tmp_path: Path) -> None:
    converter = AppWorldTraceConverter(_catalog(tmp_path))
    trace = [
        {
            "turn": 1,
            "role": "assistant",
            "tool_calls": [
                _call("executed", "mail__delete_message", '{"message_id":9}'),
                _call("silently-dropped", "mail__show_message", '{"message_id":9}'),
            ],
        },
        {
            "turn": 1,
            "role": "tool",
            "tool_call_id": "executed",
            "name": "mail__delete_message",
            "args": {"message_id": 9},
            "result": {"message": "deleted"},
        },
    ]

    bundle = converter.convert(trace, task_id="legacy", run_id="legacy", converted_at=FIXED_TIME)

    assert bundle.summary.executed_actions == 1
    assert bundle.summary.non_executed_actions == 1
    assert bundle.summary.missing_tool_results == 1
    assert bundle.effects[0].status is EffectStatus.APPLIED_UNVERIFIED
    assert bundle.summary.diagnostic_counts == {
        "LEGACY_EXECUTION_FLAGS_INFERRED": 1,
        "MISSING_TOOL_RESULT": 1,
    }


def test_real_smoke_trace_round_trips_without_hidden_evaluator_state() -> None:
    converter = AppWorldTraceConverter(AppWorldToolCatalog.from_api_docs(REAL_API_DOCS))

    bundle = converter.convert_file(REAL_TRACE, run_id="smoke-4-offline")

    assert bundle.summary.actions == bundle.summary.tool_results
    assert bundle.summary.missing_tool_results == 0
    assert bundle.summary.orphan_tool_results == 0
    assert bundle.summary.unknown_tools == 0
    assert all(effect.status is not EffectStatus.VERIFIED for effect in bundle.effects)
    assert all(item.status is EvidenceStatus.OBSERVED for item in bundle.evidence)
    assert any(action.tool_name == "supervisor__complete_task" for action in bundle.actions)
    assert any(action.kind is ActionKind.WRITE for action in bundle.actions)


def test_cli_resumes_only_when_source_catalog_and_converter_match(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    api_docs = tmp_path / "api_docs"
    traces = tmp_path / "traces"
    output = tmp_path / "converted"
    traces.mkdir()
    trace = [
        {
            "turn": 1,
            "role": "assistant",
            "tool_calls": [_call("read", "mail__show_message", '{"message_id":1}')],
        },
        _result("read", "mail__show_message", {"message_id": 1}, {"message_id": 1}),
    ]
    (traces / "task-1.json").write_text(json.dumps(trace), encoding="utf-8")
    args = Namespace(input=traces, output=output, api_docs=api_docs, run_id="cli-run", overwrite=False)

    assert len(catalog) == 4
    assert run(args) == 0
    first_summary = json.loads((output / "conversion_summary.json").read_text(encoding="utf-8"))
    assert first_summary["converted"] == 1
    assert first_summary["skipped_current"] == 0

    assert run(args) == 0
    second_summary = json.loads((output / "conversion_summary.json").read_text(encoding="utf-8"))
    assert second_summary["converted"] == 0
    assert second_summary["skipped_current"] == 1
