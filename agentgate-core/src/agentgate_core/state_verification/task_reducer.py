"""Deterministic reducer for TaskState and subgoal lifecycle transitions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from agentgate_core.contracts.base import utc_now
from agentgate_core.contracts.effect import EffectStatus
from agentgate_core.contracts.evidence import EvidenceStatus
from agentgate_core.contracts.state_verification import SubgoalTransitionRequest
from agentgate_core.contracts.task import SubgoalState, SubgoalStatus, TaskPhase, TaskState
from agentgate_core.runtime.session import AgentGateRuntimeSession
from agentgate_core.runtime.state_store import StateInvariantError, TaskStateEvent, TaskStateEventType

_LEGAL_TRANSITIONS: dict[SubgoalStatus, frozenset[SubgoalStatus]] = {
    SubgoalStatus.PENDING: frozenset({SubgoalStatus.READY, SubgoalStatus.CANCELLED}),
    SubgoalStatus.READY: frozenset({SubgoalStatus.IN_PROGRESS, SubgoalStatus.BLOCKED, SubgoalStatus.CANCELLED}),
    SubgoalStatus.IN_PROGRESS: frozenset(
        {
            SubgoalStatus.WAITING_FOR_EVIDENCE,
            SubgoalStatus.WAITING_FOR_APPROVAL,
            SubgoalStatus.BLOCKED,
            SubgoalStatus.COMPLETED_UNVERIFIED,
            SubgoalStatus.FAILED,
            SubgoalStatus.CANCELLED,
        }
    ),
    SubgoalStatus.WAITING_FOR_EVIDENCE: frozenset(
        {SubgoalStatus.IN_PROGRESS, SubgoalStatus.COMPLETED_VERIFIED, SubgoalStatus.BLOCKED, SubgoalStatus.FAILED}
    ),
    SubgoalStatus.WAITING_FOR_APPROVAL: frozenset(
        {SubgoalStatus.IN_PROGRESS, SubgoalStatus.BLOCKED, SubgoalStatus.CANCELLED}
    ),
    SubgoalStatus.BLOCKED: frozenset(
        {SubgoalStatus.READY, SubgoalStatus.IN_PROGRESS, SubgoalStatus.FAILED, SubgoalStatus.CANCELLED}
    ),
    SubgoalStatus.COMPLETED_UNVERIFIED: frozenset(
        {SubgoalStatus.WAITING_FOR_EVIDENCE, SubgoalStatus.COMPLETED_VERIFIED, SubgoalStatus.IN_PROGRESS}
    ),
    SubgoalStatus.COMPLETED_VERIFIED: frozenset({SubgoalStatus.IN_PROGRESS}),
    SubgoalStatus.FAILED: frozenset({SubgoalStatus.READY, SubgoalStatus.CANCELLED}),
    SubgoalStatus.CANCELLED: frozenset({SubgoalStatus.READY}),
}


class TaskReducer:
    """Apply validated state transitions through the runtime's append-only event path."""

    def __init__(
        self,
        session: AgentGateRuntimeSession,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.session = session
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def transition(self, request: SubgoalTransitionRequest) -> TaskState:
        state = self.session.state_store.get_task_state(request.task_id)
        contract = self.session.state_store.get_task_contract(request.task_id)
        states = {item.subgoal_id: item for item in state.subgoals}
        definitions = {item.subgoal_id: item for item in contract.subgoals}
        if request.subgoal_id not in states:
            raise StateInvariantError(f"unknown subgoal: {request.subgoal_id}")

        current = states[request.subgoal_id]
        evidence_ids = _ordered_union(current.evidence_ids, request.evidence_ids)
        effect_ids = _ordered_union(current.effect_ids, request.effect_ids)
        blocker_ids = _ordered_union(current.blocker_ids, request.blocker_ids)
        changed_links = (evidence_ids, effect_ids, blocker_ids) != (
            current.evidence_ids,
            current.effect_ids,
            current.blocker_ids,
        )
        if request.target_status is current.status:
            if not changed_links:
                return state
        elif request.target_status not in _LEGAL_TRANSITIONS[current.status]:
            raise StateInvariantError(
                f"illegal subgoal transition: {current.status.value} -> {request.target_status.value}"
            )

        definition = definitions[request.subgoal_id]
        if request.target_status in {SubgoalStatus.READY, SubgoalStatus.IN_PROGRESS}:
            unmet = [
                dependency_id
                for dependency_id in definition.dependency_ids
                if states[dependency_id].status is not SubgoalStatus.COMPLETED_VERIFIED
            ]
            if unmet:
                raise StateInvariantError(f"subgoal dependencies are not verified: {sorted(unmet)}")
        if request.target_status is SubgoalStatus.COMPLETED_VERIFIED:
            self._require_verified_completion(
                request.task_id, definition.completion_condition_ids, evidence_ids, effect_ids
            )

        states[request.subgoal_id] = current.model_copy(
            update={
                "status": request.target_status,
                "evidence_ids": evidence_ids,
                "effect_ids": effect_ids,
                "blocker_ids": blocker_ids,
            }
        )
        ordered_states = tuple(states[item.subgoal_id] for item in state.subgoals)
        active_ids = tuple(
            item.subgoal_id
            for item in ordered_states
            if item.status
            in {
                SubgoalStatus.READY,
                SubgoalStatus.IN_PROGRESS,
                SubgoalStatus.WAITING_FOR_EVIDENCE,
                SubgoalStatus.WAITING_FOR_APPROVAL,
                SubgoalStatus.BLOCKED,
                SubgoalStatus.COMPLETED_UNVERIFIED,
            }
        )
        now = max(self._clock(), state.updated_at)
        next_state = state.model_copy(
            update={
                "state_version": state.state_version + 1,
                "phase": _derive_phase(ordered_states),
                "subgoals": ordered_states,
                "active_subgoal_ids": active_ids,
                "evidence_ids": _ordered_union(state.evidence_ids, request.evidence_ids),
                "effect_ids": _ordered_union(state.effect_ids, request.effect_ids),
                "verification_ids": _ordered_union(state.verification_ids, request.verification_ids),
                "failure_ids": _ordered_union(state.failure_ids, request.failure_ids),
                "updated_at": now,
            }
        )
        event = TaskStateEvent(
            event_id=f"state-event-{self._id_factory()}",
            task_id=request.task_id,
            event_type=TaskStateEventType.TRANSITION,
            name="subgoal_transition",
            previous_state_version=state.state_version,
            next_state=next_state,
            reason=request.reason,
            source_event_id=request.source_event_id,
            payload={
                "subgoal_id": request.subgoal_id,
                "from_status": current.status.value,
                "to_status": request.target_status.value,
                "linked_evidence_ids": list(request.evidence_ids),
                "linked_effect_ids": list(request.effect_ids),
                "linked_verification_ids": list(request.verification_ids),
                "linked_failure_ids": list(request.failure_ids),
            },
            occurred_at=now,
        )
        return self.session.apply_task_event(
            request.task_id,
            event,
            state.state_version,
            turn=request.turn,
            parent_event_id=request.source_event_id,
        )

    def set_complete(self, task_id: str, *, reason: str, turn: int, source_event_id: str | None = None) -> TaskState:
        state = self.session.state_store.get_task_state(task_id)
        contract = self.session.state_store.get_task_contract(task_id)
        required_ids = {item.subgoal_id for item in contract.subgoals if item.required}
        if any(
            item.subgoal_id in required_ids and item.status is not SubgoalStatus.COMPLETED_VERIFIED
            for item in state.subgoals
        ):
            raise StateInvariantError("task phase cannot become complete until every required subgoal is verified")
        now = max(self._clock(), state.updated_at)
        next_state = state.model_copy(
            update={
                "state_version": state.state_version + 1,
                "phase": TaskPhase.COMPLETE,
                "active_subgoal_ids": (),
                "updated_at": now,
            }
        )
        event = TaskStateEvent(
            event_id=f"state-event-{self._id_factory()}",
            task_id=task_id,
            event_type=TaskStateEventType.TRANSITION,
            name="task_completed",
            previous_state_version=state.state_version,
            next_state=next_state,
            reason=reason,
            source_event_id=source_event_id,
            occurred_at=now,
        )
        return self.session.apply_task_event(
            task_id,
            event,
            state.state_version,
            turn=turn,
            parent_event_id=source_event_id,
        )

    def _require_verified_completion(
        self,
        task_id: str,
        condition_ids: tuple[str, ...],
        evidence_ids: tuple[str, ...],
        effect_ids: tuple[str, ...],
    ) -> None:
        contract = self.session.state_store.get_task_contract(task_id)
        conditions = {item.condition_id: item for item in contract.completion_conditions}
        required_evidence = set(evidence_ids)
        required_effects = set(effect_ids)
        for condition_id in condition_ids:
            condition = conditions[condition_id]
            required_evidence.update(condition.required_evidence_ids)
            required_effects.update(condition.required_effect_ids)
        if not required_evidence and not required_effects:
            raise StateInvariantError("verified completion requires linked evidence or effects")
        bad_evidence = [
            evidence_id
            for evidence_id in required_evidence
            if self.session.state_store.get_evidence(task_id, evidence_id).status is not EvidenceStatus.VERIFIED
        ]
        bad_effects = [
            effect_id
            for effect_id in required_effects
            if self.session.state_store.get_effect(task_id, effect_id).status is not EffectStatus.VERIFIED
        ]
        if bad_evidence or bad_effects:
            raise StateInvariantError(
                "verified completion has unverified records: "
                f"evidence={sorted(bad_evidence)}, effects={sorted(bad_effects)}"
            )


def _ordered_union(existing: tuple[str, ...], added: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*existing, *added)))


def _derive_phase(subgoals: tuple[SubgoalState, ...]) -> TaskPhase:
    statuses = {item.status for item in subgoals}
    if SubgoalStatus.FAILED in statuses:
        return TaskPhase.FAILED
    if SubgoalStatus.BLOCKED in statuses:
        return TaskPhase.BLOCKED
    if statuses & {SubgoalStatus.WAITING_FOR_EVIDENCE, SubgoalStatus.COMPLETED_UNVERIFIED}:
        return TaskPhase.VERIFY
    if statuses & {SubgoalStatus.IN_PROGRESS, SubgoalStatus.WAITING_FOR_APPROVAL}:
        return TaskPhase.ACT
    return TaskPhase.COLLECT


__all__ = ["TaskReducer"]
