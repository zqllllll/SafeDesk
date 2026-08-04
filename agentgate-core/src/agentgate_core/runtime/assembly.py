"""Dependency assembly for a complete four-module AgentGate runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agentgate_core.context_manager import ContextBuilder, ContextFreshnessStage, ToolResultProjectorRegistry
from agentgate_core.contracts.config import AgentGateFeatureConfig
from agentgate_core.contracts.recovery import StagnationConfig
from agentgate_core.contracts.tool_guard import PolicyRule
from agentgate_core.recovery_controller import (
    FailureClassifier,
    ProgressTracker,
    RecoveryBudgetManager,
    RecoveryController,
    RecoveryStrategyRegistry,
    StagnationDetector,
)
from agentgate_core.runtime.coordinator import AgentGateCoordinator
from agentgate_core.runtime.orchestrator import SafeDeskOrchestrator
from agentgate_core.runtime.session import AgentGateRuntimeSession
from agentgate_core.runtime.state_store import TypedStateStore
from agentgate_core.state_verification import (
    CompletionGate,
    CompletionGateStage,
    PostActionVerifier,
    ResponseGroundingGate,
    TaskReducer,
    VerifierRegistry,
)
from agentgate_core.tool_execution_guard import (
    ActionDependencyScheduler,
    ActionNormalizer,
    ActiveToolSetManager,
    DependencySchedulerStage,
    DynamicToolResolver,
    EffectPreflightStage,
    GuardedEffectLedger,
    PolicyEngine,
    PolicyGateStage,
    ToolCatalog,
    ToolSchemaGuard,
)
from agentgate_core.tracing.recorder import TraceRecorder


@dataclass(frozen=True, slots=True)
class AgentGateAssembly:
    orchestrator: SafeDeskOrchestrator
    session: AgentGateRuntimeSession
    coordinator: AgentGateCoordinator
    active_tool_sets: ActiveToolSetManager
    verifier_registry: VerifierRegistry
    recovery_controller: RecoveryController
    context_builder: ContextBuilder
    tool_resolver: DynamicToolResolver
    progress_tracker: ProgressTracker
    stagnation_detector: StagnationDetector
    guarded_effect_ledger: GuardedEffectLedger


def assemble_agentgate(
    *,
    run_id: str,
    state_store: TypedStateStore,
    trace_recorder: TraceRecorder,
    tool_catalog: ToolCatalog,
    config: AgentGateFeatureConfig,
    policy_rules: tuple[PolicyRule, ...] = (),
    verifier_registry: VerifierRegistry | None = None,
    stagnation_config: StagnationConfig | None = None,
    projector_registry: ToolResultProjectorRegistry | None = None,
    effect_id_factory: Callable[[], str] | None = None,
) -> AgentGateAssembly:
    active_sets = ActiveToolSetManager(tool_catalog)
    guarded_effects = GuardedEffectLedger(
        run_id=run_id,
        state_store=state_store,
        trace_recorder=trace_recorder,
        id_factory=effect_id_factory,
    )
    completion_gate = CompletionGate(
        run_id=run_id,
        state_store=state_store,
        trace_recorder=trace_recorder,
    )
    stages = (
        ContextFreshnessStage(state_store),
        ToolSchemaGuard(tool_catalog, active_sets),
        DependencySchedulerStage(state_store),
        PolicyGateStage(PolicyEngine(policy_rules)),
        EffectPreflightStage(guarded_effects),
        CompletionGateStage(completion_gate, mode=config.state_verification),
    )
    coordinator = AgentGateCoordinator(
        stages=stages,
        config=config,
        trace_recorder=trace_recorder,
    )
    session = AgentGateRuntimeSession(
        run_id=run_id,
        state_store=state_store,
        coordinator=coordinator,
        trace_recorder=trace_recorder,
    )
    reducer = TaskReducer(session)
    registry = verifier_registry or VerifierRegistry()
    verifier = PostActionVerifier(reducer, registry)
    recovery_config = stagnation_config or StagnationConfig()
    progress_tracker = ProgressTracker(state_store)
    stagnation_detector = StagnationDetector(recovery_config)
    recovery = RecoveryController(
        run_id=run_id,
        state_store=state_store,
        trace_recorder=trace_recorder,
        classifier=FailureClassifier(),
        strategies=RecoveryStrategyRegistry.with_defaults(),
        budgets=RecoveryBudgetManager(recovery_config),
    )
    context_builder = ContextBuilder(
        run_id=run_id,
        state_store=state_store,
        trace_recorder=trace_recorder,
        projector_registry=projector_registry or ToolResultProjectorRegistry(),
        tool_catalog=tool_catalog,
    )
    tool_resolver = DynamicToolResolver(
        tool_catalog,
        active_sets,
        run_id=run_id,
        trace_recorder=trace_recorder,
    )
    orchestrator = SafeDeskOrchestrator(
        session=session,
        active_sets=active_sets,
        normalizer=ActionNormalizer(tool_catalog),
        scheduler=ActionDependencyScheduler(),
        guarded_effects=guarded_effects,
        reducer=reducer,
        context_builder=context_builder,
        completion_gate=completion_gate,
        response_grounding=ResponseGroundingGate(session),
        verifier=verifier,
        recovery=recovery,
        tool_resolver=tool_resolver,
        progress_tracker=progress_tracker,
        stagnation_detector=stagnation_detector,
    )
    return AgentGateAssembly(
        orchestrator=orchestrator,
        session=session,
        coordinator=coordinator,
        active_tool_sets=active_sets,
        verifier_registry=registry,
        recovery_controller=recovery,
        context_builder=context_builder,
        tool_resolver=tool_resolver,
        progress_tracker=progress_tracker,
        stagnation_detector=stagnation_detector,
        guarded_effect_ledger=guarded_effects,
    )


__all__ = ["AgentGateAssembly", "assemble_agentgate"]
