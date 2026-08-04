"""Contracts for typed recovery, progress, stagnation, and budgets."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from agentgate_core.contracts.action import ActionIR
from agentgate_core.contracts.base import (
    HumanText,
    Identifier,
    JsonObject,
    NonNegativeInt,
    PositiveInt,
    VersionedContract,
    require_unique,
)
from agentgate_core.contracts.decision import DecisionOutcome
from agentgate_core.contracts.failure import FailureType
from agentgate_core.contracts.task import TaskPhase
from agentgate_core.contracts.verification import VerificationStatus


class RecoveryState(StrEnum):
    DETECTED = "detected"
    CLASSIFIED = "classified"
    STATE_REFRESHED = "state_refreshed"
    PLANNED = "planned"
    POLICY_CHECKED = "policy_checked"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    RECOVERED = "recovered"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"


class RecoveryStrategyType(StrEnum):
    COLLECT_EVIDENCE = "collect_evidence"
    REPAIR_ARGUMENTS = "repair_arguments"
    RESOLVE_TOOL = "resolve_tool"
    RELOCATE_RESOURCE = "relocate_resource"
    RESCHEDULE = "reschedule"
    REQUEST_CONFIRMATION = "request_confirmation"
    REQUEST_APPROVAL = "request_approval"
    VERIFY_BEFORE_RETRY = "verify_before_retry"
    REPAIR_VERIFICATION = "repair_verification"
    COMPLETE_MISSING_SUBGOAL = "complete_missing_subgoal"
    REBUILD_CONTEXT = "rebuild_context"
    INFRASTRUCTURE_RETRY = "infrastructure_retry"
    STOP = "stop"


class FailureSignal(VersionedContract):
    task_id: Identifier
    action: ActionIR | None = None
    guard_outcome: DecisionOutcome | None = None
    guard_reason_code: Identifier | None = None
    tool_error_code: Identifier | None = None
    tool_error_message: HumanText | None = None
    verification_status: VerificationStatus | None = None
    verification_id: Identifier | None = None
    state_changed: bool = False
    evidence_changed: bool = False
    repeated_call: bool = False
    infrastructure_error: bool = False
    metadata: JsonObject = Field(default_factory=dict)


class ToolCallFingerprint(VersionedContract):
    action_id: Identifier
    fingerprint: Identifier
    normalized_tool_name: Identifier
    normalized_resource: Identifier


class ProgressSignal(VersionedContract):
    task_id: Identifier
    turn: NonNegativeInt
    new_verified_evidence: NonNegativeInt = 0
    state_transitions: NonNegativeInt = 0
    new_verified_effects: NonNegativeInt = 0
    resolved_failures: NonNegativeInt = 0
    new_resource_ids: NonNegativeInt = 0
    completion_conditions_gained: NonNegativeInt = 0
    token_growth: NonNegativeInt = 0

    @property
    def score(self) -> int:
        return (
            self.new_verified_evidence
            + self.state_transitions
            + self.new_verified_effects
            + self.resolved_failures
            + self.new_resource_ids
            + self.completion_conditions_gained
        )


class StagnationConfig(VersionedContract):
    stagnation_window: PositiveInt = 4
    max_identical_calls: PositiveInt = 3
    max_same_failure: PositiveInt = 3
    max_recovery_attempts_per_type: PositiveInt = 3
    max_total_recovery_attempts: PositiveInt = 10
    max_recovery_tokens: PositiveInt = 20_000


class StagnationAssessment(VersionedContract):
    task_id: Identifier
    stagnant: bool
    reason_codes: tuple[Identifier, ...] = ()
    consecutive_no_progress_turns: NonNegativeInt = 0
    identical_call_count: NonNegativeInt = 0
    same_failure_count: NonNegativeInt = 0
    recommended_outcome: DecisionOutcome

    @model_validator(mode="after")
    def validate_reasons(self) -> Self:
        require_unique(self.reason_codes, "reason_codes")
        if self.stagnant != bool(self.reason_codes):
            raise ValueError("stagnant must reflect whether reason_codes are present")
        return self


class RecoveryBudgetState(VersionedContract):
    task_id: Identifier
    max_total_attempts: PositiveInt
    max_attempts_per_type: PositiveInt
    max_tokens: PositiveInt
    total_attempts_used: NonNegativeInt = 0
    attempts_by_type: dict[FailureType, NonNegativeInt] = Field(default_factory=dict)
    tokens_used: NonNegativeInt = 0

    @property
    def exhausted(self) -> bool:
        return self.total_attempts_used >= self.max_total_attempts or self.tokens_used >= self.max_tokens


class RecoveryPlan(VersionedContract):
    plan_id: Identifier
    task_id: Identifier
    failure_id: Identifier
    failure_type: FailureType
    strategy_type: RecoveryStrategyType
    state: RecoveryState = RecoveryState.PLANNED
    recommended_phase: TaskPhase
    repaired_action: ActionIR | None = None
    required_evidence_ids: tuple[Identifier, ...] = ()
    instructions: tuple[HumanText, ...] = ()
    verify_before_execution: bool = False
    reason: HumanText

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        require_unique(self.required_evidence_ids, "required_evidence_ids")
        return self


class RecoveryResult(VersionedContract):
    plan_id: Identifier
    task_id: Identifier
    failure_id: Identifier
    final_state: RecoveryState
    success: bool
    progress_before: NonNegativeInt
    progress_after: NonNegativeInt
    verification_id: Identifier | None = None
    reason: HumanText

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.success != (self.final_state is RecoveryState.RECOVERED):
            raise ValueError("success is true exactly for RECOVERED results")
        return self


class RecoveryTermination(VersionedContract):
    task_id: Identifier
    failure_id: Identifier
    response: HumanText
    final_state: RecoveryState
    retry_allowed: bool = False


__all__ = [
    "FailureSignal",
    "ProgressSignal",
    "RecoveryBudgetState",
    "RecoveryPlan",
    "RecoveryResult",
    "RecoveryState",
    "RecoveryStrategyType",
    "RecoveryTermination",
    "StagnationAssessment",
    "StagnationConfig",
    "ToolCallFingerprint",
]
