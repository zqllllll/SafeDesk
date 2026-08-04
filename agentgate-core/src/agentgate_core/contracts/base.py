"""Shared validation primitives for versioned AgentGate contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, StringConstraints

SCHEMA_VERSION = "1.0"

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
HumanText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000)]
JsonObject = dict[str, JsonValue]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(ge=1)]


def utc_now() -> datetime:
    """Return an aware UTC timestamp for contract default factories."""

    return datetime.now(UTC)


def require_unique(values: tuple[str, ...], field_name: str) -> None:
    """Reject repeated identifiers where order is meaningful but duplication is not."""

    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicate values")


class ContractModel(BaseModel):
    """Base for nested values that must reject drift and accidental mutation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


class VersionedContract(ContractModel):
    """Base for persisted top-level contracts with an explicit protocol version."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION


__all__ = [
    "AwareDatetime",
    "ContractModel",
    "HumanText",
    "Identifier",
    "JsonObject",
    "JsonValue",
    "NonNegativeInt",
    "PositiveInt",
    "Probability",
    "SCHEMA_VERSION",
    "VersionedContract",
    "require_unique",
    "utc_now",
]
