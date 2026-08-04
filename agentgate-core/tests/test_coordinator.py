from __future__ import annotations

from datetime import UTC, datetime
from itertools import count

import pytest

from agentgate_core.contracts import (
    ActionEvaluationContext,
    ActionIR,
    ActionKind,
    ActorKind,
    DecisionOutcome,
    EffectKind,
    ExpectedEffect,
    FeatureMode,
    FeatureName,
    PipelineStage,
    ResourceRef,
    RiskLevel,
    StageEvaluation,
)
from agentgate_core.runtime import AgentGateCoordinator, AgentGateFeatureConfig
from agentgate_core.tracing import InMemoryTraceSink, TracePersistenceRequiredError, TraceRecorder, TraceReplay

NOW = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)


def make_action(*, write: bool = False) -> ActionIR:
    resource = ResourceRef(resource_type="record", resource_id="1")
    return ActionIR(
        action_id="action-1",
        task_id="task-1",
        actor=ActorKind.LEAD_AGENT,
        kind=ActionKind.WRITE if write else ActionKind.READ,
        tool_name="records__update" if write else "records__show",
        operation="update" if write else "show",
        resource=resource,
        arguments={"record_id": 1},
        expected_effects=(
            ExpectedEffect(
                effect_key="primary",
                kind=EffectKind.UPDATE,
                resource=resource,
                expected_change={"name": "updated"},
            ),
        )
        if write
        else (),
        idempotency_key="sha256:write" if write else None,
        risk_level=RiskLevel.MEDIUM if write else RiskLevel.LOW,
        tool_schema_version="sha256:schema",
        source_turn=1,
    )


def make_context(*, write: bool = False) -> ActionEvaluationContext:
    return ActionEvaluationContext(task_id="task-1", run_id="run-1", turn=1, action=make_action(write=write))


class _DenyStage:
    stage = PipelineStage.SCHEMA_GUARD
    feature = FeatureName.TOOL_EXECUTION_GUARD

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, context: ActionEvaluationContext) -> StageEvaluation:
        self.calls += 1
        return StageEvaluation(
            outcome=DecisionOutcome.DENY,
            reason_code="synthetic_deny",
            explanation="Synthetic denial for coordinator semantics testing.",
        )


class _BrokenStage(_DenyStage):
    def evaluate(self, context: ActionEvaluationContext) -> StageEvaluation:
        self.calls += 1
        raise RuntimeError("synthetic internal detail")


def make_recorder() -> tuple[TraceRecorder, InMemoryTraceSink]:
    identifiers = count(1)
    sink = InMemoryTraceSink()
    recorder = TraceRecorder(sink, clock=lambda: NOW, id_factory=lambda: str(next(identifiers)))
    return recorder, sink


@pytest.mark.parametrize(
    ("mode", "expected_outcome", "expected_calls"),
    [
        (FeatureMode.OFF, DecisionOutcome.ALLOW, 0),
        (FeatureMode.SHADOW, DecisionOutcome.ALLOW, 1),
        (FeatureMode.ENFORCE, DecisionOutcome.DENY, 1),
    ],
)
def test_feature_mode_controls_stage_invocation_and_effect(mode, expected_outcome, expected_calls) -> None:
    stage = _DenyStage()
    recorder, sink = make_recorder()
    coordinator = AgentGateCoordinator(
        stages=(stage,),
        config=AgentGateFeatureConfig(tool_execution_guard=mode),
        trace_recorder=recorder,
        id_factory=lambda: "decision-1",
    )

    result = coordinator.evaluate_action(make_context())

    assert result.outcome is expected_outcome
    assert stage.calls == expected_calls
    assert result.decisions[0].proposed_outcome is (
        DecisionOutcome.ALLOW if mode is FeatureMode.OFF else DecisionOutcome.DENY
    )
    assert result.decisions[0].effective_outcome is expected_outcome
    assert len(sink.list_events("task-1", "run-1")) == 2


def test_fake_action_traverses_every_empty_stage_and_is_replayable() -> None:
    recorder, sink = make_recorder()
    coordinator = AgentGateCoordinator.with_empty_pipeline(
        config=AgentGateFeatureConfig(tool_execution_guard=FeatureMode.ENFORCE),
        trace_recorder=recorder,
        id_factory=iter(("1", "2", "3", "4")).__next__,
    )

    result = coordinator.evaluate_action(make_context(write=True))
    replay = TraceReplay.replay_sink(sink, "task-1", "run-1")

    assert result.outcome is DecisionOutcome.ALLOW
    assert [decision.stage for decision in result.decisions] == [
        PipelineStage.SCHEMA_GUARD,
        PipelineStage.DEPENDENCY_SCHEDULER,
        PipelineStage.POLICY_GATE,
        PipelineStage.EFFECT_PREFLIGHT,
    ]
    assert replay.event_count == 5
    assert len(result.trace_event_ids) == 5
    first_event = sink.list_events("task-1", "run-1")[0]
    assert first_event.payload["feature_config_hash"] == coordinator.config.configuration_hash


class _FailingTraceSink:
    def append(self, event):
        raise NotImplementedError

    def get_event(self, event_id):
        return None

    def list_events(self, task_id, run_id):
        return ()

    def last_sequence(self, task_id, run_id):
        from agentgate_core.tracing import TracePersistenceError

        raise TracePersistenceError("unavailable")

    def close(self):
        return None


def test_write_action_cannot_enter_pipeline_without_trace_persistence() -> None:
    coordinator = AgentGateCoordinator.with_empty_pipeline(
        config=AgentGateFeatureConfig(
            tool_execution_guard=FeatureMode.ENFORCE,
            allow_read_on_trace_failure=True,
        ),
        trace_recorder=TraceRecorder(_FailingTraceSink(), allow_read_degradation=True),
    )

    with pytest.raises(TracePersistenceRequiredError):
        coordinator.evaluate_action(make_context(write=True))


def test_stage_exception_is_structured_and_does_not_leak_error_text() -> None:
    recorder, sink = make_recorder()
    coordinator = AgentGateCoordinator(
        stages=(_BrokenStage(),),
        config=AgentGateFeatureConfig(tool_execution_guard=FeatureMode.ENFORCE),
        trace_recorder=recorder,
    )

    result = coordinator.evaluate_action(make_context())

    assert result.outcome is DecisionOutcome.DENY
    assert result.decisions[0].reason_code == "stage_exception"
    assert result.decisions[0].payload == {"stage_invoked": True, "error_type": "RuntimeError"}
    assert "synthetic internal detail" not in sink.list_events("task-1", "run-1")[-1].model_dump_json()
