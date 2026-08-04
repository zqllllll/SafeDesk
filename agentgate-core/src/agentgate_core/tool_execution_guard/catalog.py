"""Read-only tool catalog interface and in-memory implementation."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentgate_core.contracts.catalog import ToolCatalogEntry, ToolCatalogSnapshot


class ToolCatalogError(RuntimeError):
    """Base error for tool-catalog lookups."""


class ToolNotFoundError(ToolCatalogError):
    def __init__(self, tool_name: str) -> None:
        super().__init__(f"tool not found in catalog: {tool_name}")
        self.tool_name = tool_name


@runtime_checkable
class ToolCatalog(Protocol):
    @property
    def catalog_version(self) -> str: ...

    def get_tool(self, tool_name: str) -> ToolCatalogEntry: ...

    def list_tools(self) -> tuple[ToolCatalogEntry, ...]: ...

    def snapshot(self) -> ToolCatalogSnapshot: ...


class InMemoryToolCatalog:
    """Immutable catalog snapshot suitable for tests and adapter construction."""

    def __init__(self, snapshot: ToolCatalogSnapshot) -> None:
        self._snapshot = snapshot.model_copy(deep=True)
        self._entries = {entry.name: entry.model_copy(deep=True) for entry in snapshot.entries}

    @property
    def catalog_version(self) -> str:
        return self._snapshot.catalog_version

    def get_tool(self, tool_name: str) -> ToolCatalogEntry:
        try:
            return self._entries[tool_name].model_copy(deep=True)
        except KeyError as error:
            raise ToolNotFoundError(tool_name) from error

    def list_tools(self) -> tuple[ToolCatalogEntry, ...]:
        return tuple(entry.model_copy(deep=True) for entry in self._snapshot.entries)

    def snapshot(self) -> ToolCatalogSnapshot:
        return self._snapshot.model_copy(deep=True)


__all__ = [
    "InMemoryToolCatalog",
    "ToolCatalog",
    "ToolCatalogError",
    "ToolNotFoundError",
]
