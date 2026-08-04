"""Bounded context packages assembled for a model turn."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from agentgate_core.contracts.base import (
    AwareDatetime,
    ContractModel,
    HumanText,
    Identifier,
    JsonValue,
    NonNegativeInt,
    PositiveInt,
    VersionedContract,
    require_unique,
    utc_now,
)
from agentgate_core.contracts.task import TaskPhase


class ContextPriority(StrEnum):
    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"
    P4 = "p4"


class ContextBudgetStatus(StrEnum):
    WITHIN_BUDGET = "within_budget"
    SOFT_EXCEEDED = "soft_exceeded"
    HARD_EXCEEDED = "hard_exceeded"


class ContextSection(ContractModel):
    section_id: Identifier
    priority: ContextPriority
    content: JsonValue
    estimated_tokens: NonNegativeInt
    source_event_ids: tuple[Identifier, ...] = ()
    compressible: bool = True
    raw_reference: Identifier | None = None

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        require_unique(self.source_event_ids, "source_event_ids")
        return self


class ContextPack(VersionedContract):
    pack_id: Identifier
    task_id: Identifier
    state_version: PositiveInt
    phase: TaskPhase
    active_subgoal_ids: tuple[Identifier, ...] = ()
    hard_constraints: tuple[HumanText, ...] = ()
    verified_evidence_ids: tuple[Identifier, ...] = ()
    observed_evidence_ids: tuple[Identifier, ...] = ()
    open_failure_ids: tuple[Identifier, ...] = ()
    effect_ids: tuple[Identifier, ...] = ()
    active_tool_names: tuple[Identifier, ...] = ()
    recent_event_ids: tuple[Identifier, ...] = ()
    recovery_event_ids: tuple[Identifier, ...] = ()
    sections: tuple[ContextSection, ...] = ()
    soft_token_limit: PositiveInt
    hard_token_limit: PositiveInt
    estimated_tokens: NonNegativeInt
    budget_status: ContextBudgetStatus
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_budget_and_references(self) -> Self:
        for field_name in (
            "active_subgoal_ids",
            "verified_evidence_ids",
            "observed_evidence_ids",
            "open_failure_ids",
            "effect_ids",
            "active_tool_names",
            "recent_event_ids",
            "recovery_event_ids",
        ):
            require_unique(getattr(self, field_name), field_name)

        section_ids = tuple(section.section_id for section in self.sections)
        require_unique(section_ids, "section ids")
        if self.soft_token_limit > self.hard_token_limit:
            raise ValueError("soft_token_limit cannot exceed hard_token_limit")

        section_tokens = sum(section.estimated_tokens for section in self.sections)
        if self.estimated_tokens < section_tokens:
            raise ValueError("estimated_tokens cannot be lower than the sum of section estimates")

        if self.estimated_tokens <= self.soft_token_limit:
            expected_status = ContextBudgetStatus.WITHIN_BUDGET
        elif self.estimated_tokens <= self.hard_token_limit:
            expected_status = ContextBudgetStatus.SOFT_EXCEEDED
        else:
            expected_status = ContextBudgetStatus.HARD_EXCEEDED
        if self.budget_status is not expected_status:
            raise ValueError(f"budget_status must be {expected_status.value} for the declared token estimate")
        return self
