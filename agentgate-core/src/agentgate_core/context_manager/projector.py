"""Structured tool-result projection with raw trace references."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from agentgate_core.context_manager.token_budget import HeuristicTokenEstimator
from agentgate_core.contracts.context_management import ProjectedToolResult, RawTraceReference

_ALWAYS_KEEP = {
    "id",
    "error",
    "errors",
    "error_code",
    "message",
    "status",
    "success",
    "next_page",
    "next_page_token",
    "page",
    "page_size",
    "total",
}


@runtime_checkable
class ToolResultProjector(Protocol):
    def project(self, payload: object, required_fields: tuple[str, ...]) -> dict[str, Any]: ...


class GenericToolResultProjector:
    def project(self, payload: object, required_fields: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            return {"value": payload}
        keep = _ALWAYS_KEEP | set(required_fields)
        output: dict[str, Any] = {}
        for key, value in payload.items():
            normalized = str(key)
            if normalized in keep or normalized.endswith("_id"):
                output[normalized] = value
        if not output:
            output["summary"] = f"Result object with {len(payload)} fields; inspect raw_reference for details."
        return output


class ToolResultProjectorRegistry:
    def __init__(self, default: ToolResultProjector | None = None) -> None:
        self.default = default or GenericToolResultProjector()
        self._projectors: dict[str, ToolResultProjector] = {}

    def register(self, tool_name: str, projector: ToolResultProjector) -> None:
        if tool_name in self._projectors:
            raise ValueError(f"projector already registered: {tool_name}")
        self._projectors[tool_name] = projector

    def project(
        self,
        *,
        task_id: str,
        run_id: str,
        tool_name: str,
        source_event_id: str,
        payload: object,
        required_fields: tuple[str, ...] = (),
        estimator: HeuristicTokenEstimator | None = None,
    ) -> ProjectedToolResult:
        token_estimator = estimator or HeuristicTokenEstimator()
        projected = self._projectors.get(tool_name, self.default).project(payload, required_fields)
        original_tokens = token_estimator.estimate(payload)
        projected_tokens = token_estimator.estimate(projected)
        if projected_tokens > original_tokens and isinstance(payload, Mapping):
            projected = dict(payload)
            projected_tokens = token_estimator.estimate(projected)
        reference = RawTraceReference(
            reference_id=f"trace-ref-{uuid4()}",
            task_id=task_id,
            run_id=run_id,
            event_ids=(source_event_id,),
            content_type="tool_result",
            estimated_tokens=original_tokens,
        )
        return ProjectedToolResult(
            projection_id=f"projection-{uuid4()}",
            task_id=task_id,
            tool_name=tool_name,
            source_event_id=source_event_id,
            projected=projected,
            raw_reference=reference,
            original_tokens=original_tokens,
            projected_tokens=projected_tokens,
        )


__all__ = [
    "GenericToolResultProjector",
    "ToolResultProjector",
    "ToolResultProjectorRegistry",
]
