from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from pathlib import Path

import pytest

from agentgate_core.contracts import (
    ActionEvaluationContext,
    ActionIR,
    ActionKind,
    ActorKind,
    CompletionBlockerType,
    CompletionCondition,
    DecisionOutcome,
    EffectKind,
    EffectRecord,
    EffectStatus,
    EvidenceItem,
    EvidenceSourceType,
    EvidenceStatus,
    ExpectedEffect,
    FeatureMode,
    ObservedEffect,
    ResourceRef,
    RiskLevel,
    SubgoalDefinition,
    SubgoalStatus,
    SubgoalTransitionRequest,
    TaskContract,
    UnexpectedEffectStatus,
    VerificationObservation,
    VerificationResult,
    VerificationStatus,
    VerificationType,
    VerifierSpec,
)
from agentgate_core.runtime import (
    AgentGateCoordinator,
    AgentGateFeatureConfig,
    AgentGateRuntimeSession,
    InMemoryTypedStateStore,
    SQLiteTypedStateStore,
    StateInvariantError,
)
from agentgate_core.state_verification import (
    CompletionGate,
    CompletionGateStage,
    EffectLedger,
    EvidenceBoard,
    PostActionVerifier,
    ResponseGroundingGate,
    TaskReducer,
    VerifierRegistry,
    summarize_state_verification,
)
from agentgate_core.tracing import InMemoryTraceSink, TraceRecorder

NOW = datetime(2026, 7, 21, 9, 0, tzinfo=UTC)


def make_services(*, mode: FeatureMode = FeatureMode.ENFORCE):
    identifiers = count(1)
    store = InMemoryTypedStateStore(clock=lambda: NOW, id_factory=lambda: str(next(identifiers)))
    sink = InMemoryTraceSink()
    recorder = TraceRecorder(sink, clock=lambda: NOW, id_factory=lambda: str(next(identifiers)))
    gate = CompletionGate(
        run_id="run-1",
        state_store=store,
        trace_recorder=recorder,
        id_factory=lambda: str(next(identifiers)),
    )
    stage = CompletionGateStage(gate, mode=mode)
    coordinator = AgentGateCoordinator(
        stages=(stage,),
        config=AgentGateFeatureConfig(state_verification=mode),
        trace_recorder=recorder,
        id_factory=lambda: str(next(identifiers)),
    )
    session = AgentGateRuntimeSession(
        run_id="run-1",
        state_store=store,
        coordinator=coordinator,
        trace_recorder=recorder,
    )
    reducer = TaskReducer(session, clock=lambda: NOW, id_factory=lambda: str(next(identifiers)))
    return session, reducer, gate, sink


def make_contract() -> TaskContract:
    return TaskContract(
        task_id="task-1",
        original_instruction="Create the quarterly record and verify it.",
        normalized_goal="Create one verified quarterly record.",
        subgoals=(
            SubgoalDefinition(
                subgoal_id="create-record",
                description="Create the record.",
                completion_condition_ids=("record-exists",),
            ),
        ),
        completion_conditions=(
            CompletionCondition(
                condition_id="record-exists",
                description="The record exists with the expected name.",
                required_effect_ids=("effect-1",),
            ),
        ),
        created_at=NOW,
    )


def make_effect(*, status: EffectStatus = EffectStatus.APPLIED_UNVERIFIED) -> EffectRecord:
    return EffectRecord(
        effect_id="effect-1",
        task_id="task-1",
        action_id="action-1",
        idempotency_key="task-1:create:quarterly",
        kind=EffectKind.CREATE,
        operation="create",
        resource=ResourceRef(resource_type="record", resource_id="record-1"),
        expected_change={"name": "Quarterly"},
        actual_change={"tool_result": {"ok": True}},
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def enter_execution(session: AgentGateRuntimeSession, reducer: TaskReducer) -> None:
    session.create_task(make_contract())
    reducer.transition(
        SubgoalTransitionRequest(
            task_id="task-1",
            subgoal_id="create-record",
            target_status=SubgoalStatus.READY,
            reason="No dependencies remain.",
        )
    )
    reducer.transition(
        SubgoalTransitionRequest(
            task_id="task-1",
            subgoal_id="create-record",
            target_status=SubgoalStatus.IN_PROGRESS,
            reason="Execution started.",
        )
    )


def completion_action() -> ActionEvaluationContext:
    return ActionEvaluationContext(
        task_id="task-1",
        run_id="run-1",
        turn=4,
        action=ActionIR(
            action_id="complete-1",
            task_id="task-1",
            actor=ActorKind.LEAD_AGENT,
            kind=ActionKind.WRITE,
            tool_name="supervisor__complete_task",
            operation="complete_task",
            arguments={},
            expected_effects=(
                ExpectedEffect(
                    effect_key="task-completion",
                    kind=EffectKind.SESSION,
                    resource=ResourceRef(resource_type="task", resource_id="task-1"),
                    expected_change={"phase": "complete"},
                ),
            ),
            idempotency_key="task-1:complete",
            risk_level=RiskLevel.LOW,
            tool_schema_version="schema-1",
            source_turn=4,
        ),
    )


class StaticVerifier:
    def __init__(self, observed: dict[str, object]) -> None:
        self.observed = observed

    def observe(self, effect, spec, attempt):
        return VerificationObservation(
            task_id=effect.task_id,
            effect_id=effect.effect_id,
            source_event_id=f"readback-{attempt}",
            observed_state=self.observed,
            observed_at=NOW,
        )


def configure_verifier(reducer: TaskReducer, observed: dict[str, object]) -> PostActionVerifier:
    registry = VerifierRegistry()
    registry.register(
        VerifierSpec(
            verifier_name="record-readback",
            verifier_version="1",
            resource_types=("record",),
            expected_fields=("name",),
        ),
        StaticVerifier(observed),
    )
    return PostActionVerifier(reducer, registry, id_factory=iter(str(i) for i in range(100, 200)).__next__)


def test_reducer_rejects_skipping_directly_to_verified_completion() -> None:
    session, reducer, _, _ = make_services()
    session.create_task(make_contract())

    with pytest.raises(StateInvariantError, match="illegal subgoal transition"):
        reducer.transition(
            SubgoalTransitionRequest(
                task_id="task-1",
                subgoal_id="create-record",
                target_status=SubgoalStatus.COMPLETED_VERIFIED,
                reason="The model claimed completion.",
            )
        )


def test_evidence_board_conflicts_same_fact_with_different_values() -> None:
    session, reducer, _, _ = make_services()
    session.create_task(make_contract())
    board = EvidenceBoard(reducer)
    for evidence_id, value in (("evidence-1", "open"), ("evidence-2", "closed")):
        board.record(
            EvidenceItem(
                evidence_id=evidence_id,
                task_id="task-1",
                subject="record-1",
                predicate="status",
                value=value,
                source_type=EvidenceSourceType.TOOL_RESULT,
                source_event_id=f"event-{evidence_id}",
                observed_at=NOW,
            )
        )

    assert {item.status for item in session.state_store.list_evidence("task-1")} == {EvidenceStatus.CONFLICTED}


@pytest.mark.parametrize(
    ("resolution_status", "should_block"),
    (
        (UnexpectedEffectStatus.OBSERVED, True),
        (UnexpectedEffectStatus.ROLLBACK_PENDING, True),
        (UnexpectedEffectStatus.UNRESOLVED, True),
        (UnexpectedEffectStatus.ROLLED_BACK, False),
        (UnexpectedEffectStatus.POLICY_ACCEPTED, False),
    ),
)
def test_completion_gate_blocks_only_active_unintended_effects(
    resolution_status: UnexpectedEffectStatus,
    should_block: bool,
) -> None:
    session, _, gate, _ = make_services()
    session.create_task(make_contract())
    session.state_store.append_verification(
        "task-1",
        VerificationResult(
            verification_id=f"verification-{resolution_status.value}",
            task_id="task-1",
            verification_type=VerificationType.ACTION,
            target_id="effect-1",
            action_id="action-1",
            verifier_name="mock",
            verifier_version="v1",
            expected_state={"record": "expected"},
            observed_state={"record": "unexpected"},
            status=VerificationStatus.MISMATCH,
            unintended_effects=(
                ObservedEffect(
                    kind=EffectKind.CREATE,
                    operation="create",
                    resource=ResourceRef(resource_type="record", resource_id="unexpected"),
                    resolution_status=resolution_status,
                ),
            ),
            checked_at=NOW,
        ),
    )

    decision = gate.evaluate("task-1", turn=1, mode=FeatureMode.ENFORCE)
    has_unintended_blocker = any(
        item.blocker_type is CompletionBlockerType.UNINTENDED_EFFECT for item in decision.blockers
    )
    assert has_unintended_blocker is should_block


def test_successful_readback_promotes_effect_subgoal_and_completion() -> None:
    session, reducer, gate, _ = make_services()
    enter_execution(session, reducer)
    EffectLedger(reducer).record(make_effect(), subgoal_id="create-record", turn=2)
    reducer.transition(
        SubgoalTransitionRequest(
            task_id="task-1",
            subgoal_id="create-record",
            target_status=SubgoalStatus.COMPLETED_UNVERIFIED,
            reason="The write returned success and now requires readback.",
            turn=2,
        )
    )

    result = configure_verifier(reducer, {"name": "Quarterly", "server_field": 7}).verify_effect(
        "task-1", "effect-1", turn=3
    )
    decision = gate.evaluate("task-1", turn=4, mode=FeatureMode.ENFORCE)

    assert result.status is VerificationStatus.VERIFIED
    assert session.state_store.get_effect("task-1", "effect-1").status is EffectStatus.VERIFIED
    assert session.state_store.get_task_state("task-1").subgoals[0].status is SubgoalStatus.COMPLETED_VERIFIED
    assert decision.proposed_allowed is True


def test_mismatched_readback_records_failure_and_blocks_completion() -> None:
    session, reducer, gate, _ = make_services()
    enter_execution(session, reducer)
    EffectLedger(reducer).record(make_effect(), subgoal_id="create-record")
    reducer.transition(
        SubgoalTransitionRequest(
            task_id="task-1",
            subgoal_id="create-record",
            target_status=SubgoalStatus.COMPLETED_UNVERIFIED,
            reason="Awaiting readback.",
        )
    )

    result = configure_verifier(reducer, {"name": "Wrong"}).verify_effect("task-1", "effect-1", turn=3)
    decision = gate.evaluate("task-1", turn=4, mode=FeatureMode.ENFORCE)

    assert result.status is VerificationStatus.MISMATCH
    assert session.state_store.get_effect("task-1", "effect-1").status is EffectStatus.FAILED
    assert len(session.state_store.list_failures("task-1")) == 1
    assert decision.proposed_allowed is False
    assert CompletionBlockerType.UNRESOLVED_FAILURE in {item.blocker_type for item in decision.blockers}


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(FeatureMode.SHADOW, DecisionOutcome.ALLOW), (FeatureMode.ENFORCE, DecisionOutcome.REQUIRE_EVIDENCE)],
)
def test_completion_stage_preserves_shadow_and_enforces_when_enabled(mode, expected) -> None:
    session, _, _, _ = make_services(mode=mode)
    session.create_task(make_contract())

    result = session.evaluate_action(completion_action())

    assert result.outcome is expected
    assert result.decisions[0].proposed_outcome is DecisionOutcome.REQUIRE_EVIDENCE


def test_response_grounding_downgrades_unsupported_success_claim() -> None:
    session, _, gate, _ = make_services()
    session.create_task(make_contract())
    completion = gate.evaluate("task-1", turn=1, mode=FeatureMode.ENFORCE)

    result = ResponseGroundingGate(session, id_factory=iter(("1", "2", "3")).__next__).evaluate(
        "task-1", "任务已经完成。", completion, turn=1
    )

    assert result.downgraded is True
    assert result.grounded_status.value == "failure"
    assert "尚未完成" in result.grounded_response


def test_tool_result_cannot_be_inserted_as_verified_evidence() -> None:
    session, reducer, _, _ = make_services()
    session.create_task(make_contract())
    with pytest.raises(StateInvariantError, match="only environment verification"):
        EvidenceBoard(reducer).record(
            EvidenceItem(
                evidence_id="evidence-1",
                task_id="task-1",
                subject="record-1",
                predicate="exists",
                value=True,
                source_type=EvidenceSourceType.TOOL_RESULT,
                source_event_id="tool-result-1",
                status=EvidenceStatus.VERIFIED,
                verification_ids=("verification-1",),
            )
        )


def test_trace_metrics_count_blocked_completion_and_grounding_downgrade() -> None:
    session, _, gate, sink = make_services()
    session.create_task(make_contract())
    completion = gate.evaluate("task-1", turn=1, mode=FeatureMode.SHADOW)
    ResponseGroundingGate(session, id_factory=iter(("1", "2", "3")).__next__).evaluate(
        "task-1", "Task is complete.", completion, turn=1
    )

    metrics = summarize_state_verification(sink.list_events("task-1", "run-1"))

    assert metrics.completion_blocked == 1
    assert metrics.false_completion_candidates == 1
    assert metrics.response_downgrades == 1


def test_state_verification_records_survive_sqlite_restart(tmp_path: Path) -> None:
    identifiers = count(1)
    database = tmp_path / "state.sqlite3"
    store = SQLiteTypedStateStore(database, clock=lambda: NOW, id_factory=lambda: str(next(identifiers)))
    sink = InMemoryTraceSink()
    recorder = TraceRecorder(sink, clock=lambda: NOW, id_factory=lambda: str(next(identifiers)))
    coordinator = AgentGateCoordinator.with_empty_pipeline(
        config=AgentGateFeatureConfig(),
        trace_recorder=recorder,
    )
    session = AgentGateRuntimeSession(
        run_id="run-1",
        state_store=store,
        coordinator=coordinator,
        trace_recorder=recorder,
    )
    reducer = TaskReducer(session, clock=lambda: NOW, id_factory=lambda: str(next(identifiers)))
    enter_execution(session, reducer)
    EffectLedger(reducer).record(make_effect(), subgoal_id="create-record")
    reducer.transition(
        SubgoalTransitionRequest(
            task_id="task-1",
            subgoal_id="create-record",
            target_status=SubgoalStatus.COMPLETED_UNVERIFIED,
            reason="Awaiting durable readback.",
        )
    )
    configure_verifier(reducer, {"name": "Quarterly"}).verify_effect("task-1", "effect-1", turn=3)
    store.close()

    with SQLiteTypedStateStore(database) as reopened:
        assert reopened.get_effect("task-1", "effect-1").status is EffectStatus.VERIFIED
        assert reopened.get_task_state("task-1").subgoals[0].status is SubgoalStatus.COMPLETED_VERIFIED
        assert len(reopened.list_verifications("task-1")) == 1
