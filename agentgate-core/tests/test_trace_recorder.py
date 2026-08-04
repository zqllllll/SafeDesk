from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from pathlib import Path

import pytest

from agentgate_core.contracts import SubgoalState, TaskPhase, TaskState, TraceActor, TraceEventType
from agentgate_core.tracing import (
    InMemoryTraceSink,
    SQLiteTraceSink,
    TraceParentError,
    TracePersistenceError,
    TracePersistenceRequiredError,
    TraceRecorder,
    TraceReplay,
    TraceReplayError,
    TraceSequenceError,
)

NOW = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)


def make_recorder(sink: InMemoryTraceSink | SQLiteTraceSink) -> TraceRecorder:
    identifiers = count(1)
    return TraceRecorder(sink, clock=lambda: NOW, id_factory=lambda: str(next(identifiers)))


def test_recorder_appends_contiguous_redacted_parented_events() -> None:
    sink = InMemoryTraceSink()
    recorder = make_recorder(sink)

    first = recorder.record(
        task_id="task-1",
        run_id="run-1",
        turn=0,
        event_type=TraceEventType.ACTION_PROPOSED,
        actor=TraceActor.MODEL,
        correlation_id="action-1",
        payload={"arguments": {"access_token": "raw-token", "query": "safe"}},
        critical=True,
    ).event
    assert first is not None
    second = recorder.record(
        task_id="task-1",
        run_id="run-1",
        turn=0,
        event_type=TraceEventType.STAGE_DECISION,
        actor=TraceActor.RUNTIME,
        correlation_id="action-1",
        parent_event_id=first.event_id,
        payload={"outcome": "allow"},
        critical=True,
    ).event

    assert second is not None
    assert [event.sequence_number for event in sink.list_events("task-1", "run-1")] == [0, 1]
    assert first.payload["arguments"]["access_token"] == "[REDACTED]"
    assert first.redaction_metadata.redacted_fields == ("arguments.access_token",)
    assert second.parent_event_id == first.event_id


class _FailingSink:
    def append(self, event):
        raise TracePersistenceError("disk unavailable")

    def get_event(self, event_id):
        raise TracePersistenceError("disk unavailable")

    def list_events(self, task_id, run_id):
        raise TracePersistenceError("disk unavailable")

    def last_sequence(self, task_id, run_id):
        raise TracePersistenceError("disk unavailable")

    def close(self):
        return None


def test_trace_failure_is_fail_closed_for_writes_and_configurable_for_reads() -> None:
    strict = TraceRecorder(_FailingSink())
    with pytest.raises(TracePersistenceRequiredError):
        strict.record(
            task_id="task-1",
            run_id="run-1",
            turn=0,
            event_type=TraceEventType.ACTION_PROPOSED,
            actor=TraceActor.MODEL,
            correlation_id="write-1",
            critical=True,
        )

    degraded = TraceRecorder(_FailingSink(), allow_read_degradation=True).record(
        task_id="task-1",
        run_id="run-1",
        turn=0,
        event_type=TraceEventType.ACTION_PROPOSED,
        actor=TraceActor.MODEL,
        correlation_id="read-1",
        critical=False,
    )
    assert degraded.persisted is False
    assert degraded.degraded is True
    assert degraded.error_type == "TracePersistenceError"


def test_sqlite_trace_survives_reopen_and_replays_task_state(tmp_path: Path) -> None:
    database = tmp_path / "trace.sqlite3"
    state = TaskState(
        task_id="task-1",
        contract_version=1,
        state_version=1,
        phase=TaskPhase.COLLECT,
        subgoals=(SubgoalState(subgoal_id="goal-1"),),
        updated_at=NOW,
    )
    with SQLiteTraceSink(database) as sink:
        recorder = make_recorder(sink)
        recorder.record(
            task_id="task-1",
            run_id="run-1",
            turn=0,
            event_type=TraceEventType.TASK_CREATED,
            actor=TraceActor.RUNTIME,
            correlation_id="task-1",
            state_version=1,
            payload={"task_state": state.model_dump(mode="json")},
            critical=True,
        )

    with SQLiteTraceSink(database) as reopened:
        replay = TraceReplay.replay_sink(reopened, "task-1", "run-1")

    assert replay.event_count == 1
    assert replay.latest_state == state
    assert replay.last_state_version == 1


def test_replay_rejects_non_contiguous_or_cross_stream_input() -> None:
    sink = InMemoryTraceSink()
    recorder = make_recorder(sink)
    event = recorder.record(
        task_id="task-1",
        run_id="run-1",
        turn=0,
        event_type=TraceEventType.ACTION_PROPOSED,
        actor=TraceActor.MODEL,
        correlation_id="action-1",
    ).event
    assert event is not None
    broken = event.model_copy(update={"sequence_number": 1})

    with pytest.raises(TraceReplayError, match="not contiguous"):
        TraceReplay.replay((broken,))


def test_sink_rejects_sequence_gaps_and_cross_stream_parents() -> None:
    sink = InMemoryTraceSink()
    recorder = make_recorder(sink)
    first = recorder.record(
        task_id="task-1",
        run_id="run-1",
        turn=0,
        event_type=TraceEventType.ACTION_PROPOSED,
        actor=TraceActor.MODEL,
        correlation_id="action-1",
    ).event
    assert first is not None

    with pytest.raises(TraceSequenceError):
        sink.append(
            first.model_copy(
                update={
                    "event_id": "trace-event-gap",
                    "sequence_number": 2,
                }
            )
        )

    with pytest.raises(TraceParentError):
        sink.append(
            first.model_copy(
                update={
                    "event_id": "trace-event-cross-stream",
                    "task_id": "task-2",
                    "sequence_number": 0,
                    "parent_event_id": first.event_id,
                }
            )
        )
