"""Unified feature modes and stage decisions for AgentGate coordination."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from agentgate_core.contracts.action import ActionIR
from agentgate_core.contracts.base import (
    ContractModel,
    HumanText,
    Identifier,
    JsonObject,
    NonNegativeInt,
    VersionedContract,
    require_unique,
)


class FeatureMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    ENFORCE = "enforce"


class FeatureName(StrEnum):
    STATE_VERIFICATION = "state_verification"
    TOOL_EXECUTION_GUARD = "tool_execution_guard"
    RECOVERY_CONTROLLER = "recovery_controller"
    CONTEXT_MANAGER = "context_manager"


class PipelineStage(StrEnum):
    TASK_STATE = "task_state"
    SCHEMA_GUARD = "schema_guard"
    DEPENDENCY_SCHEDULER = "dependency_scheduler"
    POLICY_GATE = "policy_gate"
    EFFECT_PREFLIGHT = "effect_preflight"
    POST_ACTION_VERIFICATION = "post_action_verification"
    FAILURE_CLASSIFIER = "failure_classifier"
    COMPLETION_GATE = "completion_gate"
    RESPONSE_GROUNDING = "response_grounding"
    CONTEXT_BUILDER = "context_builder"


class DecisionOutcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REPLAN = "replan"
    REQUIRE_EVIDENCE = "require_evidence"
    REQUIRE_CONFIRMATION = "require_confirmation"
    REQUIRE_APPROVAL = "require_approval"
    ALREADY_APPLIED = "already_applied"
    DEFER = "defer"


class StageEvaluation(ContractModel):
    outcome: DecisionOutcome
    reason_code: Identifier
    explanation: HumanText
    evidence_ids: tuple[Identifier, ...] = ()
    payload: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_evidence_ids(self) -> Self:
        require_unique(self.evidence_ids, "evidence_ids")
        return self


class ActionEvaluationContext(VersionedContract):
    task_id: Identifier
    run_id: Identifier
    turn: NonNegativeInt
    action: ActionIR
    state_version: int | None = Field(default=None, ge=1)
    metadata: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_action_task(self) -> Self:
        if self.action.task_id != self.task_id:
            raise ValueError("context and action must belong to the same task")
        return self


class GateDecision(VersionedContract):
    decision_id: Identifier
    task_id: Identifier
    action_id: Identifier
    feature: FeatureName
    stage: PipelineStage
    mode: FeatureMode
    proposed_outcome: DecisionOutcome
    effective_outcome: DecisionOutcome
    reason_code: Identifier
    explanation: HumanText
    evidence_ids: tuple[Identifier, ...] = ()
    payload: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_mode_semantics(self) -> Self:
        require_unique(self.evidence_ids, "evidence_ids")
        if self.mode in {FeatureMode.OFF, FeatureMode.SHADOW}:
            if self.effective_outcome is not DecisionOutcome.ALLOW:
                raise ValueError("off and shadow decisions must be behavior-preserving ALLOW decisions")
        elif self.effective_outcome is not self.proposed_outcome:
            raise ValueError("enforced decisions must apply the proposed outcome")
        return self


class CoordinatorResult(VersionedContract):
    task_id: Identifier
    run_id: Identifier
    action_id: Identifier
    outcome: DecisionOutcome
    decisions: tuple[GateDecision, ...] = ()
    stopped_at: PipelineStage | None = None
    trace_event_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        require_unique(self.trace_event_ids, "trace_event_ids")
        for decision in self.decisions:
            if decision.task_id != self.task_id or decision.action_id != self.action_id:
                raise ValueError("all decisions must belong to the result task and action")
        expected_outcome = DecisionOutcome.ALLOW
        expected_stop = None
        for decision in self.decisions:
            if decision.effective_outcome is not DecisionOutcome.ALLOW:
                expected_outcome = decision.effective_outcome
                expected_stop = decision.stage
                break
        if self.outcome is not expected_outcome or self.stopped_at is not expected_stop:
            raise ValueError("coordinator outcome must match the first effective non-ALLOW decision")
        return self


__all__ = [
    "ActionEvaluationContext",
    "CoordinatorResult",
    "DecisionOutcome",
    "FeatureMode",
    "FeatureName",
    "GateDecision",
    "PipelineStage",
    "StageEvaluation",
]
