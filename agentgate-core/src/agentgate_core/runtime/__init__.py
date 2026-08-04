"""Framework-independent runtime coordination and state storage interfaces.

Assembly imports the tool-execution package, whose effect guard depends on the
state-store types defined here. Keep assembly and orchestrator exports lazy so a
consumer can import a state-store protocol without creating an import cycle.
"""

from typing import TYPE_CHECKING, Any

from agentgate_core.runtime.config import AgentGateFeatureConfig
from agentgate_core.runtime.coordinator import AgentGateCoordinator, CoordinatorStage, PassThroughStage
from agentgate_core.runtime.in_memory_state_store import InMemoryTypedStateStore
from agentgate_core.runtime.session import AgentGateRuntimeSession
from agentgate_core.runtime.sqlite_state_store import SQLiteTypedStateStore
from agentgate_core.runtime.state_store import (
    IdempotencyConflictError,
    RecordConflictError,
    RecordNotFoundError,
    RecordVersionConflictError,
    StateCheckpoint,
    StateInvariantError,
    StatePersistenceConflictError,
    StatePersistenceError,
    StateStoreError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
    TaskStateEvent,
    TaskStateEventType,
    TypedStateStore,
    VersionConflictError,
)

if TYPE_CHECKING:
    from agentgate_core.runtime.assembly import AgentGateAssembly, assemble_agentgate
    from agentgate_core.runtime.orchestrator import SafeDeskOrchestrator

__all__ = [
    "AgentGateCoordinator",
    "AgentGateAssembly",
    "AgentGateFeatureConfig",
    "AgentGateRuntimeSession",
    "CoordinatorStage",
    "IdempotencyConflictError",
    "InMemoryTypedStateStore",
    "PassThroughStage",
    "RecordConflictError",
    "RecordNotFoundError",
    "RecordVersionConflictError",
    "StateCheckpoint",
    "StateInvariantError",
    "StatePersistenceConflictError",
    "StatePersistenceError",
    "SQLiteTypedStateStore",
    "SafeDeskOrchestrator",
    "StateStoreError",
    "TaskAlreadyExistsError",
    "TaskNotFoundError",
    "TaskStateEvent",
    "TaskStateEventType",
    "TypedStateStore",
    "VersionConflictError",
    "assemble_agentgate",
]


def __getattr__(name: str) -> Any:
    """Load composition-root exports only when a runner explicitly requests them."""
    if name in {"AgentGateAssembly", "assemble_agentgate"}:
        from agentgate_core.runtime.assembly import AgentGateAssembly, assemble_agentgate

        return {"AgentGateAssembly": AgentGateAssembly, "assemble_agentgate": assemble_agentgate}[name]
    if name == "SafeDeskOrchestrator":
        from agentgate_core.runtime.orchestrator import SafeDeskOrchestrator

        return SafeDeskOrchestrator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
