"""Provenance-preserving evidence board with explicit conflict handling."""

from __future__ import annotations

from agentgate_core.contracts.evidence import EvidenceItem, EvidenceSourceType, EvidenceStatus
from agentgate_core.contracts.state_verification import SubgoalTransitionRequest
from agentgate_core.contracts.trace import TraceActor, TraceEventType
from agentgate_core.runtime.state_store import StateInvariantError
from agentgate_core.state_verification.task_reducer import TaskReducer

_ACTIVE_STATUSES = {EvidenceStatus.OBSERVED, EvidenceStatus.INFERRED, EvidenceStatus.VERIFIED}


class EvidenceBoard:
    def __init__(self, reducer: TaskReducer) -> None:
        self.reducer = reducer
        self.session = reducer.session

    def record(self, evidence: EvidenceItem, *, subgoal_id: str | None = None, turn: int = 0) -> EvidenceItem:
        if (
            evidence.status is EvidenceStatus.VERIFIED
            and evidence.source_type is not EvidenceSourceType.ENVIRONMENT_VERIFICATION
        ):
            raise StateInvariantError("only environment verification may create VERIFIED evidence")
        conflicts = tuple(
            item
            for item in self.session.state_store.list_evidence(evidence.task_id)
            if item.status in _ACTIVE_STATUSES
            and (item.subject, item.predicate, item.scope) == (evidence.subject, evidence.predicate, evidence.scope)
            and item.value != evidence.value
        )
        stored = evidence
        if conflicts:
            stored = evidence.model_copy(update={"status": EvidenceStatus.CONFLICTED})
        stored = self.session.state_store.append_evidence(evidence.task_id, stored)
        for conflict in conflicts:
            conflicted = conflict.model_copy(update={"status": EvidenceStatus.CONFLICTED})
            self.session.state_store.update_evidence(
                evidence.task_id,
                conflicted,
                expected_status=conflict.status,
            )
        event_type = TraceEventType.EVIDENCE_CONFLICTED if conflicts else TraceEventType.EVIDENCE_RECORDED
        self.session.trace_recorder.record(
            task_id=evidence.task_id,
            run_id=self.session.run_id,
            turn=turn,
            event_type=event_type,
            actor=TraceActor.RUNTIME,
            correlation_id=evidence.evidence_id,
            payload={
                "evidence": stored.model_dump(mode="json"),
                "conflicting_evidence_ids": [item.evidence_id for item in conflicts],
            },
            critical=False,
        )
        if subgoal_id is not None:
            state = self.session.state_store.get_task_state(evidence.task_id)
            current = next(item for item in state.subgoals if item.subgoal_id == subgoal_id)
            self.reducer.transition(
                SubgoalTransitionRequest(
                    task_id=evidence.task_id,
                    subgoal_id=subgoal_id,
                    target_status=current.status,
                    reason="Evidence was explicitly linked to the subgoal.",
                    source_event_id=evidence.source_event_id,
                    evidence_ids=(evidence.evidence_id,),
                    turn=turn,
                )
            )
        return stored

    def change_status(
        self,
        task_id: str,
        evidence_id: str,
        target_status: EvidenceStatus,
        *,
        turn: int,
        verification_id: str | None = None,
    ) -> EvidenceItem:
        current = self.session.state_store.get_evidence(task_id, evidence_id)
        if target_status is EvidenceStatus.VERIFIED:
            if current.source_type is not EvidenceSourceType.ENVIRONMENT_VERIFICATION or verification_id is None:
                raise StateInvariantError("VERIFIED evidence requires an environment verification result")
            verification_ids = tuple(dict.fromkeys((*current.verification_ids, verification_id)))
        else:
            verification_ids = current.verification_ids
        updated = current.model_copy(update={"status": target_status, "verification_ids": verification_ids})
        stored = self.session.state_store.update_evidence(
            task_id,
            updated,
            expected_status=current.status,
        )
        self.session.trace_recorder.record(
            task_id=task_id,
            run_id=self.session.run_id,
            turn=turn,
            event_type=TraceEventType.EVIDENCE_STATUS_CHANGED,
            actor=TraceActor.RUNTIME,
            correlation_id=evidence_id,
            payload={"from_status": current.status.value, "evidence": stored.model_dump(mode="json")},
            critical=False,
        )
        return stored


__all__ = ["EvidenceBoard"]
