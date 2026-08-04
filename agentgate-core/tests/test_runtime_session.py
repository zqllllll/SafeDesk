from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path

from agentgate_core.contracts import (
    ActionEvaluationContext,
    ActionIR,
    ActionKind,
    ActorKind,
    CompletionCondition,
    FeatureMode,
    RiskLevel,
    SubgoalDefinition,
    TaskContract,
    TaskPhase,
    TaskState,
)
from agentgate_core.runtime import (
    AgentGateCoordinator,
    AgentGateFeatureConfig,
    AgentGateRuntimeSession,
    SQLiteTypedStateStore,
    TaskStateEvent,
    TaskStateEventType,
)
from agentgate_core.tracing import SQLiteTraceSink, TraceRecorder, TraceReplay

NOW = datetime(2026, 7, 16, 11, 0, tzinfo=UTC)


def test_sqlite_runtime_session_runs_and_replays_the_phase_zero_pipeline(tmp_path: Path) -> None:
    state_database = tmp_path / "state.sqlite3"
    trace_database = tmp_path / "trace.sqlite3"
    identifiers = count(1)
    state_store = SQLiteTypedStateStore(
        state_database,
        clock=lambda: NOW,
        id_factory=lambda: str(next(identifiers)),
    )
    trace_sink = SQLiteTraceSink(trace_database)
    trace_recorder = TraceRecorder(
        trace_sink,
        clock=lambda: NOW,
        id_factory=lambda: str(next(identifiers)),
    )
    coordinator = AgentGateCoordinator.with_empty_pipeline(
        config=AgentGateFeatureConfig(tool_execution_guard=FeatureMode.ENFORCE),
        trace_recorder=trace_recorder,
        id_factory=lambda: str(next(identifiers)),
    )
    session = AgentGateRuntimeSession(
        run_id="run-1",
        state_store=state_store,
        coordinator=coordinator,
        trace_recorder=trace_recorder,
    )
    contract = TaskContract(
        task_id="task-1",
        original_instruction="Inspect the requested record.",
        normalized_goal="Inspect one record.",
        subgoals=(
            SubgoalDefinition(
                subgoal_id="inspect-record",
                description="Inspect the record.",
                completion_condition_ids=("record-observed",),
            ),
        ),
        completion_conditions=(
            CompletionCondition(condition_id="record-observed", description="The record was observed."),
        ),
        created_at=NOW,
    )

    state = session.create_task(contract)
    next_state = TaskState.model_validate(
        {
            **state.model_dump(mode="python"),
            "state_version": 2,
            "phase": TaskPhase.ACT,
            "updated_at": NOW + timedelta(seconds=1),
        }
    )
    state_event = TaskStateEvent(
        event_id="state-transition-1",
        task_id="task-1",
        event_type=TaskStateEventType.TRANSITION,
        name="enter_act",
        previous_state_version=1,
        next_state=next_state,
        reason="The task is ready for action.",
        occurred_at=NOW + timedelta(seconds=1),
    )
    session.apply_task_event("task-1", state_event, expected_version=1, turn=1)
    action = ActionIR(
        action_id="action-1",
        task_id="task-1",
        actor=ActorKind.LEAD_AGENT,
        kind=ActionKind.READ,
        tool_name="records__show",
        operation="show",
        arguments={"record_id": 1},
        risk_level=RiskLevel.LOW,
        tool_schema_version="sha256:schema",
        source_turn=1,
    )
    result = session.evaluate_action(
        ActionEvaluationContext(
            task_id="task-1",
            run_id="run-1",
            turn=1,
            action=action,
            state_version=2,
        )
    )
    state_store.close()
    trace_sink.close()

    with SQLiteTypedStateStore(state_database) as reopened_state, SQLiteTraceSink(trace_database) as reopened_trace:
        replay = TraceReplay.replay_sink(reopened_trace, "task-1", "run-1")
        assert reopened_state.get_task_state("task-1").state_version == 2
        assert replay.latest_state == next_state
        assert replay.event_count == 7
        assert result.outcome.value == "allow"
        assert len(result.decisions) == 4
