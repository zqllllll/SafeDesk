"""Explicit DeerFlow tool semantics and argument-to-resource bindings."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from agentgate_core.contracts import ActionKind, EffectKind, RiskLevel
from agentgate_core.contracts.base import ContractModel, Identifier, require_unique


class ArgumentProjection(StrEnum):
    VALUE = "value"
    SHA256 = "sha256"


class ExpectedChangeBinding(ContractModel):
    output_field: Identifier
    argument_path: tuple[Identifier, ...] = Field(min_length=1)
    projection: ArgumentProjection = ArgumentProjection.VALUE


class DeerFlowToolProfile(ContractModel):
    tool_name: Identifier
    operation: Identifier
    action_kind: ActionKind
    risk_level: RiskLevel
    side_effect_type: EffectKind | None = None
    resource_type: Identifier | None = None
    resource_id_path: tuple[Identifier, ...] | None = None
    scope_path: tuple[Identifier, ...] | None = None
    default_scope: Identifier | None = None
    expected_change_bindings: tuple[ExpectedChangeBinding, ...] = ()
    idempotency_paths: tuple[tuple[Identifier, ...], ...] = ()
    required_evidence: tuple[Identifier, ...] = ()
    required_policy: tuple[Identifier, ...] = ()
    dependency_tool_names: tuple[Identifier, ...] = ()
    verification_strategy: Identifier | None = None
    idempotency_strategy: Identifier | None = None

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        for field_name in ("required_evidence", "required_policy", "dependency_tool_names"):
            require_unique(getattr(self, field_name), field_name)
        output_fields = tuple(binding.output_field for binding in self.expected_change_bindings)
        require_unique(output_fields, "expected change output fields")
        if len(self.idempotency_paths) != len(set(self.idempotency_paths)):
            raise ValueError("idempotency_paths must not contain duplicates")
        for path_name, path in (("resource_id_path", self.resource_id_path), ("scope_path", self.scope_path)):
            if path is not None and not path:
                raise ValueError(f"{path_name} cannot be empty")
        if self.action_kind is ActionKind.WRITE:
            if self.side_effect_type is None:
                raise ValueError("write profiles must declare side_effect_type")
            if self.resource_type is None:
                raise ValueError("write profiles must declare resource_type")
            if self.verification_strategy is None:
                raise ValueError("write profiles must declare verification_strategy")
            if self.idempotency_strategy is None:
                raise ValueError("write profiles must declare idempotency_strategy")
        else:
            if self.side_effect_type is not None:
                raise ValueError("read profiles cannot declare side effects")
            if self.idempotency_strategy is not None:
                raise ValueError("read profiles cannot declare idempotency_strategy")
            if self.expected_change_bindings or self.idempotency_paths:
                raise ValueError("read profiles cannot declare write-effect bindings")
        return self


__all__ = ["ArgumentProjection", "DeerFlowToolProfile", "ExpectedChangeBinding"]
