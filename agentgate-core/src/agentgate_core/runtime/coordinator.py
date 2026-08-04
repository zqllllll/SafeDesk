"""Feature-aware AgentGate coordinator for framework-independent stage pipelines."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable
from uuid import uuid4

from agentgate_core.contracts.action import ActionKind
from agentgate_core.contracts.decision import (
    ActionEvaluationContext,
    CoordinatorResult,
    DecisionOutcome,
    FeatureMode,
    FeatureName,
    GateDecision,
    PipelineStage,
    StageEvaluation,
)
from agentgate_core.contracts.trace import TraceActor, TraceEventType
from agentgate_core.runtime.config import AgentGateFeatureConfig
from agentgate_core.tracing.recorder import TraceRecorder


@runtime_checkable
class CoordinatorStage(Protocol):
    stage: PipelineStage
    feature: FeatureName

    def evaluate(self, context: ActionEvaluationContext) -> StageEvaluation: ...


class PassThroughStage:
    """Typed no-op stage used to validate the Phase 0 coordinator pipeline."""

    def __init__(self, stage: PipelineStage, feature: FeatureName) -> None:
        self.stage = stage
        self.feature = feature

    def evaluate(self, context: ActionEvaluationContext) -> StageEvaluation:
        return StageEvaluation(
            outcome=DecisionOutcome.ALLOW,
            reason_code="pass_through",
            explanation=f"The {self.stage.value} foundation stage has no enforcement rule installed.",
            payload={"action_id": context.action.action_id},
        )


_STAGE_TRACE_TYPES = {
    PipelineStage.SCHEMA_GUARD: TraceEventType.SCHEMA_DECISION,
    PipelineStage.POLICY_GATE: TraceEventType.POLICY_DECISION,
}

_STAGE_ACTORS = {
    PipelineStage.SCHEMA_GUARD: TraceActor.SCHEMA_GUARD,
    PipelineStage.DEPENDENCY_SCHEDULER: TraceActor.SCHEDULER,
    PipelineStage.POLICY_GATE: TraceActor.POLICY_GATE,
    PipelineStage.EFFECT_PREFLIGHT: TraceActor.RUNTIME,
    PipelineStage.POST_ACTION_VERIFICATION: TraceActor.VERIFIER,
    PipelineStage.FAILURE_CLASSIFIER: TraceActor.RECOVERY_CONTROLLER,
    PipelineStage.CONTEXT_BUILDER: TraceActor.CONTEXT_MANAGER,
}


class AgentGateCoordinator:
    """Run ordered stages while keeping shadow and enforcement semantics explicit."""

    def __init__(
        self,
        *,
        stages: Sequence[CoordinatorStage],
        config: AgentGateFeatureConfig,
        trace_recorder: TraceRecorder,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        stage_names = [stage.stage for stage in stages]
        if len(stage_names) != len(set(stage_names)):
            raise ValueError("coordinator stage names must be unique")
        if trace_recorder.allow_read_degradation != config.allow_read_on_trace_failure:
            raise ValueError("trace recorder degradation policy must match AgentGate feature configuration")
        self._stages = tuple(stages)
        self.config = config
        self.trace_recorder = trace_recorder
        self._id_factory = id_factory or (lambda: str(uuid4()))

    @classmethod
    def with_empty_pipeline(
        cls,
        *,
        config: AgentGateFeatureConfig,
        trace_recorder: TraceRecorder,
        id_factory: Callable[[], str] | None = None,
    ) -> AgentGateCoordinator:
        stages = tuple(
            PassThroughStage(stage, FeatureName.TOOL_EXECUTION_GUARD)
            for stage in (
                PipelineStage.SCHEMA_GUARD,
                PipelineStage.DEPENDENCY_SCHEDULER,
                PipelineStage.POLICY_GATE,
                PipelineStage.EFFECT_PREFLIGHT,
            )
        )
        return cls(stages=stages, config=config, trace_recorder=trace_recorder, id_factory=id_factory)

    def evaluate_action(self, context: ActionEvaluationContext) -> CoordinatorResult:
        critical = context.action.kind is ActionKind.WRITE
        trace_event_ids: list[str] = []
        proposed_result = self.trace_recorder.record(
            task_id=context.task_id,
            run_id=context.run_id,
            turn=context.turn,
            event_type=TraceEventType.ACTION_PROPOSED,
            actor=TraceActor.MODEL,
            correlation_id=context.action.action_id,
            state_version=context.state_version,
            payload={
                "action": context.action.model_dump(mode="json"),
                "feature_config_hash": self.config.configuration_hash,
            },
            critical=critical,
        )
        parent_event_id = proposed_result.event.event_id if proposed_result.event is not None else None
        if parent_event_id is not None:
            trace_event_ids.append(parent_event_id)

        decisions: list[GateDecision] = []
        stopped_at: PipelineStage | None = None
        outcome = DecisionOutcome.ALLOW
        for stage in self._stages:
            mode = self.config.mode_for(stage.feature)
            evaluation = self._evaluate_stage(stage, mode, context)
            effective_outcome = evaluation.outcome if mode is FeatureMode.ENFORCE else DecisionOutcome.ALLOW
            decision = GateDecision(
                decision_id=f"decision-{self._id_factory()}",
                task_id=context.task_id,
                action_id=context.action.action_id,
                feature=stage.feature,
                stage=stage.stage,
                mode=mode,
                proposed_outcome=evaluation.outcome,
                effective_outcome=effective_outcome,
                reason_code=evaluation.reason_code,
                explanation=evaluation.explanation,
                evidence_ids=evaluation.evidence_ids,
                payload=evaluation.payload,
            )
            decisions.append(decision)
            trace_result = self.trace_recorder.record(
                task_id=context.task_id,
                run_id=context.run_id,
                turn=context.turn,
                event_type=_STAGE_TRACE_TYPES.get(stage.stage, TraceEventType.STAGE_DECISION),
                actor=_STAGE_ACTORS.get(stage.stage, TraceActor.RUNTIME),
                correlation_id=context.action.action_id,
                state_version=context.state_version,
                parent_event_id=parent_event_id,
                payload={"decision": decision.model_dump(mode="json")},
                critical=critical,
            )
            if trace_result.event is not None:
                parent_event_id = trace_result.event.event_id
                trace_event_ids.append(parent_event_id)
            if effective_outcome is not DecisionOutcome.ALLOW:
                outcome = effective_outcome
                stopped_at = stage.stage
                break

        return CoordinatorResult(
            task_id=context.task_id,
            run_id=context.run_id,
            action_id=context.action.action_id,
            outcome=outcome,
            decisions=tuple(decisions),
            stopped_at=stopped_at,
            trace_event_ids=tuple(trace_event_ids),
        )

    @staticmethod
    def _evaluate_stage(
        stage: CoordinatorStage,
        mode: FeatureMode,
        context: ActionEvaluationContext,
    ) -> StageEvaluation:
        if mode is FeatureMode.OFF:
            return StageEvaluation(
                outcome=DecisionOutcome.ALLOW,
                reason_code="feature_disabled",
                explanation=f"The {stage.feature.value} feature is disabled for this run.",
                payload={"stage_invoked": False},
            )
        try:
            return stage.evaluate(context)
        except Exception as exc:
            return StageEvaluation(
                outcome=DecisionOutcome.DENY,
                reason_code="stage_exception",
                explanation=f"The {stage.stage.value} stage raised an internal error.",
                payload={"stage_invoked": True, "error_type": type(exc).__name__},
            )


__all__ = ["AgentGateCoordinator", "CoordinatorStage", "PassThroughStage"]
