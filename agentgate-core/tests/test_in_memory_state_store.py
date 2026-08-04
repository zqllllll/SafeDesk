from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from itertools import count

import pytest

from agentgate_core.contracts import (
    CompletionCondition,
    EffectKind,
    EffectRecord,
    EffectStatus,
    EvidenceItem,
    EvidenceSourceType,
    EvidenceStatus,
    FailureRecord,
    FailureStatus,
    FailureType,
    ResourceRef,
    ResponsibleLayer,
    SubgoalDefinition,
    SubgoalState,
    TaskContract,
    TaskPhase,
    TaskState,
    VerificationResult,
    VerificationStatus,
    VerificationType,
)
from agentgate_core.runtime import (
    IdempotencyConflictError,
    InMemoryTypedStateStore,
    RecordConflictError,
    RecordNotFoundError,
    RecordVersionConflictError,
    StateInvariantError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
    TaskStateEvent,
    TaskStateEventType,
    TypedStateStore,
    VersionConflictError,
)

NOW = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)


def make_store() -> InMemoryTypedStateStore:
    identifiers = count(1)
    return InMemoryTypedStateStore(clock=lambda: NOW, id_factory=lambda: str(next(identifiers)))


def make_contract(task_id: str = "task-1") -> TaskContract:
    return TaskContract(
        task_id=task_id,
        original_instruction="Create the requested record and verify it.",
        normalized_goal="Create one verified record.",
        subgoals=(
            SubgoalDefinition(
                subgoal_id="create-record",
                description="Create the record.",
                completion_condition_ids=("record-exists",),
            ),
        ),
        completion_conditions=(
            CompletionCondition(
                condition_id="record-exists",
                description="The expected record exists.",
                required_effect_ids=("effect-1",),
            ),
        ),
        required_confirmations=("confirm-create",),
        created_at=NOW,
    )


def make_transition(
    current: TaskState,
    *,
    event_id: str,
    phase: TaskPhase,
    effect_ids: tuple[str, ...] | None = None,
    failure_ids: tuple[str, ...] | None = None,
) -> TaskStateEvent:
    updated_at = current.updated_at + timedelta(seconds=1)
    payload = current.model_dump(mode="python")
    payload.update(
        state_version=current.state_version + 1,
        phase=phase,
        effect_ids=current.effect_ids if effect_ids is None else effect_ids,
        failure_ids=current.failure_ids if failure_ids is None else failure_ids,
        updated_at=updated_at,
    )
    next_state = TaskState.model_validate(payload)
    return TaskStateEvent(
        event_id=event_id,
        task_id=current.task_id,
        event_type=TaskStateEventType.TRANSITION,
        name=f"enter_{phase.value}",
        previous_state_version=current.state_version,
        next_state=next_state,
        reason=f"Move the task into the {phase.value} phase.",
        occurred_at=updated_at,
    )


def make_evidence(*, status: EvidenceStatus = EvidenceStatus.OBSERVED) -> EvidenceItem:
    verification_ids = ("verification-1",) if status is EvidenceStatus.VERIFIED else ()
    return EvidenceItem(
        evidence_id="evidence-1",
        task_id="task-1",
        subject="record-1",
        predicate="record_fields",
        value={"name": "Quarterly review"},
        source_type=EvidenceSourceType.TOOL_RESULT,
        source_event_id="trace-event-1",
        observed_at=NOW,
        status=status,
        verification_ids=verification_ids,
    )


def make_verification() -> VerificationResult:
    return VerificationResult(
        verification_id="verification-1",
        task_id="task-1",
        verification_type=VerificationType.GOAL,
        target_id="record-exists",
        verifier_name="record-readback",
        verifier_version="1",
        expected_state={"name": "Quarterly review"},
        observed_state={"name": "Quarterly review"},
        status=VerificationStatus.VERIFIED,
        evidence_ids=("evidence-1",),
        checked_at=NOW,
    )


def make_effect(
    effect_id: str = "effect-1",
    *,
    idempotency_key: str = "task-1:create:record-1",
    status: EffectStatus = EffectStatus.PLANNED,
) -> EffectRecord:
    return EffectRecord(
        effect_id=effect_id,
        task_id="task-1",
        action_id=f"action-{effect_id}",
        idempotency_key=idempotency_key,
        kind=EffectKind.CREATE,
        operation="create",
        resource=ResourceRef(resource_type="record", resource_id=effect_id),
        expected_change={"name": "Quarterly review"},
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def make_failure(*, status: FailureStatus = FailureStatus.OPEN) -> FailureRecord:
    return FailureRecord(
        failure_id="failure-1",
        task_id="task-1",
        failure_type=FailureType.TOOL_EXECUTION_ERROR,
        message="The write tool returned an error.",
        retryable=True,
        responsible_layer=ResponsibleLayer.TOOL,
        evidence_ids=("evidence-1",),
        recovery_budget_remaining=2,
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def test_create_task_builds_versioned_initial_state_and_created_event() -> None:
    store = make_store()
    assert isinstance(store, TypedStateStore)

    state = store.create_task(make_contract())

    assert state.state_version == 1
    assert state.phase is TaskPhase.COLLECT
    assert state.pending_confirmation_ids == ("confirm-create",)
    assert state.subgoals == (SubgoalState(subgoal_id="create-record"),)
    assert store.get_task_contract("task-1") == make_contract()
    events = store.list_task_events("task-1")
    assert len(events) == 1
    assert events[0].event_type is TaskStateEventType.CREATED
    assert events[0].previous_state_version == 0


def test_create_and_lookup_errors_are_explicit() -> None:
    store = make_store()
    store.create_task(make_contract())

    with pytest.raises(TaskAlreadyExistsError):
        store.create_task(make_contract())
    with pytest.raises(TaskNotFoundError):
        store.get_task_state("missing-task")
    with pytest.raises(RecordNotFoundError):
        store.get_evidence("task-1", "missing-evidence")


def test_initial_state_must_match_contract_and_start_at_version_one() -> None:
    store = make_store()
    invalid_state = TaskState(
        task_id="task-1",
        contract_version=1,
        state_version=2,
        phase=TaskPhase.COLLECT,
        subgoals=(SubgoalState(subgoal_id="unknown-subgoal"),),
        updated_at=NOW,
    )
    with pytest.raises(StateInvariantError, match="state_version=1"):
        store.create_task(make_contract(), invalid_state)

    wrong_subgoal_state = invalid_state.model_copy(update={"state_version": 1})
    with pytest.raises(StateInvariantError, match="subgoals must exactly match"):
        store.create_task(make_contract(), wrong_subgoal_state)


def test_apply_event_uses_optimistic_versioning_and_is_idempotent() -> None:
    store = make_store()
    current = store.create_task(make_contract())
    event = make_transition(current, event_id="transition-1", phase=TaskPhase.ACT)

    updated = store.apply_task_event("task-1", event, expected_version=1)
    assert updated.state_version == 2
    assert updated.phase is TaskPhase.ACT
    assert store.apply_task_event("task-1", event, expected_version=1) == updated

    stale_event = make_transition(current, event_id="transition-stale", phase=TaskPhase.VERIFY)
    with pytest.raises(VersionConflictError) as error:
        store.apply_task_event("task-1", stale_event, expected_version=1)
    assert error.value.actual_version == 2

    conflicting_event = event.model_copy(update={"name": "different_transition"})
    with pytest.raises(RecordConflictError, match="task state event"):
        store.apply_task_event("task-1", conflicting_event, expected_version=1)


def test_state_transition_rejects_dangling_record_references() -> None:
    store = make_store()
    current = store.create_task(make_contract())
    event = make_transition(current, event_id="transition-1", phase=TaskPhase.ACT, effect_ids=("missing-effect",))

    with pytest.raises(StateInvariantError, match="unknown effect references"):
        store.apply_task_event("task-1", event, expected_version=1)


def test_parallel_writers_cannot_overwrite_each_other() -> None:
    store = make_store()
    current = store.create_task(make_contract())
    events = (
        make_transition(current, event_id="transition-act", phase=TaskPhase.ACT),
        make_transition(current, event_id="transition-verify", phase=TaskPhase.VERIFY),
    )

    def apply(event: TaskStateEvent) -> str:
        try:
            store.apply_task_event("task-1", event, expected_version=1)
        except VersionConflictError:
            return "conflict"
        return "applied"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(apply, events))

    assert sorted(results) == ["applied", "conflict"]
    assert store.get_task_state("task-1").state_version == 2


def test_state_event_identifiers_are_unique_across_tasks() -> None:
    store = make_store()
    first_state = store.create_task(make_contract("task-1"))
    second_state = store.create_task(make_contract("task-2"))
    first_event = make_transition(first_state, event_id="shared-event", phase=TaskPhase.ACT)
    second_event = make_transition(second_state, event_id="shared-event", phase=TaskPhase.ACT)
    store.apply_task_event("task-1", first_event, expected_version=1)

    with pytest.raises(RecordConflictError, match="task state event"):
        store.apply_task_event("task-2", second_event, expected_version=1)


def test_evidence_and_verification_are_idempotent_defensively_copied_and_linked() -> None:
    store = make_store()
    store.create_task(make_contract())
    evidence = make_evidence()

    assert store.append_evidence("task-1", evidence) == evidence
    assert store.append_evidence("task-1", evidence) == evidence
    assert isinstance(evidence.value, dict)
    evidence.value["name"] = "mutated outside the store"
    assert store.get_evidence("task-1", "evidence-1").value == {"name": "Quarterly review"}

    conflicting = make_evidence().model_copy(update={"value": {"name": "Different"}})
    with pytest.raises(RecordConflictError):
        store.append_evidence("task-1", conflicting)

    verification = make_verification()
    store.append_verification("task-1", verification)
    verified_evidence = make_evidence(status=EvidenceStatus.VERIFIED)
    store.update_evidence("task-1", verified_evidence, expected_status=EvidenceStatus.OBSERVED)
    assert store.get_evidence("task-1", "evidence-1").status is EvidenceStatus.VERIFIED

    with pytest.raises(RecordVersionConflictError):
        store.update_evidence("task-1", verified_evidence, expected_status=EvidenceStatus.OBSERVED)


def test_verified_records_cannot_reference_unknown_verification() -> None:
    store = make_store()
    store.create_task(make_contract())

    with pytest.raises(StateInvariantError, match="unknown verification references"):
        store.append_evidence("task-1", make_evidence(status=EvidenceStatus.VERIFIED))


def test_effect_idempotency_and_optimistic_status_update() -> None:
    store = make_store()
    store.create_task(make_contract())
    planned = make_effect()
    store.append_effect("task-1", planned)

    duplicate_key = make_effect("effect-2")
    with pytest.raises(IdempotencyConflictError):
        store.append_effect("task-1", duplicate_key)

    payload = planned.model_dump(mode="python")
    payload.update(status=EffectStatus.IN_FLIGHT, updated_at=NOW + timedelta(seconds=1))
    in_flight = EffectRecord.model_validate(payload)
    store.update_effect("task-1", in_flight, expected_status=EffectStatus.PLANNED)
    assert store.get_effect("task-1", "effect-1").status is EffectStatus.IN_FLIGHT

    with pytest.raises(RecordVersionConflictError):
        store.update_effect("task-1", in_flight, expected_status=EffectStatus.PLANNED)

    changed_operation = in_flight.model_copy(update={"operation": "delete"})
    with pytest.raises(RecordConflictError, match="immutable fields"):
        store.update_effect("task-1", changed_operation, expected_status=EffectStatus.IN_FLIGHT)


def test_failure_update_uses_expected_status_and_preserves_identity() -> None:
    store = make_store()
    store.create_task(make_contract())
    store.append_evidence("task-1", make_evidence())
    failure = make_failure()
    store.append_failure("task-1", failure)

    payload = failure.model_dump(mode="python")
    payload.update(
        status=FailureStatus.RECOVERING,
        attempt_count=1,
        recovery_budget_remaining=1,
        updated_at=NOW + timedelta(seconds=1),
    )
    recovering = FailureRecord.model_validate(payload)
    store.update_failure("task-1", recovering, expected_status=FailureStatus.OPEN)
    assert store.get_failure("task-1", "failure-1").attempt_count == 1

    with pytest.raises(RecordVersionConflictError):
        store.update_failure("task-1", recovering, expected_status=FailureStatus.OPEN)


def test_checkpoint_restore_is_monotonic_and_marks_all_in_flight_effects_unknown() -> None:
    store = make_store()
    state_v1 = store.create_task(make_contract())
    effect_before_checkpoint = make_effect(status=EffectStatus.IN_FLIGHT)
    store.append_effect("task-1", effect_before_checkpoint)
    event_v2 = make_transition(
        state_v1,
        event_id="transition-1",
        phase=TaskPhase.ACT,
        effect_ids=("effect-1",),
    )
    state_v2 = store.apply_task_event("task-1", event_v2, expected_version=1)
    checkpoint = store.create_checkpoint("task-1", "checkpoint-1")
    assert checkpoint.source_state_version == 2
    assert checkpoint.event_count == 2

    event_v3 = make_transition(state_v2, event_id="transition-2", phase=TaskPhase.REPAIR)
    store.apply_task_event("task-1", event_v3, expected_version=2)
    store.append_effect(
        "task-1",
        make_effect("effect-2", idempotency_key="task-1:create:record-2", status=EffectStatus.IN_FLIGHT),
    )

    restored = store.restore_checkpoint("task-1", "checkpoint-1", expected_version=3)
    assert restored.state_version == 4
    assert restored.phase is TaskPhase.ACT
    assert restored.effect_ids == ("effect-1", "effect-2")
    assert {effect.status for effect in store.list_effects("task-1")} == {EffectStatus.UNKNOWN}
    assert store.list_task_events("task-1")[-1].event_type is TaskStateEventType.CHECKPOINT_RESTORED

    with pytest.raises(VersionConflictError):
        store.restore_checkpoint("task-1", "checkpoint-1", expected_version=3)
    with pytest.raises(RecordConflictError, match="checkpoint"):
        store.create_checkpoint("task-1", "checkpoint-1")
