"""Normalize standard function calls into framework-independent ActionIR records."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from agentgate_core.contracts.action import ActionIR, ActionKind, ExpectedEffect, ResourceRef
from agentgate_core.contracts.tool_guard import RawToolCall
from agentgate_core.tool_execution_guard.catalog import ToolCatalog


class ActionNormalizer:
    def __init__(self, catalog: ToolCatalog) -> None:
        self.catalog = catalog

    def normalize(
        self,
        call: RawToolCall,
        *,
        dependency_action_ids: tuple[str, ...] = (),
        required_evidence_ids: tuple[str, ...] = (),
        dependency_action_by_tool: Mapping[str, str] | None = None,
        evidence_by_requirement: Mapping[str, str] | None = None,
    ) -> ActionIR:
        entry = self.catalog.get_tool(call.tool_name)
        action_bindings = dependency_action_by_tool or {}
        evidence_bindings = evidence_by_requirement or {}
        catalog_dependencies = tuple(
            action_bindings[name] for name in entry.dependency_tool_names if name in action_bindings
        )
        catalog_evidence = tuple(
            evidence_bindings.get(requirement, requirement) for requirement in entry.required_evidence
        )
        resource = _resource(entry.resource_types[0] if entry.resource_types else "tool", call.arguments)
        expected_effects: tuple[ExpectedEffect, ...] = ()
        idempotency_key: str | None = None
        if entry.action_kind is ActionKind.WRITE:
            assert entry.side_effect_type is not None
            expected_effects = (
                ExpectedEffect(
                    effect_key="primary_effect",
                    kind=entry.side_effect_type,
                    resource=resource,
                    expected_change={"operation": entry.operation, "arguments": call.arguments},
                ),
            )
            idempotency_key = _idempotency_key(call.task_id, entry.operation, resource, call.arguments)
        return ActionIR(
            action_id=call.tool_call_id,
            task_id=call.task_id,
            actor=call.actor,
            kind=entry.action_kind,
            tool_name=entry.name,
            operation=entry.operation,
            resource=resource,
            arguments=call.arguments,
            expected_effects=expected_effects,
            required_evidence_ids=tuple(dict.fromkeys((*required_evidence_ids, *catalog_evidence))),
            dependency_action_ids=tuple(dict.fromkeys((*dependency_action_ids, *catalog_dependencies))),
            idempotency_key=idempotency_key,
            risk_level=entry.risk_level,
            tool_schema_version=entry.tool_schema_version,
            source_turn=call.source_turn,
            rationale=call.rationale,
        )


def _resource(resource_type: str, arguments: dict[str, object]) -> ResourceRef:
    resource_id: str | None = None
    scope: str | None = None
    for key, value in arguments.items():
        if key.lower().endswith("_id") and isinstance(value, (str, int)) and not isinstance(value, bool):
            resource_id = str(value)
            break
    for key in ("scope", "workspace", "domain", "tenant_id"):
        value = arguments.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            scope = str(value)
            break
    return ResourceRef(resource_type=resource_type, resource_id=resource_id, scope=scope)


def _idempotency_key(task_id: str, operation: str, resource: ResourceRef, arguments: dict[str, object]) -> str:
    canonical = json.dumps(
        {
            "task_id": task_id,
            "operation": operation,
            "resource": resource.model_dump(mode="json"),
            "arguments": arguments,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


__all__ = ["ActionNormalizer"]
