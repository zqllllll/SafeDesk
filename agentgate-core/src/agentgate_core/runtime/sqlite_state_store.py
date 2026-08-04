"""SQLite persistence backend for the TypedStateStore protocol."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

from agentgate_core.contracts.base import utc_now
from agentgate_core.contracts.effect import EffectRecord, EffectStatus
from agentgate_core.contracts.evidence import EvidenceItem, EvidenceStatus
from agentgate_core.contracts.failure import FailureRecord, FailureStatus
from agentgate_core.contracts.task import TaskContract, TaskState
from agentgate_core.contracts.verification import VerificationResult
from agentgate_core.runtime.in_memory_state_store import InMemoryTypedStateStore
from agentgate_core.runtime.snapshot import StoredTaskAggregate
from agentgate_core.runtime.state_store import (
    StateCheckpoint,
    StatePersistenceConflictError,
    StatePersistenceError,
    TaskStateEvent,
)


class SQLiteTypedStateStore:
    """Persist validated aggregate snapshots with optimistic storage revisions."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._id_factory = id_factory
        self._lock = RLock()
        try:
            self._connection = sqlite3.connect(self.database_path, isolation_level=None, check_same_thread=False)
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_aggregates (
                    task_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS state_store_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO state_store_metadata(key, value) VALUES ('schema_version', '1')"
            )
        except sqlite3.Error as exc:
            raise StatePersistenceError(f"could not initialize SQLite state store: {type(exc).__name__}") from exc
        self._memory = self._new_memory_store()
        self._revisions: dict[str, int] = {}
        self.refresh()

    def _new_memory_store(self) -> InMemoryTypedStateStore:
        return InMemoryTypedStateStore(clock=self._clock, id_factory=self._id_factory)

    def refresh(self) -> None:
        """Reload committed snapshots, for example after another process won a write race."""

        with self._lock:
            try:
                rows = self._connection.execute(
                    "SELECT task_id, revision, snapshot_json FROM task_aggregates ORDER BY task_id"
                ).fetchall()
            except sqlite3.Error as exc:
                raise StatePersistenceError(f"could not load SQLite state store: {type(exc).__name__}") from exc
            restored = self._new_memory_store()
            revisions: dict[str, int] = {}
            try:
                for task_id, revision, snapshot_json in rows:
                    snapshot = StoredTaskAggregate.model_validate_json(snapshot_json)
                    if snapshot.contract.task_id != task_id:
                        raise StatePersistenceError(f"snapshot task ID does not match SQLite key: {task_id}")
                    restored.import_snapshot(snapshot)
                    revisions[task_id] = int(revision)
            except StatePersistenceError:
                raise
            except Exception as exc:
                raise StatePersistenceError(
                    f"could not validate persisted task snapshot: {type(exc).__name__}"
                ) from exc
            self._memory = restored
            self._revisions = revisions

    @staticmethod
    def _snapshot_json(snapshot: StoredTaskAggregate) -> str:
        return snapshot.model_dump_json()

    def _persist_snapshot(self, snapshot: StoredTaskAggregate, *, create: bool) -> None:
        task_id = snapshot.contract.task_id
        snapshot_json = self._snapshot_json(snapshot)
        expected_revision = self._revisions.get(task_id)
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if create:
                try:
                    self._connection.execute(
                        "INSERT INTO task_aggregates(task_id, revision, snapshot_json) VALUES (?, 1, ?)",
                        (task_id, snapshot_json),
                    )
                except sqlite3.IntegrityError:
                    row = self._connection.execute(
                        "SELECT revision FROM task_aggregates WHERE task_id = ?", (task_id,)
                    ).fetchone()
                    self._connection.execute("ROLLBACK")
                    actual_revision = int(row[0]) if row is not None else None
                    raise StatePersistenceConflictError(task_id, None, actual_revision) from None
                next_revision = 1
            else:
                if expected_revision is None:
                    self._connection.execute("ROLLBACK")
                    raise StatePersistenceConflictError(task_id, None, None)
                cursor = self._connection.execute(
                    "UPDATE task_aggregates SET revision = ?, snapshot_json = ? WHERE task_id = ? AND revision = ?",
                    (expected_revision + 1, snapshot_json, task_id, expected_revision),
                )
                if cursor.rowcount != 1:
                    row = self._connection.execute(
                        "SELECT revision FROM task_aggregates WHERE task_id = ?", (task_id,)
                    ).fetchone()
                    self._connection.execute("ROLLBACK")
                    actual_revision = int(row[0]) if row is not None else None
                    raise StatePersistenceConflictError(task_id, expected_revision, actual_revision)
                next_revision = expected_revision + 1
            self._connection.execute("COMMIT")
            self._revisions[task_id] = next_revision
        except StatePersistenceConflictError:
            raise
        except sqlite3.Error as exc:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise StatePersistenceError(f"could not persist task snapshot: {type(exc).__name__}") from exc

    def _mutate(self, task_id: str, operation: Callable[[], Any], *, create: bool = False) -> Any:
        with self._lock:
            before = None
            if not create:
                before = self._snapshot_json(self._memory.export_snapshot(task_id))
            result = operation()
            snapshot = self._memory.export_snapshot(task_id)
            after = self._snapshot_json(snapshot)
            if before == after:
                return result
            try:
                self._persist_snapshot(snapshot, create=create)
            except (StatePersistenceConflictError, StatePersistenceError):
                self.refresh()
                raise
            return result

    def create_task(self, contract: TaskContract, initial_state: TaskState | None = None) -> TaskState:
        return self._mutate(
            contract.task_id,
            lambda: self._memory.create_task(contract, initial_state),
            create=True,
        )

    def get_task_contract(self, task_id: str) -> TaskContract:
        return self._memory.get_task_contract(task_id)

    def get_task_state(self, task_id: str) -> TaskState:
        return self._memory.get_task_state(task_id)

    def apply_task_event(self, task_id: str, event: TaskStateEvent, expected_version: int) -> TaskState:
        return self._mutate(task_id, lambda: self._memory.apply_task_event(task_id, event, expected_version))

    def list_task_events(self, task_id: str) -> tuple[TaskStateEvent, ...]:
        return self._memory.list_task_events(task_id)

    def append_evidence(self, task_id: str, evidence: EvidenceItem) -> EvidenceItem:
        return self._mutate(task_id, lambda: self._memory.append_evidence(task_id, evidence))

    def update_evidence(self, task_id: str, evidence: EvidenceItem, expected_status: EvidenceStatus) -> EvidenceItem:
        return self._mutate(
            task_id,
            lambda: self._memory.update_evidence(task_id, evidence, expected_status),
        )

    def get_evidence(self, task_id: str, evidence_id: str) -> EvidenceItem:
        return self._memory.get_evidence(task_id, evidence_id)

    def list_evidence(self, task_id: str) -> tuple[EvidenceItem, ...]:
        return self._memory.list_evidence(task_id)

    def append_effect(self, task_id: str, effect: EffectRecord) -> EffectRecord:
        return self._mutate(task_id, lambda: self._memory.append_effect(task_id, effect))

    def update_effect(self, task_id: str, effect: EffectRecord, expected_status: EffectStatus) -> EffectRecord:
        return self._mutate(task_id, lambda: self._memory.update_effect(task_id, effect, expected_status))

    def get_effect(self, task_id: str, effect_id: str) -> EffectRecord:
        return self._memory.get_effect(task_id, effect_id)

    def list_effects(self, task_id: str) -> tuple[EffectRecord, ...]:
        return self._memory.list_effects(task_id)

    def append_verification(self, task_id: str, verification: VerificationResult) -> VerificationResult:
        return self._mutate(task_id, lambda: self._memory.append_verification(task_id, verification))

    def get_verification(self, task_id: str, verification_id: str) -> VerificationResult:
        return self._memory.get_verification(task_id, verification_id)

    def list_verifications(self, task_id: str) -> tuple[VerificationResult, ...]:
        return self._memory.list_verifications(task_id)

    def append_failure(self, task_id: str, failure: FailureRecord) -> FailureRecord:
        return self._mutate(task_id, lambda: self._memory.append_failure(task_id, failure))

    def update_failure(self, task_id: str, failure: FailureRecord, expected_status: FailureStatus) -> FailureRecord:
        return self._mutate(task_id, lambda: self._memory.update_failure(task_id, failure, expected_status))

    def get_failure(self, task_id: str, failure_id: str) -> FailureRecord:
        return self._memory.get_failure(task_id, failure_id)

    def list_failures(self, task_id: str) -> tuple[FailureRecord, ...]:
        return self._memory.list_failures(task_id)

    def create_checkpoint(self, task_id: str, checkpoint_id: str | None = None) -> StateCheckpoint:
        return self._mutate(task_id, lambda: self._memory.create_checkpoint(task_id, checkpoint_id))

    def restore_checkpoint(
        self,
        task_id: str,
        checkpoint_id: str,
        *,
        expected_version: int | None = None,
    ) -> TaskState:
        return self._mutate(
            task_id,
            lambda: self._memory.restore_checkpoint(
                task_id,
                checkpoint_id,
                expected_version=expected_version,
            ),
        )

    def list_checkpoints(self, task_id: str) -> tuple[StateCheckpoint, ...]:
        return self._memory.list_checkpoints(task_id)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> SQLiteTypedStateStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = ["SQLiteTypedStateStore"]
