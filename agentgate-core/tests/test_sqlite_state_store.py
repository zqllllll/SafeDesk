from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path

import pytest

from agentgate_core.contracts import (
    CompletionCondition,
    EffectKind,
    EffectRecord,
    EffectStatus,
    EvidenceItem,
    EvidenceSourceType,
    ResourceRef,
    SubgoalDefinition,
    TaskContract,
    TaskPhase,
    TaskState,
)
from agentgate_core.runtime import (
    SQLiteTypedStateStore,
    StatePersistenceConflictError,
    TaskStateEvent,
    TaskStateEventType,
    TypedStateStore,
)

NOW = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)


def make_contract() -> TaskContract:
    return TaskContract(
        task_id="task-1",
        original_instruction="Create the requested record.",
        normalized_goal="Create one record.",
        subgoals=(
            SubgoalDefinition(
                subgoal_id="create-record",
                description="Create the record.",
                completion_condition_ids=("record-exists",),
            ),
        ),
        completion_conditions=(CompletionCondition(condition_id="record-exists", description="The record exists."),),
        created_at=NOW,
    )


def make_evidence() -> EvidenceItem:
    return EvidenceItem(
        evidence_id="evidence-1",
        task_id="task-1",
        subject="record-1",
        predicate="record_fields",
        value={"name": "Quarterly review"},
        source_type=EvidenceSourceType.TOOL_RESULT,
        source_event_id="trace-event-1",
        observed_at=NOW,
    )


def make_effect() -> EffectRecord:
    return EffectRecord(
        effect_id="effect-1",
        task_id="task-1",
        action_id="action-1",
        idempotency_key="task-1:create:record-1",
        kind=EffectKind.CREATE,
        operation="create",
        resource=ResourceRef(resource_type="record", resource_id="1"),
        expected_change={"name": "Quarterly review"},
        status=EffectStatus.IN_FLIGHT,
        created_at=NOW,
        updated_at=NOW,
    )


def make_transition(current: TaskState) -> TaskStateEvent:
    updated_at = current.updated_at + timedelta(seconds=1)
    next_state = TaskState.model_validate(
        {
            **current.model_dump(mode="python"),
            "state_version": current.state_version + 1,
            "phase": TaskPhase.ACT,
            "effect_ids": ("effect-1",),
            "updated_at": updated_at,
        }
    )
    return TaskStateEvent(
        event_id="transition-1",
        task_id="task-1",
        event_type=TaskStateEventType.TRANSITION,
        name="enter_act",
        previous_state_version=current.state_version,
        next_state=next_state,
        reason="Enter action phase.",
        occurred_at=updated_at,
    )


def open_store(path: Path) -> SQLiteTypedStateStore:
    identifiers = count(1)
    return SQLiteTypedStateStore(path, clock=lambda: NOW, id_factory=lambda: str(next(identifiers)))


def test_sqlite_store_persists_full_aggregate_and_checkpoint_restore(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    with open_store(database) as store:
        assert isinstance(store, TypedStateStore)
        state = store.create_task(make_contract())
        store.append_evidence("task-1", make_evidence())
        store.append_effect("task-1", make_effect())
        state = store.apply_task_event("task-1", make_transition(state), expected_version=1)
        store.create_checkpoint("task-1", "checkpoint-1")
        assert state.state_version == 2

    with open_store(database) as reopened:
        assert reopened.get_task_contract("task-1") == make_contract()
        assert reopened.get_evidence("task-1", "evidence-1") == make_evidence()
        assert reopened.get_effect("task-1", "effect-1").status is EffectStatus.IN_FLIGHT
        assert reopened.list_checkpoints("task-1")[0].checkpoint_id == "checkpoint-1"
        restored = reopened.restore_checkpoint("task-1", "checkpoint-1", expected_version=2)
        assert restored.state_version == 3
        assert reopened.get_effect("task-1", "effect-1").status is EffectStatus.UNKNOWN

    with open_store(database) as final:
        assert final.get_task_state("task-1").state_version == 3
        assert final.get_effect("task-1", "effect-1").status is EffectStatus.UNKNOWN
        assert len(final.list_task_events("task-1")) == 3


def test_sqlite_storage_revision_prevents_cross_instance_lost_updates(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    first = open_store(database)
    first.create_task(make_contract())
    second = open_store(database)
    try:
        first.append_evidence("task-1", make_evidence())
        with pytest.raises(StatePersistenceConflictError):
            second.append_effect("task-1", make_effect())
        assert second.get_evidence("task-1", "evidence-1") == make_evidence()
        assert second.list_effects("task-1") == ()
    finally:
        first.close()
        second.close()


def test_sqlite_cross_instance_effect_reservation_allows_only_one_writer(tmp_path: Path) -> None:
    """A stale second writer cannot persist the same fingerprint after reservation."""

    database = tmp_path / "state.sqlite3"
    first = open_store(database)
    first.create_task(make_contract())
    second = open_store(database)
    try:
        first.append_effect(make_contract().task_id, make_effect())
        competing = make_effect().model_copy(update={"effect_id": "effect-2", "action_id": "action-2"})
        with pytest.raises(StatePersistenceConflictError):
            second.append_effect("task-1", competing)
        assert second.list_effects("task-1") == (make_effect(),)
    finally:
        first.close()
        second.close()


def test_idempotent_replay_does_not_create_extra_storage_revision(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    with open_store(database) as store:
        store.create_task(make_contract())
        store.append_evidence("task-1", make_evidence())
        store.append_evidence("task-1", make_evidence())

    import sqlite3

    with sqlite3.connect(database) as connection:
        revision = connection.execute("SELECT revision FROM task_aggregates WHERE task_id = 'task-1'").fetchone()[0]
    assert revision == 2
