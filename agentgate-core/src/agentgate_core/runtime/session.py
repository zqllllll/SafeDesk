"""Minimal runtime session linking state lifecycle, coordination, and trace."""

from __future__ import annotations

from agentgate_core.contracts.decision import ActionEvaluationContext, CoordinatorResult
from agentgate_core.contracts.task import TaskContract, TaskState
from agentgate_core.contracts.trace import TraceActor, TraceEventType
from agentgate_core.runtime.coordinator import AgentGateCoordinator
from agentgate_core.runtime.state_store import StateCheckpoint, TaskStateEvent, TypedStateStore
from agentgate_core.tracing.recorder import TraceRecorder


class AgentGateRuntimeSession:
    """Bind one run to the foundation services without owning module policy."""

    def __init__(
        self,
        *,
        run_id: str,
        state_store: TypedStateStore,
        coordinator: AgentGateCoordinator,
        trace_recorder: TraceRecorder,
    ) -> None:
        if coordinator.trace_recorder is not trace_recorder:
            raise ValueError("runtime session and coordinator must share one TraceRecorder")
        self.run_id = run_id
        self.state_store = state_store
        self.coordinator = coordinator
        self.trace_recorder = trace_recorder

    def create_task(
        self,
        contract: TaskContract,
        initial_state: TaskState | None = None,
        *,
        turn: int = 0,
    ) -> TaskState:
        state = self.state_store.create_task(contract, initial_state)
        state_event = self.state_store.list_task_events(contract.task_id)[-1]
        self.trace_recorder.record(
            task_id=contract.task_id,
            run_id=self.run_id,
            turn=turn,
            event_type=TraceEventType.TASK_CREATED,
            actor=TraceActor.RUNTIME,
            correlation_id=contract.task_id,
            state_version=state.state_version,
            payload={
                "task_contract": contract.model_dump(mode="json"),
                "task_state": state.model_dump(mode="json"),
                "state_event": state_event.model_dump(mode="json"),
            },
            critical=True,
        )
        return state

    def apply_task_event(
        self,
        task_id: str,
        event: TaskStateEvent,
        expected_version: int,
        *,
        turn: int,
        parent_event_id: str | None = None,
    ) -> TaskState:
        state = self.state_store.apply_task_event(task_id, event, expected_version)
        self.trace_recorder.record(
            task_id=task_id,
            run_id=self.run_id,
            turn=turn,
            event_type=TraceEventType.TASK_STATE_CHANGED,
            actor=TraceActor.RUNTIME,
            correlation_id=event.event_id,
            parent_event_id=parent_event_id,
            state_version=state.state_version,
            payload={
                "task_state": state.model_dump(mode="json"),
                "state_event": event.model_dump(mode="json"),
            },
            critical=True,
        )
        return state

    def create_checkpoint(
        self,
        task_id: str,
        checkpoint_id: str | None = None,
        *,
        turn: int,
    ) -> StateCheckpoint:
        checkpoint = self.state_store.create_checkpoint(task_id, checkpoint_id)
        self.trace_recorder.record(
            task_id=task_id,
            run_id=self.run_id,
            turn=turn,
            event_type=TraceEventType.CHECKPOINT_CREATED,
            actor=TraceActor.RUNTIME,
            correlation_id=checkpoint.checkpoint_id,
            state_version=checkpoint.source_state_version,
            payload={"checkpoint": checkpoint.model_dump(mode="json")},
            critical=True,
        )
        return checkpoint

    def restore_checkpoint(
        self,
        task_id: str,
        checkpoint_id: str,
        *,
        turn: int,
        expected_version: int | None = None,
    ) -> TaskState:
        state = self.state_store.restore_checkpoint(
            task_id,
            checkpoint_id,
            expected_version=expected_version,
        )
        state_event = self.state_store.list_task_events(task_id)[-1]
        self.trace_recorder.record(
            task_id=task_id,
            run_id=self.run_id,
            turn=turn,
            event_type=TraceEventType.CHECKPOINT_RESTORED,
            actor=TraceActor.RUNTIME,
            correlation_id=checkpoint_id,
            state_version=state.state_version,
            payload={
                "checkpoint_id": checkpoint_id,
                "task_state": state.model_dump(mode="json"),
                "state_event": state_event.model_dump(mode="json"),
            },
            critical=True,
        )
        return state

    def evaluate_action(self, context: ActionEvaluationContext) -> CoordinatorResult:
        if context.run_id != self.run_id:
            raise ValueError("action context run_id does not match the runtime session")
        return self.coordinator.evaluate_action(context)


__all__ = ["AgentGateRuntimeSession"]
