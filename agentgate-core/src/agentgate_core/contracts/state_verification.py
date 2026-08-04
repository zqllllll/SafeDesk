"""Public contracts for deterministic state, verification, and completion control."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from agentgate_core.contracts.base import (
    AwareDatetime,
    HumanText,
    Identifier,
    JsonObject,
    NonNegativeInt,
    PositiveInt,
    VersionedContract,
    require_unique,
    utc_now,
)
from agentgate_core.contracts.decision import FeatureMode
from agentgate_core.contracts.task import SubgoalStatus, TaskPhase
from agentgate_core.contracts.verification import ObservedEffect


class SubgoalTransitionRequest(VersionedContract):
    task_id: Identifier
    subgoal_id: Identifier
    target_status: SubgoalStatus
    reason: HumanText
    source_event_id: Identifier | None = None
    evidence_ids: tuple[Identifier, ...] = ()
    effect_ids: tuple[Identifier, ...] = ()
    verification_ids: tuple[Identifier, ...] = ()
    failure_ids: tuple[Identifier, ...] = ()
    blocker_ids: tuple[Identifier, ...] = ()
    turn: NonNegativeInt = 0

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        for field_name in ("evidence_ids", "effect_ids", "verification_ids", "failure_ids", "blocker_ids"):
            require_unique(getattr(self, field_name), field_name)
        return self


class VerifierSpec(VersionedContract):
    verifier_name: Identifier
    verifier_version: Identifier
    resource_types: tuple[Identifier, ...] = Field(min_length=1)
    expected_fields: tuple[Identifier, ...] = ()
    ignored_fields: tuple[Identifier, ...] = ()
    forbidden_fields: tuple[Identifier, ...] = ()
    eventual_consistency_delay_ms: NonNegativeInt = 0
    max_attempts: PositiveInt = 1
    require_exact_resource_id: bool = True
    adapter_config: JsonObject = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_fields(self) -> Self:
        for field_name in ("resource_types", "expected_fields", "ignored_fields", "forbidden_fields"):
            require_unique(getattr(self, field_name), field_name)
        overlap = set(self.expected_fields) & set(self.ignored_fields)
        if overlap:
            raise ValueError(f"fields cannot be both expected and ignored: {sorted(overlap)}")
        overlap = set(self.expected_fields) & set(self.forbidden_fields)
        if overlap:
            raise ValueError(f"fields cannot be both expected and forbidden: {sorted(overlap)}")
        return self


class VerificationObservation(VersionedContract):
    task_id: Identifier
    effect_id: Identifier
    source_event_id: Identifier
    observed_state: JsonObject | None = None
    unintended_effects: tuple[ObservedEffect, ...] = ()
    error_code: Identifier | None = None
    error_message: HumanText | None = None
    observed_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        if self.error_code is None and self.error_message is not None:
            raise ValueError("error_message requires error_code")
        if self.error_code is not None and self.error_message is None:
            raise ValueError("error_code requires error_message")
        return self


class CompletionBlockerType(StrEnum):
    MISSING_TASK_CONTRACT = "missing_task_contract"
    REQUIRED_SUBGOAL_NOT_VERIFIED = "required_subgoal_not_verified"
    COMPLETION_CONDITION_NOT_VERIFIED = "completion_condition_not_verified"
    EFFECT_NOT_VERIFIED = "effect_not_verified"
    UNRESOLVED_FAILURE = "unresolved_failure"
    PENDING_CONFIRMATION = "pending_confirmation"
    PENDING_APPROVAL = "pending_approval"
    EVIDENCE_CONFLICT = "evidence_conflict"
    UNINTENDED_EFFECT = "unintended_effect"
    DUPLICATE_IRREVERSIBLE_EFFECT = "duplicate_irreversible_effect"


class CompletionBlocker(VersionedContract):
    blocker_type: CompletionBlockerType
    reference_id: Identifier
    explanation: HumanText


class CompletionGateDecision(VersionedContract):
    decision_id: Identifier
    task_id: Identifier
    run_id: Identifier
    mode: FeatureMode
    proposed_allowed: bool
    effective_allowed: bool
    blockers: tuple[CompletionBlocker, ...] = ()
    evidence_ids: tuple[Identifier, ...] = ()
    recommended_phase: TaskPhase
    checked_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        require_unique(self.evidence_ids, "evidence_ids")
        if self.proposed_allowed == bool(self.blockers):
            raise ValueError("proposed_allowed must be true exactly when blockers are empty")
        expected_effective = self.proposed_allowed if self.mode is FeatureMode.ENFORCE else True
        if self.effective_allowed is not expected_effective:
            raise ValueError("effective_allowed does not match feature-mode semantics")
        return self


class ResponseClaimType(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    UNKNOWN = "unknown"
    WAITING_CONFIRMATION = "waiting_confirmation"
    WAITING_APPROVAL = "waiting_approval"


class ResponseClaim(VersionedContract):
    claim_id: Identifier
    claim_type: ResponseClaimType
    text: HumanText
    start_offset: NonNegativeInt
    end_offset: PositiveInt

    @model_validator(mode="after")
    def validate_offsets(self) -> Self:
        if self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be greater than start_offset")
        return self


class ResponseGroundingDecision(VersionedContract):
    decision_id: Identifier
    task_id: Identifier
    run_id: Identifier
    original_response: HumanText
    grounded_response: HumanText
    requested_status: ResponseClaimType
    grounded_status: ResponseClaimType
    claims: tuple[ResponseClaim, ...] = ()
    completion_decision_id: Identifier | None = None
    evidence_ids: tuple[Identifier, ...] = ()
    downgraded: bool = False
    reason: HumanText
    checked_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_grounding(self) -> Self:
        require_unique(tuple(claim.claim_id for claim in self.claims), "claim ids")
        require_unique(self.evidence_ids, "evidence_ids")
        if self.downgraded == (self.requested_status is self.grounded_status):
            raise ValueError("downgraded must reflect whether the response status changed")
        return self


__all__ = [
    "CompletionBlocker",
    "CompletionBlockerType",
    "CompletionGateDecision",
    "ResponseClaim",
    "ResponseClaimType",
    "ResponseGroundingDecision",
    "SubgoalTransitionRequest",
    "VerificationObservation",
    "VerifierSpec",
]
