"""Evidence provenance and verification-state schemas."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from agentgate_core.contracts.base import (
    AwareDatetime,
    HumanText,
    Identifier,
    JsonValue,
    Probability,
    VersionedContract,
    require_unique,
    utc_now,
)


class EvidenceSourceType(StrEnum):
    USER = "user"
    TOOL_RESULT = "tool_result"
    ENVIRONMENT_VERIFICATION = "environment_verification"
    MODEL_INFERENCE = "model_inference"
    POLICY = "policy"
    RUNTIME = "runtime"


class EvidenceStatus(StrEnum):
    OBSERVED = "observed"
    INFERRED = "inferred"
    VERIFIED = "verified"
    CONFLICTED = "conflicted"
    STALE = "stale"
    REVOKED = "revoked"


class EvidenceItem(VersionedContract):
    evidence_id: Identifier
    task_id: Identifier
    subject: Identifier
    predicate: Identifier
    value: JsonValue
    source_type: EvidenceSourceType
    source_event_id: Identifier
    observed_at: AwareDatetime = Field(default_factory=utc_now)
    scope: Identifier = "task"
    confidence: Probability = 1.0
    status: EvidenceStatus = EvidenceStatus.OBSERVED
    verification_ids: tuple[Identifier, ...] = ()
    valid_until: AwareDatetime | None = None
    supersedes: tuple[Identifier, ...] = ()
    note: HumanText | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        require_unique(self.verification_ids, "verification_ids")
        require_unique(self.supersedes, "supersedes")
        if self.evidence_id in self.supersedes:
            raise ValueError("evidence cannot supersede itself")
        if self.status is EvidenceStatus.VERIFIED and not self.verification_ids:
            raise ValueError("verified evidence must reference a verification result")
        if self.valid_until is not None and self.valid_until <= self.observed_at:
            raise ValueError("valid_until must be later than observed_at")
        return self
