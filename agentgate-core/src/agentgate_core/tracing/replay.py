"""Deterministic structural replay for AgentGate trace streams."""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, ConfigDict, Field

from agentgate_core.contracts.task import TaskState
from agentgate_core.contracts.trace import TraceEvent, TraceEventType
from agentgate_core.tracing.sink import TraceSink


class TraceReplayError(RuntimeError):
    """Raised when a trace stream violates replay invariants."""


class TraceReplayResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    run_id: str
    event_count: int = Field(ge=0)
    event_type_counts: dict[str, int]
    latest_state: TaskState | None = None
    last_state_version: int | None = Field(default=None, ge=1)
    unmatched_tool_start_correlation_ids: tuple[str, ...] = ()
    unmatched_tool_finish_correlation_ids: tuple[str, ...] = ()
    final_event_id: str | None = None


class TraceReplay:
    @staticmethod
    def replay(events: tuple[TraceEvent, ...]) -> TraceReplayResult:
        if not events:
            raise TraceReplayError("cannot replay an empty trace stream without task and run identifiers")
        task_id = events[0].task_id
        run_id = events[0].run_id
        seen_ids: set[str] = set()
        last_state_version: int | None = None
        latest_state: TaskState | None = None
        tool_balance: Counter[str] = Counter()
        unmatched_finishes: list[str] = []
        event_counts: Counter[str] = Counter()

        for expected_sequence, event in enumerate(events):
            if (event.task_id, event.run_id) != (task_id, run_id):
                raise TraceReplayError("trace replay input contains more than one task/run stream")
            if event.sequence_number != expected_sequence:
                raise TraceReplayError(
                    f"trace sequence is not contiguous: expected {expected_sequence}, got {event.sequence_number}"
                )
            if event.event_id in seen_ids:
                raise TraceReplayError(f"duplicate trace event ID: {event.event_id}")
            if event.parent_event_id is not None and event.parent_event_id not in seen_ids:
                raise TraceReplayError(f"parent event was not observed before child: {event.parent_event_id}")
            seen_ids.add(event.event_id)
            event_counts[event.event_type.value] += 1

            if event.state_version is not None:
                if last_state_version is not None and event.state_version < last_state_version:
                    raise TraceReplayError("trace state_version moved backwards")
                last_state_version = event.state_version

            if event.event_type in {TraceEventType.TASK_CREATED, TraceEventType.TASK_STATE_CHANGED}:
                raw_state = event.payload.get("task_state")
                if raw_state is not None:
                    latest_state = TaskState.model_validate(raw_state)
                    if latest_state.task_id != task_id:
                        raise TraceReplayError("task_state payload belongs to another task")
                    if event.state_version != latest_state.state_version:
                        raise TraceReplayError("trace state_version does not match task_state payload")

            if event.event_type is TraceEventType.TOOL_STARTED:
                tool_balance[event.correlation_id] += 1
            elif event.event_type is TraceEventType.TOOL_FINISHED:
                if tool_balance[event.correlation_id] <= 0:
                    unmatched_finishes.append(event.correlation_id)
                else:
                    tool_balance[event.correlation_id] -= 1

        unmatched_starts = tuple(
            correlation_id for correlation_id, count in sorted(tool_balance.items()) for _ in range(max(count, 0))
        )
        return TraceReplayResult(
            task_id=task_id,
            run_id=run_id,
            event_count=len(events),
            event_type_counts=dict(sorted(event_counts.items())),
            latest_state=latest_state,
            last_state_version=last_state_version,
            unmatched_tool_start_correlation_ids=unmatched_starts,
            unmatched_tool_finish_correlation_ids=tuple(unmatched_finishes),
            final_event_id=events[-1].event_id,
        )

    @staticmethod
    def replay_sink(sink: TraceSink, task_id: str, run_id: str) -> TraceReplayResult:
        return TraceReplay.replay(sink.list_events(task_id, run_id))


__all__ = ["TraceReplay", "TraceReplayError", "TraceReplayResult"]
