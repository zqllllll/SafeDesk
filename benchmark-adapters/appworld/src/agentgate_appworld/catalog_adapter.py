"""Convert AppWorld's public function schemas into an AgentGate tool catalog."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from agentgate_core.contracts import (
    ActionKind,
    EffectKind,
    RiskLevel,
    ToolCatalogEntry,
    ToolCatalogSnapshot,
)
from agentgate_core.tool_execution_guard import InMemoryToolCatalog

CATALOG_RULESET_VERSION = "appworld-public-api-semantics-v1"

# AppWorld's public function schemas do not declare side effects. Only these
# operation families are observational; every other family fails closed as a write.
READ_OPERATION_PREFIXES = frozenset({"get", "search", "show"})

EFFECT_PREFIXES: dict[EffectKind, frozenset[str]] = {
    EffectKind.CREATE: frozenset(
        {
            "add",
            "attach",
            "copy",
            "create",
            "directory",
            "file",
            "follow",
            "post",
            "record",
            "signup",
            "subscribe",
            "upload",
        }
    ),
    EffectKind.UPDATE: frozenset(
        {
            "accept",
            "apply",
            "approve",
            "assign",
            "clear",
            "compress",
            "decompress",
            "deny",
            "download",
            "label",
            "like",
            "loop",
            "mark",
            "move",
            "next",
            "pause",
            "place",
            "play",
            "previous",
            "regenerate",
            "remind",
            "reset",
            "review",
            "seek",
            "set",
            "settle",
            "shuffle",
            "undelete",
            "unlabel",
            "unlike",
            "update",
            "verify",
            "withdraw",
            "write",
        }
    ),
    EffectKind.DELETE: frozenset({"delete", "remove", "unfollow"}),
    EffectKind.SEND: frozenset({"forward", "reply", "send"}),
    EffectKind.SUBMIT: frozenset({"complete", "initiate", "submit"}),
    EffectKind.SESSION: frozenset({"exit", "login", "logout"}),
}

HIGH_RISK_EFFECTS = frozenset({EffectKind.DELETE, EffectKind.SEND, EffectKind.SUBMIT})
SENSITIVE_PARAMETER_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "card_number",
        "cvv",
        "password",
        "secret",
        "token",
    }
)


def _split_tool_name(name: str) -> tuple[str, str]:
    if "__" not in name:
        return "unknown", name
    return tuple(name.split("__", 1))  # type: ignore[return-value]


def _operation_prefix(operation: str) -> str:
    return operation.split("_", 1)[0].lower()


def _effect_kind(operation: str) -> EffectKind:
    prefix = _operation_prefix(operation)
    for effect_kind, prefixes in EFFECT_PREFIXES.items():
        if prefix in prefixes:
            return effect_kind
    return EffectKind.OTHER


def _resource_type(app: str, operation: str) -> str:
    prefix = _operation_prefix(operation)
    suffix = operation[len(prefix) :].lstrip("_")
    return f"{app}.{suffix or 'session'}"


def _risk_level(action_kind: ActionKind, effect_kind: EffectKind | None, tool_name: str) -> RiskLevel:
    if tool_name == "supervisor__complete_task":
        return RiskLevel.HIGH
    if action_kind is ActionKind.READ:
        return RiskLevel.LOW
    if effect_kind in HIGH_RISK_EFFECTS:
        return RiskLevel.HIGH
    if effect_kind is EffectKind.OTHER:
        return RiskLevel.CRITICAL
    return RiskLevel.MEDIUM


def _has_parameter(input_schema: Mapping[str, Any], parameter_name: str) -> bool:
    properties = input_schema.get("properties", {})
    return isinstance(properties, Mapping) and parameter_name in properties


def _entry_from_function_schema(function_schema: Mapping[str, Any]) -> ToolCatalogEntry:
    function = function_schema.get("function")
    if not isinstance(function, Mapping):
        raise ValueError("AppWorld schema entry must contain a function object")

    name = function.get("name")
    description = function.get("description")
    input_schema = function.get("parameters")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("AppWorld function name must be a non-empty string")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"AppWorld function {name!r} must have a description")
    if not isinstance(input_schema, dict):
        raise ValueError(f"AppWorld function {name!r} must have an object parameter schema")

    app, operation = _split_tool_name(name)
    prefix = _operation_prefix(operation)
    # Completion is a control-plane state-changing action. It is normalized as
    # a write so the execution guard schedules and validates it conservatively;
    # the orchestrator keeps it out of the domain Effect Ledger after the
    # Completion Gate has evaluated the task state.
    action_kind = ActionKind.READ if prefix in READ_OPERATION_PREFIXES else ActionKind.WRITE
    effect_kind = None if action_kind is ActionKind.READ else _effect_kind(operation)
    resource_type = _resource_type(app, operation)
    dependency_tool_names: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()
    if _has_parameter(input_schema, "access_token") and operation != "login":
        dependency_tool_names = (f"{app}__login",)
        required_evidence = (f"{app}.authenticated_session",)

    return ToolCatalogEntry(
        name=name,
        description=description,
        operation=operation,
        input_schema=input_schema,
        output_schema=None,
        action_kind=action_kind,
        risk_level=_risk_level(action_kind, effect_kind, name),
        side_effect_type=effect_kind,
        resource_types=(resource_type,),
        required_evidence=required_evidence,
        dependency_tool_names=dependency_tool_names,
        verification_strategy=("appworld.post_action_readback" if action_kind is ActionKind.WRITE else None),
        idempotency_strategy=(
            "canonical_task_operation_resource_arguments" if action_kind is ActionKind.WRITE else None
        ),
    )


def _catalog_version(entries: Iterable[ToolCatalogEntry]) -> str:
    semantic_fingerprint = [
        {
            "name": entry.name,
            "schema": entry.tool_schema_version,
            "action_kind": entry.action_kind,
            "effect_kind": entry.side_effect_type,
            "resource_types": entry.resource_types,
            "risk_level": entry.risk_level,
        }
        for entry in sorted(entries, key=lambda item: item.name)
    ]
    canonical = json.dumps(
        {"ruleset": CATALOG_RULESET_VERSION, "entries": semantic_fingerprint},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


class AppWorldToolCatalog:
    """Read-only lookup around a validated public AppWorld catalog snapshot."""

    def __init__(self, snapshot: ToolCatalogSnapshot) -> None:
        self.snapshot = snapshot
        self._entries = {entry.name: entry for entry in snapshot.entries}

    @classmethod
    def from_api_docs(cls, api_docs_dir: Path) -> AppWorldToolCatalog:
        if not api_docs_dir.is_dir():
            raise FileNotFoundError(f"AppWorld function-calling API docs directory not found: {api_docs_dir}")

        entries: list[ToolCatalogEntry] = []
        for path in sorted(api_docs_dir.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError(f"AppWorld API docs file must contain a list: {path}")
            entries.extend(_entry_from_function_schema(item) for item in payload)
        if not entries:
            raise ValueError(f"No AppWorld function schemas found in {api_docs_dir}")

        names = {entry.name for entry in entries}
        normalized_entries = tuple(
            entry.model_copy(
                update={
                    "dependency_tool_names": tuple(
                        dependency for dependency in entry.dependency_tool_names if dependency in names
                    )
                }
            )
            for entry in entries
        )
        snapshot = ToolCatalogSnapshot(
            catalog_version=_catalog_version(normalized_entries),
            entries=normalized_entries,
        )
        return cls(snapshot)

    def get(self, tool_name: str) -> ToolCatalogEntry | None:
        return self._entries.get(tool_name)

    @property
    def catalog_version(self) -> str:
        return self.snapshot.catalog_version

    def get_tool(self, tool_name: str) -> ToolCatalogEntry:
        return self.require(tool_name)

    def list_tools(self) -> tuple[ToolCatalogEntry, ...]:
        return self.snapshot.entries

    def to_core_catalog(self) -> InMemoryToolCatalog:
        """Return the framework-neutral catalog interface consumed by AgentGate assembly."""

        return InMemoryToolCatalog(self.snapshot)

    def require(self, tool_name: str) -> ToolCatalogEntry:
        try:
            return self._entries[tool_name]
        except KeyError as exc:
            raise KeyError(f"Unknown AppWorld tool: {tool_name}") from exc

    def __len__(self) -> int:
        return len(self._entries)


def conservative_unknown_entry(tool_name: str) -> ToolCatalogEntry:
    """Represent an out-of-catalog call without ever classifying it as harmless."""

    raw_name = tool_name.strip()
    safe_name = raw_name or "unknown_tool"
    if len(safe_name) > 256:
        digest = hashlib.sha256(safe_name.encode("utf-8")).hexdigest()[:24]
        safe_name = f"unknown_tool_{digest}"
    app, operation = _split_tool_name(safe_name)
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    return ToolCatalogEntry(
        name=safe_name,
        description="Conservative synthetic entry for an out-of-catalog AppWorld call.",
        operation=operation or "unknown_operation",
        input_schema=input_schema,
        output_schema=None,
        action_kind=ActionKind.WRITE,
        risk_level=RiskLevel.CRITICAL,
        side_effect_type=EffectKind.OTHER,
        resource_types=(_resource_type(app, operation or "unknown_operation"),),
        verification_strategy="appworld.manual_unknown_tool_review",
        idempotency_strategy="canonical_task_operation_resource_arguments",
    )


__all__ = [
    "AppWorldToolCatalog",
    "CATALOG_RULESET_VERSION",
    "READ_OPERATION_PREFIXES",
    "SENSITIVE_PARAMETER_KEYS",
    "conservative_unknown_entry",
]
