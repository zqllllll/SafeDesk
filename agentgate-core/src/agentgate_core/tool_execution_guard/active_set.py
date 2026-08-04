"""Versioned task-local active tool sets."""

from __future__ import annotations

from threading import RLock

from agentgate_core.contracts.tool_guard import ActiveToolSet
from agentgate_core.tool_execution_guard.catalog import ToolCatalog


class ActiveToolSetManager:
    def __init__(self, catalog: ToolCatalog) -> None:
        self.catalog = catalog
        self._sets: dict[str, ActiveToolSet] = {}
        self._lock = RLock()

    def initialize(self, task_id: str, tool_names: tuple[str, ...], *, reason: str) -> ActiveToolSet:
        with self._lock:
            if task_id in self._sets:
                raise ValueError(f"active tool set already exists: {task_id}")
            active = self._build(task_id, 1, tool_names, reason)
            self._sets[task_id] = active
            return active.model_copy(deep=True)

    def get(self, task_id: str) -> ActiveToolSet:
        with self._lock:
            try:
                return self._sets[task_id].model_copy(deep=True)
            except KeyError as exc:
                raise KeyError(f"active tool set not found: {task_id}") from exc

    def expand(self, task_id: str, tool_names: tuple[str, ...], *, reason: str) -> ActiveToolSet:
        with self._lock:
            current = self.get(task_id)
            merged = tuple(dict.fromkeys((*current.tool_names, *tool_names)))
            if merged == current.tool_names:
                return current
            active = self._build(task_id, current.set_version + 1, merged, reason)
            self._sets[task_id] = active
            return active.model_copy(deep=True)

    def replace(self, task_id: str, tool_names: tuple[str, ...], *, reason: str) -> ActiveToolSet:
        with self._lock:
            current = self.get(task_id)
            active = self._build(task_id, current.set_version + 1, tool_names, reason)
            self._sets[task_id] = active
            return active.model_copy(deep=True)

    def restore(self, active: ActiveToolSet) -> ActiveToolSet:
        """Restore a persisted active set after validating it against the current catalog."""

        with self._lock:
            validated = self._build(
                active.task_id,
                active.set_version,
                active.tool_names,
                active.reason,
            )
            if validated.catalog_version != active.catalog_version:
                raise ValueError("active tool set catalog_version does not match the current catalog")
            if validated.schema_versions != active.schema_versions:
                raise ValueError("active tool set schema versions do not match the current catalog")
            self._sets[active.task_id] = active.model_copy(deep=True)
            return active.model_copy(deep=True)

    def _build(self, task_id: str, version: int, names: tuple[str, ...], reason: str) -> ActiveToolSet:
        entries = tuple(self.catalog.get_tool(name) for name in names)
        return ActiveToolSet(
            task_id=task_id,
            set_version=version,
            catalog_version=self.catalog.catalog_version,
            tool_names=names,
            schema_versions={entry.name: entry.tool_schema_version for entry in entries},
            reason=reason,
        )


__all__ = ["ActiveToolSetManager"]
