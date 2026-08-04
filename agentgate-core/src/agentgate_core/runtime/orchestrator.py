"""Runner-facing lifecycle orchestration across all four AgentGate modules."""

from __future__ import annotations

from uuid import uuid4

from agentgate_core.context_manager.builder import ContextBuilder
from agentgate_core.contracts.action import ActionIR
from agentgate_core.contracts.context import ContextPack
from agentgate_core.contracts.decision import ActionEvaluationContext, DecisionOutcome, FeatureMode, PipelineStage
from agentgate_core.contracts.effect import EffectStatus
from agentgate_core.contracts.evidence import EvidenceItem, EvidenceSourceType, EvidenceStatus
from agentgate_core.contracts.failure import FailureType
from agentgate_core.contracts.orchestration import GuardedToolBatch, GuardedToolCall, ToolExecutionReport
from agentgate_core.contracts.recovery import (
    FailureSignal,
    ProgressSignal,
    RecoveryPlan,
    RecoveryResult,
    RecoveryTermination,
    StagnationAssessment,
)
from agentgate_core.contracts.state_verification import CompletionGateDecision, ResponseGroundingDecision
from agentgate_core.contracts.tool_guard import (
    RawToolCall,
    ScheduleDisposition,
    ToolRequirement,
    ToolResolution,
)
from agentgate_core.contracts.trace import TraceActor, TraceEventType
from agentgate_core.recovery_controller.controller import RecoveryController
from agentgate_core.recovery_controller.progress import ProgressSnapshot, ProgressTracker, fingerprint_action
from agentgate_core.recovery_controller.stagnation import StagnationDetector
from agentgate_core.runtime.session import AgentGateRuntimeSession
from agentgate_core.runtime.state_store import IdempotencyConflictError, StatePersistenceConflictError
from agentgate_core.state_verification.completion_gate import CompletionGate
from agentgate_core.state_verification.effect_ledger import EffectLedger
from agentgate_core.state_verification.evidence_board import EvidenceBoard
from agentgate_core.state_verification.response_grounding import ResponseGroundingGate
from agentgate_core.state_verification.task_reducer import TaskReducer
from agentgate_core.state_verification.verifier import PostActionVerifier
from agentgate_core.tool_execution_guard.active_set import ActiveToolSetManager
from agentgate_core.tool_execution_guard.effect_guard import GuardedEffectLedger
from agentgate_core.tool_execution_guard.normalizer import ActionNormalizer
from agentgate_core.tool_execution_guard.resolver import DynamicToolResolver
from agentgate_core.tool_execution_guard.scheduler import ActionDependencyScheduler

_COMPLETION_TOOLS = frozenset({"supervisor__complete_task", "complete_task"})


class SafeDeskOrchestrator:
    def __init__(
        self,
        *,
        session: AgentGateRuntimeSession,
        active_sets: ActiveToolSetManager,
        normalizer: ActionNormalizer,
        scheduler: ActionDependencyScheduler,
        guarded_effects: GuardedEffectLedger,
        reducer: TaskReducer,
        context_builder: ContextBuilder,
        completion_gate: CompletionGate,
        response_grounding: ResponseGroundingGate,
        verifier: PostActionVerifier,
        recovery: RecoveryController,
        tool_resolver: DynamicToolResolver,
        progress_tracker: ProgressTracker,
        stagnation_detector: StagnationDetector,
    ) -> None:
        self.session = session
        self.active_sets = active_sets
        self.normalizer = normalizer
        self.scheduler = scheduler
        self.guarded_effects = guarded_effects
        self.reducer = reducer
        self.effect_linker = EffectLedger(reducer)
        self.evidence_board = EvidenceBoard(reducer)
        self.context_builder = context_builder
        self.completion_gate = completion_gate
        self.response_grounding = response_grounding
        self.verifier = verifier
        self.recovery = recovery
        self.tool_resolver = tool_resolver
        self.progress_tracker = progress_tracker
        self.stagnation_detector = stagnation_detector

    def before_model(
        self,
        task_id: str,
        *,
        turn: int,
        soft_token_limit: int,
        hard_token_limit: int,
        reserved_output_tokens: int = 0,
    ) -> ContextPack:
        return self.context_builder.build(
            task_id,
            turn=turn,
            active_tool_set=self.active_sets.get(task_id),
            soft_token_limit=soft_token_limit,
            hard_token_limit=hard_token_limit,
            reserved_output_tokens=reserved_output_tokens,
        )

    def guard_tool_batch(
        self,
        calls: tuple[RawToolCall, ...],
        *,
        context_pack: ContextPack,
        subgoal_by_call_id: dict[str, str],
        completed_action_ids: tuple[str, ...] = (),
        policy_context: dict[str, object] | None = None,
        dependency_action_by_tool: dict[str, str] | None = None,
        evidence_by_requirement: dict[str, str] | None = None,
    ) -> GuardedToolBatch:
        if not calls:
            raise ValueError("guard_tool_batch requires at least one call")
        if len({call.tool_call_id for call in calls}) != len(calls):
            raise ValueError("tool_call_id values must be unique within a model response")
        normalized = []
        early: dict[str, GuardedToolCall] = {}
        for call in calls:
            if call.task_id != context_pack.task_id:
                raise ValueError("tool call and context pack must belong to the same task")
            try:
                normalized.append(
                    self.normalizer.normalize(
                        call,
                        dependency_action_by_tool=dependency_action_by_tool,
                        evidence_by_requirement=evidence_by_requirement,
                    )
                )
            except Exception as exc:
                early[call.tool_call_id] = GuardedToolCall(
                    tool_call_id=call.tool_call_id,
                    schedule_disposition=ScheduleDisposition.REPLAN,
                    outcome=DecisionOutcome.REPLAN,
                    should_execute=False,
                    reason=f"Tool call normalization failed with {type(exc).__name__}; select an active public tool.",
                    tool_result_status=_non_execution("normalization_failed"),
                )
        schedule_by_id = {}
        if normalized:
            verified_evidence = tuple(
                item.evidence_id
                for item in self.session.state_store.list_evidence(context_pack.task_id)
                if item.status is EvidenceStatus.VERIFIED
            )
            schedule = self.scheduler.plan(
                context_pack.task_id,
                context_pack.state_version,
                tuple(normalized),
                completed_action_ids=completed_action_ids,
                verified_evidence_ids=verified_evidence,
            )
            schedule_by_id = {item.action_id: item for item in schedule.actions}
        action_by_id = {action.action_id: action for action in normalized}
        decisions: list[GuardedToolCall] = []
        executed_write = False
        for call in calls:
            if call.tool_call_id in early:
                decisions.append(early[call.tool_call_id])
                continue
            action = action_by_id[call.tool_call_id]
            scheduled = schedule_by_id[action.action_id]
            if scheduled.disposition is not ScheduleDisposition.EXECUTE:
                outcome = (
                    DecisionOutcome.REPLAN
                    if scheduled.disposition is ScheduleDisposition.REPLAN
                    else DecisionOutcome.DEFER
                )
                decisions.append(
                    GuardedToolCall(
                        tool_call_id=call.tool_call_id,
                        action=action,
                        schedule_disposition=scheduled.disposition,
                        outcome=outcome,
                        should_execute=False,
                        reason=scheduled.reason,
                        tool_result_status=_non_execution(scheduled.disposition.value),
                    )
                )
                continue
            result = self.session.evaluate_action(
                ActionEvaluationContext(
                    task_id=action.task_id,
                    run_id=self.session.run_id,
                    turn=action.source_turn,
                    action=action,
                    state_version=context_pack.state_version,
                    metadata={
                        "completed_action_ids": list(completed_action_ids),
                        "policy_context": policy_context or {},
                    },
                )
            )
            effect_ids: tuple[str, ...] = ()
            should_execute = result.outcome is DecisionOutcome.ALLOW
            if should_execute and action.kind.value == "write" and action.tool_name not in _COMPLETION_TOOLS:
                subgoal_id = subgoal_by_call_id.get(call.tool_call_id)
                if subgoal_id is None:
                    should_execute = False
                    result_reason = "Write action lacks an explicit owning subgoal."
                else:
                    try:
                        reserved = self.guarded_effects.reserve(action, turn=action.source_turn)
                    except (IdempotencyConflictError, StatePersistenceConflictError):
                        # Reservation is the dispatch boundary: a racing writer must re-plan,
                        # never execute after another transaction acquired the fingerprint.
                        should_execute = False
                        result_reason = (
                            "Write reservation lost an idempotency or persistence race; "
                            "refresh state and verify the existing effect before re-planning."
                        )
                    else:
                        for effect in reserved:
                            self.effect_linker.link_existing(
                                effect.task_id,
                                effect.effect_id,
                                subgoal_id=subgoal_id,
                                turn=action.source_turn,
                            )
                        effect_ids = tuple(effect.effect_id for effect in reserved)
                        executed_write = True
                        result_reason = "All guard stages allowed the write and its effects were reserved."
            elif should_execute and action.tool_name in _COMPLETION_TOOLS:
                result_reason = (
                    "Completion Gate allowed the control-plane completion action; "
                    "it does not reserve a domain effect."
                )
            else:
                result_reason = (
                    "All guard stages allowed the action."
                    if should_execute
                    else f"Coordinator stopped execution with outcome {result.outcome.value}."
                )
            decisions.append(
                GuardedToolCall(
                    tool_call_id=call.tool_call_id,
                    action=action,
                    schedule_disposition=scheduled.disposition,
                    outcome=result.outcome
                    if should_execute
                    else (result.outcome if result.outcome is not DecisionOutcome.ALLOW else DecisionOutcome.DEFER),
                    should_execute=should_execute,
                    reason=result_reason,
                    effect_ids=effect_ids,
                    coordinator_result=result,
                    tool_result_status=None if should_execute else _non_execution("guard_denied"),
                )
            )
        batch = GuardedToolBatch(
            task_id=context_pack.task_id,
            state_version=context_pack.state_version,
            proposed_calls=calls,
            decisions=tuple(decisions),
            requires_state_refresh=executed_write,
        )
        for decision in batch.decisions:
            if decision.should_execute:
                continue
            self.session.trace_recorder.record(
                task_id=batch.task_id,
                run_id=self.session.run_id,
                turn=decision.action.source_turn if decision.action is not None else calls[0].source_turn,
                event_type=TraceEventType.TOOL_NOT_EXECUTED,
                actor=TraceActor.RUNTIME,
                correlation_id=decision.tool_call_id,
                state_version=batch.state_version,
                payload={
                    "decision": decision.model_dump(mode="json"),
                    "tool_result": decision.tool_result_status or _non_execution("guard_denied"),
                },
                critical=decision.action is not None and decision.action.kind.value == "write",
            )
        return batch

    def mark_tool_started(self, decision: GuardedToolCall, *, turn: int) -> None:
        if not decision.should_execute or decision.action is None:
            raise ValueError("only an executable guarded call may be marked started")
        for effect_id in decision.effect_ids:
            self.guarded_effects.transition(
                decision.action.task_id,
                effect_id,
                EffectStatus.IN_FLIGHT,
                turn=turn,
            )
        self.session.trace_recorder.record(
            task_id=decision.action.task_id,
            run_id=self.session.run_id,
            turn=turn,
            event_type=TraceEventType.TOOL_STARTED,
            actor=TraceActor.TOOL,
            correlation_id=decision.action.action_id,
            payload={"action": decision.action.model_dump(mode="json")},
            critical=decision.action.kind.value == "write",
        )

    def after_tool(
        self,
        decision: GuardedToolCall,
        report: ToolExecutionReport,
        *,
        turn: int,
        subgoal_id: str | None = None,
    ) -> tuple[str, ...]:
        if decision.action is None or report.action_id != decision.action.action_id:
            raise ValueError("execution report does not match the guarded action")
        if not decision.should_execute or not report.executed:
            raise ValueError("after_tool requires an executed call previously allowed by the guard")
        action = decision.action
        verification_ids: list[str] = []
        trace = self.session.trace_recorder.record(
            task_id=action.task_id,
            run_id=self.session.run_id,
            turn=turn,
            event_type=TraceEventType.TOOL_FINISHED,
            actor=TraceActor.TOOL,
            correlation_id=action.action_id,
            payload={
                "action_id": action.action_id,
                "tool_name": action.tool_name,
                "result": report.result,
                "success": report.success,
                "error_code": report.error_code,
                "error_message": report.error_message,
            },
            critical=action.kind.value == "write",
        )
        if action.kind.value == "write" and action.tool_name not in _COMPLETION_TOOLS:
            for effect_id in decision.effect_ids:
                self.guarded_effects.transition(
                    action.task_id,
                    effect_id,
                    EffectStatus.APPLIED_UNVERIFIED if report.success else EffectStatus.FAILED,
                    turn=turn,
                    actual_change=report.result,
                )
                if report.success:
                    try:
                        verification = self.verifier.verify_effect(action.task_id, effect_id, turn=turn)
                    except LookupError:
                        continue
                    verification_ids.append(verification.verification_id)
        else:
            source_event_id = trace.event.event_id if trace.event is not None else f"tool-result-{uuid4()}"
            evidence = EvidenceItem(
                evidence_id=f"evidence-{uuid4()}",
                task_id=action.task_id,
                subject=f"tool_call:{action.action_id}",
                predicate="tool_result",
                value=report.result,
                source_type=EvidenceSourceType.TOOL_RESULT,
                source_event_id=source_event_id,
                status=EvidenceStatus.OBSERVED,
                note="Tool return observed; environment verification has not upgraded it.",
            )
            self.evidence_board.record(evidence, subgoal_id=subgoal_id, turn=turn)
        completion_verified = decision.coordinator_result is not None and any(
            item.stage is PipelineStage.COMPLETION_GATE and item.proposed_outcome is DecisionOutcome.ALLOW
            for item in decision.coordinator_result.decisions
        )
        if report.success and action.tool_name in _COMPLETION_TOOLS and completion_verified:
            self.reducer.set_complete(
                action.task_id,
                reason="Completion tool executed after the coordinator applied Completion Gate.",
                turn=turn,
            )
        return tuple(verification_ids)

    def request_completion(
        self,
        task_id: str,
        response: str,
        *,
        turn: int,
    ) -> tuple[CompletionGateDecision, ResponseGroundingDecision | None]:
        mode = self.session.coordinator.config.state_verification
        completion = self.completion_gate.evaluate(task_id, turn=turn, mode=mode)
        grounding = None
        if mode is not FeatureMode.OFF:
            grounding = self.response_grounding.evaluate(task_id, response, completion, turn=turn)
        if completion.proposed_allowed and completion.effective_allowed:
            self.reducer.set_complete(task_id, reason="Completion Gate verified every required condition.", turn=turn)
        return completion, grounding

    def recover(
        self, signal: FailureSignal, *, turn: int, estimated_tokens: int = 0
    ) -> RecoveryPlan | RecoveryTermination:
        return self.recovery.start(signal, turn=turn, estimated_tokens=estimated_tokens)

    def finish_recovery(
        self,
        plan: RecoveryPlan,
        before: ProgressSignal,
        after: ProgressSignal,
        *,
        turn: int,
        verification_id: str | None = None,
    ) -> RecoveryResult:
        return self.recovery.finish(
            plan,
            before,
            after,
            turn=turn,
            verification_id=verification_id,
        )

    def resolve_tools(self, requirement: ToolRequirement, *, turn: int) -> ToolResolution:
        return self.tool_resolver.resolve(
            requirement,
            mode=self.session.coordinator.config.tool_execution_guard,
            turn=turn,
        )

    def capture_progress(self, task_id: str) -> ProgressSnapshot:
        return self.progress_tracker.capture(task_id)

    def assess_progress(
        self,
        task_id: str,
        before: ProgressSnapshot,
        *,
        turn: int,
        action: ActionIR | None = None,
        failure_type: FailureType | None = None,
        token_growth: int = 0,
    ) -> tuple[ProgressSignal, StagnationAssessment]:
        after = self.progress_tracker.capture(task_id)
        signal = self.progress_tracker.compare(task_id, turn, before, after, token_growth=token_growth)
        assessment = self.stagnation_detector.observe(
            task_id,
            signal,
            fingerprint=fingerprint_action(action) if action is not None else None,
            failure_type=failure_type,
        )
        self.session.trace_recorder.record(
            task_id=task_id,
            run_id=self.session.run_id,
            turn=turn,
            event_type=(
                TraceEventType.STAGNATION_DETECTED if assessment.stagnant else TraceEventType.PROGRESS_ASSESSED
            ),
            actor=TraceActor.RECOVERY_CONTROLLER,
            correlation_id=action.action_id if action is not None else f"progress-{turn}",
            payload={
                "progress": signal.model_dump(mode="json"),
                "stagnation": assessment.model_dump(mode="json"),
            },
            critical=False,
        )
        if signal.score > 0:
            self.stagnation_detector.reset_after_progress(task_id)
        return signal, assessment


def _non_execution(reason: str) -> dict[str, object]:
    return {
        "executed": False,
        "reason": reason,
        "message": "This tool call was not executed. Re-plan using the latest environment state.",
    }


__all__ = ["SafeDeskOrchestrator"]
