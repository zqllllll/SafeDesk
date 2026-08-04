"""Offline conversion of AppWorld runtime traces into AgentGate contracts."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentgate_appworld.catalog_adapter import AppWorldToolCatalog, conservative_unknown_entry
from agentgate_core.contracts import (
    ActionIR,
    ActionKind,
    ActorKind,
    EffectRecord,
    EffectStatus,
    EvidenceItem,
    EvidenceSourceType,
    EvidenceStatus,
    ExpectedEffect,
    ResourceRef,
    ToolCatalogEntry,
)

CONVERTER_VERSION = "appworld-trace-converter-v1"
REDACTED_VALUE = "[REDACTED]"
_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "card_number",
        "credit_card_number",
        "cvv",
        "password",
        "passwords",
        "secret",
        "token",
    }
)


class DiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ConversionDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=128)
    severity: DiagnosticSeverity
    message: str = Field(min_length=1, max_length=2_000)
    trace_index: int | None = Field(default=None, ge=0)
    turn: int | None = Field(default=None, ge=0)
    tool_call_id: str | None = None
    tool_name: str | None = None


class ConversionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_entries: int = Field(ge=0)
    proposed_tool_calls: int = Field(ge=0)
    tool_results: int = Field(ge=0)
    actions: int = Field(ge=0)
    read_actions: int = Field(ge=0)
    write_actions: int = Field(ge=0)
    executed_actions: int = Field(ge=0)
    non_executed_actions: int = Field(ge=0)
    effects: int = Field(ge=0)
    effect_status_counts: dict[str, int]
    evidence_items: int = Field(ge=0)
    evidence_status_counts: dict[str, int]
    missing_tool_results: int = Field(ge=0)
    orphan_tool_results: int = Field(ge=0)
    unknown_tools: int = Field(ge=0)
    redacted_values: int = Field(ge=0)
    diagnostic_counts: dict[str, int]


class ConversionBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    converter_version: str
    task_id: str
    run_id: str
    source_trace_path: str
    source_sha256: str
    catalog_version: str
    converted_at: datetime
    actions: tuple[ActionIR, ...]
    effects: tuple[EffectRecord, ...]
    evidence: tuple[EvidenceItem, ...]
    diagnostics: tuple[ConversionDiagnostic, ...]
    summary: ConversionSummary


@dataclass(frozen=True)
class _ProposedCall:
    trace_index: int
    call_index: int
    turn: int
    call_id: str
    name: str
    raw_arguments: Any


@dataclass(frozen=True)
class _ToolResult:
    trace_index: int
    turn: int
    call_id: str
    name: str
    arguments: Any
    executed: bool
    result: Any


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    return str(value)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower()
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith("_password")
        or normalized.endswith("_secret")
        or normalized.endswith("_token")
    )


def _redact(value: Any) -> tuple[Any, int]:
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        count = 0
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if _is_sensitive_key(key) and raw_value is not None:
                output[key] = REDACTED_VALUE
                count += 1
                continue
            sanitized, child_count = _redact(raw_value)
            output[key] = sanitized
            count += child_count
        return output, count
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        output_list: list[Any] = []
        count = 0
        for item in value:
            sanitized, child_count = _redact(item)
            output_list.append(sanitized)
            count += child_count
        return output_list, count
    return _json_safe(value), 0


def _stable_id(namespace: str, *parts: Any) -> str:
    canonical = json.dumps(parts, ensure_ascii=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"appworld:{namespace}:{digest}"


def _canonical_idempotency_key(
    task_id: str,
    entry: ToolCatalogEntry,
    resource: ResourceRef,
    arguments: Mapping[str, Any],
) -> str:
    canonical = json.dumps(
        {
            "task_id": task_id,
            "operation": entry.operation,
            "resource": resource.model_dump(mode="json"),
            "arguments": _json_safe(arguments),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _resource_ref(entry: ToolCatalogEntry, arguments: Mapping[str, Any]) -> ResourceRef:
    resource_id: str | None = None
    for key, value in arguments.items():
        if key.lower().endswith("_id") and isinstance(value, (str, int)) and not isinstance(value, bool):
            resource_id = str(value)
            break
    return ResourceRef(resource_type=entry.resource_types[0], resource_id=resource_id)


def _contains_explicit_error(result: Any) -> bool:
    if not isinstance(result, Mapping):
        return False
    if result.get("error") not in (None, "", False):
        return True
    if result.get("errors") not in (None, "", False, []):
        return True
    if result.get("exception") not in (None, "", False):
        return True
    if result.get("success") is False:
        return True
    status = result.get("status")
    return isinstance(status, str) and status.lower() in {"error", "failed", "failure"}


def _parse_arguments(raw_arguments: Any) -> tuple[dict[str, Any], str | None]:
    if raw_arguments is None or raw_arguments == "":
        return {}, None
    if isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments)
        except json.JSONDecodeError:
            return {}, "INVALID_ARGUMENTS_JSON"
    else:
        parsed = raw_arguments
    if not isinstance(parsed, Mapping):
        return {}, "ARGUMENTS_NOT_OBJECT"
    return {str(key): _json_safe(value) for key, value in parsed.items()}, None


def _non_negative_turn(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


class AppWorldTraceConverter:
    """Deterministically replay trace records into conservative AgentGate state."""

    def __init__(self, catalog: AppWorldToolCatalog) -> None:
        self.catalog = catalog

    def convert_file(self, trace_path: Path, *, task_id: str | None = None, run_id: str) -> ConversionBundle:
        source_bytes = trace_path.read_bytes()
        trace = json.loads(source_bytes.decode("utf-8-sig"))
        return self.convert(
            trace,
            task_id=task_id or trace_path.stem,
            run_id=run_id,
            source_trace_path=str(trace_path.resolve()),
            source_sha256=f"sha256:{hashlib.sha256(source_bytes).hexdigest()}",
        )

    def convert(
        self,
        trace: Any,
        *,
        task_id: str,
        run_id: str,
        source_trace_path: str = "memory",
        source_sha256: str = "sha256:in-memory",
        converted_at: datetime | None = None,
    ) -> ConversionBundle:
        if not isinstance(trace, list):
            raise ValueError("AppWorld trace must be a JSON array")
        timestamp = converted_at or datetime.now(UTC)
        diagnostics: list[ConversionDiagnostic] = []
        proposals = self._collect_proposals(trace, diagnostics)
        tool_results = self._collect_tool_results(trace, diagnostics)
        result_queues: dict[str, deque[_ToolResult]] = defaultdict(deque)
        for result in tool_results:
            result_queues[result.call_id].append(result)

        actions: list[ActionIR] = []
        effects: list[EffectRecord] = []
        evidence: list[EvidenceItem] = []
        matched_result_indexes: set[int] = set()
        successful_action_by_tool: dict[str, tuple[str, str]] = {}
        redacted_values = 0
        unknown_tools = 0
        missing_tool_results = 0

        for proposal in proposals:
            result = result_queues[proposal.call_id].popleft() if result_queues[proposal.call_id] else None
            if result is not None:
                matched_result_indexes.add(result.trace_index)
            else:
                missing_tool_results += 1
                diagnostics.append(
                    ConversionDiagnostic(
                        code="MISSING_TOOL_RESULT",
                        severity=DiagnosticSeverity.ERROR,
                        message="Model tool call has no corresponding tool result record.",
                        trace_index=proposal.trace_index,
                        turn=proposal.turn,
                        tool_call_id=proposal.call_id,
                        tool_name=proposal.name or None,
                    )
                )
            converted = self._convert_call(
                proposal=proposal,
                result=result,
                task_id=task_id,
                timestamp=timestamp,
                successful_action_by_tool=successful_action_by_tool,
                diagnostics=diagnostics,
            )
            action, effect, evidence_item, redact_count, was_unknown = converted
            actions.append(action)
            redacted_values += redact_count
            unknown_tools += int(was_unknown)
            if effect is not None:
                effects.append(effect)
            if evidence_item is not None:
                evidence.append(evidence_item)
            if result is not None and result.executed and not _contains_explicit_error(result.result):
                if evidence_item is not None:
                    successful_action_by_tool[action.tool_name] = (action.action_id, evidence_item.evidence_id)

        orphan_results = [result for result in tool_results if result.trace_index not in matched_result_indexes]
        for orphan_index, result in enumerate(orphan_results):
            diagnostics.append(
                ConversionDiagnostic(
                    code="ORPHAN_TOOL_RESULT",
                    severity=DiagnosticSeverity.WARNING,
                    message="Tool result has no corresponding model tool call; a conservative action was synthesized.",
                    trace_index=result.trace_index,
                    turn=result.turn,
                    tool_call_id=result.call_id,
                    tool_name=result.name or None,
                )
            )
            proposal = _ProposedCall(
                trace_index=result.trace_index,
                call_index=orphan_index,
                turn=result.turn,
                call_id=result.call_id,
                name=result.name,
                raw_arguments=result.arguments,
            )
            action, effect, evidence_item, redact_count, was_unknown = self._convert_call(
                proposal=proposal,
                result=result,
                task_id=task_id,
                timestamp=timestamp,
                successful_action_by_tool=successful_action_by_tool,
                diagnostics=diagnostics,
            )
            actions.append(action)
            redacted_values += redact_count
            unknown_tools += int(was_unknown)
            if effect is not None:
                effects.append(effect)
            if evidence_item is not None:
                evidence.append(evidence_item)

        effect_counts = Counter(effect.status.value for effect in effects)
        evidence_counts = Counter(item.status.value for item in evidence)
        diagnostic_counts = Counter(item.code for item in diagnostics)
        executed_actions = sum(result.executed for result in tool_results)
        summary = ConversionSummary(
            trace_entries=len(trace),
            proposed_tool_calls=len(proposals),
            tool_results=len(tool_results),
            actions=len(actions),
            read_actions=sum(action.kind is ActionKind.READ for action in actions),
            write_actions=sum(action.kind is ActionKind.WRITE for action in actions),
            executed_actions=executed_actions,
            non_executed_actions=len(actions) - executed_actions,
            effects=len(effects),
            effect_status_counts=dict(sorted(effect_counts.items())),
            evidence_items=len(evidence),
            evidence_status_counts=dict(sorted(evidence_counts.items())),
            missing_tool_results=missing_tool_results,
            orphan_tool_results=len(orphan_results),
            unknown_tools=unknown_tools,
            redacted_values=redacted_values,
            diagnostic_counts=dict(sorted(diagnostic_counts.items())),
        )
        return ConversionBundle(
            converter_version=CONVERTER_VERSION,
            task_id=task_id,
            run_id=run_id,
            source_trace_path=source_trace_path,
            source_sha256=source_sha256,
            catalog_version=self.catalog.snapshot.catalog_version,
            converted_at=timestamp,
            actions=tuple(actions),
            effects=tuple(effects),
            evidence=tuple(evidence),
            diagnostics=tuple(diagnostics),
            summary=summary,
        )

    def _collect_proposals(self, trace: list[Any], diagnostics: list[ConversionDiagnostic]) -> list[_ProposedCall]:
        proposals: list[_ProposedCall] = []
        seen_ids: Counter[str] = Counter()
        for trace_index, record in enumerate(trace):
            if not isinstance(record, Mapping) or record.get("role") != "assistant":
                continue
            turn = _non_negative_turn(record.get("turn"))
            calls = record.get("tool_calls", [])
            if not isinstance(calls, list):
                diagnostics.append(
                    ConversionDiagnostic(
                        code="MALFORMED_TOOL_CALL_LIST",
                        severity=DiagnosticSeverity.ERROR,
                        message="Assistant tool_calls field is not an array.",
                        trace_index=trace_index,
                        turn=turn,
                    )
                )
                continue
            for call_index, call in enumerate(calls):
                function = call.get("function", {}) if isinstance(call, Mapping) else {}
                if not isinstance(function, Mapping):
                    function = {}
                raw_id = call.get("id") if isinstance(call, Mapping) else None
                call_id = (
                    raw_id
                    if isinstance(raw_id, str) and raw_id.strip()
                    else _stable_id("source-call", trace_index, call_index)
                )
                name = function.get("name") if isinstance(function.get("name"), str) else ""
                if not name:
                    diagnostics.append(
                        ConversionDiagnostic(
                            code="MISSING_TOOL_NAME",
                            severity=DiagnosticSeverity.ERROR,
                            message="Model tool call has no valid function name.",
                            trace_index=trace_index,
                            turn=turn,
                            tool_call_id=call_id,
                        )
                    )
                seen_ids[call_id] += 1
                if seen_ids[call_id] > 1:
                    diagnostics.append(
                        ConversionDiagnostic(
                            code="DUPLICATE_TOOL_CALL_ID",
                            severity=DiagnosticSeverity.ERROR,
                            message="Tool call ID is repeated in the source trace.",
                            trace_index=trace_index,
                            turn=turn,
                            tool_call_id=call_id,
                            tool_name=name or None,
                        )
                    )
                proposals.append(
                    _ProposedCall(
                        trace_index=trace_index,
                        call_index=call_index,
                        turn=turn,
                        call_id=call_id,
                        name=name,
                        raw_arguments=function.get("arguments", "{}"),
                    )
                )
        return proposals

    def _collect_tool_results(self, trace: list[Any], diagnostics: list[ConversionDiagnostic]) -> list[_ToolResult]:
        results: list[_ToolResult] = []
        tool_records = [
            (trace_index, record)
            for trace_index, record in enumerate(trace)
            if isinstance(record, Mapping) and record.get("role") == "tool"
        ]
        legacy_execution_mode = bool(tool_records) and all("executed" not in record for _, record in tool_records)
        if legacy_execution_mode:
            first_index, first_record = tool_records[0]
            diagnostics.append(
                ConversionDiagnostic(
                    code="LEGACY_EXECUTION_FLAGS_INFERRED",
                    severity=DiagnosticSeverity.INFO,
                    message=(
                        "No tool result record contains an executed flag; legacy runner semantics infer that "
                        "recorded tool results were executed."
                    ),
                    trace_index=first_index,
                    turn=_non_negative_turn(first_record.get("turn")),
                )
            )
        for trace_index, record in enumerate(trace):
            if not isinstance(record, Mapping) or record.get("role") != "tool":
                continue
            turn = _non_negative_turn(record.get("turn"))
            raw_id = record.get("tool_call_id")
            call_id = raw_id if isinstance(raw_id, str) and raw_id.strip() else _stable_id("source-result", trace_index)
            name = record.get("name") if isinstance(record.get("name"), str) else ""
            if "executed" not in record and not legacy_execution_mode:
                diagnostics.append(
                    ConversionDiagnostic(
                        code="MISSING_EXECUTED_FLAG",
                        severity=DiagnosticSeverity.WARNING,
                        message="Tool result lacks an executed flag; it is treated as not executed.",
                        trace_index=trace_index,
                        turn=turn,
                        tool_call_id=call_id,
                        tool_name=name or None,
                    )
                )
            results.append(
                _ToolResult(
                    trace_index=trace_index,
                    turn=turn,
                    call_id=call_id,
                    name=name,
                    arguments=record.get("args", {}),
                    executed=legacy_execution_mode or record.get("executed") is True,
                    result=record.get("result"),
                )
            )
        return results

    def _convert_call(
        self,
        *,
        proposal: _ProposedCall,
        result: _ToolResult | None,
        task_id: str,
        timestamp: datetime,
        successful_action_by_tool: Mapping[str, tuple[str, str]],
        diagnostics: list[ConversionDiagnostic],
    ) -> tuple[ActionIR, EffectRecord | None, EvidenceItem | None, int, bool]:
        arguments, parse_error = _parse_arguments(proposal.raw_arguments)
        if parse_error:
            diagnostics.append(
                ConversionDiagnostic(
                    code=parse_error,
                    severity=DiagnosticSeverity.ERROR,
                    message="Tool arguments could not be normalized into a JSON object.",
                    trace_index=proposal.trace_index,
                    turn=proposal.turn,
                    tool_call_id=proposal.call_id,
                    tool_name=proposal.name or None,
                )
            )
        tool_name = proposal.name or "unknown_tool"
        entry = self.catalog.get(tool_name)
        was_unknown = entry is None
        if entry is None:
            entry = conservative_unknown_entry(tool_name)
            diagnostics.append(
                ConversionDiagnostic(
                    code="UNKNOWN_TOOL",
                    severity=DiagnosticSeverity.ERROR,
                    message="Tool is absent from the public AppWorld catalog and was classified as a critical write.",
                    trace_index=proposal.trace_index,
                    turn=proposal.turn,
                    tool_call_id=proposal.call_id,
                    tool_name=tool_name,
                )
            )
        if result is not None and (result.name != proposal.name or result.turn != proposal.turn):
            diagnostics.append(
                ConversionDiagnostic(
                    code="CALL_RESULT_MISMATCH",
                    severity=DiagnosticSeverity.ERROR,
                    message="Tool result name or turn does not match the proposed call.",
                    trace_index=result.trace_index,
                    turn=result.turn,
                    tool_call_id=proposal.call_id,
                    tool_name=tool_name,
                )
            )

        redacted_arguments, argument_redactions = _redact(arguments)
        assert isinstance(redacted_arguments, dict)
        resource = _resource_ref(entry, arguments)
        dependency_action_ids: list[str] = []
        required_evidence_ids: list[str] = []
        for dependency_name in entry.dependency_tool_names:
            dependency = successful_action_by_tool.get(dependency_name)
            if dependency is not None:
                dependency_action_ids.append(dependency[0])
                required_evidence_ids.append(dependency[1])

        action_id = _stable_id("action", task_id, proposal.trace_index, proposal.call_index, proposal.call_id)
        expected_effects: tuple[ExpectedEffect, ...] = ()
        idempotency_key: str | None = None
        if entry.action_kind is ActionKind.WRITE:
            idempotency_key = _canonical_idempotency_key(task_id, entry, resource, arguments)
            expected_effects = (
                ExpectedEffect(
                    effect_key="primary_effect",
                    kind=entry.side_effect_type,
                    resource=resource,
                    expected_change={
                        "operation": entry.operation,
                        "arguments": redacted_arguments,
                    },
                ),
            )
        action = ActionIR(
            action_id=action_id,
            task_id=task_id,
            actor=ActorKind.LEAD_AGENT,
            kind=entry.action_kind,
            tool_name=entry.name,
            operation=entry.operation,
            resource=resource,
            arguments=redacted_arguments,
            expected_effects=expected_effects,
            required_evidence_ids=tuple(required_evidence_ids),
            dependency_action_ids=tuple(dependency_action_ids),
            idempotency_key=idempotency_key,
            risk_level=entry.risk_level,
            tool_schema_version=entry.tool_schema_version,
            source_turn=proposal.turn,
        )

        evidence_item: EvidenceItem | None = None
        result_redactions = 0
        if result is not None:
            redacted_result, result_redactions = _redact(result.result)
            executed = result.executed
            evidence_item = EvidenceItem(
                evidence_id=_stable_id("evidence", task_id, result.trace_index, result.call_id),
                task_id=task_id,
                subject=f"tool_call:{action_id}",
                predicate="tool_result" if executed else "non_execution_result",
                value=redacted_result,
                source_type=EvidenceSourceType.TOOL_RESULT if executed else EvidenceSourceType.RUNTIME,
                source_event_id=_stable_id("source-event", task_id, result.trace_index, result.call_id),
                observed_at=timestamp,
                scope="task",
                confidence=1.0,
                status=EvidenceStatus.OBSERVED,
                note=(
                    "Runtime result observed; no environment verification was performed."
                    if executed
                    else "Runtime reported that the proposed tool call was not executed."
                ),
            )

        effect: EffectRecord | None = None
        if entry.action_kind is ActionKind.WRITE:
            assert idempotency_key is not None
            status = EffectStatus.PLANNED
            actual_change = None
            if result is not None and result.executed:
                redacted_result, _ = _redact(result.result)
                actual_change = {"tool_result": redacted_result}
                status = (
                    EffectStatus.FAILED if _contains_explicit_error(result.result) else EffectStatus.APPLIED_UNVERIFIED
                )
            effect = EffectRecord(
                effect_id=_stable_id("effect", action_id, "primary_effect"),
                task_id=task_id,
                action_id=action_id,
                idempotency_key=idempotency_key,
                kind=entry.side_effect_type,
                operation=entry.operation,
                resource=resource,
                expected_change=expected_effects[0].expected_change,
                actual_change=actual_change,
                status=status,
                created_at=timestamp,
                updated_at=timestamp,
            )
        return action, effect, evidence_item, argument_redactions + result_redactions, was_unknown


__all__ = [
    "AppWorldTraceConverter",
    "CONVERTER_VERSION",
    "ConversionBundle",
    "ConversionDiagnostic",
    "ConversionSummary",
    "DiagnosticSeverity",
    "REDACTED_VALUE",
]
