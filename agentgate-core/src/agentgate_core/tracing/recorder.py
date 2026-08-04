"""TraceEvent construction, redaction, and fail-closed persistence policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, model_validator

from agentgate_core.contracts.base import utc_now
from agentgate_core.contracts.trace import TraceActor, TraceEvent, TraceEventType
from agentgate_core.tracing.redaction import KeyBasedTraceRedactor
from agentgate_core.tracing.sink import TracePersistenceError, TraceSink


class TracePersistenceRequiredError(TracePersistenceError):
    """Raised when policy forbids continuing without a durable trace."""


class TraceWriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event: TraceEvent | None
    persisted: bool
    degraded: bool
    error_type: str | None = None

    @model_validator(mode="after")
    def validate_result(self) -> TraceWriteResult:
        if self.persisted:
            if self.event is None or self.degraded or self.error_type is not None:
                raise ValueError("persisted trace results must contain only a successful event")
        elif not self.degraded or self.event is not None or self.error_type is None:
            raise ValueError("non-persisted trace results must describe a degraded write")
        return self


class TraceRecorder:
    """Serialize TraceEvent creation so every stream remains contiguous."""

    def __init__(
        self,
        sink: TraceSink,
        *,
        allow_read_degradation: bool = False,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] | None = None,
        redactor: KeyBasedTraceRedactor | None = None,
    ) -> None:
        self.sink = sink
        self.allow_read_degradation = allow_read_degradation
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._redactor = redactor or KeyBasedTraceRedactor()
        self._lock = RLock()

    def record(
        self,
        *,
        task_id: str,
        run_id: str,
        turn: int,
        event_type: TraceEventType,
        actor: TraceActor,
        correlation_id: str,
        payload: Mapping[str, Any] | None = None,
        parent_event_id: str | None = None,
        state_version: int | None = None,
        critical: bool = False,
    ) -> TraceWriteResult:
        with self._lock:
            redaction = self._redactor.redact(payload or {})
            try:
                last_sequence = self.sink.last_sequence(task_id, run_id)
            except TracePersistenceError as exc:
                return self._handle_persistence_failure(exc, event_type=event_type, critical=critical)
            event = TraceEvent(
                event_id=f"trace-event-{self._id_factory()}",
                task_id=task_id,
                run_id=run_id,
                sequence_number=0 if last_sequence is None else last_sequence + 1,
                turn=turn,
                timestamp=self._clock(),
                event_type=event_type,
                actor=actor,
                parent_event_id=parent_event_id,
                correlation_id=correlation_id,
                state_version=state_version,
                payload=redaction.payload,
                redaction_metadata=redaction.metadata,
            )
            try:
                persisted = self.sink.append(event)
            except TracePersistenceError as exc:
                return self._handle_persistence_failure(exc, event_type=event_type, critical=critical)
            return TraceWriteResult(event=persisted, persisted=True, degraded=False)

    def _handle_persistence_failure(
        self,
        error: TracePersistenceError,
        *,
        event_type: TraceEventType,
        critical: bool,
    ) -> TraceWriteResult:
        if critical or not self.allow_read_degradation:
            raise TracePersistenceRequiredError(
                f"trace persistence is required for {event_type.value}: {type(error).__name__}"
            ) from error
        return TraceWriteResult(
            event=None,
            persisted=False,
            degraded=True,
            error_type=type(error).__name__,
        )

    def close(self) -> None:
        self.sink.close()


__all__ = [
    "TracePersistenceRequiredError",
    "TraceRecorder",
    "TraceWriteResult",
]
