"""Contracts for runner-facing guarded tool batches and lifecycle results."""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from agentgate_core.contracts.action import ActionIR
from agentgate_core.contracts.base import HumanText, Identifier, JsonObject, VersionedContract, require_unique
from agentgate_core.contracts.decision import CoordinatorResult, DecisionOutcome
from agentgate_core.contracts.tool_guard import RawToolCall, ScheduleDisposition


class GuardedToolCall(VersionedContract):
    tool_call_id: Identifier
    action: ActionIR | None = None
    schedule_disposition: ScheduleDisposition
    outcome: DecisionOutcome
    should_execute: bool
    reason: HumanText
    effect_ids: tuple[Identifier, ...] = ()
    coordinator_result: CoordinatorResult | None = None
    tool_result_status: JsonObject | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        require_unique(self.effect_ids, "effect_ids")
        if self.should_execute and (self.action is None or self.outcome is not DecisionOutcome.ALLOW):
            raise ValueError("executable calls require an allowed normalized action")
        return self


class GuardedToolBatch(VersionedContract):
    task_id: Identifier
    state_version: int = Field(ge=1)
    proposed_calls: tuple[RawToolCall, ...] = Field(min_length=1)
    decisions: tuple[GuardedToolCall, ...] = Field(min_length=1)
    requires_state_refresh: bool

    @model_validator(mode="after")
    def validate_batch(self) -> Self:
        proposed_ids = tuple(item.tool_call_id for item in self.proposed_calls)
        decision_ids = tuple(item.tool_call_id for item in self.decisions)
        require_unique(proposed_ids, "proposed tool_call_ids")
        if proposed_ids != decision_ids:
            raise ValueError("every proposed call must have one ordered guarded decision")
        return self


class ToolExecutionReport(VersionedContract):
    task_id: Identifier
    action_id: Identifier
    executed: bool
    success: bool
    result: JsonObject = Field(default_factory=dict)
    error_code: Identifier | None = None
    error_message: HumanText | None = None

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if not self.executed and self.success:
            raise ValueError("a non-executed action cannot report success")
        if self.success and (self.error_code or self.error_message):
            raise ValueError("successful execution cannot include an error")
        if not self.success and self.executed and self.error_message is None:
            raise ValueError("failed execution requires error_message")
        return self


__all__ = ["GuardedToolBatch", "GuardedToolCall", "ToolExecutionReport"]
