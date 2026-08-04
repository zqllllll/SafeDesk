"""Action, goal, invariant, and trace verification result schemas."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from agentgate_core.contracts.action import EffectKind, ResourceRef
from agentgate_core.contracts.base import (
    AwareDatetime,
    ContractModel,
    HumanText,
    Identifier,
    JsonObject,
    JsonValue,
    VersionedContract,
    require_unique,
    utc_now,
)


class VerificationType(StrEnum):
    ACTION = "action"
    GOAL = "goal"
    INVARIANT = "invariant"
    TRACE = "trace"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"
    ERROR = "error"


class DifferenceKind(StrEnum):
    MISSING = "missing"
    UNEXPECTED = "unexpected"
    DIFFERENT = "different"
    TYPE_MISMATCH = "type_mismatch"


class UnexpectedEffectStatus(StrEnum):
    """Resolution lifecycle for an unexpected observed side effect."""

    OBSERVED = "observed"
    ROLLBACK_PENDING = "rollback_pending"
    ROLLED_BACK = "rolled_back"
    POLICY_ACCEPTED = "policy_accepted"
    UNRESOLVED = "unresolved"


class FieldDifference(ContractModel):
    path: Identifier
    kind: DifferenceKind
    expected: JsonValue = None
    observed: JsonValue = None


class ObservedEffect(ContractModel):
    kind: EffectKind
    operation: Identifier
    resource: ResourceRef
    change: JsonObject = Field(default_factory=dict)
    resolution_status: UnexpectedEffectStatus = UnexpectedEffectStatus.OBSERVED


class VerificationResult(VersionedContract):
    verification_id: Identifier
    task_id: Identifier
    verification_type: VerificationType
    target_id: Identifier
    action_id: Identifier | None = None
    verifier_name: Identifier
    verifier_version: Identifier
    expected_state: JsonValue
    observed_state: JsonValue = None
    status: VerificationStatus
    differences: tuple[FieldDifference, ...] = ()
    unintended_effects: tuple[ObservedEffect, ...] = ()
    evidence_ids: tuple[Identifier, ...] = ()
    error_code: Identifier | None = None
    error_message: HumanText | None = None
    checked_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        require_unique(self.evidence_ids, "evidence_ids")
        if self.verification_type is VerificationType.ACTION and self.action_id is None:
            raise ValueError("action verification requires action_id")
        if self.status is VerificationStatus.VERIFIED:
            if self.observed_state is None:
                raise ValueError("verified results must include observed_state")
            if self.differences or self.unintended_effects:
                raise ValueError("verified results cannot contain differences or unintended effects")
            if self.error_code or self.error_message:
                raise ValueError("verified results cannot contain errors")
        elif self.status is VerificationStatus.MISMATCH:
            if not (self.differences or self.unintended_effects):
                raise ValueError("mismatch results must describe a difference or unintended effect")
        elif self.status is VerificationStatus.ERROR and self.error_message is None:
            raise ValueError("error results must include error_message")
        return self
