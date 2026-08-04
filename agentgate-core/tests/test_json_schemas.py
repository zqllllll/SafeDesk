from __future__ import annotations

from importlib.resources import files

import pytest

from agentgate_core.contracts.schema import CONTRACT_MODELS, render_schema, schema_for


@pytest.mark.parametrize("contract_name", sorted(CONTRACT_MODELS))
def test_checked_in_schema_matches_model(contract_name: str) -> None:
    schema_file = files("agentgate_core.schemas.v1").joinpath(f"{contract_name}.schema.json")
    assert schema_file.read_text(encoding="utf-8") == render_schema(contract_name)


def test_schema_has_stable_versioned_identity() -> None:
    schema = schema_for("trace-event")
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "https://schemas.safedesk.dev/agentgate/v1/trace-event.schema.json"
    assert schema["properties"]["schema_version"]["const"] == "1.0"


def test_unknown_contract_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown contract"):
        schema_for("not-a-contract")
