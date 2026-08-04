"""Canonical action fingerprints and state/evidence progress signals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from agentgate_core.contracts.action import ActionIR
from agentgate_core.contracts.effect import EffectStatus
from agentgate_core.contracts.evidence import EvidenceStatus
from agentgate_core.contracts.failure import FailureStatus
from agentgate_core.contracts.recovery import ProgressSignal, ToolCallFingerprint
from agentgate_core.runtime.state_store import TypedStateStore


def fingerprint_action(action: ActionIR, *, volatile_argument_names: tuple[str, ...] = ()) -> ToolCallFingerprint:
    arguments = {key: value for key, value in action.arguments.items() if key not in volatile_argument_names}
    resource = action.resource.model_dump(mode="json") if action.resource is not None else None
    canonical = json.dumps(
        {
            "tool": action.tool_name.lower(),
            "operation": action.operation.lower(),
            "resource": resource,
            "args": arguments,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    resource_name = "none" if resource is None else json.dumps(resource, ensure_ascii=True, sort_keys=True)
    return ToolCallFingerprint(
        action_id=action.action_id,
        fingerprint=f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}",
        normalized_tool_name=action.tool_name.lower(),
        normalized_resource=f"sha256:{hashlib.sha256(resource_name.encode()).hexdigest()}",
    )


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    state_events: int
    verified_evidence: frozenset[str]
    verified_effects: frozenset[str]
    resolved_failures: frozenset[str]
    resource_ids: frozenset[str]
    completion_conditions: frozenset[str]


class ProgressTracker:
    def __init__(self, state_store: TypedStateStore) -> None:
        self.state_store = state_store

    def capture(self, task_id: str) -> ProgressSnapshot:
        contract = self.state_store.get_task_contract(task_id)
        evidence = self.state_store.list_evidence(task_id)
        effects = self.state_store.list_effects(task_id)
        failures = self.state_store.list_failures(task_id)
        verified_evidence = frozenset(item.evidence_id for item in evidence if item.status is EvidenceStatus.VERIFIED)
        verified_effects = frozenset(item.effect_id for item in effects if item.status is EffectStatus.VERIFIED)
        fulfilled_conditions = frozenset(
            condition.condition_id
            for condition in contract.completion_conditions
            if set(condition.required_evidence_ids).issubset(verified_evidence)
            and set(condition.required_effect_ids).issubset(verified_effects)
            and bool(condition.required_evidence_ids or condition.required_effect_ids)
        )
        return ProgressSnapshot(
            state_events=len(self.state_store.list_task_events(task_id)),
            verified_evidence=verified_evidence,
            verified_effects=verified_effects,
            resolved_failures=frozenset(item.failure_id for item in failures if item.status is FailureStatus.RESOLVED),
            resource_ids=frozenset(
                item.resource.resource_id for item in effects if item.resource.resource_id is not None
            ),
            completion_conditions=fulfilled_conditions,
        )

    @staticmethod
    def compare(
        task_id: str, turn: int, before: ProgressSnapshot, after: ProgressSnapshot, *, token_growth: int = 0
    ) -> ProgressSignal:
        return ProgressSignal(
            task_id=task_id,
            turn=turn,
            new_verified_evidence=len(after.verified_evidence - before.verified_evidence),
            state_transitions=max(0, after.state_events - before.state_events),
            new_verified_effects=len(after.verified_effects - before.verified_effects),
            resolved_failures=len(after.resolved_failures - before.resolved_failures),
            new_resource_ids=len(after.resource_ids - before.resource_ids),
            completion_conditions_gained=len(after.completion_conditions - before.completion_conditions),
            token_growth=token_growth,
        )


__all__ = ["ProgressSnapshot", "ProgressTracker", "fingerprint_action"]
