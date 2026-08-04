"""Capability-based dynamic tool resolution from a public catalog."""

from __future__ import annotations

import re

from agentgate_core.contracts.decision import FeatureMode
from agentgate_core.contracts.tool_guard import (
    ToolRequirement,
    ToolResolution,
    ToolResolutionCandidate,
)
from agentgate_core.contracts.trace import TraceActor, TraceEventType
from agentgate_core.tool_execution_guard.active_set import ActiveToolSetManager
from agentgate_core.tool_execution_guard.catalog import ToolCatalog
from agentgate_core.tracing.recorder import TraceRecorder


class DynamicToolResolver:
    def __init__(
        self,
        catalog: ToolCatalog,
        active_sets: ActiveToolSetManager,
        *,
        run_id: str | None = None,
        trace_recorder: TraceRecorder | None = None,
    ) -> None:
        self.catalog = catalog
        self.active_sets = active_sets
        self.run_id = run_id
        self.trace_recorder = trace_recorder

    def resolve(self, requirement: ToolRequirement, *, mode: FeatureMode, turn: int = 0) -> ToolResolution:
        active = self.active_sets.get(requirement.task_id)
        terms = {_normalize(term) for term in requirement.operation_terms}
        terms.update(_tokens(requirement.description))
        candidates: list[ToolResolutionCandidate] = []
        for entry in self.catalog.list_tools():
            if entry.name in requirement.excluded_tool_names or entry.name in active.tool_names:
                continue
            if requirement.action_kind is not None and entry.action_kind is not requirement.action_kind:
                continue
            if requirement.resource_types and not (set(requirement.resource_types) & set(entry.resource_types)):
                continue
            if requirement.required_policy_ids and not set(requirement.required_policy_ids).issubset(
                entry.required_policy
            ):
                continue
            haystack = _tokens(f"{entry.name} {entry.operation} {entry.description}")
            matched = tuple(sorted(term for term in terms if term in haystack))
            resource_bonus = 3 * len(set(requirement.resource_types) & set(entry.resource_types))
            operation_bonus = 4 if _normalize(entry.operation) in terms else 0
            score = len(matched) + resource_bonus + operation_bonus
            if score == 0:
                continue
            candidates.append(
                ToolResolutionCandidate(
                    tool_name=entry.name,
                    score=score,
                    matched_terms=matched,
                    reason="Candidate matched public operation, description, resource, and policy metadata.",
                )
            )
        candidates.sort(key=lambda item: (-item.score, item.tool_name))
        selected = tuple(candidates[: requirement.max_candidates])
        resulting_set = None
        if mode is FeatureMode.ENFORCE and selected:
            resulting_set = self.active_sets.expand(
                requirement.task_id,
                tuple(item.tool_name for item in selected),
                reason=f"Dynamic resolution for requirement {requirement.requirement_id}.",
            )
        resolution = ToolResolution(
            requirement_id=requirement.requirement_id,
            task_id=requirement.task_id,
            mode=mode,
            candidates=selected,
            previous_set_version=active.set_version,
            resulting_tool_set=resulting_set,
            replan_required=bool(selected),
        )
        if self.trace_recorder is not None and self.run_id is not None:
            self.trace_recorder.record(
                task_id=requirement.task_id,
                run_id=self.run_id,
                turn=turn,
                event_type=(
                    TraceEventType.TOOL_SET_CHANGED
                    if resolution.resulting_tool_set is not None
                    else TraceEventType.TOOL_RESOLUTION
                ),
                actor=TraceActor.RUNTIME,
                correlation_id=requirement.requirement_id,
                payload={"tool_resolution": resolution.model_dump(mode="json")},
                critical=False,
            )
        return resolution


def _normalize(value: str) -> str:
    return value.strip().lower().replace("__", "_")


def _tokens(value: str) -> set[str]:
    return {_normalize(item) for item in re.findall(r"[A-Za-z0-9_]+", value) if item}


__all__ = ["DynamicToolResolver"]
