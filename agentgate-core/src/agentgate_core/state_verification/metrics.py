"""Trace-derived metrics for State & Verification shadow and enforcement runs."""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, ConfigDict, Field

from agentgate_core.contracts.trace import TraceEvent, TraceEventType


class StateVerificationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    completion_decisions: int = Field(ge=0)
    completion_allowed: int = Field(ge=0)
    completion_blocked: int = Field(ge=0)
    false_completion_candidates: int = Field(ge=0)
    response_grounding_decisions: int = Field(ge=0)
    response_downgrades: int = Field(ge=0)
    verification_results: int = Field(ge=0)
    verification_status_counts: dict[str, int]
    blocker_type_counts: dict[str, int]


def summarize_state_verification(events: tuple[TraceEvent, ...]) -> StateVerificationMetrics:
    completion_allowed = 0
    completion_blocked = 0
    false_completion_candidates = 0
    response_decisions = 0
    response_downgrades = 0
    verification_results = 0
    verification_statuses: Counter[str] = Counter()
    blocker_types: Counter[str] = Counter()
    for event in events:
        if event.event_type is TraceEventType.COMPLETION_DECISION:
            payload = event.payload.get("completion_decision", {})
            if not isinstance(payload, dict):
                continue
            proposed_allowed = payload.get("proposed_allowed") is True
            completion_allowed += int(proposed_allowed)
            completion_blocked += int(not proposed_allowed)
            false_completion_candidates += int(not proposed_allowed)
            blockers = payload.get("blockers", [])
            if isinstance(blockers, list):
                blocker_types.update(
                    item.get("blocker_type")
                    for item in blockers
                    if isinstance(item, dict) and isinstance(item.get("blocker_type"), str)
                )
        elif event.event_type is TraceEventType.RESPONSE_GROUNDING_DECISION:
            payload = event.payload.get("response_grounding", {})
            if isinstance(payload, dict):
                response_decisions += 1
                response_downgrades += int(payload.get("downgraded") is True)
        elif event.event_type is TraceEventType.VERIFICATION_FINISHED:
            payload = event.payload.get("verification", {})
            if isinstance(payload, dict) and isinstance(payload.get("status"), str):
                verification_results += 1
                verification_statuses[payload["status"]] += 1
    return StateVerificationMetrics(
        completion_decisions=completion_allowed + completion_blocked,
        completion_allowed=completion_allowed,
        completion_blocked=completion_blocked,
        false_completion_candidates=false_completion_candidates,
        response_grounding_decisions=response_decisions,
        response_downgrades=response_downgrades,
        verification_results=verification_results,
        verification_status_counts=dict(sorted(verification_statuses.items())),
        blocker_type_counts=dict(sorted(blocker_types.items())),
    )


__all__ = ["StateVerificationMetrics", "summarize_state_verification"]
