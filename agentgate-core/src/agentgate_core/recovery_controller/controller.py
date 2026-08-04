"""Recovery orchestration with budgets, effect refresh, and truthful termination."""

from __future__ import annotations

from agentgate_core.contracts.base import utc_now
from agentgate_core.contracts.effect import EffectStatus
from agentgate_core.contracts.failure import FailureRecord, FailureStatus
from agentgate_core.contracts.recovery import (
    FailureSignal,
    ProgressSignal,
    RecoveryPlan,
    RecoveryResult,
    RecoveryState,
    RecoveryStrategyType,
    RecoveryTermination,
)
from agentgate_core.contracts.task import TaskPhase
from agentgate_core.contracts.trace import TraceActor, TraceEventType
from agentgate_core.recovery_controller.budget import RecoveryBudgetManager
from agentgate_core.recovery_controller.classifier import FailureClassifier
from agentgate_core.recovery_controller.strategies import RecoveryStrategyRegistry
from agentgate_core.runtime.state_store import TypedStateStore
from agentgate_core.tracing.recorder import TraceRecorder


class RecoveryController:
    def __init__(
        self,
        *,
        run_id: str,
        state_store: TypedStateStore,
        trace_recorder: TraceRecorder,
        classifier: FailureClassifier,
        strategies: RecoveryStrategyRegistry,
        budgets: RecoveryBudgetManager,
    ) -> None:
        self.run_id = run_id
        self.state_store = state_store
        self.trace_recorder = trace_recorder
        self.classifier = classifier
        self.strategies = strategies
        self.budgets = budgets

    def start(
        self,
        signal: FailureSignal,
        *,
        turn: int,
        estimated_tokens: int = 0,
    ) -> RecoveryPlan | RecoveryTermination:
        provisional = self.classifier.classify(signal, recovery_budget=0)
        budget = self.budgets.consume(signal.task_id, provisional.failure_type, estimated_tokens=estimated_tokens)
        remaining = self.budgets.remaining_for_type(signal.task_id, provisional.failure_type)
        failure = provisional.model_copy(
            update={
                "recovery_budget_remaining": remaining,
                "attempt_count": 0 if budget is None else budget.attempts_by_type[provisional.failure_type],
                "status": FailureStatus.BUDGET_EXHAUSTED if budget is None else FailureStatus.OPEN,
            }
        )
        self.state_store.append_failure(signal.task_id, failure)
        classified_trace = self.trace_recorder.record(
            task_id=signal.task_id,
            run_id=self.run_id,
            turn=turn,
            event_type=TraceEventType.FAILURE_CLASSIFIED,
            actor=TraceActor.RECOVERY_CONTROLLER,
            correlation_id=failure.failure_id,
            payload={"failure": failure.model_dump(mode="json"), "signal": signal.model_dump(mode="json")},
            critical=signal.action is not None and signal.action.kind.value == "write",
        )
        if budget is None:
            return self.termination(failure)
        strategy = self.strategies.resolve(failure.failure_type)
        plan = strategy.plan(failure, signal)
        if self._effect_state_requires_refresh(failure):
            plan = plan.model_copy(
                update={
                    "strategy_type": RecoveryStrategyType.VERIFY_BEFORE_RETRY,
                    "recommended_phase": TaskPhase.VERIFY,
                    "repaired_action": None,
                    "verify_before_execution": True,
                    "instructions": ("Read back the environment because the failed write may already have executed.",),
                    "reason": "Effect Ledger is uncertain or unverified; blind replay is forbidden.",
                }
            )
        recovering = failure.model_copy(update={"status": FailureStatus.RECOVERING})
        self.state_store.update_failure(signal.task_id, recovering, expected_status=failure.status)
        self.trace_recorder.record(
            task_id=signal.task_id,
            run_id=self.run_id,
            turn=turn,
            event_type=TraceEventType.RECOVERY_PLANNED,
            actor=TraceActor.RECOVERY_CONTROLLER,
            correlation_id=plan.plan_id,
            parent_event_id=(classified_trace.event.event_id if classified_trace.event is not None else None),
            payload={"recovery_plan": plan.model_dump(mode="json"), "budget": budget.model_dump(mode="json")},
            critical=signal.action is not None and signal.action.kind.value == "write",
        )
        return plan

    def finish(
        self,
        plan: RecoveryPlan,
        before: ProgressSignal,
        after: ProgressSignal,
        *,
        turn: int,
        verification_id: str | None = None,
    ) -> RecoveryResult:
        if before.task_id != plan.task_id or after.task_id != plan.task_id:
            raise ValueError("progress signals and recovery plan must belong to the same task")
        progressed = after.score > before.score
        requires_verification = plan.verify_before_execution or (
            plan.strategy_type is RecoveryStrategyType.REPAIR_VERIFICATION
        )
        success = progressed and (not requires_verification or verification_id is not None)
        result = RecoveryResult(
            plan_id=plan.plan_id,
            task_id=plan.task_id,
            failure_id=plan.failure_id,
            final_state=RecoveryState.RECOVERED if success else RecoveryState.FAILED,
            success=success,
            progress_before=before.score,
            progress_after=after.score,
            verification_id=verification_id,
            reason=(
                "Recovery produced measurable progress and satisfied its verification requirement."
                if success
                else "Recovery produced no measurable progress or did not satisfy required verification."
            ),
        )
        trace = self.trace_recorder.record(
            task_id=plan.task_id,
            run_id=self.run_id,
            turn=turn,
            event_type=TraceEventType.RECOVERY_FINISHED,
            actor=TraceActor.RECOVERY_CONTROLLER,
            correlation_id=plan.plan_id,
            payload={"recovery_result": result.model_dump(mode="json")},
            critical=False,
        )
        failure = self.state_store.get_failure(plan.task_id, plan.failure_id)
        updated = failure.model_copy(
            update={
                "status": FailureStatus.RESOLVED if success else FailureStatus.OPEN,
                "resolved_event_id": trace.event.event_id if success and trace.event is not None else None,
                "updated_at": max(utc_now(), failure.updated_at),
            }
        )
        self.state_store.update_failure(plan.task_id, updated, expected_status=failure.status)
        return result

    @staticmethod
    def termination(failure: FailureRecord) -> RecoveryTermination:
        return RecoveryTermination(
            task_id=failure.task_id,
            failure_id=failure.failure_id,
            response=(
                "The task is not complete. Recovery stopped because its bounded budget was exhausted; "
                f"the unresolved failure is {failure.failure_type.value}."
            ),
            final_state=RecoveryState.BUDGET_EXHAUSTED,
            retry_allowed=False,
        )

    def _effect_state_requires_refresh(self, failure: FailureRecord) -> bool:
        if failure.action_id is None:
            return False
        risky = {EffectStatus.IN_FLIGHT, EffectStatus.APPLIED_UNVERIFIED, EffectStatus.UNKNOWN}
        return any(
            effect.action_id == failure.action_id and effect.status in risky
            for effect in self.state_store.list_effects(failure.task_id)
        )


__all__ = ["RecoveryController"]
