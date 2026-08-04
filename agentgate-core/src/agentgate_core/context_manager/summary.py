"""Evidence-safe structured summaries of older trace events."""

from __future__ import annotations

from uuid import uuid4

from agentgate_core.context_manager.token_budget import HeuristicTokenEstimator
from agentgate_core.contracts.context_management import HistorySummary, RawTraceReference
from agentgate_core.contracts.trace import TraceEvent, TraceEventType


class StructuredHistorySummarizer:
    def __init__(self, estimator: HeuristicTokenEstimator | None = None) -> None:
        self.estimator = estimator or HeuristicTokenEstimator()

    def summarize(self, task_id: str, run_id: str, events: tuple[TraceEvent, ...]) -> HistorySummary | None:
        if not events:
            return None
        facts: list[str] = []
        completed_actions: list[str] = []
        resolved_failures: list[str] = []
        for event in events:
            if event.event_type is TraceEventType.VERIFICATION_FINISHED:
                verification = event.payload.get("verification", {})
                if isinstance(verification, dict):
                    status = verification.get("status")
                    target = verification.get("target_id")
                    if isinstance(status, str) and isinstance(target, str):
                        facts.append(f"Verification for {target} ended with explicit status {status}.")
            elif event.event_type is TraceEventType.RECOVERY_FINISHED:
                result = event.payload.get("recovery_result", {})
                if isinstance(result, dict):
                    failure_id = result.get("failure_id")
                    if result.get("success") is True and isinstance(failure_id, str):
                        resolved_failures.append(failure_id)
                        facts.append(f"Recovery explicitly resolved failure {failure_id}.")
            elif event.event_type is TraceEventType.TOOL_FINISHED:
                action_id = event.payload.get("action_id")
                if isinstance(action_id, str):
                    completed_actions.append(action_id)
                    facts.append(f"Tool execution returned for action {action_id}; effect verification is separate.")
        reference = RawTraceReference(
            reference_id=f"trace-ref-{uuid4()}",
            task_id=task_id,
            run_id=run_id,
            event_ids=tuple(event.event_id for event in events),
            content_type="history",
            estimated_tokens=self.estimator.estimate([event.model_dump(mode="json") for event in events]),
        )
        return HistorySummary(
            summary_id=f"history-{uuid4()}",
            task_id=task_id,
            source_event_ids=reference.event_ids,
            facts=tuple(facts),
            completed_action_ids=tuple(dict.fromkeys(completed_actions)),
            resolved_failure_ids=tuple(dict.fromkeys(resolved_failures)),
            raw_reference=reference,
        )


__all__ = ["StructuredHistorySummarizer"]
