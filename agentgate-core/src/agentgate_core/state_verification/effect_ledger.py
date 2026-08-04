"""Effect registration and explicit subgoal linking."""

from __future__ import annotations

from agentgate_core.contracts.effect import EffectRecord
from agentgate_core.contracts.state_verification import SubgoalTransitionRequest
from agentgate_core.contracts.trace import TraceActor, TraceEventType
from agentgate_core.state_verification.task_reducer import TaskReducer


class EffectLedger:
    def __init__(self, reducer: TaskReducer) -> None:
        self.reducer = reducer
        self.session = reducer.session

    def record(self, effect: EffectRecord, *, subgoal_id: str, turn: int = 0) -> EffectRecord:
        stored = self.session.state_store.append_effect(effect.task_id, effect)
        return self._link(stored, subgoal_id=subgoal_id, turn=turn)

    def link_existing(self, task_id: str, effect_id: str, *, subgoal_id: str, turn: int = 0) -> EffectRecord:
        """Link an effect already reserved by Tool Execution Guard without appending it twice."""

        stored = self.session.state_store.get_effect(task_id, effect_id)
        return self._link(stored, subgoal_id=subgoal_id, turn=turn)

    def _link(self, stored: EffectRecord, *, subgoal_id: str, turn: int) -> EffectRecord:
        effect = stored
        state = self.session.state_store.get_task_state(effect.task_id)
        current = next(item for item in state.subgoals if item.subgoal_id == subgoal_id)
        self.reducer.transition(
            SubgoalTransitionRequest(
                task_id=effect.task_id,
                subgoal_id=subgoal_id,
                target_status=current.status,
                reason="Effect was explicitly linked to the subgoal.",
                effect_ids=(effect.effect_id,),
                turn=turn,
            )
        )
        self.session.trace_recorder.record(
            task_id=effect.task_id,
            run_id=self.session.run_id,
            turn=turn,
            event_type=TraceEventType.EFFECT_LINKED,
            actor=TraceActor.RUNTIME,
            correlation_id=effect.effect_id,
            payload={"effect": stored.model_dump(mode="json"), "subgoal_id": subgoal_id},
            critical=True,
        )
        return stored


__all__ = ["EffectLedger"]
