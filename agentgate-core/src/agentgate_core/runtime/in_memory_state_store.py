"""Thread-safe in-memory implementation of the AgentGate typed state store."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel

from agentgate_core.contracts.base import utc_now
from agentgate_core.contracts.effect import EffectRecord, EffectStatus
from agentgate_core.contracts.evidence import EvidenceItem, EvidenceStatus
from agentgate_core.contracts.failure import FailureRecord, FailureStatus
from agentgate_core.contracts.task import SubgoalState, TaskContract, TaskPhase, TaskState
from agentgate_core.contracts.verification import VerificationResult
from agentgate_core.runtime.snapshot import StoredTaskAggregate
from agentgate_core.runtime.state_store import (
    IdempotencyConflictError,
    RecordConflictError,
    RecordNotFoundError,
    RecordVersionConflictError,
    StateCheckpoint,
    StateInvariantError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
    TaskStateEvent,
    TaskStateEventType,
    VersionConflictError,
)


def _clone[ModelT: BaseModel](model: ModelT) -> ModelT:
    return model.model_copy(deep=True)


@dataclass
class _TaskAggregate:
    contract: TaskContract
    state: TaskState
    evidence: dict[str, EvidenceItem] = field(default_factory=dict)
    effects: dict[str, EffectRecord] = field(default_factory=dict)
    effects_by_idempotency_key: dict[str, str] = field(default_factory=dict)
    verifications: dict[str, VerificationResult] = field(default_factory=dict)
    failures: dict[str, FailureRecord] = field(default_factory=dict)
    events: list[TaskStateEvent] = field(default_factory=list)
    events_by_id: dict[str, TaskStateEvent] = field(default_factory=dict)
    checkpoints: dict[str, StateCheckpoint] = field(default_factory=dict)


class InMemoryTypedStateStore:
    """In-process store with atomic writes, defensive copies, and optimistic versions."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._lock = RLock()
        self._tasks: dict[str, _TaskAggregate] = {}
        self._event_owners: dict[str, str] = {}

    def create_task(self, contract: TaskContract, initial_state: TaskState | None = None) -> TaskState:
        with self._lock:
            if contract.task_id in self._tasks:
                raise TaskAlreadyExistsError(contract.task_id)

            stored_contract = _clone(contract)
            state = _clone(initial_state) if initial_state is not None else self._default_initial_state(stored_contract)
            if state.state_version != 1:
                raise StateInvariantError("an initial task state must have state_version=1")

            aggregate = _TaskAggregate(contract=stored_contract, state=state)
            self._validate_state(aggregate, state)
            created_event = TaskStateEvent(
                event_id=self._new_unique_id("state-event", self._event_owners),
                task_id=contract.task_id,
                event_type=TaskStateEventType.CREATED,
                name="task_created",
                previous_state_version=0,
                next_state=state,
                reason="The task aggregate was initialized.",
                payload={"contract_version": contract.version},
                occurred_at=max(self._clock(), state.updated_at),
            )
            aggregate.events.append(_clone(created_event))
            aggregate.events_by_id[created_event.event_id] = _clone(created_event)
            self._event_owners[created_event.event_id] = contract.task_id
            self._tasks[contract.task_id] = aggregate
            return _clone(state)

    def get_task_contract(self, task_id: str) -> TaskContract:
        with self._lock:
            return _clone(self._get_aggregate(task_id).contract)

    def get_task_state(self, task_id: str) -> TaskState:
        with self._lock:
            return _clone(self._get_aggregate(task_id).state)

    def apply_task_event(self, task_id: str, event: TaskStateEvent, expected_version: int) -> TaskState:
        with self._lock:
            aggregate = self._get_aggregate(task_id)
            existing_event = aggregate.events_by_id.get(event.event_id)
            if existing_event is not None:
                if existing_event != event:
                    raise RecordConflictError("task state event", event.event_id)
                return _clone(aggregate.state)
            event_owner = self._event_owners.get(event.event_id)
            if event_owner is not None:
                raise RecordConflictError("task state event", event.event_id)

            if event.event_type is not TaskStateEventType.TRANSITION:
                raise StateInvariantError("apply_task_event only accepts transition events")
            if event.task_id != task_id:
                raise StateInvariantError("event task_id does not match the requested task")
            if aggregate.state.state_version != expected_version:
                raise VersionConflictError(task_id, expected_version, aggregate.state.state_version)
            if event.previous_state_version != expected_version:
                raise StateInvariantError("event previous_state_version does not match expected_version")
            if event.next_state.updated_at < aggregate.state.updated_at:
                raise StateInvariantError("a state transition cannot move updated_at backwards")

            self._validate_state(aggregate, event.next_state)
            stored_event = _clone(event)
            aggregate.state = _clone(event.next_state)
            aggregate.events.append(stored_event)
            aggregate.events_by_id[event.event_id] = _clone(stored_event)
            self._event_owners[event.event_id] = task_id
            return _clone(aggregate.state)

    def list_task_events(self, task_id: str) -> tuple[TaskStateEvent, ...]:
        with self._lock:
            return tuple(_clone(event) for event in self._get_aggregate(task_id).events)

    def append_evidence(self, task_id: str, evidence: EvidenceItem) -> EvidenceItem:
        with self._lock:
            aggregate = self._get_aggregate(task_id)
            self._require_task_ownership(task_id, evidence.task_id, "evidence")
            self._validate_evidence_references(aggregate, evidence)
            return self._append_record(aggregate.evidence, evidence.evidence_id, evidence, "evidence")

    def update_evidence(
        self,
        task_id: str,
        evidence: EvidenceItem,
        expected_status: EvidenceStatus,
    ) -> EvidenceItem:
        with self._lock:
            aggregate = self._get_aggregate(task_id)
            self._require_task_ownership(task_id, evidence.task_id, "evidence")
            current = self._get_record(aggregate.evidence, evidence.evidence_id, "evidence")
            self._require_status("evidence", evidence.evidence_id, expected_status.value, current.status.value)
            self._require_same_fields(
                "evidence",
                evidence.evidence_id,
                current,
                evidence,
                ("task_id", "subject", "predicate", "value", "source_type", "source_event_id", "observed_at", "scope"),
            )
            self._validate_evidence_references(aggregate, evidence)
            aggregate.evidence[evidence.evidence_id] = _clone(evidence)
            return _clone(evidence)

    def get_evidence(self, task_id: str, evidence_id: str) -> EvidenceItem:
        with self._lock:
            return _clone(self._get_record(self._get_aggregate(task_id).evidence, evidence_id, "evidence"))

    def list_evidence(self, task_id: str) -> tuple[EvidenceItem, ...]:
        with self._lock:
            return tuple(_clone(item) for item in self._get_aggregate(task_id).evidence.values())

    def append_effect(self, task_id: str, effect: EffectRecord) -> EffectRecord:
        with self._lock:
            aggregate = self._get_aggregate(task_id)
            self._require_task_ownership(task_id, effect.task_id, "effect")
            self._validate_effect_references(aggregate, effect)
            owner = aggregate.effects_by_idempotency_key.get(effect.idempotency_key)
            if owner is not None and owner != effect.effect_id:
                raise IdempotencyConflictError(effect.idempotency_key, owner, effect.effect_id)
            stored = self._append_record(aggregate.effects, effect.effect_id, effect, "effect")
            aggregate.effects_by_idempotency_key[effect.idempotency_key] = effect.effect_id
            return stored

    def update_effect(
        self,
        task_id: str,
        effect: EffectRecord,
        expected_status: EffectStatus,
    ) -> EffectRecord:
        with self._lock:
            aggregate = self._get_aggregate(task_id)
            self._require_task_ownership(task_id, effect.task_id, "effect")
            current = self._get_record(aggregate.effects, effect.effect_id, "effect")
            self._require_status("effect", effect.effect_id, expected_status.value, current.status.value)
            self._require_same_fields(
                "effect",
                effect.effect_id,
                current,
                effect,
                (
                    "task_id",
                    "action_id",
                    "idempotency_key",
                    "kind",
                    "operation",
                    "resource",
                    "expected_change",
                    "created_at",
                ),
            )
            if effect.updated_at < current.updated_at:
                raise StateInvariantError("an effect update cannot move updated_at backwards")
            self._validate_effect_references(aggregate, effect)
            aggregate.effects[effect.effect_id] = _clone(effect)
            return _clone(effect)

    def get_effect(self, task_id: str, effect_id: str) -> EffectRecord:
        with self._lock:
            return _clone(self._get_record(self._get_aggregate(task_id).effects, effect_id, "effect"))

    def list_effects(self, task_id: str) -> tuple[EffectRecord, ...]:
        with self._lock:
            return tuple(_clone(item) for item in self._get_aggregate(task_id).effects.values())

    def append_verification(self, task_id: str, verification: VerificationResult) -> VerificationResult:
        with self._lock:
            aggregate = self._get_aggregate(task_id)
            self._require_task_ownership(task_id, verification.task_id, "verification")
            self._require_known_ids("evidence", verification.evidence_ids, aggregate.evidence)
            return self._append_record(
                aggregate.verifications,
                verification.verification_id,
                verification,
                "verification",
            )

    def get_verification(self, task_id: str, verification_id: str) -> VerificationResult:
        with self._lock:
            return _clone(self._get_record(self._get_aggregate(task_id).verifications, verification_id, "verification"))

    def list_verifications(self, task_id: str) -> tuple[VerificationResult, ...]:
        with self._lock:
            return tuple(_clone(item) for item in self._get_aggregate(task_id).verifications.values())

    def append_failure(self, task_id: str, failure: FailureRecord) -> FailureRecord:
        with self._lock:
            aggregate = self._get_aggregate(task_id)
            self._require_task_ownership(task_id, failure.task_id, "failure")
            self._require_known_ids("evidence", failure.evidence_ids, aggregate.evidence)
            return self._append_record(aggregate.failures, failure.failure_id, failure, "failure")

    def update_failure(
        self,
        task_id: str,
        failure: FailureRecord,
        expected_status: FailureStatus,
    ) -> FailureRecord:
        with self._lock:
            aggregate = self._get_aggregate(task_id)
            self._require_task_ownership(task_id, failure.task_id, "failure")
            current = self._get_record(aggregate.failures, failure.failure_id, "failure")
            self._require_status("failure", failure.failure_id, expected_status.value, current.status.value)
            self._require_same_fields(
                "failure",
                failure.failure_id,
                current,
                failure,
                ("task_id", "action_id", "failure_type", "created_at"),
            )
            if failure.updated_at < current.updated_at:
                raise StateInvariantError("a failure update cannot move updated_at backwards")
            self._require_known_ids("evidence", failure.evidence_ids, aggregate.evidence)
            aggregate.failures[failure.failure_id] = _clone(failure)
            return _clone(failure)

    def get_failure(self, task_id: str, failure_id: str) -> FailureRecord:
        with self._lock:
            return _clone(self._get_record(self._get_aggregate(task_id).failures, failure_id, "failure"))

    def list_failures(self, task_id: str) -> tuple[FailureRecord, ...]:
        with self._lock:
            return tuple(_clone(item) for item in self._get_aggregate(task_id).failures.values())

    def create_checkpoint(self, task_id: str, checkpoint_id: str | None = None) -> StateCheckpoint:
        with self._lock:
            aggregate = self._get_aggregate(task_id)
            resolved_checkpoint_id = checkpoint_id or self._new_unique_id("checkpoint", aggregate.checkpoints)
            if resolved_checkpoint_id in aggregate.checkpoints:
                raise RecordConflictError("checkpoint", resolved_checkpoint_id)
            checkpoint = StateCheckpoint(
                checkpoint_id=resolved_checkpoint_id,
                task_id=task_id,
                source_state_version=aggregate.state.state_version,
                state=aggregate.state,
                evidence_ids=tuple(aggregate.evidence),
                effect_ids=tuple(aggregate.effects),
                verification_ids=tuple(aggregate.verifications),
                failure_ids=tuple(aggregate.failures),
                event_count=len(aggregate.events),
                created_at=max(self._clock(), aggregate.state.updated_at),
            )
            aggregate.checkpoints[checkpoint.checkpoint_id] = _clone(checkpoint)
            return _clone(checkpoint)

    def restore_checkpoint(
        self,
        task_id: str,
        checkpoint_id: str,
        *,
        expected_version: int | None = None,
    ) -> TaskState:
        with self._lock:
            aggregate = self._get_aggregate(task_id)
            checkpoint = self._get_record(aggregate.checkpoints, checkpoint_id, "checkpoint")
            current_version = aggregate.state.state_version
            if expected_version is not None and current_version != expected_version:
                raise VersionConflictError(task_id, expected_version, current_version)

            now = max(self._clock(), aggregate.state.updated_at)
            unknown_effect_ids: list[str] = []
            for effect_id, effect in tuple(aggregate.effects.items()):
                if effect.status is EffectStatus.IN_FLIGHT:
                    payload = effect.model_dump(mode="python")
                    payload.update(status=EffectStatus.UNKNOWN, updated_at=max(now, effect.updated_at))
                    aggregate.effects[effect_id] = EffectRecord.model_validate(payload)
                    unknown_effect_ids.append(effect_id)

            restored_payload = checkpoint.state.model_dump(mode="python")
            restored_payload.update(
                state_version=current_version + 1,
                effect_ids=tuple(aggregate.effects),
                updated_at=now,
            )
            restored_state = TaskState.model_validate(restored_payload)
            self._validate_state(aggregate, restored_state)
            restore_event = TaskStateEvent(
                event_id=self._new_unique_id("state-event", self._event_owners),
                task_id=task_id,
                event_type=TaskStateEventType.CHECKPOINT_RESTORED,
                name="checkpoint_restored",
                previous_state_version=current_version,
                next_state=restored_state,
                reason="The logical task state was restored without discarding the append-only audit records.",
                payload={
                    "checkpoint_id": checkpoint_id,
                    "checkpoint_state_version": checkpoint.source_state_version,
                    "effects_marked_unknown": unknown_effect_ids,
                },
                occurred_at=now,
            )
            aggregate.state = _clone(restored_state)
            aggregate.events.append(_clone(restore_event))
            aggregate.events_by_id[restore_event.event_id] = _clone(restore_event)
            self._event_owners[restore_event.event_id] = task_id
            return _clone(restored_state)

    def list_checkpoints(self, task_id: str) -> tuple[StateCheckpoint, ...]:
        with self._lock:
            return tuple(_clone(item) for item in self._get_aggregate(task_id).checkpoints.values())

    def export_snapshot(self, task_id: str) -> StoredTaskAggregate:
        """Return a fully validated persistence image without exposing mutable internals."""

        with self._lock:
            aggregate = self._get_aggregate(task_id)
            return StoredTaskAggregate(
                contract=_clone(aggregate.contract),
                state=_clone(aggregate.state),
                evidence=tuple(_clone(item) for item in aggregate.evidence.values()),
                effects=tuple(_clone(item) for item in aggregate.effects.values()),
                verifications=tuple(_clone(item) for item in aggregate.verifications.values()),
                failures=tuple(_clone(item) for item in aggregate.failures.values()),
                events=tuple(_clone(item) for item in aggregate.events),
                checkpoints=tuple(_clone(item) for item in aggregate.checkpoints.values()),
            )

    def import_snapshot(self, snapshot: StoredTaskAggregate) -> None:
        """Restore a persistence image after checking all aggregate invariants."""

        with self._lock:
            task_id = snapshot.contract.task_id
            if task_id in self._tasks:
                raise TaskAlreadyExistsError(task_id)
            duplicate_event_ids = set(self._event_owners).intersection(event.event_id for event in snapshot.events)
            if duplicate_event_ids:
                raise RecordConflictError("task state event", sorted(duplicate_event_ids)[0])

            aggregate = _TaskAggregate(
                contract=_clone(snapshot.contract),
                state=_clone(snapshot.state),
                evidence={item.evidence_id: _clone(item) for item in snapshot.evidence},
                effects={item.effect_id: _clone(item) for item in snapshot.effects},
                effects_by_idempotency_key={item.idempotency_key: item.effect_id for item in snapshot.effects},
                verifications={item.verification_id: _clone(item) for item in snapshot.verifications},
                failures={item.failure_id: _clone(item) for item in snapshot.failures},
                events=[_clone(item) for item in snapshot.events],
                events_by_id={item.event_id: _clone(item) for item in snapshot.events},
                checkpoints={item.checkpoint_id: _clone(item) for item in snapshot.checkpoints},
            )
            if len(aggregate.effects_by_idempotency_key) != len(aggregate.effects):
                raise StateInvariantError("snapshot effects must have unique idempotency keys")
            self._validate_imported_aggregate(aggregate)
            self._tasks[task_id] = aggregate
            self._event_owners.update({event.event_id: task_id for event in snapshot.events})

    def _default_initial_state(self, contract: TaskContract) -> TaskState:
        return TaskState(
            task_id=contract.task_id,
            contract_version=contract.version,
            state_version=1,
            phase=TaskPhase.COLLECT,
            subgoals=tuple(SubgoalState(subgoal_id=item.subgoal_id) for item in contract.subgoals),
            pending_confirmation_ids=contract.required_confirmations,
            updated_at=self._clock(),
        )

    def _validate_state(self, aggregate: _TaskAggregate, state: TaskState) -> None:
        contract = aggregate.contract
        if state.task_id != contract.task_id:
            raise StateInvariantError("state and contract must belong to the same task")
        if state.contract_version != contract.version:
            raise StateInvariantError("state contract_version does not match the stored contract")
        contract_subgoals = {item.subgoal_id for item in contract.subgoals}
        state_subgoals = {item.subgoal_id for item in state.subgoals}
        if state_subgoals != contract_subgoals:
            raise StateInvariantError("state subgoals must exactly match the task contract")
        unknown_confirmations = set(state.pending_confirmation_ids) - set(contract.required_confirmations)
        if unknown_confirmations:
            raise StateInvariantError(f"state references unknown confirmations: {sorted(unknown_confirmations)}")

        self._require_known_ids("evidence", state.evidence_ids, aggregate.evidence)
        self._require_known_ids("effect", state.effect_ids, aggregate.effects)
        self._require_known_ids("verification", state.verification_ids, aggregate.verifications)
        self._require_known_ids("failure", state.failure_ids, aggregate.failures)
        blocker_ids = {blocker.blocker_id for blocker in state.blockers}
        for blocker in state.blockers:
            if blocker.source_failure_id is not None and blocker.source_failure_id not in aggregate.failures:
                raise StateInvariantError(f"blocker references unknown failure: {blocker.source_failure_id}")
        for subgoal in state.subgoals:
            self._require_known_ids("evidence", subgoal.evidence_ids, aggregate.evidence)
            self._require_known_ids("effect", subgoal.effect_ids, aggregate.effects)
            unknown_blockers = set(subgoal.blocker_ids) - blocker_ids
            if unknown_blockers:
                raise StateInvariantError(
                    f"subgoal {subgoal.subgoal_id} references unknown blockers: {sorted(unknown_blockers)}"
                )

    def _validate_imported_aggregate(self, aggregate: _TaskAggregate) -> None:
        events = aggregate.events
        if events[0].event_type is not TaskStateEventType.CREATED:
            raise StateInvariantError("snapshot event stream must begin with CREATED")
        previous_version = 0
        for event in events:
            if event.previous_state_version != previous_version:
                raise StateInvariantError("snapshot task events are not a contiguous state history")
            previous_version = event.next_state.state_version
        if events[-1].next_state != aggregate.state:
            raise StateInvariantError("snapshot final event state must equal the current task state")
        self._validate_state(aggregate, aggregate.state)
        for evidence in aggregate.evidence.values():
            self._validate_evidence_references(aggregate, evidence)
        for effect in aggregate.effects.values():
            self._validate_effect_references(aggregate, effect)
        for verification in aggregate.verifications.values():
            self._require_known_ids("evidence", verification.evidence_ids, aggregate.evidence)
        for failure in aggregate.failures.values():
            self._require_known_ids("evidence", failure.evidence_ids, aggregate.evidence)
        for checkpoint in aggregate.checkpoints.values():
            if checkpoint.event_count > len(events):
                raise StateInvariantError("snapshot checkpoint event_count exceeds the task event history")
            self._require_known_ids("evidence", checkpoint.evidence_ids, aggregate.evidence)
            self._require_known_ids("effect", checkpoint.effect_ids, aggregate.effects)
            self._require_known_ids("verification", checkpoint.verification_ids, aggregate.verifications)
            self._require_known_ids("failure", checkpoint.failure_ids, aggregate.failures)

    def _validate_evidence_references(self, aggregate: _TaskAggregate, evidence: EvidenceItem) -> None:
        self._require_known_ids("verification", evidence.verification_ids, aggregate.verifications)
        self._require_known_ids("evidence", evidence.supersedes, aggregate.evidence)

    def _validate_effect_references(self, aggregate: _TaskAggregate, effect: EffectRecord) -> None:
        if effect.verification_id is not None and effect.verification_id not in aggregate.verifications:
            raise StateInvariantError(f"effect references unknown verification: {effect.verification_id}")

    def _get_aggregate(self, task_id: str) -> _TaskAggregate:
        try:
            return self._tasks[task_id]
        except KeyError as error:
            raise TaskNotFoundError(task_id) from error

    @staticmethod
    def _append_record[ModelT: BaseModel](
        records: dict[str, ModelT],
        record_id: str,
        record: ModelT,
        record_type: str,
    ) -> ModelT:
        existing = records.get(record_id)
        if existing is not None:
            if existing != record:
                raise RecordConflictError(record_type, record_id)
            return _clone(existing)
        records[record_id] = _clone(record)
        return _clone(record)

    @staticmethod
    def _get_record[ModelT: BaseModel](records: dict[str, ModelT], record_id: str, record_type: str) -> ModelT:
        try:
            return records[record_id]
        except KeyError as error:
            raise RecordNotFoundError(record_type, record_id) from error

    @staticmethod
    def _require_task_ownership(requested_task_id: str, record_task_id: str, record_type: str) -> None:
        if requested_task_id != record_task_id:
            raise StateInvariantError(f"{record_type} belongs to task {record_task_id}, not {requested_task_id}")

    @staticmethod
    def _require_known_ids(record_type: str, record_ids: tuple[str, ...], records: dict[str, object]) -> None:
        unknown = set(record_ids) - set(records)
        if unknown:
            raise StateInvariantError(f"unknown {record_type} references: {sorted(unknown)}")

    @staticmethod
    def _require_status(record_type: str, record_id: str, expected_status: str, actual_status: str) -> None:
        if expected_status != actual_status:
            raise RecordVersionConflictError(record_type, record_id, expected_status, actual_status)

    @staticmethod
    def _require_same_fields(
        record_type: str,
        record_id: str,
        current: BaseModel,
        proposed: BaseModel,
        field_names: tuple[str, ...],
    ) -> None:
        changed = [
            field_name for field_name in field_names if getattr(current, field_name) != getattr(proposed, field_name)
        ]
        if changed:
            raise RecordConflictError(record_type, f"{record_id} (immutable fields changed: {', '.join(changed)})")

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}-{self._id_factory()}"

    def _new_unique_id(self, prefix: str, existing: dict[str, object]) -> str:
        for _ in range(100):
            candidate = self._new_id(prefix)
            if candidate not in existing:
                return candidate
        raise StateInvariantError(f"could not generate a unique {prefix} identifier after 100 attempts")


__all__ = ["InMemoryTypedStateStore"]
