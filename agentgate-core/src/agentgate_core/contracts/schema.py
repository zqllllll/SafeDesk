"""Deterministic JSON Schema export for the public AgentGate contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from agentgate_core.contracts.action import ActionIR
from agentgate_core.contracts.base import VersionedContract
from agentgate_core.contracts.catalog import ToolCatalogSnapshot
from agentgate_core.contracts.config import AgentGateFeatureConfig
from agentgate_core.contracts.context import ContextPack
from agentgate_core.contracts.context_management import (
    ContextInvariantReport,
    ProjectedToolResult,
    RawTraceReference,
    TokenBudgetReport,
)
from agentgate_core.contracts.decision import ActionEvaluationContext, CoordinatorResult, GateDecision
from agentgate_core.contracts.effect import EffectRecord
from agentgate_core.contracts.evidence import EvidenceItem
from agentgate_core.contracts.failure import FailureRecord
from agentgate_core.contracts.orchestration import GuardedToolBatch, ToolExecutionReport
from agentgate_core.contracts.recovery import (
    ProgressSignal,
    RecoveryPlan,
    RecoveryResult,
    StagnationAssessment,
)
from agentgate_core.contracts.state_verification import (
    CompletionGateDecision,
    ResponseGroundingDecision,
    SubgoalTransitionRequest,
    VerificationObservation,
    VerifierSpec,
)
from agentgate_core.contracts.task import TaskContract, TaskState
from agentgate_core.contracts.tool_guard import (
    ActionSchedule,
    ActiveToolSet,
    EffectPreflightDecision,
    PolicyDecision,
    RawToolCall,
    ToolResolution,
)
from agentgate_core.contracts.trace import TraceEvent
from agentgate_core.contracts.verification import VerificationResult

SCHEMA_BASE_URI: Final = "https://schemas.safedesk.dev/agentgate/v1"
DEFAULT_SCHEMA_DIRECTORY: Final = Path(__file__).parents[1] / "schemas" / "v1"
CONTRACT_MODELS: Final[dict[str, type[VersionedContract]]] = {
    "action-ir": ActionIR,
    "action-schedule": ActionSchedule,
    "action-evaluation-context": ActionEvaluationContext,
    "agentgate-feature-config": AgentGateFeatureConfig,
    "active-tool-set": ActiveToolSet,
    "context-pack": ContextPack,
    "context-invariant-report": ContextInvariantReport,
    "coordinator-result": CoordinatorResult,
    "completion-gate-decision": CompletionGateDecision,
    "effect-record": EffectRecord,
    "effect-preflight-decision": EffectPreflightDecision,
    "evidence-item": EvidenceItem,
    "failure-record": FailureRecord,
    "gate-decision": GateDecision,
    "guarded-tool-batch": GuardedToolBatch,
    "policy-decision": PolicyDecision,
    "progress-signal": ProgressSignal,
    "projected-tool-result": ProjectedToolResult,
    "raw-tool-call": RawToolCall,
    "raw-trace-reference": RawTraceReference,
    "recovery-plan": RecoveryPlan,
    "recovery-result": RecoveryResult,
    "response-grounding-decision": ResponseGroundingDecision,
    "subgoal-transition-request": SubgoalTransitionRequest,
    "stagnation-assessment": StagnationAssessment,
    "task-contract": TaskContract,
    "task-state": TaskState,
    "tool-catalog-snapshot": ToolCatalogSnapshot,
    "tool-execution-report": ToolExecutionReport,
    "tool-resolution": ToolResolution,
    "token-budget-report": TokenBudgetReport,
    "trace-event": TraceEvent,
    "verification-result": VerificationResult,
    "verification-observation": VerificationObservation,
    "verifier-spec": VerifierSpec,
}


def schema_for(contract_name: str) -> dict[str, object]:
    """Build the canonical Draft 2020-12 schema for one public contract."""

    try:
        model = CONTRACT_MODELS[contract_name]
    except KeyError as error:
        raise ValueError(f"unknown contract: {contract_name}") from error
    schema = model.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"{SCHEMA_BASE_URI}/{contract_name}.schema.json"
    return schema


def render_schema(contract_name: str) -> str:
    """Render a schema deterministically for review and drift checks."""

    return json.dumps(schema_for(contract_name), ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def export_schemas(output_directory: Path = DEFAULT_SCHEMA_DIRECTORY) -> tuple[Path, ...]:
    """Write every public schema and return the generated file paths."""

    output_directory.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    for contract_name in sorted(CONTRACT_MODELS):
        output_path = output_directory / f"{contract_name}.schema.json"
        output_path.write_text(render_schema(contract_name), encoding="utf-8", newline="\n")
        generated.append(output_path)
    return tuple(generated)


def main() -> None:
    """Export schemas to the package's versioned schema directory."""

    for output_path in export_schemas():
        print(output_path)


if __name__ == "__main__":
    main()
