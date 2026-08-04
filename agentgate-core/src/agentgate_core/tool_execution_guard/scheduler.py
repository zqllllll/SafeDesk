"""Dependency-aware scheduling for model-proposed tool-call batches."""

from __future__ import annotations

from agentgate_core.contracts.action import ActionIR, ActionKind
from agentgate_core.contracts.decision import (
    ActionEvaluationContext,
    DecisionOutcome,
    FeatureName,
    PipelineStage,
    StageEvaluation,
)
from agentgate_core.contracts.evidence import EvidenceStatus
from agentgate_core.contracts.tool_guard import ActionSchedule, ScheduledAction, ScheduleDisposition
from agentgate_core.runtime.state_store import TypedStateStore


class ActionDependencyScheduler:
    def plan(
        self,
        task_id: str,
        state_version: int,
        actions: tuple[ActionIR, ...],
        *,
        completed_action_ids: tuple[str, ...] = (),
        verified_evidence_ids: tuple[str, ...] = (),
        stale_action_ids: tuple[str, ...] = (),
    ) -> ActionSchedule:
        if not actions:
            raise ValueError("an action schedule requires at least one proposed action")
        if any(action.task_id != task_id for action in actions):
            raise ValueError("all scheduled actions must belong to the task")
        known_action_ids = {action.action_id for action in actions} | set(completed_action_ids)
        for action in actions:
            unknown = set(action.dependency_action_ids) - known_action_ids
            if unknown:
                raise ValueError(f"action {action.action_id} has unknown dependencies: {sorted(unknown)}")
        _require_acyclic(actions)
        completed = set(completed_action_ids)
        verified = set(verified_evidence_ids)
        stale = set(stale_action_ids)
        scheduled: dict[str, ScheduledAction] = {}
        execution_groups: dict[str, int] = {}
        pending = list(actions)
        write_scheduled = False
        while pending:
            progressed = False
            for action in tuple(pending):
                if action.action_id in stale:
                    scheduled[action.action_id] = ScheduledAction(
                        action_id=action.action_id,
                        disposition=ScheduleDisposition.REPLAN,
                        reason="The action was planned against stale environment state.",
                    )
                    pending.remove(action)
                    progressed = True
                    continue
                unmet_actions = tuple(item for item in action.dependency_action_ids if item not in completed)
                unmet_evidence = tuple(item for item in action.required_evidence_ids if item not in verified)
                if unmet_actions or unmet_evidence:
                    continue
                if write_scheduled:
                    scheduled[action.action_id] = ScheduledAction(
                        action_id=action.action_id,
                        disposition=ScheduleDisposition.SUPPRESSED_PENDING_STATE_REFRESH,
                        reason="A preceding write requires state refresh and model re-planning.",
                    )
                    pending.remove(action)
                    progressed = True
                    continue
                dependency_groups = [
                    execution_groups[item] for item in action.dependency_action_ids if item in execution_groups
                ]
                execution_group = max(dependency_groups, default=-1) + 1
                if action.kind is ActionKind.WRITE:
                    execution_group = max(execution_groups.values(), default=-1) + 1
                scheduled[action.action_id] = ScheduledAction(
                    action_id=action.action_id,
                    disposition=ScheduleDisposition.EXECUTE,
                    execution_group=execution_group,
                    reason=(
                        "Independent read is safe in the current execution group."
                        if action.kind is ActionKind.READ
                        else "This is the first dependency-ready write and must execute alone."
                    ),
                )
                pending.remove(action)
                completed.add(action.action_id)
                execution_groups[action.action_id] = execution_group
                progressed = True
                if action.kind is ActionKind.WRITE:
                    write_scheduled = True
            if not progressed:
                for action in pending:
                    scheduled[action.action_id] = ScheduledAction(
                        action_id=action.action_id,
                        disposition=ScheduleDisposition.DEFER,
                        reason="Action dependencies or required evidence are not satisfied.",
                        unmet_action_ids=tuple(item for item in action.dependency_action_ids if item not in completed),
                        unmet_evidence_ids=tuple(item for item in action.required_evidence_ids if item not in verified),
                    )
                break
        return ActionSchedule(
            task_id=task_id,
            state_version=state_version,
            actions=tuple(scheduled[action.action_id] for action in actions),
            contains_write=any(action.kind is ActionKind.WRITE for action in actions),
            requires_state_refresh=write_scheduled,
        )


class DependencySchedulerStage:
    stage = PipelineStage.DEPENDENCY_SCHEDULER
    feature = FeatureName.TOOL_EXECUTION_GUARD

    def __init__(self, state_store: TypedStateStore) -> None:
        self.state_store = state_store

    def evaluate(self, context: ActionEvaluationContext) -> StageEvaluation:
        completed = set(_string_tuple(context.metadata.get("completed_action_ids")))
        evidence = {
            item.evidence_id
            for item in self.state_store.list_evidence(context.task_id)
            if item.status is EvidenceStatus.VERIFIED
        }
        unmet_actions = tuple(item for item in context.action.dependency_action_ids if item not in completed)
        unmet_evidence = tuple(item for item in context.action.required_evidence_ids if item not in evidence)
        if unmet_actions or unmet_evidence:
            return StageEvaluation(
                outcome=DecisionOutcome.DEFER,
                reason_code="dependencies_unsatisfied",
                explanation="The action depends on unfinished actions or unverified evidence.",
                payload={"unmet_action_ids": list(unmet_actions), "unmet_evidence_ids": list(unmet_evidence)},
            )
        return StageEvaluation(
            outcome=DecisionOutcome.ALLOW,
            reason_code="dependencies_satisfied",
            explanation="All declared action and evidence dependencies are satisfied.",
        )


def _require_acyclic(actions: tuple[ActionIR, ...]) -> None:
    proposed_ids = {action.action_id for action in actions}
    graph = {
        action.action_id: tuple(item for item in action.dependency_action_ids if item in proposed_ids)
        for action in actions
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(action_id: str) -> None:
        if action_id in visiting:
            raise ValueError(f"action dependency cycle detected at {action_id}")
        if action_id in visited:
            return
        visiting.add(action_id)
        for dependency in graph[action_id]:
            visit(dependency)
        visiting.remove(action_id)
        visited.add(action_id)

    for action_id in graph:
        visit(action_id)


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


__all__ = ["ActionDependencyScheduler", "DependencySchedulerStage"]
