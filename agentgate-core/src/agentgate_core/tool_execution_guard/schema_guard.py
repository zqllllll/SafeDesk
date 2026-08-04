"""Deterministic validation of actions against the active JSON tool schema."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from agentgate_core.contracts.decision import (
    ActionEvaluationContext,
    DecisionOutcome,
    FeatureName,
    PipelineStage,
    StageEvaluation,
)
from agentgate_core.contracts.tool_guard import SchemaViolation, SchemaViolationCode
from agentgate_core.tool_execution_guard.active_set import ActiveToolSetManager
from agentgate_core.tool_execution_guard.catalog import ToolCatalog, ToolNotFoundError


class ToolSchemaGuard:
    stage = PipelineStage.SCHEMA_GUARD
    feature = FeatureName.TOOL_EXECUTION_GUARD

    def __init__(self, catalog: ToolCatalog, active_sets: ActiveToolSetManager) -> None:
        self.catalog = catalog
        self.active_sets = active_sets

    def evaluate(self, context: ActionEvaluationContext) -> StageEvaluation:
        try:
            entry = self.catalog.get_tool(context.action.tool_name)
        except ToolNotFoundError:
            return _denied(
                SchemaViolation(
                    code=SchemaViolationCode.TOOL_NOT_FOUND,
                    path="tool_name",
                    message="The tool is absent from the public catalog.",
                    observed=context.action.tool_name,
                )
            )
        active = self.active_sets.get(context.task_id)
        if entry.name not in active.tool_names:
            return _denied(
                SchemaViolation(
                    code=SchemaViolationCode.TOOL_NOT_ACTIVE,
                    path="tool_name",
                    message="The tool is not present in the task's active tool set.",
                    observed=entry.name,
                ),
                outcome=DecisionOutcome.REPLAN,
            )
        expected_version = active.schema_versions[entry.name]
        if context.action.tool_schema_version != expected_version:
            return _denied(
                SchemaViolation(
                    code=SchemaViolationCode.SCHEMA_VERSION_MISMATCH,
                    path="tool_schema_version",
                    message="The action was generated from a stale tool schema.",
                    expected=expected_version,
                    observed=context.action.tool_schema_version,
                ),
                outcome=DecisionOutcome.REPLAN,
            )
        violations = validate_json_schema(context.action.arguments, entry.input_schema)
        if violations:
            return StageEvaluation(
                outcome=DecisionOutcome.DENY,
                reason_code="invalid_tool_arguments",
                explanation="Tool arguments do not satisfy the active public schema.",
                payload={"violations": [item.model_dump(mode="json") for item in violations]},
            )
        return StageEvaluation(
            outcome=DecisionOutcome.ALLOW,
            reason_code="tool_schema_valid",
            explanation="The tool exists, is active, and its arguments satisfy the bound schema.",
            payload={"active_tool_set_version": active.set_version},
        )


def validate_json_schema(value: Any, schema: Mapping[str, Any], path: str = "arguments") -> tuple[SchemaViolation, ...]:
    violations: list[SchemaViolation] = []
    if "allOf" in schema:
        for child in _schema_sequence(schema["allOf"]):
            violations.extend(validate_json_schema(value, child, path))
    if "anyOf" in schema:
        options = _schema_sequence(schema["anyOf"])
        if options and not any(not validate_json_schema(value, child, path) for child in options):
            violations.append(_violation(SchemaViolationCode.COMPOSITION, path, "Value matches no anyOf branch."))
    if "oneOf" in schema:
        options = _schema_sequence(schema["oneOf"])
        matches = sum(not validate_json_schema(value, child, path) for child in options)
        if matches != 1:
            violations.append(_violation(SchemaViolationCode.COMPOSITION, path, "Value must match one oneOf branch."))
    if "const" in schema and value != schema["const"]:
        violations.append(
            _violation(SchemaViolationCode.CONST, path, "Value differs from const.", schema["const"], value)
        )
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        violations.append(_violation(SchemaViolationCode.ENUM, path, "Value is outside the allowed enum.", enum, value))
    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(value, expected_type):
        return (
            *violations,
            _violation(
                SchemaViolationCode.TYPE, path, "Value has the wrong JSON type.", expected_type, _type_name(value)
            ),
        )
    if isinstance(value, Mapping):
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    violations.append(
                        _violation(SchemaViolationCode.REQUIRED, f"{path}.{key}", "Required property is missing.")
                    )
        properties = schema.get("properties", {})
        properties = properties if isinstance(properties, Mapping) else {}
        for key, child_value in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, Mapping):
                violations.extend(validate_json_schema(child_value, child_schema, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                violations.append(
                    _violation(
                        SchemaViolationCode.ADDITIONAL_PROPERTY, f"{path}.{key}", "Additional property is forbidden."
                    )
                )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                violations.extend(validate_json_schema(item, item_schema, f"{path}.{index}"))
        _length_violations(violations, len(value), schema, path)
    if isinstance(value, str):
        _length_violations(violations, len(value), schema, path)
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            violations.append(
                _violation(SchemaViolationCode.PATTERN, path, "String does not match pattern.", pattern, value)
            )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            violations.append(_violation(SchemaViolationCode.RANGE, path, "Number is below minimum.", minimum, value))
        if isinstance(maximum, (int, float)) and value > maximum:
            violations.append(_violation(SchemaViolationCode.RANGE, path, "Number is above maximum.", maximum, value))
        if isinstance(exclusive_minimum, (int, float)) and value <= exclusive_minimum:
            violations.append(
                _violation(
                    SchemaViolationCode.RANGE,
                    path,
                    "Number must be greater than exclusiveMinimum.",
                    exclusive_minimum,
                    value,
                )
            )
        if isinstance(exclusive_maximum, (int, float)) and value >= exclusive_maximum:
            violations.append(
                _violation(
                    SchemaViolationCode.RANGE,
                    path,
                    "Number must be less than exclusiveMaximum.",
                    exclusive_maximum,
                    value,
                )
            )
    return tuple(violations)


def _matches_type(value: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_matches_type(value, item) for item in expected)
    checks = {
        "object": lambda: isinstance(value, Mapping),
        "array": lambda: isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)),
        "string": lambda: isinstance(value, str),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
        "null": lambda: value is None,
    }
    return checks.get(expected, lambda: True)()


def _length_violations(output: list[SchemaViolation], length: int, schema: Mapping[str, Any], path: str) -> None:
    minimum = schema.get("minLength", schema.get("minItems"))
    maximum = schema.get("maxLength", schema.get("maxItems"))
    if isinstance(minimum, int) and length < minimum:
        output.append(_violation(SchemaViolationCode.LENGTH, path, "Value is shorter than allowed.", minimum, length))
    if isinstance(maximum, int) and length > maximum:
        output.append(_violation(SchemaViolationCode.LENGTH, path, "Value is longer than allowed.", maximum, length))


def _schema_sequence(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def _violation(
    code: SchemaViolationCode, path: str, message: str, expected: Any = None, observed: Any = None
) -> SchemaViolation:
    return SchemaViolation(code=code, path=path, message=message, expected=expected, observed=observed)


def _denied(violation: SchemaViolation, *, outcome: DecisionOutcome = DecisionOutcome.DENY) -> StageEvaluation:
    return StageEvaluation(
        outcome=outcome,
        reason_code=violation.code.value,
        explanation=violation.message,
        payload={"violations": [violation.model_dump(mode="json")]},
    )


__all__ = ["ToolSchemaGuard", "validate_json_schema"]
