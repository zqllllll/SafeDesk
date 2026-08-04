"""Expected and observed side-effect ledger schemas."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from agentgate_core.contracts.action import EffectKind, ResourceRef
from agentgate_core.contracts.base import (
    AwareDatetime,
    Identifier,
    JsonObject,
    VersionedContract,
    utc_now,
)


class EffectStatus(StrEnum):
    PLANNED = "planned"
    RESERVED = "reserved"
    IN_FLIGHT = "in_flight"
    APPLIED_UNVERIFIED = "applied_unverified"
    VERIFIED = "verified"
    FAILED = "failed"
    UNKNOWN = "unknown"
    ROLLED_BACK = "rolled_back"


class EffectRecord(VersionedContract):
    effect_id: Identifier
    task_id: Identifier
    action_id: Identifier
    idempotency_key: Identifier
    kind: EffectKind
    operation: Identifier
    resource: ResourceRef
    expected_change: JsonObject = Field(default_factory=dict)
    actual_change: JsonObject | None = None
    status: EffectStatus = EffectStatus.PLANNED
    verification_id: Identifier | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_effect(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        if self.status is EffectStatus.VERIFIED:
            if self.verification_id is None:
                raise ValueError("verified effects must reference a verification result")
            if self.actual_change is None:
                raise ValueError("verified effects must include the observed actual_change")
        if self.status in {EffectStatus.PLANNED, EffectStatus.RESERVED} and self.actual_change is not None:
            raise ValueError("an effect cannot have actual_change before execution")
        return self
