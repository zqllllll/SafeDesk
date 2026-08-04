from __future__ import annotations

import json
from pathlib import Path

from agentgate_appworld.catalog_adapter import AppWorldToolCatalog, conservative_unknown_entry
from agentgate_core.contracts import ActionKind, EffectKind, RiskLevel

WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
REAL_API_DOCS = WORKSPACE_ROOT / "benchmarks" / "appworld-root" / "data" / "api_docs" / "function_calling"


def _schema(name: str, description: str, properties: dict | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties or {}},
        },
    }


def test_catalog_uses_explicit_read_allowlist_and_fails_unknown_operations_closed(tmp_path: Path) -> None:
    payload = [
        _schema("mail__show_message", "Show a message."),
        _schema("mail__login", "Log in."),
        _schema("mail__send_message", "Send a message.", {"access_token": {"type": "string"}}),
        _schema("mail__dance_message", "An unreviewed mutating operation."),
    ]
    (tmp_path / "mail.json").write_text(json.dumps(payload), encoding="utf-8")

    catalog = AppWorldToolCatalog.from_api_docs(tmp_path)

    read = catalog.require("mail__show_message")
    assert read.action_kind is ActionKind.READ
    assert read.side_effect_type is None
    assert read.risk_level is RiskLevel.LOW

    send = catalog.require("mail__send_message")
    assert send.action_kind is ActionKind.WRITE
    assert send.side_effect_type is EffectKind.SEND
    assert send.risk_level is RiskLevel.HIGH
    assert send.dependency_tool_names == ("mail__login",)
    assert send.required_evidence == ("mail.authenticated_session",)

    unknown_mutation = catalog.require("mail__dance_message")
    assert unknown_mutation.action_kind is ActionKind.WRITE
    assert unknown_mutation.side_effect_type is EffectKind.OTHER
    assert unknown_mutation.risk_level is RiskLevel.CRITICAL


def test_real_public_appworld_catalog_converts_all_457_tools() -> None:
    catalog = AppWorldToolCatalog.from_api_docs(REAL_API_DOCS)

    assert len(catalog) == 457
    assert catalog.require("supervisor__complete_task").action_kind is ActionKind.WRITE
    assert catalog.require("supervisor__complete_task").side_effect_type is EffectKind.SUBMIT
    assert catalog.require("amazon__show_account").action_kind is ActionKind.READ
    assert all(entry.tool_schema_version.startswith("sha256:") for entry in catalog.snapshot.entries)


def test_conservative_unknown_entry_bounds_malformed_tool_names() -> None:
    entry = conservative_unknown_entry("x" * 1_000)

    assert entry.name.startswith("unknown_tool_")
    assert len(entry.name) <= 256
    assert entry.action_kind is ActionKind.WRITE
    assert entry.risk_level is RiskLevel.CRITICAL
