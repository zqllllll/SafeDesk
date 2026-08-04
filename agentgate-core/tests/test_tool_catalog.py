from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agentgate_core.contracts import (
    ActionKind,
    EffectKind,
    RiskLevel,
    ToolCatalogEntry,
    ToolCatalogSnapshot,
    compute_tool_schema_version,
)
from agentgate_core.tool_execution_guard import InMemoryToolCatalog, ToolCatalog, ToolNotFoundError

NOW = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)


def read_entry() -> ToolCatalogEntry:
    return ToolCatalogEntry(
        name="read_file",
        description="Read one text file.",
        operation="read_file",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        action_kind=ActionKind.READ,
        risk_level=RiskLevel.LOW,
        resource_types=("file",),
    )


def write_entry() -> ToolCatalogEntry:
    return ToolCatalogEntry(
        name="write_file",
        description="Write one text file.",
        operation="write_file",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        action_kind=ActionKind.WRITE,
        risk_level=RiskLevel.HIGH,
        side_effect_type=EffectKind.UPDATE,
        resource_types=("file",),
        required_evidence=("current_file_version",),
        dependency_tool_names=("read_file",),
        verification_strategy="file_content_readback",
        idempotency_strategy="canonical_selected_arguments",
    )


def test_schema_version_is_deterministic_for_semantically_equal_json() -> None:
    first = compute_tool_schema_version(
        {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}},
        None,
    )
    second = compute_tool_schema_version(
        {"properties": {"limit": {"type": "integer"}, "path": {"type": "string"}}, "type": "object"},
        None,
    )
    assert first == second
    assert first.startswith("sha256:")
    assert read_entry().tool_schema_version == compute_tool_schema_version(read_entry().input_schema, None)


def test_write_entry_requires_explicit_execution_semantics() -> None:
    payload = write_entry().model_dump(mode="python")
    payload["verification_strategy"] = None
    with pytest.raises(ValidationError, match="verification_strategy"):
        ToolCatalogEntry.model_validate(payload)

    read_payload = read_entry().model_dump(mode="python")
    read_payload["side_effect_type"] = EffectKind.UPDATE
    with pytest.raises(ValidationError, match="read tools cannot declare side effects"):
        ToolCatalogEntry.model_validate(read_payload)


def test_snapshot_rejects_unknown_tool_dependencies() -> None:
    with pytest.raises(ValidationError, match="unknown dependencies"):
        ToolCatalogSnapshot(
            catalog_version="catalog-1",
            entries=(write_entry(),),
            created_at=NOW,
        )


def test_in_memory_catalog_is_read_only_and_structural() -> None:
    snapshot = ToolCatalogSnapshot(
        catalog_version="catalog-1",
        entries=(read_entry(), write_entry()),
        created_at=NOW,
    )
    catalog = InMemoryToolCatalog(snapshot)
    assert isinstance(catalog, ToolCatalog)
    assert catalog.catalog_version == "catalog-1"
    assert catalog.get_tool("write_file") == write_entry()
    assert catalog.snapshot() == snapshot

    returned = catalog.get_tool("read_file")
    returned.input_schema["mutated"] = True
    assert "mutated" not in catalog.get_tool("read_file").input_schema

    with pytest.raises(ToolNotFoundError):
        catalog.get_tool("missing")
