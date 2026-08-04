"""Normalize DeerFlow tool calls into framework-independent AgentGate actions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Self

from pydantic import Field, JsonValue, TypeAdapter, ValidationError, model_validator

from agentgate_core.contracts import (
    ActionIR,
    ActionKind,
    ActorKind,
    ExpectedEffect,
    ResourceRef,
    ToolCatalogEntry,
    ToolCatalogSnapshot,
)
from agentgate_core.contracts.base import (
    ContractModel,
    HumanText,
    Identifier,
    JsonObject,
    NonNegativeInt,
    require_unique,
)
from agentgate_core.tool_execution_guard import InMemoryToolCatalog, ToolCatalog, ToolNotFoundError
from agentgate_deerflow.tool_profile import ArgumentProjection, DeerFlowToolProfile

_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])
_MISSING = object()


class ToolCallErrorCode(StrEnum):
    NOT_A_MAPPING = "not_a_mapping"
    INVALID_TOOL_CALL = "invalid_tool_call"
    MISSING_CALL_ID = "missing_call_id"
    MISSING_TOOL_NAME = "missing_tool_name"
    INVALID_ARGUMENT_JSON = "invalid_argument_json"
    ARGUMENTS_NOT_OBJECT = "arguments_not_object"
    ARGUMENTS_NOT_JSON = "arguments_not_json"
    UNKNOWN_TOOL = "unknown_tool"
    MISSING_PROFILE = "missing_profile"
    PROFILE_CATALOG_MISMATCH = "profile_catalog_mismatch"
    INVALID_RESOURCE_VALUE = "invalid_resource_value"
    DUPLICATE_CALL_ID = "duplicate_call_id"


class DeerFlowToolCallError(ValueError):
    def __init__(
        self,
        code: ToolCallErrorCode,
        message: str,
        *,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name


class NormalizedDeerFlowToolCall(ContractModel):
    tool_call_id: Identifier
    tool_name: Identifier
    arguments: JsonObject = Field(default_factory=dict)


class DeerFlowActionContext(ContractModel):
    task_id: Identifier
    source_turn: NonNegativeInt
    actor: ActorKind = ActorKind.LEAD_AGENT
    required_evidence_ids: tuple[Identifier, ...] = ()
    dependency_action_ids: tuple[Identifier, ...] = ()
    rationale: HumanText | None = None

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        require_unique(self.required_evidence_ids, "required_evidence_ids")
        require_unique(self.dependency_action_ids, "dependency_action_ids")
        return self


def _extract_schema_model(tool: object) -> object:
    tool_call_schema = getattr(tool, "tool_call_schema", None)
    if tool_call_schema is not None:
        return tool_call_schema
    getter = getattr(tool, "get_input_schema", None)
    if callable(getter):
        return getter()
    schema_model = getattr(tool, "args_schema", None)
    if schema_model is None:
        raise ValueError(f"tool {getattr(tool, 'name', '<unknown>')} does not expose an input schema")
    return schema_model


def extract_deerflow_input_schema(tool: object) -> JsonObject:
    """Extract the actual JSON Schema exposed by a LangChain-compatible tool."""

    schema_model = _extract_schema_model(tool)
    if isinstance(schema_model, Mapping):
        raw_schema: object = dict(schema_model)
    else:
        schema_builder = getattr(schema_model, "model_json_schema", None)
        if not callable(schema_builder):
            raise ValueError(f"tool {getattr(tool, 'name', '<unknown>')} has an unsupported input schema")
        raw_schema = schema_builder()
    try:
        return _JSON_OBJECT_ADAPTER.validate_python(raw_schema)
    except ValidationError as error:
        raise ValueError(f"tool {getattr(tool, 'name', '<unknown>')} produced a non-JSON input schema") from error


def build_deerflow_tool_catalog(
    tools: Iterable[object],
    profiles: Iterable[DeerFlowToolProfile],
    *,
    catalog_version: str,
) -> InMemoryToolCatalog:
    """Build a strict catalog from the actual bound tools and reviewed profiles."""

    materialized_profiles = tuple(profiles)
    profiles_by_name = {profile.tool_name: profile for profile in materialized_profiles}
    if len(profiles_by_name) != len(materialized_profiles):
        raise ValueError("profiles must have unique tool names")
    entries: list[ToolCatalogEntry] = []
    seen_tool_names: set[str] = set()
    for tool in tools:
        tool_name = getattr(tool, "name", None)
        if not isinstance(tool_name, str) or not tool_name:
            raise ValueError("every DeerFlow tool must expose a non-empty name")
        if tool_name in seen_tool_names:
            raise ValueError(f"duplicate DeerFlow tool name: {tool_name}")
        seen_tool_names.add(tool_name)
        profile = profiles_by_name.get(tool_name)
        if profile is None:
            raise DeerFlowToolCallError(
                ToolCallErrorCode.MISSING_PROFILE,
                f"tool {tool_name} has no reviewed DeerFlow action profile",
                tool_name=tool_name,
            )
        description = getattr(tool, "description", None)
        if not isinstance(description, str) or not description.strip():
            description = f"DeerFlow tool {tool_name}."
        entries.append(
            ToolCatalogEntry(
                name=tool_name,
                description=description,
                operation=profile.operation,
                input_schema=extract_deerflow_input_schema(tool),
                action_kind=profile.action_kind,
                risk_level=profile.risk_level,
                side_effect_type=profile.side_effect_type,
                resource_types=(profile.resource_type,) if profile.resource_type is not None else (),
                required_evidence=profile.required_evidence,
                required_policy=profile.required_policy,
                dependency_tool_names=profile.dependency_tool_names,
                verification_strategy=profile.verification_strategy,
                idempotency_strategy=profile.idempotency_strategy,
            )
        )
    unused_profiles = set(profiles_by_name) - seen_tool_names
    if unused_profiles:
        raise ValueError(f"profiles do not correspond to bound DeerFlow tools: {sorted(unused_profiles)}")
    return InMemoryToolCatalog(ToolCatalogSnapshot(catalog_version=catalog_version, entries=tuple(entries)))


class DeerFlowToolCallAdapter:
    def __init__(self, catalog: ToolCatalog, profiles: Iterable[DeerFlowToolProfile]) -> None:
        materialized_profiles = tuple(profiles)
        self._catalog = catalog
        self._profiles = {profile.tool_name: profile.model_copy(deep=True) for profile in materialized_profiles}
        if len(self._profiles) != len(materialized_profiles):
            raise ValueError("profiles must have unique tool names")
        self._validate_profiles_against_catalog()

    @classmethod
    def from_tools(
        cls,
        tools: Iterable[object],
        profiles: Iterable[DeerFlowToolProfile],
        *,
        catalog_version: str,
    ) -> DeerFlowToolCallAdapter:
        materialized_profiles = tuple(profiles)
        catalog = build_deerflow_tool_catalog(tools, materialized_profiles, catalog_version=catalog_version)
        return cls(catalog, materialized_profiles)

    @property
    def catalog_version(self) -> str:
        return self._catalog.catalog_version

    def catalog_snapshot(self) -> ToolCatalogSnapshot:
        return self._catalog.snapshot()

    def normalize_tool_call(self, raw_call_or_request: object) -> NormalizedDeerFlowToolCall:
        candidate = getattr(raw_call_or_request, "tool_call", raw_call_or_request)
        if not isinstance(candidate, Mapping):
            raise DeerFlowToolCallError(ToolCallErrorCode.NOT_A_MAPPING, "tool call must be a mapping")

        raw_call = dict(candidate)
        raw_id = raw_call.get("id")
        tool_call_id = raw_id.strip() if isinstance(raw_id, str) else None
        function = raw_call.get("function")
        function_mapping = function if isinstance(function, Mapping) else None
        raw_name = raw_call.get("name")
        if not raw_name and function_mapping is not None:
            raw_name = function_mapping.get("name")
        tool_name = raw_name.strip() if isinstance(raw_name, str) else None

        if raw_call.get("invalid") is True or raw_call.get("type") == "invalid_tool_call":
            raise DeerFlowToolCallError(
                ToolCallErrorCode.INVALID_TOOL_CALL,
                "provider marked the tool call as invalid",
                tool_call_id=tool_call_id,
                tool_name=tool_name,
            )
        if not tool_call_id:
            raise DeerFlowToolCallError(
                ToolCallErrorCode.MISSING_CALL_ID,
                "tool call id is required for trace correlation",
                tool_name=tool_name,
            )
        if not tool_name:
            raise DeerFlowToolCallError(
                ToolCallErrorCode.MISSING_TOOL_NAME,
                "tool name is required",
                tool_call_id=tool_call_id,
            )

        raw_arguments = raw_call.get("args", _MISSING)
        if raw_arguments is _MISSING and function_mapping is not None:
            raw_arguments = function_mapping.get("arguments", {})
        arguments = self._normalize_arguments(raw_arguments, tool_call_id=tool_call_id, tool_name=tool_name)
        return NormalizedDeerFlowToolCall(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
        )

    def to_action(self, raw_call_or_request: object, context: DeerFlowActionContext) -> ActionIR:
        tool_call = self.normalize_tool_call(raw_call_or_request)
        try:
            catalog_entry = self._catalog.get_tool(tool_call.tool_name)
        except ToolNotFoundError as error:
            raise DeerFlowToolCallError(
                ToolCallErrorCode.UNKNOWN_TOOL,
                str(error),
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
            ) from error
        profile = self._profiles.get(tool_call.tool_name)
        if profile is None:
            raise DeerFlowToolCallError(
                ToolCallErrorCode.MISSING_PROFILE,
                f"tool {tool_call.tool_name} has no action profile",
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
            )

        resource = self._build_resource(profile, tool_call)
        expected_effects: tuple[ExpectedEffect, ...] = ()
        idempotency_key: str | None = None
        if catalog_entry.action_kind is ActionKind.WRITE:
            if resource is None or catalog_entry.side_effect_type is None:
                raise self._profile_mismatch(tool_call, "write tool lacks resource or side-effect semantics")
            expected_change = self._build_expected_change(profile, tool_call.arguments)
            expected_effects = (
                ExpectedEffect(
                    effect_key=self._effect_key(context.task_id, tool_call.tool_call_id),
                    kind=catalog_entry.side_effect_type,
                    resource=resource,
                    expected_change=expected_change,
                ),
            )
            idempotency_key = self._idempotency_key(context.task_id, catalog_entry, profile, tool_call, resource)

        return ActionIR(
            action_id=tool_call.tool_call_id,
            task_id=context.task_id,
            actor=context.actor,
            kind=catalog_entry.action_kind,
            tool_name=tool_call.tool_name,
            operation=catalog_entry.operation,
            resource=resource,
            arguments=tool_call.arguments,
            expected_effects=expected_effects,
            required_evidence_ids=context.required_evidence_ids,
            dependency_action_ids=context.dependency_action_ids,
            idempotency_key=idempotency_key,
            risk_level=catalog_entry.risk_level,
            tool_schema_version=catalog_entry.tool_schema_version,
            source_turn=context.source_turn,
            rationale=context.rationale,
        )

    def to_actions(
        self,
        raw_calls: Sequence[object],
        context: DeerFlowActionContext,
        *,
        dependencies_by_call_id: Mapping[str, tuple[str, ...]] | None = None,
    ) -> tuple[ActionIR, ...]:
        normalized = tuple(self.normalize_tool_call(raw_call) for raw_call in raw_calls)
        call_ids = tuple(call.tool_call_id for call in normalized)
        if len(call_ids) != len(set(call_ids)):
            duplicate = next(call_id for call_id in call_ids if call_ids.count(call_id) > 1)
            raise DeerFlowToolCallError(
                ToolCallErrorCode.DUPLICATE_CALL_ID,
                f"duplicate tool call id in model response: {duplicate}",
                tool_call_id=duplicate,
            )

        actions: list[ActionIR] = []
        for raw_call, normalized_call in zip(raw_calls, normalized, strict=True):
            dependencies = context.dependency_action_ids
            if dependencies_by_call_id is not None:
                dependencies = tuple(
                    dict.fromkeys((*dependencies, *dependencies_by_call_id.get(normalized_call.tool_call_id, ())))
                )
            action_context = context.model_copy(update={"dependency_action_ids": dependencies})
            actions.append(self.to_action(raw_call, action_context))
        return tuple(actions)

    def _validate_profiles_against_catalog(self) -> None:
        catalog_entries = {entry.name: entry for entry in self._catalog.list_tools()}
        if set(catalog_entries) != set(self._profiles):
            raise ValueError("catalog tools and DeerFlow profiles must have identical names")
        for tool_name, profile in self._profiles.items():
            entry = catalog_entries[tool_name]
            expected_resource_types = (profile.resource_type,) if profile.resource_type is not None else ()
            if (
                entry.operation != profile.operation
                or entry.action_kind is not profile.action_kind
                or entry.risk_level is not profile.risk_level
                or entry.side_effect_type is not profile.side_effect_type
                or entry.resource_types != expected_resource_types
            ):
                raise ValueError(f"catalog entry and profile semantics differ for tool {tool_name}")

    @staticmethod
    def _normalize_arguments(raw_arguments: object, *, tool_call_id: str, tool_name: str) -> JsonObject:
        if isinstance(raw_arguments, str):
            try:
                raw_arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as error:
                raise DeerFlowToolCallError(
                    ToolCallErrorCode.INVALID_ARGUMENT_JSON,
                    f"tool arguments are not valid JSON: {error.msg}",
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                ) from error
        if not isinstance(raw_arguments, Mapping):
            raise DeerFlowToolCallError(
                ToolCallErrorCode.ARGUMENTS_NOT_OBJECT,
                "tool arguments must be a JSON object",
                tool_call_id=tool_call_id,
                tool_name=tool_name,
            )
        try:
            return _JSON_OBJECT_ADAPTER.validate_python(dict(raw_arguments))
        except ValidationError as error:
            raise DeerFlowToolCallError(
                ToolCallErrorCode.ARGUMENTS_NOT_JSON,
                "tool arguments contain values that cannot be represented as JSON",
                tool_call_id=tool_call_id,
                tool_name=tool_name,
            ) from error

    def _build_resource(
        self,
        profile: DeerFlowToolProfile,
        tool_call: NormalizedDeerFlowToolCall,
    ) -> ResourceRef | None:
        if profile.resource_type is None:
            return None
        resource_id = self._resource_scalar(profile.resource_id_path, tool_call, "resource id")
        scope = self._resource_scalar(profile.scope_path, tool_call, "scope") or profile.default_scope
        return ResourceRef(resource_type=profile.resource_type, resource_id=resource_id, scope=scope)

    def _resource_scalar(
        self,
        path: tuple[str, ...] | None,
        tool_call: NormalizedDeerFlowToolCall,
        label: str,
    ) -> str | None:
        if path is None:
            return None
        value = self._lookup_path(tool_call.arguments, path)
        if value is _MISSING or value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise DeerFlowToolCallError(
                ToolCallErrorCode.INVALID_RESOURCE_VALUE,
                f"{label} at {'.'.join(path)} must be a string or number",
                tool_call_id=tool_call.tool_call_id,
                tool_name=tool_call.tool_name,
            )
        return str(value)

    @staticmethod
    def _build_expected_change(profile: DeerFlowToolProfile, arguments: JsonObject) -> JsonObject:
        expected_change: JsonObject = {}
        for binding in profile.expected_change_bindings:
            value = DeerFlowToolCallAdapter._lookup_path(arguments, binding.argument_path)
            if value is not _MISSING:
                expected_change[binding.output_field] = DeerFlowToolCallAdapter._project_value(
                    value,
                    binding.projection,
                )
        return expected_change

    @staticmethod
    def _project_value(value: object, projection: ArgumentProjection) -> JsonValue:
        if projection is ArgumentProjection.VALUE:
            return _JSON_OBJECT_ADAPTER.validate_python({"value": value})["value"]
        canonical = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _idempotency_key(
        task_id: str,
        catalog_entry: ToolCatalogEntry,
        profile: DeerFlowToolProfile,
        tool_call: NormalizedDeerFlowToolCall,
        resource: ResourceRef,
    ) -> str:
        if profile.idempotency_paths:
            material: object = {
                ".".join(path): DeerFlowToolCallAdapter._lookup_path(tool_call.arguments, path)
                if DeerFlowToolCallAdapter._lookup_path(tool_call.arguments, path) is not _MISSING
                else None
                for path in profile.idempotency_paths
            }
        else:
            material = tool_call.arguments
        canonical = json.dumps(
            {
                "task_id": task_id,
                "tool_name": catalog_entry.name,
                "operation": catalog_entry.operation,
                "resource": resource.model_dump(mode="json"),
                "material": material,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _effect_key(task_id: str, tool_call_id: str) -> str:
        digest = hashlib.sha256(f"{task_id}:{tool_call_id}:effect:0".encode()).hexdigest()
        return f"effect:{digest}"

    @staticmethod
    def _lookup_path(value: object, path: tuple[str, ...]) -> object:
        current = value
        for segment in path:
            if not isinstance(current, Mapping) or segment not in current:
                return _MISSING
            current = current[segment]
        return current

    @staticmethod
    def _profile_mismatch(
        tool_call: NormalizedDeerFlowToolCall,
        message: str,
    ) -> DeerFlowToolCallError:
        return DeerFlowToolCallError(
            ToolCallErrorCode.PROFILE_CATALOG_MISMATCH,
            message,
            tool_call_id=tool_call.tool_call_id,
            tool_name=tool_call.tool_name,
        )


__all__ = [
    "DeerFlowActionContext",
    "DeerFlowToolCallAdapter",
    "DeerFlowToolCallError",
    "NormalizedDeerFlowToolCall",
    "ToolCallErrorCode",
    "build_deerflow_tool_catalog",
    "extract_deerflow_input_schema",
]
