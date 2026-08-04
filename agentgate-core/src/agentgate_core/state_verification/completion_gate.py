"""Evidence-based task completion gate and coordinator stage integration."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from uuid import uuid4

from agentgate_core.contracts.action import EffectKind
from agentgate_core.contracts.decision import (
    ActionEvaluationContext,
    DecisionOutcome,
    FeatureMode,
    FeatureName,
    PipelineStage,
    StageEvaluation,
)
from agentgate_core.contracts.effect import EffectStatus
from agentgate_core.contracts.evidence import EvidenceStatus
from agentgate_core.contracts.failure import FailureStatus
from agentgate_core.contracts.state_verification import (
    CompletionBlocker,
    CompletionBlockerType,
    CompletionGateDecision,
)
from agentgate_core.contracts.task import SubgoalStatus, TaskPhase
from agentgate_core.contracts.trace import TraceActor, TraceEventType
from agentgate_core.contracts.verification import UnexpectedEffectStatus
from agentgate_core.runtime.state_store import TypedStateStore
from agentgate_core.tracing.recorder import TraceRecorder

_IRREVERSIBLE_KINDS = {EffectKind.CREATE, EffectKind.DELETE, EffectKind.SEND, EffectKind.SUBMIT}
_OPEN_FAILURE_STATUSES = {
    FailureStatus.OPEN,
    FailureStatus.RECOVERING,
    FailureStatus.BUDGET_EXHAUSTED,
    FailureStatus.ESCALATED,
}


class CompletionGate:
    def __init__(
        self,
        *,
        run_id: str,
        state_store: TypedStateStore,
        trace_recorder: TraceRecorder,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.run_id = run_id
        self.state_store = state_store
        self.trace_recorder = trace_recorder
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def evaluate(self, task_id: str, *, turn: int, mode: FeatureMode) -> CompletionGateDecision:
        contract = self.state_store.get_task_contract(task_id)
        state = self.state_store.get_task_state(task_id)
        evidence = {item.evidence_id: item for item in self.state_store.list_evidence(task_id)}
        effects = {item.effect_id: item for item in self.state_store.list_effects(task_id)}
        blockers: list[CompletionBlocker] = []

        for definition in contract.subgoals:
            subgoal = next(item for item in state.subgoals if item.subgoal_id == definition.subgoal_id)
            if definition.required and subgoal.status is not SubgoalStatus.COMPLETED_VERIFIED:
                blockers.append(
                    _blocker(
                        CompletionBlockerType.REQUIRED_SUBGOAL_NOT_VERIFIED,
                        subgoal.subgoal_id,
                        f"Required subgoal is {subgoal.status.value}, not completed_verified.",
                    )
                )
        for condition in contract.completion_conditions:
            missing = [
                evidence_id
                for evidence_id in condition.required_evidence_ids
                if evidence_id not in evidence or evidence[evidence_id].status is not EvidenceStatus.VERIFIED
            ]
            missing.extend(
                effect_id
                for effect_id in condition.required_effect_ids
                if effect_id not in effects or effects[effect_id].status is not EffectStatus.VERIFIED
            )
            if (not condition.required_evidence_ids and not condition.required_effect_ids) or missing:
                blockers.append(
                    _blocker(
                        CompletionBlockerType.COMPLETION_CONDITION_NOT_VERIFIED,
                        condition.condition_id,
                        "Completion condition lacks verified evidence/effects."
                        if not missing
                        else f"Completion condition has unverified references: {sorted(missing)}",
                    )
                )
        for effect in effects.values():
            if effect.status is not EffectStatus.VERIFIED:
                blockers.append(
                    _blocker(
                        CompletionBlockerType.EFFECT_NOT_VERIFIED,
                        effect.effect_id,
                        f"Effect is {effect.status.value}, not verified by environment readback.",
                    )
                )
        for failure in self.state_store.list_failures(task_id):
            if failure.status in _OPEN_FAILURE_STATUSES:
                blockers.append(
                    _blocker(
                        CompletionBlockerType.UNRESOLVED_FAILURE,
                        failure.failure_id,
                        f"Failure remains {failure.status.value}: {failure.failure_type.value}.",
                    )
                )
        blockers.extend(
            _blocker(CompletionBlockerType.PENDING_CONFIRMATION, item, "Required user confirmation is pending.")
            for item in state.pending_confirmation_ids
        )
        blockers.extend(
            _blocker(CompletionBlockerType.PENDING_APPROVAL, item, "Required approval is pending.")
            for item in state.pending_approval_ids
        )
        blockers.extend(
            _blocker(CompletionBlockerType.EVIDENCE_CONFLICT, item.evidence_id, "Evidence conflict is unresolved.")
            for item in evidence.values()
            if item.status is EvidenceStatus.CONFLICTED
        )
        active_unintended_statuses = {
            UnexpectedEffectStatus.OBSERVED,
            UnexpectedEffectStatus.ROLLBACK_PENDING,
            UnexpectedEffectStatus.UNRESOLVED,
        }
        for verification in self.state_store.list_verifications(task_id):
            for index, unintended_effect in enumerate(verification.unintended_effects):
                if unintended_effect.resolution_status not in active_unintended_statuses:
                    continue
                blockers.append(
                    _blocker(
                        CompletionBlockerType.UNINTENDED_EFFECT,
                        f"{verification.verification_id}:{index}",
                        "Environment verification observed an active unresolved unintended side effect.",
                    )
                )
        irreversible_keys = [
            (
                effect.kind.value,
                effect.operation,
                effect.resource.resource_type,
                effect.resource.resource_id or "unknown",
            )
            for effect in effects.values()
            if effect.kind in _IRREVERSIBLE_KINDS and effect.status is not EffectStatus.ROLLED_BACK
        ]
        for key, count in Counter(irreversible_keys).items():
            if count > 1:
                blockers.append(
                    _blocker(
                        CompletionBlockerType.DUPLICATE_IRREVERSIBLE_EFFECT,
                        ":".join(key),
                        f"The same irreversible resource operation appears {count} times.",
                    )
                )

        proposed_allowed = not blockers
        decision = CompletionGateDecision(
            decision_id=f"completion-{self._id_factory()}",
            task_id=task_id,
            run_id=self.run_id,
            mode=mode,
            proposed_allowed=proposed_allowed,
            effective_allowed=proposed_allowed if mode is FeatureMode.ENFORCE else True,
            blockers=tuple(blockers),
            evidence_ids=tuple(
                item.evidence_id for item in evidence.values() if item.status is EvidenceStatus.VERIFIED
            ),
            recommended_phase=_recommended_phase(blockers),
        )
        self.trace_recorder.record(
            task_id=task_id,
            run_id=self.run_id,
            turn=turn,
            event_type=TraceEventType.COMPLETION_DECISION,
            actor=TraceActor.VERIFIER,
            correlation_id=decision.decision_id,
            state_version=state.state_version,
            payload={"completion_decision": decision.model_dump(mode="json")},
            critical=True,
        )
        return decision


class CompletionGateStage:
    stage = PipelineStage.COMPLETION_GATE
    feature = FeatureName.STATE_VERIFICATION

    def __init__(
        self,
        gate: CompletionGate,
        *,
        mode: FeatureMode,
        completion_tools: tuple[str, ...] = ("supervisor__complete_task", "complete_task"),
    ) -> None:
        self.gate = gate
        self.mode = mode
        self.completion_tools = frozenset(completion_tools)

    def evaluate(self, context: ActionEvaluationContext) -> StageEvaluation:
        if context.action.tool_name not in self.completion_tools:
            return StageEvaluation(
                outcome=DecisionOutcome.ALLOW,
                reason_code="not_completion_action",
                explanation="The proposed action is not a completion action.",
            )
        decision = self.gate.evaluate(context.task_id, turn=context.turn, mode=self.mode)
        if decision.proposed_allowed:
            return StageEvaluation(
                outcome=DecisionOutcome.ALLOW,
                reason_code="completion_verified",
                explanation="All required completion conditions have environment-backed evidence.",
                evidence_ids=decision.evidence_ids,
                payload={"completion_decision_id": decision.decision_id},
            )
        return StageEvaluation(
            outcome=DecisionOutcome.REQUIRE_EVIDENCE,
            reason_code="completion_blocked",
            explanation="Completion lacks required verified state or has unresolved safety blockers.",
            evidence_ids=decision.evidence_ids,
            payload={
                "completion_decision_id": decision.decision_id,
                "blockers": [item.model_dump(mode="json") for item in decision.blockers],
                "recommended_phase": decision.recommended_phase.value,
            },
        )


def _blocker(blocker_type: CompletionBlockerType, reference_id: str, explanation: str) -> CompletionBlocker:
    return CompletionBlocker(blocker_type=blocker_type, reference_id=reference_id, explanation=explanation)


def _recommended_phase(blockers: list[CompletionBlocker]) -> TaskPhase:
    types = {item.blocker_type for item in blockers}
    if not blockers:
        return TaskPhase.COMPLETE
    if types & {
        CompletionBlockerType.EFFECT_NOT_VERIFIED,
        CompletionBlockerType.COMPLETION_CONDITION_NOT_VERIFIED,
        CompletionBlockerType.EVIDENCE_CONFLICT,
    }:
        return TaskPhase.VERIFY
    if types & {CompletionBlockerType.UNRESOLVED_FAILURE, CompletionBlockerType.UNINTENDED_EFFECT}:
        return TaskPhase.REPAIR
    if types & {CompletionBlockerType.PENDING_APPROVAL, CompletionBlockerType.PENDING_CONFIRMATION}:
        return TaskPhase.BLOCKED
    return TaskPhase.ACT


__all__ = ["CompletionGate", "CompletionGateStage"]
