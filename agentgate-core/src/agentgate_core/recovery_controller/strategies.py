"""Typed recovery strategy registry and deterministic built-in strategies."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Protocol, runtime_checkable
from uuid import uuid4

from agentgate_core.contracts.action import ActionIR, ActionKind
from agentgate_core.contracts.failure import FailureRecord, FailureType
from agentgate_core.contracts.recovery import (
    FailureSignal,
    RecoveryPlan,
    RecoveryStrategyType,
)
from agentgate_core.contracts.task import TaskPhase


@runtime_checkable
class RecoveryStrategy(Protocol):
    def plan(self, failure: FailureRecord, signal: FailureSignal) -> RecoveryPlan: ...


class StaticRecoveryStrategy:
    def __init__(
        self,
        strategy_type: RecoveryStrategyType,
        phase: TaskPhase,
        instructions: tuple[str, ...],
        *,
        verify_before_execution: bool = False,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.strategy_type = strategy_type
        self.phase = phase
        self.instructions = instructions
        self.verify_before_execution = verify_before_execution
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def plan(self, failure: FailureRecord, signal: FailureSignal) -> RecoveryPlan:
        return RecoveryPlan(
            plan_id=f"recovery-{self._id_factory()}",
            task_id=failure.task_id,
            failure_id=failure.failure_id,
            failure_type=failure.failure_type,
            strategy_type=self.strategy_type,
            recommended_phase=self.phase,
            required_evidence_ids=failure.evidence_ids,
            instructions=self.instructions,
            verify_before_execution=self.verify_before_execution,
            reason=f"Typed recovery selected for {failure.failure_type.value}.",
        )


class ParameterRepairStrategy(StaticRecoveryStrategy):
    def __init__(self, *, id_factory: Callable[[], str] | None = None) -> None:
        super().__init__(
            RecoveryStrategyType.REPAIR_ARGUMENTS,
            TaskPhase.ACT,
            ("Apply only unambiguous schema-derived coercions and remove forbidden extra fields.",),
            id_factory=id_factory,
        )

    def plan(self, failure: FailureRecord, signal: FailureSignal) -> RecoveryPlan:
        base = super().plan(failure, signal)
        action = signal.action
        if action is None:
            return base.model_copy(update={"reason": "No source action is available for deterministic repair."})
        repaired = json.loads(json.dumps(action.arguments))
        changed = False
        violations = signal.metadata.get("violations", [])
        if isinstance(violations, list):
            for violation in violations:
                if not isinstance(violation, dict):
                    continue
                path = violation.get("path")
                code = violation.get("code")
                if not isinstance(path, str) or not path.startswith("arguments."):
                    continue
                segments = tuple(path.split("."))[1:]
                if code == "additional_property":
                    changed |= _delete_path(repaired, segments)
                elif code == "type" and violation.get("expected") == "integer":
                    observed = violation.get("observed")
                    if isinstance(observed, str) and observed.strip().lstrip("-").isdigit():
                        changed |= _set_path(repaired, segments, int(observed))
        repaired_action = _rebuild_action(action, repaired) if changed else None
        return base.model_copy(
            update={
                "repaired_action": repaired_action,
                "reason": (
                    "Schema violations allowed a deterministic argument repair."
                    if changed
                    else "Schema violations require model re-planning because no safe coercion exists."
                ),
            }
        )


class ResourceRelocationStrategy(StaticRecoveryStrategy):
    def __init__(self, *, id_factory: Callable[[], str] | None = None) -> None:
        super().__init__(
            RecoveryStrategyType.RELOCATE_RESOURCE,
            TaskPhase.COLLECT,
            (
                "Discard the stale resource ID.",
                "Query the authoritative read API and select a resource using task constraints.",
                "Re-plan the write with the newly observed resource ID.",
            ),
            id_factory=id_factory,
        )


class VerificationRepairStrategy(StaticRecoveryStrategy):
    def __init__(self, *, id_factory: Callable[[], str] | None = None) -> None:
        super().__init__(
            RecoveryStrategyType.REPAIR_VERIFICATION,
            TaskPhase.REPAIR,
            ("Repair only fields identified by VerificationResult and re-run environment readback.",),
            verify_before_execution=True,
            id_factory=id_factory,
        )

    def plan(self, failure: FailureRecord, signal: FailureSignal) -> RecoveryPlan:
        base = super().plan(failure, signal)
        repair_arguments = signal.metadata.get("repair_arguments")
        if signal.action is None or not isinstance(repair_arguments, dict):
            return base
        merged = dict(signal.action.arguments)
        merged.update(repair_arguments)
        return base.model_copy(
            update={
                "repaired_action": _rebuild_action(signal.action, merged),
                "reason": "Verification differences supplied a typed local repair action.",
            }
        )


class RecoveryStrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[FailureType, RecoveryStrategy] = {}

    def register(self, failure_type: FailureType, strategy: RecoveryStrategy) -> None:
        if failure_type in self._strategies:
            raise ValueError(f"recovery strategy already registered: {failure_type.value}")
        self._strategies[failure_type] = strategy

    def resolve(self, failure_type: FailureType) -> RecoveryStrategy:
        try:
            return self._strategies[failure_type]
        except KeyError as exc:
            raise LookupError(f"no recovery strategy registered: {failure_type.value}") from exc

    @classmethod
    def with_defaults(cls) -> RecoveryStrategyRegistry:
        registry = cls()
        definitions: dict[FailureType, RecoveryStrategy] = {
            FailureType.MISSING_EVIDENCE: StaticRecoveryStrategy(
                RecoveryStrategyType.COLLECT_EVIDENCE, TaskPhase.COLLECT, ("Collect only missing evidence.",)
            ),
            FailureType.INVALID_ARGUMENT: ParameterRepairStrategy(),
            FailureType.OUT_OF_SCHEMA: StaticRecoveryStrategy(
                RecoveryStrategyType.RESOLVE_TOOL, TaskPhase.COLLECT, ("Run Dynamic Tool Resolver and re-plan.",)
            ),
            FailureType.WRONG_TOOL: StaticRecoveryStrategy(
                RecoveryStrategyType.RESOLVE_TOOL, TaskPhase.COLLECT, ("Resolve a compatible public tool.",)
            ),
            FailureType.WRONG_RESOURCE: ResourceRelocationStrategy(),
            FailureType.DEPENDENCY_NOT_SATISFIED: StaticRecoveryStrategy(
                RecoveryStrategyType.RESCHEDULE, TaskPhase.ACT, ("Rebuild the Action DAG in dependency order.",)
            ),
            FailureType.POLICY_DENIED: StaticRecoveryStrategy(
                RecoveryStrategyType.REQUEST_CONFIRMATION,
                TaskPhase.BLOCKED,
                ("Stop execution and request the missing user confirmation.",),
            ),
            FailureType.APPROVAL_REQUIRED: StaticRecoveryStrategy(
                RecoveryStrategyType.REQUEST_APPROVAL,
                TaskPhase.BLOCKED,
                ("Stop execution and request approval.",),
            ),
            FailureType.TOOL_TIMEOUT: StaticRecoveryStrategy(
                RecoveryStrategyType.VERIFY_BEFORE_RETRY,
                TaskPhase.VERIFY,
                ("Read back environment state before considering retry.",),
                verify_before_execution=True,
            ),
            FailureType.TOOL_EXECUTION_ERROR: StaticRecoveryStrategy(
                RecoveryStrategyType.VERIFY_BEFORE_RETRY,
                TaskPhase.VERIFY,
                ("Inspect the typed tool error and verify environment state before selecting a retry path.",),
                verify_before_execution=True,
            ),
            FailureType.AUTHENTICATION_FAILED: StaticRecoveryStrategy(
                RecoveryStrategyType.COLLECT_EVIDENCE,
                TaskPhase.COLLECT,
                ("Refresh authentication state through the public login flow; never reuse rejected credentials.",),
            ),
            FailureType.PERMISSION_DENIED: StaticRecoveryStrategy(
                RecoveryStrategyType.STOP,
                TaskPhase.BLOCKED,
                ("Stop the restricted action and report the missing permission truthfully.",),
            ),
            FailureType.RATE_LIMITED: StaticRecoveryStrategy(
                RecoveryStrategyType.INFRASTRUCTURE_RETRY,
                TaskPhase.BLOCKED,
                ("Apply bounded infrastructure backoff without changing task intent.",),
            ),
            FailureType.VERIFICATION_FAILED: VerificationRepairStrategy(),
            FailureType.DUPLICATE_ACTION: StaticRecoveryStrategy(
                RecoveryStrategyType.VERIFY_BEFORE_RETRY,
                TaskPhase.VERIFY,
                ("Do not replay the action; read back the environment.",),
                verify_before_execution=True,
            ),
            FailureType.UNINTENDED_SIDE_EFFECT: StaticRecoveryStrategy(
                RecoveryStrategyType.VERIFY_BEFORE_RETRY,
                TaskPhase.VERIFY,
                ("Stop new writes, inventory the unintended change, and use only an explicitly supported rollback.",),
                verify_before_execution=True,
            ),
            FailureType.PARTIAL_COMPLETION: StaticRecoveryStrategy(
                RecoveryStrategyType.COMPLETE_MISSING_SUBGOAL,
                TaskPhase.ACT,
                ("Re-plan only required subgoals that are not verified.",),
            ),
            FailureType.NO_PROGRESS: StaticRecoveryStrategy(
                RecoveryStrategyType.RESOLVE_TOOL, TaskPhase.REPAIR, ("Change retrieval path or locally re-plan.",)
            ),
            FailureType.CONTEXT_DEGRADED: StaticRecoveryStrategy(
                RecoveryStrategyType.REBUILD_CONTEXT,
                TaskPhase.COLLECT,
                ("Rebuild ContextPack from current state and discard stale plans.",),
            ),
            FailureType.INFRASTRUCTURE_ERROR: StaticRecoveryStrategy(
                RecoveryStrategyType.INFRASTRUCTURE_RETRY,
                TaskPhase.BLOCKED,
                ("Apply infrastructure backoff without counting a model failure.",),
            ),
        }
        fallback = StaticRecoveryStrategy(
            RecoveryStrategyType.STOP,
            TaskPhase.FAILED,
            ("Stop because no automatic recovery is safe for this failure type.",),
        )
        for failure_type in FailureType:
            registry.register(failure_type, definitions.get(failure_type, fallback))
        return registry


def _rebuild_action(action: ActionIR, arguments: dict[str, object]) -> ActionIR:
    expected_effects = tuple(
        effect.model_copy(
            update={
                "expected_change": {
                    **effect.expected_change,
                    **({"arguments": arguments} if "arguments" in effect.expected_change else {}),
                }
            }
        )
        for effect in action.expected_effects
    )
    idempotency_key = action.idempotency_key
    if action.kind is ActionKind.WRITE:
        canonical = json.dumps(
            {"task_id": action.task_id, "operation": action.operation, "arguments": arguments},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        idempotency_key = f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"
    repair_digest = hashlib.sha256(
        json.dumps(arguments, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()[:16]
    rebuilt = action.model_copy(
        update={
            "action_id": f"recovery-{repair_digest}",
            "arguments": arguments,
            "expected_effects": expected_effects,
            "idempotency_key": idempotency_key,
            "dependency_action_ids": tuple(dict.fromkeys((*action.dependency_action_ids, action.action_id))),
        }
    )
    return ActionIR.model_validate(rebuilt.model_dump(mode="python"))


def _delete_path(root: dict[str, object], segments: tuple[str, ...]) -> bool:
    if not segments:
        return False
    current: object = root
    for segment in segments[:-1]:
        if not isinstance(current, dict) or segment not in current:
            return False
        current = current[segment]
    if not isinstance(current, dict) or segments[-1] not in current:
        return False
    del current[segments[-1]]
    return True


def _set_path(root: dict[str, object], segments: tuple[str, ...], value: object) -> bool:
    if not segments:
        return False
    current: object = root
    for segment in segments[:-1]:
        if not isinstance(current, dict) or segment not in current:
            return False
        current = current[segment]
    if not isinstance(current, dict) or segments[-1] not in current:
        return False
    current[segments[-1]] = value
    return True


__all__ = [
    "ParameterRepairStrategy",
    "RecoveryStrategy",
    "RecoveryStrategyRegistry",
    "ResourceRelocationStrategy",
    "StaticRecoveryStrategy",
    "VerificationRepairStrategy",
]
