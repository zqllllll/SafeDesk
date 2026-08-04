"""Raw trace reference creation and bounded retrieval."""

from __future__ import annotations

from agentgate_core.context_manager.token_budget import HeuristicTokenEstimator
from agentgate_core.contracts.context_management import RawTraceReference
from agentgate_core.contracts.trace import TraceEvent
from agentgate_core.tracing.sink import TraceSink


class RawTraceRetriever:
    def __init__(self, sink: TraceSink, estimator: HeuristicTokenEstimator | None = None) -> None:
        self.sink = sink
        self.estimator = estimator or HeuristicTokenEstimator()

    def retrieve(self, reference: RawTraceReference, *, max_events: int = 20) -> tuple[TraceEvent, ...]:
        events: list[TraceEvent] = []
        for event_id in reference.event_ids[:max_events]:
            event = self.sink.get_event(event_id)
            if event is None:
                raise KeyError(f"trace reference points to a missing event: {event_id}")
            if (event.task_id, event.run_id) != (reference.task_id, reference.run_id):
                raise ValueError("trace reference crosses task/run boundaries")
            events.append(event)
        return tuple(events)


__all__ = ["RawTraceRetriever"]
