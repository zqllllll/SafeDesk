"""Validated persistence snapshot for one typed task aggregate."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from agentgate_core.contracts.effect import EffectRecord
from agentgate_core.contracts.evidence import EvidenceItem
from agentgate_core.contracts.failure import FailureRecord
from agentgate_core.contracts.task import TaskContract, TaskState
from agentgate_core.contracts.verification import VerificationResult
from agentgate_core.runtime.state_store import StateCheckpoint, TaskStateEvent


class StoredTaskAggregate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: TaskContract
    state: TaskState
    evidence: tuple[EvidenceItem, ...] = ()
    effects: tuple[EffectRecord, ...] = ()
    verifications: tuple[VerificationResult, ...] = ()
    failures: tuple[FailureRecord, ...] = ()
    events: tuple[TaskStateEvent, ...]
    checkpoints: tuple[StateCheckpoint, ...] = ()

    @model_validator(mode="after")
    def validate_ownership_and_identity(self) -> Self:
        task_id = self.contract.task_id
        if self.state.task_id != task_id:
            raise ValueError("snapshot state and contract must belong to the same task")
        collections = (
            ("evidence", self.evidence, "evidence_id"),
            ("effects", self.effects, "effect_id"),
            ("verifications", self.verifications, "verification_id"),
            ("failures", self.failures, "failure_id"),
            ("events", self.events, "event_id"),
            ("checkpoints", self.checkpoints, "checkpoint_id"),
        )
        for name, records, identifier_field in collections:
            identifiers = [getattr(record, identifier_field) for record in records]
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"snapshot {name} identifiers must be unique")
            if any(record.task_id != task_id for record in records):
                raise ValueError(f"snapshot {name} must belong to the contract task")
        if not self.events:
            raise ValueError("snapshot must contain at least the task-created event")
        return self


__all__ = ["StoredTaskAggregate"]
