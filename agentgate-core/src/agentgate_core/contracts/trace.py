"""Append-only trace event protocol for runtime replay and audit."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from agentgate_core.contracts.base import (
    AwareDatetime,
    ContractModel,
    Identifier,
    JsonObject,
    NonNegativeInt,
    PositiveInt,
    VersionedContract,
    require_unique,
    utc_now,
)


class TraceEventType(StrEnum):
    TASK_CREATED = "task_created"
    TASK_STATE_CHANGED = "task_state_changed"
    CONTEXT_BUILT = "context_built"
    MODEL_RESPONSE = "model_response"
    ACTION_PROPOSED = "action_proposed"
    STAGE_DECISION = "stage_decision"
    SCHEMA_DECISION = "schema_decision"
    POLICY_DECISION = "policy_decision"
    TOOL_RESOLUTION = "tool_resolution"
    TOOL_SET_CHANGED = "tool_set_changed"
    EFFECT_RESERVED = "effect_reserved"
    EFFECT_LINKED = "effect_linked"
    EFFECT_STATUS_CHANGED = "effect_status_changed"
    EVIDENCE_RECORDED = "evidence_recorded"
    EVIDENCE_STATUS_CHANGED = "evidence_status_changed"
    EVIDENCE_CONFLICTED = "evidence_conflicted"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    TOOL_NOT_EXECUTED = "tool_not_executed"
    VERIFICATION_FINISHED = "verification_finished"
    FAILURE_CLASSIFIED = "failure_classified"
    PROGRESS_ASSESSED = "progress_assessed"
    STAGNATION_DETECTED = "stagnation_detected"
    RECOVERY_PLANNED = "recovery_planned"
    RECOVERY_FINISHED = "recovery_finished"
    COMPLETION_DECISION = "completion_decision"
    RESPONSE_GROUNDING_DECISION = "response_grounding_decision"
    CHECKPOINT_CREATED = "checkpoint_created"
    CHECKPOINT_RESTORED = "checkpoint_restored"
    FINAL_RESPONSE = "final_response"


class TraceActor(StrEnum):
    USER = "user"
    MODEL = "model"
    RUNTIME = "runtime"
    TOOL = "tool"
    SCHEMA_GUARD = "schema_guard"
    POLICY_GATE = "policy_gate"
    SCHEDULER = "scheduler"
    VERIFIER = "verifier"
    RECOVERY_CONTROLLER = "recovery_controller"
    CONTEXT_MANAGER = "context_manager"


class RedactionMetadata(ContractModel):
    redacted: bool = False
    redacted_fields: tuple[Identifier, ...] = ()
    policy_version: Identifier | None = None

    @model_validator(mode="after")
    def validate_redaction(self) -> Self:
        require_unique(self.redacted_fields, "redacted_fields")
        if not self.redacted and self.redacted_fields:
            raise ValueError("redacted_fields require redacted=true")
        return self


class TraceEvent(VersionedContract):
    event_id: Identifier
    task_id: Identifier
    run_id: Identifier
    sequence_number: NonNegativeInt
    turn: NonNegativeInt
    timestamp: AwareDatetime = Field(default_factory=utc_now)
    event_type: TraceEventType
    actor: TraceActor
    parent_event_id: Identifier | None = None
    correlation_id: Identifier
    state_version: PositiveInt | None = None
    payload: JsonObject = Field(default_factory=dict)
    redaction_metadata: RedactionMetadata = Field(default_factory=RedactionMetadata)

    @model_validator(mode="after")
    def validate_relationships(self) -> Self:
        if self.parent_event_id == self.event_id:
            raise ValueError("a trace event cannot be its own parent")
        return self
