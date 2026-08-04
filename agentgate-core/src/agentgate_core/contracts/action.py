"""Normalized action and expected side-effect schemas."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from agentgate_core.contracts.base import (
    ContractModel,
    HumanText,
    Identifier,
    JsonObject,
    NonNegativeInt,
    VersionedContract,
    require_unique,
)


class ActorKind(StrEnum):
    USER = "user"
    LEAD_AGENT = "lead_agent"
    SUBAGENT = "subagent"
    RUNTIME = "runtime"
    RECOVERY_CONTROLLER = "recovery_controller"


class ActionKind(StrEnum):
    READ = "read"
    WRITE = "write"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EffectKind(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    SEND = "send"
    SUBMIT = "submit"
    SESSION = "session"
    OTHER = "other"


class ResourceRef(ContractModel):
    resource_type: Identifier
    resource_id: Identifier | None = None
    scope: Identifier | None = None


class ExpectedEffect(ContractModel):
    effect_key: Identifier
    kind: EffectKind
    resource: ResourceRef
    expected_change: JsonObject = Field(default_factory=dict)


class ActionIR(VersionedContract):
    action_id: Identifier
    task_id: Identifier
    actor: ActorKind
    kind: ActionKind
    tool_name: Identifier
    operation: Identifier
    resource: ResourceRef | None = None
    arguments: JsonObject = Field(default_factory=dict)
    expected_effects: tuple[ExpectedEffect, ...] = ()
    required_evidence_ids: tuple[Identifier, ...] = ()
    dependency_action_ids: tuple[Identifier, ...] = ()
    idempotency_key: Identifier | None = None
    risk_level: RiskLevel = RiskLevel.LOW
    tool_schema_version: Identifier
    source_turn: NonNegativeInt
    rationale: HumanText | None = None

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        effect_keys = tuple(item.effect_key for item in self.expected_effects)
        require_unique(effect_keys, "expected effect keys")
        require_unique(self.required_evidence_ids, "required_evidence_ids")
        require_unique(self.dependency_action_ids, "dependency_action_ids")
        if self.action_id in self.dependency_action_ids:
            raise ValueError("an action cannot depend on itself")

        if self.kind is ActionKind.WRITE:
            if not self.idempotency_key:
                raise ValueError("write actions require an idempotency_key")
            if not self.expected_effects:
                raise ValueError("write actions require at least one expected effect")
        else:
            if self.idempotency_key is not None:
                raise ValueError("read actions must not reserve an idempotency_key")
            if self.expected_effects:
                raise ValueError("read actions must not declare side effects")
        return self
