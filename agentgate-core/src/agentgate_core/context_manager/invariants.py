"""Safety validation for compressed ContextPack snapshots."""

from __future__ import annotations

from agentgate_core.contracts.context import ContextPack
from agentgate_core.contracts.context_management import (
    ContextInvariantCode,
    ContextInvariantReport,
    ContextInvariantViolation,
)
from agentgate_core.contracts.effect import EffectStatus
from agentgate_core.contracts.evidence import EvidenceStatus
from agentgate_core.contracts.failure import FailureStatus
from agentgate_core.contracts.task import ConstraintKind
from agentgate_core.runtime.state_store import TypedStateStore


class ContextInvariantValidator:
    def __init__(self, state_store: TypedStateStore) -> None:
        self.state_store = state_store

    def validate(self, pack: ContextPack) -> ContextInvariantReport:
        contract = self.state_store.get_task_contract(pack.task_id)
        state = self.state_store.get_task_state(pack.task_id)
        evidence = self.state_store.list_evidence(pack.task_id)
        effects = self.state_store.list_effects(pack.task_id)
        failures = self.state_store.list_failures(pack.task_id)
        violations: list[ContextInvariantViolation] = []
        if pack.state_version != state.state_version:
            violations.append(
                _violation(ContextInvariantCode.STATE_VERSION_MISMATCH, pack.task_id, "Context uses stale state.")
            )
        task_section = next((item for item in pack.sections if item.section_id == "task_contract"), None)
        if task_section is None or not isinstance(task_section.content, dict):
            violations.append(
                _violation(ContextInvariantCode.ORIGINAL_GOAL_MISSING, contract.task_id, "Task contract is missing.")
            )
        elif task_section.content.get("original_instruction") != contract.original_instruction:
            violations.append(
                _violation(
                    ContextInvariantCode.ORIGINAL_GOAL_MISSING, contract.task_id, "Original instruction changed."
                )
            )
        required_constraints = {
            item.description for item in contract.constraints if item.active and item.kind is not ConstraintKind.SOFT
        }
        if not required_constraints.issubset(set(pack.hard_constraints)):
            violations.append(
                _violation(
                    ContextInvariantCode.HARD_CONSTRAINT_MISSING, contract.task_id, "A hard constraint is missing."
                )
            )
        for subgoal_id in state.active_subgoal_ids:
            if subgoal_id not in pack.active_subgoal_ids:
                violations.append(
                    _violation(ContextInvariantCode.ACTIVE_SUBGOAL_MISSING, subgoal_id, "Active subgoal is missing.")
                )
        verified = {item.evidence_id for item in evidence if item.status is EvidenceStatus.VERIFIED}
        observed = {item.evidence_id for item in evidence if item.status is not EvidenceStatus.VERIFIED}
        for evidence_id in verified - set(pack.verified_evidence_ids):
            violations.append(
                _violation(ContextInvariantCode.VERIFIED_EVIDENCE_MISSING, evidence_id, "Verified evidence is missing.")
            )
        for evidence_id in set(pack.verified_evidence_ids) & observed:
            violations.append(
                _violation(
                    ContextInvariantCode.EVIDENCE_STATUS_UPGRADED, evidence_id, "Observed evidence was upgraded."
                )
            )
        unresolved_effects = {
            item.effect_id for item in effects if item.status not in {EffectStatus.VERIFIED, EffectStatus.ROLLED_BACK}
        }
        for effect_id in unresolved_effects - set(pack.effect_ids):
            violations.append(
                _violation(ContextInvariantCode.UNRESOLVED_EFFECT_MISSING, effect_id, "Unresolved effect is missing.")
            )
        ledger_section = next((item for item in pack.sections if item.section_id == "effect_ledger"), None)
        ledger_effects = _ledger_effects(ledger_section.content if ledger_section is not None else None)
        for effect in effects:
            serialized = ledger_effects.get(effect.effect_id)
            if serialized is None:
                if effect.status not in {EffectStatus.VERIFIED, EffectStatus.ROLLED_BACK}:
                    violations.append(
                        _violation(
                            ContextInvariantCode.UNRESOLVED_EFFECT_MISSING,
                            effect.effect_id,
                            "Unresolved effect details are missing from the context ledger.",
                        )
                    )
                continue
            resource = serialized.get("resource")
            if isinstance(resource, dict) and resource.get("resource_id") != effect.resource.resource_id:
                violations.append(
                    _violation(
                        ContextInvariantCode.RESOURCE_ID_CHANGED,
                        effect.effect_id,
                        "An effect resource ID changed during context construction.",
                    )
                )
        open_failures = {item.failure_id for item in failures if item.status is not FailureStatus.RESOLVED}
        for failure_id in open_failures - set(pack.open_failure_ids):
            violations.append(
                _violation(ContextInvariantCode.OPEN_FAILURE_MISSING, failure_id, "Open failure is missing.")
            )
        state_section = next((item for item in pack.sections if item.section_id == "task_state"), None)
        if state_section is None or not isinstance(state_section.content, dict):
            violations.append(
                _violation(ContextInvariantCode.CONFIRMATION_STATE_CHANGED, state.task_id, "Task state is missing.")
            )
        else:
            if tuple(state_section.content.get("pending_confirmation_ids", ())) != state.pending_confirmation_ids:
                violations.append(
                    _violation(
                        ContextInvariantCode.CONFIRMATION_STATE_CHANGED, state.task_id, "Confirmation state changed."
                    )
                )
        return ContextInvariantReport(
            task_id=pack.task_id,
            state_version=pack.state_version,
            valid=not violations,
            violations=tuple(violations),
        )


def _violation(code: ContextInvariantCode, reference_id: str, message: str) -> ContextInvariantViolation:
    return ContextInvariantViolation(code=code, reference_id=reference_id, message=message)


def _ledger_effects(content: object) -> dict[str, dict[str, object]]:
    if not isinstance(content, dict):
        return {}
    output: dict[str, dict[str, object]] = {}
    for group_name in ("unresolved", "verified"):
        group = content.get(group_name)
        if not isinstance(group, list):
            continue
        for item in group:
            if not isinstance(item, dict):
                continue
            effect_id = item.get("effect_id")
            if isinstance(effect_id, str):
                output[effect_id] = item
    return output


__all__ = ["ContextInvariantValidator"]
