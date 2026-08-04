from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agentgate_core.contracts import (
    ActionIR,
    ActionKind,
    ActorKind,
    CompletionCondition,
    ContextBudgetStatus,
    ContextPack,
    ContextPriority,
    ContextSection,
    EffectKind,
    EffectRecord,
    EffectStatus,
    EvidenceItem,
    EvidenceSourceType,
    EvidenceStatus,
    ExpectedEffect,
    FailureRecord,
    FailureStatus,
    FailureType,
    RedactionMetadata,
    ResourceRef,
    ResponsibleLayer,
    RiskLevel,
    SubgoalDefinition,
    SubgoalState,
    TaskContract,
    TaskPhase,
    TaskState,
    TraceActor,
    TraceEvent,
    TraceEventType,
    VerificationResult,
    VerificationStatus,
    VerificationType,
)

NOW = datetime(2026, 7, 16, 8, 0, tzinfo=UTC)


def make_task_contract() -> TaskContract:
    return TaskContract(
        task_id="task-1",
        original_instruction="Create the requested record and verify it.",
        normalized_goal="Create one record with the requested fields.",
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
                description="The record exists with the expected fields.",
                required_effect_ids=("effect-1",),
            ),
        ),
        allowed_effects=("create:record",),
        created_at=NOW,
    )


def make_resource() -> ResourceRef:
    return ResourceRef(resource_type="record", resource_id="record-1", scope="workspace")


def make_expected_effect() -> ExpectedEffect:
    return ExpectedEffect(
        effect_key="effect-1",
        kind=EffectKind.CREATE,
        resource=make_resource(),
        expected_change={"name": "Quarterly review"},
    )


def test_task_contract_round_trips_and_rejects_unknown_dependencies() -> None:
    contract = make_task_contract()
    assert TaskContract.model_validate_json(contract.model_dump_json()) == contract

    payload = contract.model_dump(mode="json")
    payload["subgoals"][0]["dependency_ids"] = ["missing-subgoal"]
    with pytest.raises(ValidationError, match="unknown dependencies"):
        TaskContract.model_validate(payload)


def test_task_contract_rejects_cyclic_dependencies() -> None:
    contract = make_task_contract()
    payload = contract.model_dump(mode="json")
    payload["subgoals"] = [
        {
            "subgoal_id": "first",
            "description": "Complete the first step.",
            "dependency_ids": ["second"],
            "completion_condition_ids": ["record-exists"],
            "required": True,
        },
        {
            "subgoal_id": "second",
            "description": "Complete the second step.",
            "dependency_ids": ["first"],
            "completion_condition_ids": ["record-exists"],
            "required": True,
        },
    ]
    with pytest.raises(ValidationError, match="contains a cycle"):
        TaskContract.model_validate(payload)


def test_contracts_are_frozen_and_reject_unknown_fields() -> None:
    contract = make_task_contract()
    with pytest.raises(ValidationError, match="frozen"):
        contract.task_id = "changed"  # type: ignore[misc]

    payload = contract.model_dump(mode="json")
    payload["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TaskContract.model_validate(payload)


def test_write_action_requires_declared_effect_and_idempotency_key() -> None:
    with pytest.raises(ValidationError, match="idempotency_key"):
        ActionIR(
            action_id="action-1",
            task_id="task-1",
            actor=ActorKind.LEAD_AGENT,
            kind=ActionKind.WRITE,
            tool_name="records.create",
            operation="create",
            resource=make_resource(),
            expected_effects=(make_expected_effect(),),
            risk_level=RiskLevel.MEDIUM,
            tool_schema_version="1",
            source_turn=2,
        )

    action = ActionIR(
        action_id="action-1",
        task_id="task-1",
        actor=ActorKind.LEAD_AGENT,
        kind=ActionKind.WRITE,
        tool_name="records.create",
        operation="create",
        resource=make_resource(),
        expected_effects=(make_expected_effect(),),
        idempotency_key="task-1:create:record-1",
        risk_level=RiskLevel.MEDIUM,
        tool_schema_version="1",
        source_turn=2,
    )
    assert action.expected_effects[0].effect_key == "effect-1"


def test_read_action_cannot_claim_side_effects() -> None:
    with pytest.raises(ValidationError, match="must not declare side effects"):
        ActionIR(
            action_id="action-2",
            task_id="task-1",
            actor=ActorKind.LEAD_AGENT,
            kind=ActionKind.READ,
            tool_name="records.get",
            operation="get",
            resource=make_resource(),
            expected_effects=(make_expected_effect(),),
            tool_schema_version="1",
            source_turn=1,
        )


def test_verified_evidence_and_effect_require_verification_provenance() -> None:
    with pytest.raises(ValidationError, match="verified evidence"):
        EvidenceItem(
            evidence_id="evidence-1",
            task_id="task-1",
            subject="record-1",
            predicate="exists",
            value=True,
            source_type=EvidenceSourceType.TOOL_RESULT,
            source_event_id="event-1",
            observed_at=NOW,
            status=EvidenceStatus.VERIFIED,
        )

    with pytest.raises(ValidationError, match="verified effects"):
        EffectRecord(
            effect_id="effect-1",
            task_id="task-1",
            action_id="action-1",
            idempotency_key="task-1:create:record-1",
            kind=EffectKind.CREATE,
            operation="create",
            resource=make_resource(),
            status=EffectStatus.VERIFIED,
            actual_change={"name": "Quarterly review"},
            created_at=NOW,
            updated_at=NOW,
        )


def test_verification_status_must_match_evidence() -> None:
    with pytest.raises(ValidationError, match="must include observed_state"):
        VerificationResult(
            verification_id="verification-1",
            task_id="task-1",
            verification_type=VerificationType.ACTION,
            target_id="effect-1",
            action_id="action-1",
            verifier_name="database-readback",
            verifier_version="1",
            expected_state={"exists": True},
            status=VerificationStatus.VERIFIED,
            checked_at=NOW,
        )


def test_task_state_rejects_terminal_active_subgoal() -> None:
    with pytest.raises(ValidationError, match="terminal subgoals"):
        TaskState(
            task_id="task-1",
            contract_version=1,
            state_version=2,
            phase=TaskPhase.ACT,
            subgoals=(
                SubgoalState(
                    subgoal_id="create-record",
                    status="completed_verified",
                    effect_ids=("effect-1",),
                ),
            ),
            active_subgoal_ids=("create-record",),
            updated_at=NOW,
        )


def test_failure_status_encodes_recovery_invariants() -> None:
    with pytest.raises(ValidationError, match="zero recovery budget"):
        FailureRecord(
            failure_id="failure-1",
            task_id="task-1",
            failure_type=FailureType.NO_PROGRESS,
            message="No state progress was observed.",
            retryable=True,
            responsible_layer=ResponsibleLayer.RECOVERY,
            recovery_budget_remaining=1,
            status=FailureStatus.BUDGET_EXHAUSTED,
            created_at=NOW,
            updated_at=NOW,
        )


def test_context_pack_budget_status_is_derived_consistently() -> None:
    section = ContextSection(
        section_id="task-state",
        priority=ContextPriority.P0,
        content={"phase": "act"},
        estimated_tokens=500,
        source_event_ids=("event-1",),
        compressible=False,
    )
    pack = ContextPack(
        pack_id="context-1",
        task_id="task-1",
        state_version=2,
        phase=TaskPhase.ACT,
        sections=(section,),
        soft_token_limit=1_000,
        hard_token_limit=2_000,
        estimated_tokens=700,
        budget_status=ContextBudgetStatus.WITHIN_BUDGET,
        created_at=NOW,
    )
    assert pack.sections[0].priority is ContextPriority.P0

    payload = pack.model_dump(mode="json")
    payload["budget_status"] = ContextBudgetStatus.HARD_EXCEEDED
    with pytest.raises(ValidationError, match="budget_status must be within_budget"):
        ContextPack.model_validate(payload)


def test_trace_event_is_replayable_redaction_aware_and_not_self_parented() -> None:
    event = TraceEvent(
        event_id="event-2",
        task_id="task-1",
        run_id="run-1",
        sequence_number=2,
        turn=1,
        timestamp=NOW,
        event_type=TraceEventType.TOOL_STARTED,
        actor=TraceActor.RUNTIME,
        parent_event_id="event-1",
        correlation_id="tool-call-1",
        state_version=2,
        payload={"tool_name": "records.create"},
        redaction_metadata=RedactionMetadata(
            redacted=True,
            redacted_fields=("payload.arguments.api_key",),
            policy_version="1",
        ),
    )
    assert TraceEvent.model_validate_json(event.model_dump_json()) == event

    payload = event.model_dump(mode="json")
    payload["parent_event_id"] = "event-2"
    with pytest.raises(ValidationError, match="own parent"):
        TraceEvent.model_validate(payload)
