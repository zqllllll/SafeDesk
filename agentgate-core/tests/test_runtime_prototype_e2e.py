"""Deterministic end-to-end prototype validation for all SafeDesk modules."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count

import pytest

from agentgate_core.contracts import (
    ActionKind,
    ActorKind,
    CompletionCondition,
    EffectKind,
    FailureSignal,
    FeatureMode,
    ProgressSignal,
    RawToolCall,
    RiskLevel,
    SubgoalDefinition,
    SubgoalStatus,
    SubgoalTransitionRequest,
    TaskContract,
    ToolCatalogEntry,
    ToolCatalogSnapshot,
    ToolExecutionReport,
    VerificationObservation,
    VerifierSpec,
)
from agentgate_core.runtime import AgentGateFeatureConfig, SQLiteTypedStateStore, assemble_agentgate
from agentgate_core.state_verification import VerifierRegistry
from agentgate_core.tool_execution_guard import InMemoryToolCatalog
from agentgate_core.tracing import SQLiteTraceSink, TraceRecorder

NOW = datetime(2026, 7, 27, tzinfo=UTC)


class _StaticReadback:
    def observe(self, effect, spec, attempt):
        return VerificationObservation(
            task_id=effect.task_id,
            effect_id=effect.effect_id,
            source_event_id=f"readback-{effect.effect_id}-{attempt}",
            observed_state={"arguments": {"record_id": effect.resource.resource_id, "name": "Quarterly"}},
            observed_at=effect.updated_at,
        )


def _catalog() -> InMemoryToolCatalog:
    entry = ToolCatalogEntry(
        name="records__create",
        description="Create one record.",
        operation="create_record",
        input_schema={
            "type": "object",
            "properties": {"record_id": {"type": "string"}, "name": {"type": "string"}},
            "required": ["record_id", "name"],
            "additionalProperties": False,
        },
        action_kind=ActionKind.WRITE,
        risk_level=RiskLevel.MEDIUM,
        side_effect_type=EffectKind.CREATE,
        resource_types=("record",),
        verification_strategy="mock.readback",
        idempotency_strategy="canonical_arguments",
    )
    return InMemoryToolCatalog(
        ToolCatalogSnapshot(catalog_version="mock-catalog-v1", entries=(entry,), created_at=NOW)
    )


def _contract(task_id: str, effect_id: str) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        original_instruction="Create one Quarterly record and verify it.",
        normalized_goal="Create one verified Quarterly record.",
        subgoals=(
            SubgoalDefinition(
                subgoal_id="create-record",
                description="Create the record.",
                completion_condition_ids=("record-verified",),
            ),
        ),
        completion_conditions=(
            CompletionCondition(
                condition_id="record-verified",
                description="The created record is verified by environment readback.",
                required_effect_ids=(effect_id,),
            ),
        ),
        created_at=NOW,
    )


@pytest.mark.parametrize("index", range(20))
def test_four_modules_complete_a_deterministic_mock_task(index: int, tmp_path) -> None:
    """Exercise guard, state/verification, recovery signals, and bounded context together."""

    ids = count()
    task_id = f"prototype-{index}"
    state_path = tmp_path / f"{task_id}.state.sqlite3"
    trace_path = tmp_path / f"{task_id}.trace.sqlite3"
    store = SQLiteTypedStateStore(state_path, clock=lambda: NOW, id_factory=lambda: f"state-{next(ids)}")
    sink = SQLiteTraceSink(trace_path)
    recorder = TraceRecorder(sink, clock=lambda: NOW, id_factory=lambda: f"trace-{next(ids)}")
    registry = VerifierRegistry()
    assembly = assemble_agentgate(
        run_id=f"run-{index}",
        state_store=store,
        trace_recorder=recorder,
        tool_catalog=_catalog(),
        config=AgentGateFeatureConfig(
            state_verification=FeatureMode.ENFORCE,
            tool_execution_guard=FeatureMode.ENFORCE,
            recovery_controller=FeatureMode.ENFORCE,
            context_manager=FeatureMode.ENFORCE,
        ),
        verifier_registry=registry,
        effect_id_factory=lambda: "prototype-effect",
    )
    session = assembly.session
    orchestrator = assembly.orchestrator
    session.create_task(_contract(task_id, "effect-prototype-effect"))
    assembly.active_tool_sets.initialize(task_id, ("records__create",), reason="prototype fixture")
    orchestrator.reducer.transition(
        SubgoalTransitionRequest(
            task_id=task_id,
            subgoal_id="create-record",
            target_status=SubgoalStatus.READY,
            reason="The contract has no dependencies.",
            turn=1,
        )
    )
    orchestrator.reducer.transition(
        SubgoalTransitionRequest(
            task_id=task_id,
            subgoal_id="create-record",
            target_status=SubgoalStatus.IN_PROGRESS,
            reason="The write action is ready to execute.",
            turn=1,
        )
    )
    context = orchestrator.before_model(task_id, turn=1, soft_token_limit=4_000, hard_token_limit=8_000)

    invalid = orchestrator.guard_tool_batch(
        (
            RawToolCall(
                tool_call_id=f"invalid-{index}",
                task_id=task_id,
                tool_name="records__missing",
                arguments={},
                actor=ActorKind.LEAD_AGENT,
                source_turn=1,
            ),
        ),
        context_pack=context,
        subgoal_by_call_id={},
    )
    assert invalid.decisions[0].should_execute is False
    assert invalid.decisions[0].tool_result_status is not None
    recovery_plan = orchestrator.recover(
        FailureSignal(task_id=task_id, tool_error_code="invalid_call", tool_error_message="Unknown tool."),
        turn=1,
    )
    assert hasattr(recovery_plan, "plan_id")
    recovery_result = orchestrator.finish_recovery(
        recovery_plan,
        ProgressSignal(task_id=task_id, turn=1),
        ProgressSignal(task_id=task_id, turn=2, new_verified_evidence=1),
        turn=2,
        verification_id="recovery-verification",
    )
    assert recovery_result.success is True

    call_id = f"create-{index}"
    batch = orchestrator.guard_tool_batch(
        (
            RawToolCall(
                tool_call_id=call_id,
                task_id=task_id,
                tool_name="records__create",
                arguments={"record_id": task_id, "name": "Quarterly"},
                actor=ActorKind.LEAD_AGENT,
                source_turn=2,
            ),
        ),
        context_pack=context,
        subgoal_by_call_id={call_id: "create-record"},
    )
    decision = batch.decisions[0]
    assert decision.should_execute is True
    assert decision.effect_ids
    orchestrator.mark_tool_started(decision, turn=2)
    orchestrator.after_tool(
        decision,
        ToolExecutionReport(
            task_id=task_id,
            action_id=call_id,
            executed=True,
            success=True,
            result={"created": task_id},
        ),
        turn=2,
        subgoal_id="create-record",
    )
    orchestrator.reducer.transition(
        SubgoalTransitionRequest(
            task_id=task_id,
            subgoal_id="create-record",
            target_status=SubgoalStatus.COMPLETED_UNVERIFIED,
            reason="The write completed and waits for readback.",
            turn=2,
        )
    )

    blocked, _ = orchestrator.request_completion(task_id, "Completed.", turn=2)
    assert blocked.effective_allowed is False
    registry.register(
        VerifierSpec(
            verifier_name="mock-readback",
            verifier_version="v1",
            resource_types=("record",),
            expected_fields=("arguments",),
        ),
        _StaticReadback(),
    )
    verification = orchestrator.verifier.verify_effect(task_id, decision.effect_ids[0], turn=3)
    assert verification.status.value == "verified"

    refreshed = orchestrator.before_model(task_id, turn=3, soft_token_limit=4_000, hard_token_limit=8_000)
    duplicate = orchestrator.guard_tool_batch(
        (
            RawToolCall(
                tool_call_id=f"duplicate-{index}",
                task_id=task_id,
                tool_name="records__create",
                arguments={"record_id": task_id, "name": "Quarterly"},
                actor=ActorKind.LEAD_AGENT,
                source_turn=3,
            ),
        ),
        context_pack=refreshed,
        subgoal_by_call_id={f"duplicate-{index}": "create-record"},
    )
    assert duplicate.decisions[0].should_execute is False
    assert duplicate.decisions[0].outcome.value == "already_applied"

    allowed, _ = orchestrator.request_completion(task_id, "Completed with verification.", turn=4)
    assert allowed.effective_allowed is True, allowed.blockers
    assert store.get_task_state(task_id).phase.value == "complete"
    event_count = len(sink.list_events(task_id, f"run-{index}"))
    assert event_count > 10
    store.close()
    sink.close()

    with SQLiteTypedStateStore(state_path, clock=lambda: NOW) as reloaded_store:
        assert reloaded_store.get_task_state(task_id).phase.value == "complete"
        assert len(reloaded_store.list_effects(task_id)) == 1
    with SQLiteTraceSink(trace_path) as reloaded_sink:
        assert len(reloaded_sink.list_events(task_id, f"run-{index}")) == event_count
