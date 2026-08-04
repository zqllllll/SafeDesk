"""Framework-independent tool catalog contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Self

from pydantic import Field, model_validator

from agentgate_core.contracts.action import ActionKind, EffectKind, RiskLevel
from agentgate_core.contracts.base import (
    AwareDatetime,
    HumanText,
    Identifier,
    JsonObject,
    VersionedContract,
    require_unique,
    utc_now,
)


def compute_tool_schema_version(input_schema: JsonObject, output_schema: JsonObject | None) -> str:
    """Return a deterministic fingerprint for the schemas actually bound to a model."""

    canonical = json.dumps(
        {"input_schema": input_schema, "output_schema": output_schema},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


class ToolCatalogEntry(VersionedContract):
    name: Identifier
    description: HumanText
    operation: Identifier
    input_schema: JsonObject
    output_schema: JsonObject | None = None
    action_kind: ActionKind
    risk_level: RiskLevel
    side_effect_type: EffectKind | None = None
    resource_types: tuple[Identifier, ...] = ()
    required_evidence: tuple[Identifier, ...] = ()
    required_policy: tuple[Identifier, ...] = ()
    dependency_tool_names: tuple[Identifier, ...] = ()
    verification_strategy: Identifier | None = None
    idempotency_strategy: Identifier | None = None

    @property
    def tool_schema_version(self) -> str:
        return compute_tool_schema_version(self.input_schema, self.output_schema)

    @model_validator(mode="after")
    def validate_semantics(self) -> Self:
        for field_name in (
            "resource_types",
            "required_evidence",
            "required_policy",
            "dependency_tool_names",
        ):
            require_unique(getattr(self, field_name), field_name)
        if self.name in self.dependency_tool_names:
            raise ValueError("a tool cannot depend on itself")
        if self.action_kind is ActionKind.WRITE:
            if self.side_effect_type is None:
                raise ValueError("write tools must declare side_effect_type")
            if not self.resource_types:
                raise ValueError("write tools must declare at least one resource_type")
            if self.idempotency_strategy is None:
                raise ValueError("write tools must declare idempotency_strategy")
            if self.verification_strategy is None:
                raise ValueError("write tools must declare verification_strategy")
        else:
            if self.side_effect_type is not None:
                raise ValueError("read tools cannot declare side effects")
            if self.idempotency_strategy is not None:
                raise ValueError("read tools cannot declare an idempotency strategy")
        return self


class ToolCatalogSnapshot(VersionedContract):
    catalog_version: Identifier
    entries: tuple[ToolCatalogEntry, ...] = Field(min_length=1)
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_catalog(self) -> Self:
        names = tuple(entry.name for entry in self.entries)
        require_unique(names, "tool names")
        known_names = set(names)
        for entry in self.entries:
            unknown = set(entry.dependency_tool_names) - known_names
            if unknown:
                raise ValueError(f"tool {entry.name} has unknown dependencies: {sorted(unknown)}")
        return self


__all__ = [
    "ToolCatalogEntry",
    "ToolCatalogSnapshot",
    "compute_tool_schema_version",
]
