"""Build state-versioned ContextPack snapshots from durable runtime truth."""

from __future__ import annotations

from uuid import uuid4

from agentgate_core.context_manager.invariants import ContextInvariantValidator
from agentgate_core.context_manager.projector import ToolResultProjectorRegistry
from agentgate_core.context_manager.summary import StructuredHistorySummarizer
from agentgate_core.context_manager.token_budget import ContextBudgetAllocator, HeuristicTokenEstimator
from agentgate_core.contracts.context import ContextBudgetStatus, ContextPack, ContextPriority, ContextSection
from agentgate_core.contracts.decision import (
    ActionEvaluationContext,
    DecisionOutcome,
    FeatureName,
    PipelineStage,
    StageEvaluation,
)
from agentgate_core.contracts.effect import EffectStatus
from agentgate_core.contracts.evidence import EvidenceStatus
from agentgate_core.contracts.failure import FailureStatus
from agentgate_core.contracts.task import ConstraintKind
from agentgate_core.contracts.tool_guard import ActiveToolSet
from agentgate_core.contracts.trace import TraceActor, TraceEvent, TraceEventType
from agentgate_core.runtime.state_store import TypedStateStore
from agentgate_core.tool_execution_guard.catalog import ToolCatalog
from agentgate_core.tracing.recorder import TraceRecorder


class ContextBuildError(RuntimeError):
    pass


class ContextBuilder:
    def __init__(
        self,
        *,
        run_id: str,
        state_store: TypedStateStore,
        trace_recorder: TraceRecorder,
        projector_registry: ToolResultProjectorRegistry,
        tool_catalog: ToolCatalog | None = None,
        estimator: HeuristicTokenEstimator | None = None,
    ) -> None:
        self.run_id = run_id
        self.state_store = state_store
        self.trace_recorder = trace_recorder
        self.projectors = projector_registry
        self.tool_catalog = tool_catalog
        self.estimator = estimator or HeuristicTokenEstimator()
        self.allocator = ContextBudgetAllocator(self.estimator)
        self.summarizer = StructuredHistorySummarizer(self.estimator)
        self.validator = ContextInvariantValidator(state_store)

    def build(
        self,
        task_id: str,
        *,
        turn: int,
        active_tool_set: ActiveToolSet,
        soft_token_limit: int,
        hard_token_limit: int,
        reserved_output_tokens: int = 0,
        recent_event_limit: int = 12,
    ) -> ContextPack:
        contract = self.state_store.get_task_contract(task_id)
        state = self.state_store.get_task_state(task_id)
        if active_tool_set.task_id != task_id:
            raise ContextBuildError("active tool set belongs to another task")
        evidence = self.state_store.list_evidence(task_id)
        effects = self.state_store.list_effects(task_id)
        failures = self.state_store.list_failures(task_id)
        events = self.trace_recorder.sink.list_events(task_id, self.run_id)
        recent_events = events[-recent_event_limit:]
        older_events = events[:-recent_event_limit] if len(events) > recent_event_limit else ()
        sections: list[ContextSection] = [
            self._section("task_contract", ContextPriority.P0, contract.model_dump(mode="json"), compressible=False),
            self._section("task_state", ContextPriority.P1, state.model_dump(mode="json"), compressible=False),
        ]
        verified_evidence = tuple(item for item in evidence if item.status is EvidenceStatus.VERIFIED)
        relevant_observed = tuple(
            item
            for item in evidence
            if item.status in {EvidenceStatus.OBSERVED, EvidenceStatus.INFERRED, EvidenceStatus.CONFLICTED}
        )
        unresolved_effects = tuple(
            item for item in effects if item.status not in {EffectStatus.VERIFIED, EffectStatus.ROLLED_BACK}
        )
        verified_effects = tuple(item for item in effects if item.status is EffectStatus.VERIFIED)
        open_failures = tuple(item for item in failures if item.status is not FailureStatus.RESOLVED)
        sections.extend(
            (
                self._section(
                    "verified_evidence",
                    ContextPriority.P1,
                    [item.model_dump(mode="json") for item in verified_evidence],
                    compressible=False,
                ),
                self._section(
                    "relevant_observed_evidence",
                    ContextPriority.P1,
                    [item.model_dump(mode="json") for item in relevant_observed],
                    compressible=False,
                ),
                self._section(
                    "effect_ledger",
                    ContextPriority.P1,
                    {
                        "unresolved": [item.model_dump(mode="json") for item in unresolved_effects],
                        "verified": [
                            {
                                "effect_id": item.effect_id,
                                "operation": item.operation,
                                "resource": item.resource.model_dump(mode="json"),
                            }
                            for item in verified_effects
                        ],
                    },
                    compressible=False,
                ),
                self._section(
                    "open_failures",
                    ContextPriority.P1,
                    [item.model_dump(mode="json") for item in open_failures],
                    compressible=False,
                ),
            )
        )
        if self.tool_catalog is not None:
            schemas = [
                {
                    "name": name,
                    "description": self.tool_catalog.get_tool(name).description,
                    "input_schema": self.tool_catalog.get_tool(name).input_schema,
                    "schema_version": active_tool_set.schema_versions[name],
                }
                for name in active_tool_set.tool_names
            ]
            sections.append(self._section("active_tool_schemas", ContextPriority.P2, schemas, compressible=False))
        for event in recent_events:
            projected = self._project_event(task_id, event)
            if projected is not None:
                sections.append(
                    ContextSection(
                        section_id=f"tool_result_{event.sequence_number}",
                        priority=ContextPriority.P3,
                        content=projected.projected,
                        estimated_tokens=projected.projected_tokens,
                        source_event_ids=(event.event_id,),
                        compressible=True,
                        raw_reference=projected.raw_reference.reference_id,
                    )
                )
        summary = self.summarizer.summarize(task_id, self.run_id, older_events)
        if summary is not None:
            sections.append(
                ContextSection(
                    section_id="history_summary",
                    priority=ContextPriority.P4,
                    content=summary.model_dump(mode="json"),
                    estimated_tokens=self.estimator.estimate(summary.model_dump(mode="json")),
                    source_event_ids=summary.source_event_ids,
                    compressible=True,
                    raw_reference=summary.raw_reference.reference_id,
                )
            )
        allocated, budget = self.allocator.allocate(
            tuple(sections),
            soft_limit=soft_token_limit,
            hard_limit=hard_token_limit,
            reserved_output_tokens=reserved_output_tokens,
        )
        if budget.status is ContextBudgetStatus.HARD_EXCEEDED:
            raise ContextBuildError(
                "non-compressible context sections exceed the hard token budget; "
                "reduce the active tool set or resolve retained state before calling the model"
            )
        pack = ContextPack(
            pack_id=f"context-{uuid4()}",
            task_id=task_id,
            state_version=state.state_version,
            phase=state.phase,
            active_subgoal_ids=state.active_subgoal_ids,
            hard_constraints=tuple(
                item.description
                for item in contract.constraints
                if item.active and item.kind is not ConstraintKind.SOFT
            ),
            verified_evidence_ids=tuple(item.evidence_id for item in verified_evidence),
            observed_evidence_ids=tuple(item.evidence_id for item in relevant_observed),
            open_failure_ids=tuple(item.failure_id for item in open_failures),
            effect_ids=tuple(item.effect_id for item in effects),
            active_tool_names=active_tool_set.tool_names,
            recent_event_ids=tuple(event.event_id for event in recent_events),
            recovery_event_ids=tuple(
                event.event_id
                for event in events
                if event.event_type in {TraceEventType.RECOVERY_PLANNED, TraceEventType.RECOVERY_FINISHED}
            ),
            sections=allocated,
            soft_token_limit=soft_token_limit,
            hard_token_limit=hard_token_limit,
            estimated_tokens=budget.estimated_input_tokens,
            budget_status=budget.status,
        )
        report = self.validator.validate(pack)
        if not report.valid:
            raise ContextBuildError(
                f"context invariant validation failed: {[item.code.value for item in report.violations]}"
            )
        self.trace_recorder.record(
            task_id=task_id,
            run_id=self.run_id,
            turn=turn,
            event_type=TraceEventType.CONTEXT_BUILT,
            actor=TraceActor.CONTEXT_MANAGER,
            correlation_id=pack.pack_id,
            state_version=state.state_version,
            payload={"context_pack": pack.model_dump(mode="json"), "budget_report": budget.model_dump(mode="json")},
            critical=False,
        )
        return pack

    def _section(
        self,
        section_id: str,
        priority: ContextPriority,
        content: object,
        *,
        compressible: bool,
    ) -> ContextSection:
        return ContextSection(
            section_id=section_id,
            priority=priority,
            content=content,
            estimated_tokens=self.estimator.estimate(content),
            compressible=compressible,
        )

    def _project_event(self, task_id: str, event: TraceEvent):
        if event.event_type is not TraceEventType.TOOL_FINISHED:
            return None
        tool_name = event.payload.get("tool_name")
        action = event.payload.get("action")
        if not isinstance(tool_name, str) and isinstance(action, dict):
            tool_name = action.get("tool_name")
        if not isinstance(tool_name, str):
            tool_name = "unknown_tool"
        payload = event.payload.get("result", event.payload)
        return self.projectors.project(
            task_id=task_id,
            run_id=self.run_id,
            tool_name=tool_name,
            source_event_id=event.event_id,
            payload=payload,
            estimator=self.estimator,
        )


class ContextFreshnessStage:
    stage = PipelineStage.CONTEXT_BUILDER
    feature = FeatureName.CONTEXT_MANAGER

    def __init__(self, state_store: TypedStateStore) -> None:
        self.state_store = state_store

    def evaluate(self, context: ActionEvaluationContext) -> StageEvaluation:
        current = self.state_store.get_task_state(context.task_id)
        if context.state_version != current.state_version:
            return StageEvaluation(
                outcome=DecisionOutcome.REPLAN,
                reason_code="stale_context_state_version",
                explanation="Task state changed after the model context was built; rebuild context and re-plan.",
                payload={"action_state_version": context.state_version, "current_state_version": current.state_version},
            )
        return StageEvaluation(
            outcome=DecisionOutcome.ALLOW,
            reason_code="context_state_current",
            explanation="The action was generated from the current TaskState version.",
        )


__all__ = ["ContextBuildError", "ContextBuilder", "ContextFreshnessStage"]
