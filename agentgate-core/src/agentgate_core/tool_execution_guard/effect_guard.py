"""Effect preflight, reservation, and guarded status transitions."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from agentgate_core.contracts.action import ActionIR
from agentgate_core.contracts.base import utc_now
from agentgate_core.contracts.decision import (
    ActionEvaluationContext,
    DecisionOutcome,
    FeatureName,
    PipelineStage,
    StageEvaluation,
)
from agentgate_core.contracts.effect import EffectRecord, EffectStatus
from agentgate_core.contracts.tool_guard import EffectPreflightDecision, EffectPreflightOutcome
from agentgate_core.contracts.trace import TraceActor, TraceEventType
from agentgate_core.runtime.state_store import StateInvariantError, TypedStateStore
from agentgate_core.tracing.recorder import TraceRecorder

_LEGAL_EFFECT_TRANSITIONS: dict[EffectStatus, frozenset[EffectStatus]] = {
    EffectStatus.PLANNED: frozenset({EffectStatus.RESERVED, EffectStatus.FAILED}),
    EffectStatus.RESERVED: frozenset({EffectStatus.IN_FLIGHT, EffectStatus.FAILED}),
    EffectStatus.IN_FLIGHT: frozenset({EffectStatus.APPLIED_UNVERIFIED, EffectStatus.FAILED, EffectStatus.UNKNOWN}),
    EffectStatus.APPLIED_UNVERIFIED: frozenset(
        {EffectStatus.VERIFIED, EffectStatus.FAILED, EffectStatus.UNKNOWN, EffectStatus.ROLLED_BACK}
    ),
    EffectStatus.UNKNOWN: frozenset(
        {EffectStatus.VERIFIED, EffectStatus.FAILED, EffectStatus.APPLIED_UNVERIFIED, EffectStatus.ROLLED_BACK}
    ),
    EffectStatus.FAILED: frozenset({EffectStatus.RESERVED, EffectStatus.ROLLED_BACK}),
    EffectStatus.VERIFIED: frozenset({EffectStatus.ROLLED_BACK}),
    EffectStatus.ROLLED_BACK: frozenset(),
}


class GuardedEffectLedger:
    def __init__(
        self,
        *,
        run_id: str,
        state_store: TypedStateStore,
        trace_recorder: TraceRecorder,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.run_id = run_id
        self.state_store = state_store
        self.trace_recorder = trace_recorder
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def preflight(self, action: ActionIR) -> EffectPreflightDecision:
        if action.idempotency_key is None:
            raise ValueError("effect preflight requires a write action with idempotency_key")
        existing = self._matching_effect(action)
        if existing is None:
            return EffectPreflightDecision(
                task_id=action.task_id,
                action_id=action.action_id,
                outcome=EffectPreflightOutcome.RESERVE,
                idempotency_key=action.idempotency_key,
                reason="No prior effect owns the normalized idempotency key.",
            )
        outcome = {
            EffectStatus.VERIFIED: EffectPreflightOutcome.ALREADY_APPLIED,
            EffectStatus.IN_FLIGHT: EffectPreflightOutcome.VERIFY_FIRST,
            EffectStatus.UNKNOWN: EffectPreflightOutcome.VERIFY_FIRST,
            EffectStatus.APPLIED_UNVERIFIED: EffectPreflightOutcome.VERIFY_FIRST,
            EffectStatus.FAILED: EffectPreflightOutcome.RECOVERY_REQUIRED,
            EffectStatus.PLANNED: EffectPreflightOutcome.WAIT,
            EffectStatus.RESERVED: EffectPreflightOutcome.WAIT,
            EffectStatus.ROLLED_BACK: EffectPreflightOutcome.RESERVE,
        }[existing.status]
        return EffectPreflightDecision(
            task_id=action.task_id,
            action_id=action.action_id,
            outcome=outcome,
            idempotency_key=action.idempotency_key,
            existing_effect_id=existing.effect_id,
            existing_status=existing.status,
            reason=f"The idempotency key is already associated with a {existing.status.value} effect.",
        )

    def reserve(self, action: ActionIR, *, turn: int) -> tuple[EffectRecord, ...]:
        decision = self.preflight(action)
        if decision.outcome is not EffectPreflightOutcome.RESERVE:
            raise StateInvariantError(f"effect cannot be reserved after preflight outcome {decision.outcome.value}")
        now = self._clock()
        effects: list[EffectRecord] = []
        for expected in action.expected_effects:
            effect = EffectRecord(
                effect_id=f"effect-{self._id_factory()}",
                task_id=action.task_id,
                action_id=action.action_id,
                idempotency_key=_effect_key(action, expected.effect_key),
                kind=expected.kind,
                operation=action.operation,
                resource=expected.resource,
                expected_change=expected.expected_change,
                status=EffectStatus.RESERVED,
                created_at=now,
                updated_at=now,
            )
            self.state_store.append_effect(action.task_id, effect)
            effects.append(effect)
            self.trace_recorder.record(
                task_id=action.task_id,
                run_id=self.run_id,
                turn=turn,
                event_type=TraceEventType.EFFECT_RESERVED,
                actor=TraceActor.RUNTIME,
                correlation_id=effect.effect_id,
                payload={"effect": effect.model_dump(mode="json")},
                critical=True,
            )
        return tuple(effects)

    def transition(
        self,
        task_id: str,
        effect_id: str,
        target_status: EffectStatus,
        *,
        turn: int,
        actual_change: dict[str, object] | None = None,
        verification_id: str | None = None,
    ) -> EffectRecord:
        current = self.state_store.get_effect(task_id, effect_id)
        if target_status not in _LEGAL_EFFECT_TRANSITIONS[current.status]:
            raise StateInvariantError(f"illegal effect transition: {current.status.value} -> {target_status.value}")
        updated = current.model_copy(
            update={
                "status": target_status,
                "actual_change": actual_change if actual_change is not None else current.actual_change,
                "verification_id": verification_id,
                "updated_at": max(self._clock(), current.updated_at),
            }
        )
        updated = EffectRecord.model_validate(updated.model_dump(mode="python"))
        stored = self.state_store.update_effect(task_id, updated, expected_status=current.status)
        self.trace_recorder.record(
            task_id=task_id,
            run_id=self.run_id,
            turn=turn,
            event_type=TraceEventType.EFFECT_STATUS_CHANGED,
            actor=TraceActor.RUNTIME,
            correlation_id=effect_id,
            payload={"effect_transition": {"from": current.status.value, "effect": stored.model_dump(mode="json")}},
            critical=True,
        )
        return stored

    def _matching_effect(self, action: ActionIR) -> EffectRecord | None:
        keys = {_effect_key(action, item.effect_key) for item in action.expected_effects}
        return next(
            (effect for effect in self.state_store.list_effects(action.task_id) if effect.idempotency_key in keys), None
        )


class EffectPreflightStage:
    stage = PipelineStage.EFFECT_PREFLIGHT
    feature = FeatureName.TOOL_EXECUTION_GUARD

    def __init__(self, ledger: GuardedEffectLedger) -> None:
        self.ledger = ledger

    def evaluate(self, context: ActionEvaluationContext) -> StageEvaluation:
        if context.action.idempotency_key is None:
            return StageEvaluation(
                outcome=DecisionOutcome.ALLOW,
                reason_code="read_has_no_effect",
                explanation="Read actions do not reserve side effects.",
            )
        decision = self.ledger.preflight(context.action)
        outcome = {
            EffectPreflightOutcome.RESERVE: DecisionOutcome.ALLOW,
            EffectPreflightOutcome.ALREADY_APPLIED: DecisionOutcome.ALREADY_APPLIED,
            EffectPreflightOutcome.VERIFY_FIRST: DecisionOutcome.REQUIRE_EVIDENCE,
            EffectPreflightOutcome.RECOVERY_REQUIRED: DecisionOutcome.REPLAN,
            EffectPreflightOutcome.WAIT: DecisionOutcome.DEFER,
        }[decision.outcome]
        return StageEvaluation(
            outcome=outcome,
            reason_code=f"effect_{decision.outcome.value}",
            explanation=decision.reason,
            payload={"preflight": decision.model_dump(mode="json")},
        )


def _effect_key(action: ActionIR, effect_key: str) -> str:
    assert action.idempotency_key is not None
    if len(action.expected_effects) == 1:
        return action.idempotency_key
    digest = hashlib.sha256(f"{action.idempotency_key}:{effect_key}".encode()).hexdigest()
    return f"sha256:{digest}"


__all__ = ["EffectPreflightStage", "GuardedEffectLedger"]
