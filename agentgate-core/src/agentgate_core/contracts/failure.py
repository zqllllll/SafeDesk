"""Failure taxonomy and recovery-tracking schemas."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from agentgate_core.contracts.base import (
    AwareDatetime,
    HumanText,
    Identifier,
    NonNegativeInt,
    Probability,
    VersionedContract,
    require_unique,
    utc_now,
)


class FailureType(StrEnum):
    MISSING_EVIDENCE = "missing_evidence"
    INVALID_ARGUMENT = "invalid_argument"
    OUT_OF_SCHEMA = "out_of_schema"
    WRONG_TOOL = "wrong_tool"
    WRONG_RESOURCE = "wrong_resource"
    DEPENDENCY_NOT_SATISFIED = "dependency_not_satisfied"
    POLICY_DENIED = "policy_denied"
    APPROVAL_REQUIRED = "approval_required"
    TOOL_EXECUTION_ERROR = "tool_execution_error"
    TOOL_TIMEOUT = "tool_timeout"
    AUTHENTICATION_FAILED = "authentication_failed"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    PARTIAL_COMPLETION = "partial_completion"
    VERIFICATION_FAILED = "verification_failed"
    DUPLICATE_ACTION = "duplicate_action"
    UNINTENDED_SIDE_EFFECT = "unintended_side_effect"
    NO_PROGRESS = "no_progress"
    CONTEXT_DEGRADED = "context_degraded"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


class ResponsibleLayer(StrEnum):
    MODEL = "model"
    CONTEXT = "context"
    TOOL_SCHEMA = "tool_schema"
    POLICY = "policy"
    SCHEDULER = "scheduler"
    TOOL = "tool"
    VERIFIER = "verifier"
    RECOVERY = "recovery"
    INFRASTRUCTURE = "infrastructure"


class FailureStatus(StrEnum):
    OPEN = "open"
    RECOVERING = "recovering"
    RESOLVED = "resolved"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ESCALATED = "escalated"


class FailureRecord(VersionedContract):
    failure_id: Identifier
    task_id: Identifier
    action_id: Identifier | None = None
    failure_type: FailureType
    message: HumanText
    retryable: bool
    responsible_layer: ResponsibleLayer
    confidence: Probability = 1.0
    evidence_ids: tuple[Identifier, ...] = ()
    attempt_count: NonNegativeInt = 0
    recovery_budget_remaining: NonNegativeInt = 0
    status: FailureStatus = FailureStatus.OPEN
    resolved_event_id: Identifier | None = None
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_failure(self) -> Self:
        require_unique(self.evidence_ids, "evidence_ids")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        if self.status is FailureStatus.RESOLVED and self.resolved_event_id is None:
            raise ValueError("resolved failures must reference the resolving event")
        if self.status is FailureStatus.BUDGET_EXHAUSTED and self.recovery_budget_remaining != 0:
            raise ValueError("budget-exhausted failures must have zero recovery budget")
        return self
