"""Append-only TraceEvent sinks with deterministic integrity checks."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from threading import RLock
from typing import Protocol, runtime_checkable

from agentgate_core.contracts.trace import TraceEvent


class TraceError(RuntimeError):
    """Base error for trace recording and persistence."""


class TraceConflictError(TraceError):
    """Raised when an event identifier is reused with different content."""


class TraceSequenceError(TraceError):
    """Raised when a stream is not appended with the next sequence number."""


class TraceParentError(TraceError):
    """Raised when a parent event is absent or belongs to another stream."""


class TracePersistenceError(TraceError):
    """Raised when a sink cannot durably append an otherwise valid event."""


@runtime_checkable
class TraceSink(Protocol):
    def append(self, event: TraceEvent) -> TraceEvent: ...

    def get_event(self, event_id: str) -> TraceEvent | None: ...

    def list_events(self, task_id: str, run_id: str) -> tuple[TraceEvent, ...]: ...

    def last_sequence(self, task_id: str, run_id: str) -> int | None: ...

    def close(self) -> None: ...


class InMemoryTraceSink:
    """Thread-safe sink used by unit tests and in-process experiments."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._events_by_id: dict[str, TraceEvent] = {}
        self._streams: dict[tuple[str, str], list[TraceEvent]] = defaultdict(list)

    def append(self, event: TraceEvent) -> TraceEvent:
        with self._lock:
            existing = self._events_by_id.get(event.event_id)
            if existing is not None:
                if existing != event:
                    raise TraceConflictError(f"trace event ID already exists with different content: {event.event_id}")
                return existing.model_copy(deep=True)
            stream_key = (event.task_id, event.run_id)
            stream = self._streams[stream_key]
            expected_sequence = len(stream)
            if event.sequence_number != expected_sequence:
                raise TraceSequenceError(
                    f"expected sequence {expected_sequence} for {stream_key}, got {event.sequence_number}"
                )
            if event.parent_event_id is not None:
                parent = self._events_by_id.get(event.parent_event_id)
                if parent is None or (parent.task_id, parent.run_id) != stream_key:
                    raise TraceParentError(f"invalid parent event: {event.parent_event_id}")
            stored = event.model_copy(deep=True)
            self._events_by_id[event.event_id] = stored
            stream.append(stored)
            return stored.model_copy(deep=True)

    def get_event(self, event_id: str) -> TraceEvent | None:
        with self._lock:
            event = self._events_by_id.get(event_id)
            return event.model_copy(deep=True) if event is not None else None

    def list_events(self, task_id: str, run_id: str) -> tuple[TraceEvent, ...]:
        with self._lock:
            return tuple(event.model_copy(deep=True) for event in self._streams.get((task_id, run_id), ()))

    def last_sequence(self, task_id: str, run_id: str) -> int | None:
        with self._lock:
            stream = self._streams.get((task_id, run_id), ())
            return stream[-1].sequence_number if stream else None

    def close(self) -> None:
        return None


def _canonical_event(event: TraceEvent) -> str:
    return json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


class SQLiteTraceSink:
    """Durable SQLite sink with global event IDs and per-run sequence constraints."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        try:
            self._connection = sqlite3.connect(self.database_path, isolation_level=None, check_same_thread=False)
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trace_events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    sequence_number INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    UNIQUE(task_id, run_id, sequence_number)
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS trace_stream_idx ON trace_events(task_id, run_id, sequence_number)"
            )
        except sqlite3.Error as exc:
            raise TracePersistenceError(f"could not initialize SQLite trace sink: {type(exc).__name__}") from exc

    def append(self, event: TraceEvent) -> TraceEvent:
        event_json = _canonical_event(event)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                existing = self._connection.execute(
                    "SELECT event_json FROM trace_events WHERE event_id = ?", (event.event_id,)
                ).fetchone()
                if existing is not None:
                    self._connection.execute("ROLLBACK")
                    existing_event = TraceEvent.model_validate_json(existing[0])
                    if existing_event != event:
                        raise TraceConflictError(
                            f"trace event ID already exists with different content: {event.event_id}"
                        )
                    return existing_event
                row = self._connection.execute(
                    "SELECT MAX(sequence_number) FROM trace_events WHERE task_id = ? AND run_id = ?",
                    (event.task_id, event.run_id),
                ).fetchone()
                last_sequence = row[0] if row is not None else None
                expected_sequence = 0 if last_sequence is None else int(last_sequence) + 1
                if event.sequence_number != expected_sequence:
                    self._connection.execute("ROLLBACK")
                    raise TraceSequenceError(
                        f"expected sequence {expected_sequence} for {(event.task_id, event.run_id)}, "
                        f"got {event.sequence_number}"
                    )
                if event.parent_event_id is not None:
                    parent = self._connection.execute(
                        "SELECT task_id, run_id FROM trace_events WHERE event_id = ?", (event.parent_event_id,)
                    ).fetchone()
                    if parent != (event.task_id, event.run_id):
                        self._connection.execute("ROLLBACK")
                        raise TraceParentError(f"invalid parent event: {event.parent_event_id}")
                self._connection.execute(
                    "INSERT INTO trace_events(event_id, task_id, run_id, sequence_number, event_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (event.event_id, event.task_id, event.run_id, event.sequence_number, event_json),
                )
                self._connection.execute("COMMIT")
                return event.model_copy(deep=True)
            except (TraceConflictError, TraceParentError, TraceSequenceError):
                raise
            except sqlite3.Error as exc:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise TracePersistenceError(f"could not append SQLite trace event: {type(exc).__name__}") from exc

    def get_event(self, event_id: str) -> TraceEvent | None:
        with self._lock:
            try:
                row = self._connection.execute(
                    "SELECT event_json FROM trace_events WHERE event_id = ?", (event_id,)
                ).fetchone()
            except sqlite3.Error as exc:
                raise TracePersistenceError(f"could not read SQLite trace event: {type(exc).__name__}") from exc
        return TraceEvent.model_validate_json(row[0]) if row is not None else None

    def list_events(self, task_id: str, run_id: str) -> tuple[TraceEvent, ...]:
        with self._lock:
            try:
                rows = self._connection.execute(
                    "SELECT event_json FROM trace_events WHERE task_id = ? AND run_id = ? ORDER BY sequence_number",
                    (task_id, run_id),
                ).fetchall()
            except sqlite3.Error as exc:
                raise TracePersistenceError(f"could not list SQLite trace events: {type(exc).__name__}") from exc
        return tuple(TraceEvent.model_validate_json(row[0]) for row in rows)

    def last_sequence(self, task_id: str, run_id: str) -> int | None:
        with self._lock:
            try:
                row = self._connection.execute(
                    "SELECT MAX(sequence_number) FROM trace_events WHERE task_id = ? AND run_id = ?",
                    (task_id, run_id),
                ).fetchone()
            except sqlite3.Error as exc:
                raise TracePersistenceError(f"could not read SQLite trace sequence: {type(exc).__name__}") from exc
        return int(row[0]) if row is not None and row[0] is not None else None

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> SQLiteTraceSink:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "InMemoryTraceSink",
    "SQLiteTraceSink",
    "TraceConflictError",
    "TraceError",
    "TraceParentError",
    "TracePersistenceError",
    "TraceSequenceError",
    "TraceSink",
]
