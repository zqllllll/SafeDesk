"""Typed trace recording, replay, and persistence interfaces."""

from agentgate_core.tracing.recorder import (
    TracePersistenceRequiredError,
    TraceRecorder,
    TraceWriteResult,
)
from agentgate_core.tracing.redaction import KeyBasedTraceRedactor, RedactionResult
from agentgate_core.tracing.replay import TraceReplay, TraceReplayError, TraceReplayResult
from agentgate_core.tracing.sink import (
    InMemoryTraceSink,
    SQLiteTraceSink,
    TraceConflictError,
    TraceError,
    TraceParentError,
    TracePersistenceError,
    TraceSequenceError,
    TraceSink,
)

__all__ = [
    "InMemoryTraceSink",
    "KeyBasedTraceRedactor",
    "RedactionResult",
    "SQLiteTraceSink",
    "TraceConflictError",
    "TraceError",
    "TraceParentError",
    "TracePersistenceError",
    "TracePersistenceRequiredError",
    "TraceRecorder",
    "TraceReplay",
    "TraceReplayError",
    "TraceReplayResult",
    "TraceSequenceError",
    "TraceSink",
    "TraceWriteResult",
]
