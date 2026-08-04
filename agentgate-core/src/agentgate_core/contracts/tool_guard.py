"""Contracts for tool normalization, scheduling, policy, and resolution."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from agentgate_core.contracts.action import ActionKind, ActorKind, RiskLevel
from agentgate_core.contracts.base import (
    HumanText,
    Identifier,
    JsonObject,
    JsonValue,
    NonNegativeInt,
    PositiveInt,
    VersionedContract,
    require_unique,
)
from agentgate_core.contracts.decision import DecisionOutcome, FeatureMode
from agentgate_core.contracts.effect import EffectStatus


class RawToolCall(VersionedContract):
    tool_call_id: Identifier
    task_id: Identifier
    tool_name: Identifier
    arguments: JsonObject = Field(default_factory=dict)
    actor: ActorKind = ActorKind.LEAD_AGENT
    source_turn: NonNegativeInt
    rationale: HumanText | None = None


class SchemaViolationCode(StrEnum):
    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_NOT_ACTIVE = "tool_not_active"
    SCHEMA_VERSION_MISMATCH = "schema_version_mismatch"
    REQUIRED = "required"
    TYPE = "type"
    ENUM = "enum"
    CONST = "const"
    RANGE = "range"
    LENGTH = "length"
    PATTERN = "pattern"
    ADDITIONAL_PROPERTY = "additional_property"
    COMPOSITION = "composition"


class SchemaViolation(VersionedContract):
    code: SchemaViolationCode
    path: Identifier
    message: HumanText
    expected: JsonValue = None
    observed: JsonValue = None


class ActiveToolSet(VersionedContract):
    task_id: Identifier
    set_version: PositiveInt
    catalog_version: Identifier
    tool_names: tuple[Identifier, ...] = Field(min_length=1)
    schema_versions: dict[Identifier, Identifier]
    reason: HumanText

    @model_validator(mode="after")
    def validate_set(self) -> Self:
        require_unique(self.tool_names, "tool_names")
        if set(self.schema_versions) != set(self.tool_names):
            raise ValueError("schema_versions must exactly cover active tool_names")
        return self


class EffectPreflightOutcome(StrEnum):
    RESERVE = "reserve"
    ALREADY_APPLIED = "already_applied"
    VERIFY_FIRST = "verify_first"
    RECOVERY_REQUIRED = "recovery_required"
    WAIT = "wait"


class EffectPreflightDecision(VersionedContract):
    task_id: Identifier
    action_id: Identifier
    outcome: EffectPreflightOutcome
    idempotency_key: Identifier
    existing_effect_id: Identifier | None = None
    existing_status: EffectStatus | None = None
    reason: HumanText

    @model_validator(mode="after")
    def validate_existing_effect(self) -> Self:
        if (self.existing_effect_id is None) != (self.existing_status is None):
            raise ValueError("existing_effect_id and existing_status must be provided together")
        if self.outcome is not EffectPreflightOutcome.RESERVE and self.existing_effect_id is None:
            raise ValueError("non-reserve outcomes require an existing effect")
        return self


class ScheduleDisposition(StrEnum):
    EXECUTE = "execute"
    DEFER = "defer"
    REPLAN = "replan"
    SUPPRESSED_PENDING_STATE_REFRESH = "suppressed_pending_state_refresh"


class ScheduledAction(VersionedContract):
    action_id: Identifier
    disposition: ScheduleDisposition
    execution_group: NonNegativeInt | None = None
    reason: HumanText
    unmet_action_ids: tuple[Identifier, ...] = ()
    unmet_evidence_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_schedule(self) -> Self:
        require_unique(self.unmet_action_ids, "unmet_action_ids")
        require_unique(self.unmet_evidence_ids, "unmet_evidence_ids")
        if (self.disposition is ScheduleDisposition.EXECUTE) != (self.execution_group is not None):
            raise ValueError("only executable actions may have an execution_group")
        return self


class ActionSchedule(VersionedContract):
    task_id: Identifier
    state_version: PositiveInt
    actions: tuple[ScheduledAction, ...] = Field(min_length=1)
    contains_write: bool
    requires_state_refresh: bool

    @model_validator(mode="after")
    def validate_actions(self) -> Self:
        require_unique(tuple(item.action_id for item in self.actions), "scheduled action ids")
        return self


class ArgumentOperator(StrEnum):
    EXISTS = "exists"
    EQUALS = "equals"
    IN = "in"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"


class ArgumentPolicy(VersionedContract):
    path: tuple[Identifier, ...] = Field(min_length=1)
    operator: ArgumentOperator
    expected: JsonValue = None
    message: HumanText


class PolicyRuleEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"
    REQUIRE_APPROVAL = "require_approval"


class PolicyRule(VersionedContract):
    rule_id: Identifier
    description: HumanText
    effect: PolicyRuleEffect
    tool_names: tuple[Identifier, ...] = ()
    actor_kinds: tuple[ActorKind, ...] = ()
    resource_types: tuple[Identifier, ...] = ()
    max_risk_level: RiskLevel | None = None
    required_identity: bool = False
    confirmation_id: Identifier | None = None
    approval_id: Identifier | None = None
    argument_policies: tuple[ArgumentPolicy, ...] = ()
    active: bool = True

    @model_validator(mode="after")
    def validate_rule(self) -> Self:
        for field_name in ("tool_names", "actor_kinds", "resource_types"):
            require_unique(tuple(str(item) for item in getattr(self, field_name)), field_name)
        if self.effect is PolicyRuleEffect.REQUIRE_CONFIRMATION and self.confirmation_id is None:
            raise ValueError("confirmation rules require confirmation_id")
        if self.effect is PolicyRuleEffect.REQUIRE_APPROVAL and self.approval_id is None:
            raise ValueError("approval rules require approval_id")
        return self


class PolicyEvaluationContext(VersionedContract):
    task_id: Identifier
    identity_verified: bool = False
    confirmation_ids: tuple[Identifier, ...] = ()
    approval_ids: tuple[Identifier, ...] = ()
    allowed_resource_scopes: tuple[Identifier, ...] = ()
    tool_call_count: NonNegativeInt = 0
    write_action_count: NonNegativeInt = 0
    max_tool_calls: PositiveInt | None = None
    max_write_actions: PositiveInt | None = None

    @model_validator(mode="after")
    def validate_ids(self) -> Self:
        for field_name in ("confirmation_ids", "approval_ids", "allowed_resource_scopes"):
            require_unique(getattr(self, field_name), field_name)
        return self


class PolicyDecision(VersionedContract):
    task_id: Identifier
    action_id: Identifier
    outcome: DecisionOutcome
    matched_rule_ids: tuple[Identifier, ...] = ()
    reason: HumanText

    @model_validator(mode="after")
    def validate_rules(self) -> Self:
        require_unique(self.matched_rule_ids, "matched_rule_ids")
        return self


class ToolRequirement(VersionedContract):
    requirement_id: Identifier
    task_id: Identifier
    description: HumanText
    operation_terms: tuple[Identifier, ...] = ()
    action_kind: ActionKind | None = None
    resource_types: tuple[Identifier, ...] = ()
    required_policy_ids: tuple[Identifier, ...] = ()
    excluded_tool_names: tuple[Identifier, ...] = ()
    max_candidates: PositiveInt = 5

    @model_validator(mode="after")
    def validate_requirement(self) -> Self:
        for field_name in (
            "operation_terms",
            "resource_types",
            "required_policy_ids",
            "excluded_tool_names",
        ):
            require_unique(getattr(self, field_name), field_name)
        return self


class ToolResolutionCandidate(VersionedContract):
    tool_name: Identifier
    score: NonNegativeInt
    matched_terms: tuple[Identifier, ...] = ()
    reason: HumanText


class ToolResolution(VersionedContract):
    requirement_id: Identifier
    task_id: Identifier
    mode: FeatureMode
    candidates: tuple[ToolResolutionCandidate, ...] = ()
    previous_set_version: PositiveInt
    resulting_tool_set: ActiveToolSet | None = None
    replan_required: bool = True


__all__ = [
    "ActionSchedule",
    "ActiveToolSet",
    "ArgumentOperator",
    "ArgumentPolicy",
    "EffectPreflightDecision",
    "EffectPreflightOutcome",
    "PolicyDecision",
    "PolicyEvaluationContext",
    "PolicyRule",
    "PolicyRuleEffect",
    "RawToolCall",
    "ScheduleDisposition",
    "ScheduledAction",
    "SchemaViolation",
    "SchemaViolationCode",
    "ToolRequirement",
    "ToolResolution",
    "ToolResolutionCandidate",
]
