from __future__ import annotations

from datetime import UTC, datetime

from agentgate_appworld import AppWorldStateVerificationAuditor
from agentgate_appworld.trace_converter import (
    ConversionBundle,
    ConversionSummary,
)
from agentgate_core.contracts import (
    ActionIR,
    ActionKind,
    ActorKind,
    EffectKind,
    EffectRecord,
    EffectStatus,
    EvidenceItem,
    EvidenceSourceType,
    EvidenceStatus,
    ExpectedEffect,
    ResourceRef,
    RiskLevel,
)

NOW = datetime(2026, 7, 21, tzinfo=UTC)
RESOURCE = ResourceRef(resource_type="record", resource_id="1")


def action(action_id: str, tool_name: str, turn: int) -> ActionIR:
    return ActionIR(
        action_id=action_id,
        task_id="task-1",
        actor=ActorKind.LEAD_AGENT,
        kind=ActionKind.WRITE,
        tool_name=tool_name,
        operation="complete_task" if "complete" in tool_name else "update",
        resource=RESOURCE,
        arguments={},
        expected_effects=(
            ExpectedEffect(
                effect_key="primary",
                kind=EffectKind.UPDATE,
                resource=RESOURCE,
                expected_change={"name": "expected"},
            ),
        ),
        idempotency_key=f"key-{action_id}",
        risk_level=RiskLevel.LOW,
        tool_schema_version="schema-1",
        source_turn=turn,
    )


def test_offline_audit_blocks_completion_without_contract_or_verified_runtime_state() -> None:
    write = action("write-1", "records__update", 1)
    complete = action("complete-1", "supervisor__complete_task", 2)
    effect = EffectRecord(
        effect_id="effect-1",
        task_id="task-1",
        action_id="write-1",
        idempotency_key="key-write-1",
        kind=EffectKind.UPDATE,
        operation="update",
        resource=RESOURCE,
        expected_change={"name": "expected"},
        actual_change={"ok": True},
        status=EffectStatus.APPLIED_UNVERIFIED,
        created_at=NOW,
        updated_at=NOW,
    )
    evidence = EvidenceItem(
        evidence_id="evidence-1",
        task_id="task-1",
        subject="tool_call:write-1",
        predicate="tool_result",
        value={"ok": True},
        source_type=EvidenceSourceType.TOOL_RESULT,
        source_event_id="event-1",
        status=EvidenceStatus.OBSERVED,
    )
    summary = ConversionSummary(
        trace_entries=4,
        proposed_tool_calls=2,
        tool_results=2,
        actions=2,
        read_actions=0,
        write_actions=2,
        executed_actions=2,
        non_executed_actions=0,
        effects=1,
        effect_status_counts={"applied_unverified": 1},
        evidence_items=1,
        evidence_status_counts={"observed": 1},
        missing_tool_results=0,
        orphan_tool_results=0,
        unknown_tools=0,
        redacted_values=0,
        diagnostic_counts={},
    )
    bundle = ConversionBundle(
        converter_version="test",
        task_id="task-1",
        run_id="run-1",
        source_trace_path="trace.json",
        source_sha256="sha256:test",
        catalog_version="catalog-1",
        converted_at=NOW,
        actions=(write, complete),
        effects=(effect,),
        evidence=(evidence,),
        diagnostics=(),
        summary=summary,
    )

    audit = AppWorldStateVerificationAuditor().audit(bundle)

    assert audit.would_block_count == 1
    assert audit.completion_attempts[0].blocker_codes == (
        "task_contract_unavailable",
        "effect_not_verified",
        "verified_evidence_unavailable",
    )
