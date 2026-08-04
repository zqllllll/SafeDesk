"""Contracts for context budgeting, projection, retrieval, and safety validation."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from agentgate_core.contracts.base import (
    HumanText,
    Identifier,
    JsonObject,
    NonNegativeInt,
    PositiveInt,
    VersionedContract,
    require_unique,
)
from agentgate_core.contracts.context import ContextBudgetStatus, ContextPriority


class TokenBudgetReport(VersionedContract):
    soft_limit: PositiveInt
    hard_limit: PositiveInt
    estimated_input_tokens: NonNegativeInt
    reserved_output_tokens: NonNegativeInt
    section_tokens: dict[Identifier, NonNegativeInt] = Field(default_factory=dict)
    dropped_section_ids: tuple[Identifier, ...] = ()
    projected_section_ids: tuple[Identifier, ...] = ()
    status: ContextBudgetStatus

    @model_validator(mode="after")
    def validate_budget(self) -> Self:
        require_unique(self.dropped_section_ids, "dropped_section_ids")
        require_unique(self.projected_section_ids, "projected_section_ids")
        if self.soft_limit > self.hard_limit:
            raise ValueError("soft_limit cannot exceed hard_limit")
        return self


class RawTraceReference(VersionedContract):
    reference_id: Identifier
    task_id: Identifier
    run_id: Identifier
    event_ids: tuple[Identifier, ...] = Field(min_length=1)
    content_type: Identifier
    estimated_tokens: NonNegativeInt

    @model_validator(mode="after")
    def validate_events(self) -> Self:
        require_unique(self.event_ids, "event_ids")
        return self


class ProjectedToolResult(VersionedContract):
    projection_id: Identifier
    task_id: Identifier
    tool_name: Identifier
    source_event_id: Identifier
    projected: JsonObject
    raw_reference: RawTraceReference
    original_tokens: NonNegativeInt
    projected_tokens: NonNegativeInt


class ContextInvariantCode(StrEnum):
    ORIGINAL_GOAL_MISSING = "original_goal_missing"
    HARD_CONSTRAINT_MISSING = "hard_constraint_missing"
    ACTIVE_SUBGOAL_MISSING = "active_subgoal_missing"
    VERIFIED_EVIDENCE_MISSING = "verified_evidence_missing"
    EVIDENCE_STATUS_UPGRADED = "evidence_status_upgraded"
    UNRESOLVED_EFFECT_MISSING = "unresolved_effect_missing"
    OPEN_FAILURE_MISSING = "open_failure_missing"
    RESOURCE_ID_CHANGED = "resource_id_changed"
    CONFIRMATION_STATE_CHANGED = "confirmation_state_changed"
    STATE_VERSION_MISMATCH = "state_version_mismatch"


class ContextInvariantViolation(VersionedContract):
    code: ContextInvariantCode
    reference_id: Identifier
    message: HumanText


class ContextInvariantReport(VersionedContract):
    task_id: Identifier
    state_version: PositiveInt
    valid: bool
    violations: tuple[ContextInvariantViolation, ...] = ()

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.valid == bool(self.violations):
            raise ValueError("valid must be true exactly when violations are empty")
        return self


class HistorySummary(VersionedContract):
    summary_id: Identifier
    task_id: Identifier
    source_event_ids: tuple[Identifier, ...] = Field(min_length=1)
    priority: ContextPriority = ContextPriority.P4
    facts: tuple[HumanText, ...] = ()
    completed_action_ids: tuple[Identifier, ...] = ()
    resolved_failure_ids: tuple[Identifier, ...] = ()
    raw_reference: RawTraceReference

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        for field_name in ("source_event_ids", "completed_action_ids", "resolved_failure_ids"):
            require_unique(getattr(self, field_name), field_name)
        return self


__all__ = [
    "ContextInvariantCode",
    "ContextInvariantReport",
    "ContextInvariantViolation",
    "HistorySummary",
    "ProjectedToolResult",
    "RawTraceReference",
    "TokenBudgetReport",
]
