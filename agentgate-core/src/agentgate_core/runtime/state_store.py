"""Typed state-store protocol, storage events, checkpoints, and errors."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import Field, model_validator

from agentgate_core.contracts.base import (
    AwareDatetime,
    HumanText,
    Identifier,
    JsonObject,
    NonNegativeInt,
    PositiveInt,
    VersionedContract,
    require_unique,
    utc_now,
)
from agentgate_core.contracts.effect import EffectRecord, EffectStatus
from agentgate_core.contracts.evidence import EvidenceItem, EvidenceStatus
from agentgate_core.contracts.failure import FailureRecord, FailureStatus
from agentgate_core.contracts.task import TaskContract, TaskState
from agentgate_core.contracts.verification import VerificationResult


class TaskStateEventType(StrEnum):
    CREATED = "created"
    TRANSITION = "transition"
    CHECKPOINT_RESTORED = "checkpoint_restored"


class TaskStateEvent(VersionedContract):
    event_id: Identifier
    task_id: Identifier
    event_type: TaskStateEventType
    name: Identifier
    previous_state_version: NonNegativeInt
    next_state: TaskState
    reason: HumanText
    source_event_id: Identifier | None = None
    payload: JsonObject = Field(default_factory=dict)
    occurred_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_transition(self) -> TaskStateEvent:
        if self.next_state.task_id != self.task_id:
            raise ValueError("event and next_state must belong to the same task")
        if self.next_state.state_version != self.previous_state_version + 1:
            raise ValueError("next_state version must be exactly one greater than previous_state_version")
        if self.event_type is TaskStateEventType.CREATED:
            if self.previous_state_version != 0:
                raise ValueError("created events must start from state version zero")
        elif self.previous_state_version == 0:
            raise ValueError("only created events may start from state version zero")
        if self.occurred_at < self.next_state.updated_at:
            raise ValueError("event occurred_at cannot be earlier than next_state.updated_at")
        return self


class StateCheckpoint(VersionedContract):
    checkpoint_id: Identifier
    task_id: Identifier
    source_state_version: PositiveInt
    state: TaskState
    evidence_ids: tuple[Identifier, ...] = ()
    effect_ids: tuple[Identifier, ...] = ()
    verification_ids: tuple[Identifier, ...] = ()
    failure_ids: tuple[Identifier, ...] = ()
    event_count: PositiveInt
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_snapshot(self) -> StateCheckpoint:
        if self.state.task_id != self.task_id:
            raise ValueError("checkpoint and state must belong to the same task")
        if self.state.state_version != self.source_state_version:
            raise ValueError("checkpoint source_state_version must match the captured state")
        for field_name in ("evidence_ids", "effect_ids", "verification_ids", "failure_ids"):
            require_unique(getattr(self, field_name), field_name)
        return self


class StateStoreError(RuntimeError):
    """Base error for deterministic state-store failures."""


class TaskAlreadyExistsError(StateStoreError):
    def __init__(self, task_id: str) -> None:
        super().__init__(f"task already exists: {task_id}")
        self.task_id = task_id


class TaskNotFoundError(StateStoreError):
    def __init__(self, task_id: str) -> None:
        super().__init__(f"task not found: {task_id}")
        self.task_id = task_id


class VersionConflictError(StateStoreError):
    def __init__(self, task_id: str, expected_version: int, actual_version: int) -> None:
        super().__init__(
            f"state version conflict for task {task_id}: expected {expected_version}, actual {actual_version}"
        )
        self.task_id = task_id
        self.expected_version = expected_version
        self.actual_version = actual_version


class StateInvariantError(StateStoreError):
    """Raised when a write would leave the stored task aggregate inconsistent."""


class RecordNotFoundError(StateStoreError):
    def __init__(self, record_type: str, record_id: str) -> None:
        super().__init__(f"{record_type} not found: {record_id}")
        self.record_type = record_type
        self.record_id = record_id


class RecordConflictError(StateStoreError):
    def __init__(self, record_type: str, record_id: str) -> None:
        super().__init__(f"conflicting {record_type} already exists: {record_id}")
        self.record_type = record_type
        self.record_id = record_id


class RecordVersionConflictError(StateStoreError):
    def __init__(self, record_type: str, record_id: str, expected_status: str, actual_status: str) -> None:
        super().__init__(
            f"{record_type} status conflict for {record_id}: expected {expected_status}, actual {actual_status}"
        )
        self.record_type = record_type
        self.record_id = record_id
        self.expected_status = expected_status
        self.actual_status = actual_status


class IdempotencyConflictError(StateStoreError):
    def __init__(self, idempotency_key: str, existing_effect_id: str, proposed_effect_id: str) -> None:
        super().__init__(
            f"idempotency key {idempotency_key} is already owned by effect {existing_effect_id}, "
            f"not {proposed_effect_id}"
        )
        self.idempotency_key = idempotency_key
        self.existing_effect_id = existing_effect_id
        self.proposed_effect_id = proposed_effect_id


class StatePersistenceError(StateStoreError):
    """Raised when a persistent backend cannot atomically save or load state."""


class StatePersistenceConflictError(StatePersistenceError):
    def __init__(self, task_id: str, expected_revision: int | None, actual_revision: int | None) -> None:
        super().__init__(
            f"persistent revision conflict for task {task_id}: expected {expected_revision}, actual {actual_revision}"
        )
        self.task_id = task_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision


@runtime_checkable
class TypedStateStore(Protocol):
    def create_task(self, contract: TaskContract, initial_state: TaskState | None = None) -> TaskState: ...

    def get_task_contract(self, task_id: str) -> TaskContract: ...

    def get_task_state(self, task_id: str) -> TaskState: ...

    def apply_task_event(self, task_id: str, event: TaskStateEvent, expected_version: int) -> TaskState: ...

    def list_task_events(self, task_id: str) -> tuple[TaskStateEvent, ...]: ...

    def append_evidence(self, task_id: str, evidence: EvidenceItem) -> EvidenceItem: ...

    def update_evidence(
        self,
        task_id: str,
        evidence: EvidenceItem,
        expected_status: EvidenceStatus,
    ) -> EvidenceItem: ...

    def get_evidence(self, task_id: str, evidence_id: str) -> EvidenceItem: ...

    def list_evidence(self, task_id: str) -> tuple[EvidenceItem, ...]: ...

    def append_effect(self, task_id: str, effect: EffectRecord) -> EffectRecord: ...

    def update_effect(
        self,
        task_id: str,
        effect: EffectRecord,
        expected_status: EffectStatus,
    ) -> EffectRecord: ...

    def get_effect(self, task_id: str, effect_id: str) -> EffectRecord: ...

    def list_effects(self, task_id: str) -> tuple[EffectRecord, ...]: ...

    def append_verification(self, task_id: str, verification: VerificationResult) -> VerificationResult: ...

    def get_verification(self, task_id: str, verification_id: str) -> VerificationResult: ...

    def list_verifications(self, task_id: str) -> tuple[VerificationResult, ...]: ...

    def append_failure(self, task_id: str, failure: FailureRecord) -> FailureRecord: ...

    def update_failure(
        self,
        task_id: str,
        failure: FailureRecord,
        expected_status: FailureStatus,
    ) -> FailureRecord: ...

    def get_failure(self, task_id: str, failure_id: str) -> FailureRecord: ...

    def list_failures(self, task_id: str) -> tuple[FailureRecord, ...]: ...

    def create_checkpoint(self, task_id: str, checkpoint_id: str | None = None) -> StateCheckpoint: ...

    def restore_checkpoint(
        self,
        task_id: str,
        checkpoint_id: str,
        *,
        expected_version: int | None = None,
    ) -> TaskState: ...

    def list_checkpoints(self, task_id: str) -> tuple[StateCheckpoint, ...]: ...


__all__ = [
    "IdempotencyConflictError",
    "RecordConflictError",
    "RecordNotFoundError",
    "RecordVersionConflictError",
    "StateCheckpoint",
    "StateInvariantError",
    "StatePersistenceConflictError",
    "StatePersistenceError",
    "StateStoreError",
    "TaskAlreadyExistsError",
    "TaskNotFoundError",
    "TaskStateEvent",
    "TaskStateEventType",
    "TypedStateStore",
    "VersionConflictError",
]
