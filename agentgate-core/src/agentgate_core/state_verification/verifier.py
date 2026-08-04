"""Verifier registry and generic post-action environment verification."""

from __future__ import annotations

from collections.abc import Callable
from time import sleep
from typing import Protocol, runtime_checkable
from uuid import uuid4

from agentgate_core.contracts.effect import EffectRecord, EffectStatus
from agentgate_core.contracts.evidence import EvidenceItem, EvidenceSourceType, EvidenceStatus
from agentgate_core.contracts.failure import FailureRecord, FailureStatus, FailureType, ResponsibleLayer
from agentgate_core.contracts.state_verification import (
    SubgoalTransitionRequest,
    VerificationObservation,
    VerifierSpec,
)
from agentgate_core.contracts.task import SubgoalStatus
from agentgate_core.contracts.trace import TraceActor, TraceEventType
from agentgate_core.contracts.verification import (
    DifferenceKind,
    FieldDifference,
    VerificationResult,
    VerificationStatus,
    VerificationType,
)
from agentgate_core.state_verification.evidence_board import EvidenceBoard
from agentgate_core.state_verification.task_reducer import TaskReducer


@runtime_checkable
class EnvironmentVerifier(Protocol):
    def observe(self, effect: EffectRecord, spec: VerifierSpec, attempt: int) -> VerificationObservation: ...


class VerifierRegistry:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], tuple[VerifierSpec, EnvironmentVerifier]] = {}

    def register(self, spec: VerifierSpec, verifier: EnvironmentVerifier) -> None:
        for resource_type in spec.resource_types:
            key = (resource_type, spec.verifier_name)
            if key in self._entries:
                raise ValueError(f"verifier already registered: {resource_type}/{spec.verifier_name}")
            self._entries[key] = (spec, verifier)

    def resolve(self, resource_type: str, verifier_name: str | None = None) -> tuple[VerifierSpec, EnvironmentVerifier]:
        matches = [
            value
            for (registered_type, registered_name), value in self._entries.items()
            if registered_type == resource_type and (verifier_name is None or registered_name == verifier_name)
        ]
        if not matches:
            raise LookupError(f"no verifier registered for resource type: {resource_type}")
        if len(matches) > 1:
            raise LookupError(
                f"multiple verifiers registered for resource type: {resource_type}; specify verifier_name"
            )
        return matches[0]


class PostActionVerifier:
    def __init__(
        self,
        reducer: TaskReducer,
        registry: VerifierRegistry,
        *,
        id_factory: Callable[[], str] | None = None,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self.reducer = reducer
        self.session = reducer.session
        self.registry = registry
        self.evidence_board = EvidenceBoard(reducer)
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._sleeper = sleeper

    def verify_effect(
        self,
        task_id: str,
        effect_id: str,
        *,
        turn: int,
        verifier_name: str | None = None,
    ) -> VerificationResult:
        effect = self.session.state_store.get_effect(task_id, effect_id)
        spec, verifier = self.registry.resolve(effect.resource.resource_type, verifier_name)
        expected = _select_expected(effect.expected_change, spec)
        observation = self._observe(effect, spec, verifier, expected)
        differences = _compare(expected, observation.observed_state, spec)
        if observation.error_code is not None:
            status = VerificationStatus.ERROR
        elif observation.observed_state is None:
            status = VerificationStatus.UNKNOWN
        elif differences or observation.unintended_effects:
            status = VerificationStatus.MISMATCH
        else:
            status = VerificationStatus.VERIFIED
        verification_id = f"verification-{self._id_factory()}"
        result = VerificationResult(
            verification_id=verification_id,
            task_id=task_id,
            verification_type=VerificationType.ACTION,
            target_id=effect.effect_id,
            action_id=effect.action_id,
            verifier_name=spec.verifier_name,
            verifier_version=spec.verifier_version,
            expected_state=expected,
            observed_state=observation.observed_state,
            status=status,
            differences=differences,
            unintended_effects=observation.unintended_effects,
            error_code=observation.error_code,
            error_message=observation.error_message,
            checked_at=observation.observed_at,
        )
        self.session.state_store.append_verification(task_id, result)
        evidence_ids: tuple[str, ...] = ()
        if status is VerificationStatus.VERIFIED:
            evidence = EvidenceItem(
                evidence_id=f"evidence-{self._id_factory()}",
                task_id=task_id,
                subject=f"effect:{effect.effect_id}",
                predicate="environment_state_matches_expected",
                value=observation.observed_state,
                source_type=EvidenceSourceType.ENVIRONMENT_VERIFICATION,
                source_event_id=observation.source_event_id,
                observed_at=observation.observed_at,
                status=EvidenceStatus.VERIFIED,
                verification_ids=(verification_id,),
                note="Environment readback matched the declared expected effect.",
            )
            self.evidence_board.record(evidence, turn=turn)
            evidence_ids = (evidence.evidence_id,)
        updated_effect = effect.model_copy(
            update={
                "status": _effect_status(status),
                "actual_change": observation.observed_state,
                "verification_id": verification_id if status is VerificationStatus.VERIFIED else None,
                "updated_at": observation.observed_at,
            }
        )
        stored_effect = self.session.state_store.update_effect(task_id, updated_effect, expected_status=effect.status)
        self.session.trace_recorder.record(
            task_id=task_id,
            run_id=self.session.run_id,
            turn=turn,
            event_type=TraceEventType.EFFECT_STATUS_CHANGED,
            actor=TraceActor.VERIFIER,
            correlation_id=effect.effect_id,
            payload={
                "effect_transition": {
                    "from": effect.status.value,
                    "effect": stored_effect.model_dump(mode="json"),
                }
            },
            critical=True,
        )
        failure_ids: tuple[str, ...] = ()
        if status is not VerificationStatus.VERIFIED:
            failure = FailureRecord(
                failure_id=f"failure-{self._id_factory()}",
                task_id=task_id,
                action_id=effect.action_id,
                failure_type=FailureType.VERIFICATION_FAILED,
                message=observation.error_message or "Environment readback did not match the expected effect.",
                retryable=status in {VerificationStatus.UNKNOWN, VerificationStatus.ERROR},
                responsible_layer=ResponsibleLayer.VERIFIER,
                evidence_ids=evidence_ids,
                status=FailureStatus.OPEN,
                created_at=observation.observed_at,
                updated_at=observation.observed_at,
            )
            self.session.state_store.append_failure(task_id, failure)
            failure_ids = (failure.failure_id,)
        self._link_result(task_id, effect_id, evidence_ids, verification_id, failure_ids, turn)
        self.session.trace_recorder.record(
            task_id=task_id,
            run_id=self.session.run_id,
            turn=turn,
            event_type=TraceEventType.VERIFICATION_FINISHED,
            actor=TraceActor.VERIFIER,
            correlation_id=verification_id,
            state_version=self.session.state_store.get_task_state(task_id).state_version,
            payload={"verification": result.model_dump(mode="json")},
            critical=True,
        )
        if status is VerificationStatus.VERIFIED:
            self._promote_subgoals(task_id, effect_id, turn)
        return result

    def _observe(
        self,
        effect: EffectRecord,
        spec: VerifierSpec,
        verifier: EnvironmentVerifier,
        expected: dict[str, object],
    ) -> VerificationObservation:
        observation: VerificationObservation | None = None
        for attempt in range(1, spec.max_attempts + 1):
            if attempt > 1 and spec.eventual_consistency_delay_ms:
                self._sleeper(spec.eventual_consistency_delay_ms / 1000)
            observation = verifier.observe(effect, spec, attempt)
            if observation.task_id != effect.task_id or observation.effect_id != effect.effect_id:
                raise ValueError("verifier observation does not belong to the requested effect")
            if (
                observation.error_code is None
                and observation.observed_state is not None
                and not _compare(expected, observation.observed_state, spec)
                and not observation.unintended_effects
            ):
                break
        assert observation is not None
        return observation

    def _link_result(
        self,
        task_id: str,
        effect_id: str,
        evidence_ids: tuple[str, ...],
        verification_id: str,
        failure_ids: tuple[str, ...],
        turn: int,
    ) -> None:
        state = self.session.state_store.get_task_state(task_id)
        owners = [item for item in state.subgoals if effect_id in item.effect_ids]
        if not owners:
            raise ValueError(f"effect is not linked to a subgoal: {effect_id}")
        for owner in owners:
            current = self.session.state_store.get_task_state(task_id)
            current_owner = next(item for item in current.subgoals if item.subgoal_id == owner.subgoal_id)
            self.reducer.transition(
                SubgoalTransitionRequest(
                    task_id=task_id,
                    subgoal_id=owner.subgoal_id,
                    target_status=current_owner.status,
                    reason="Verification records were linked to the owning subgoal.",
                    evidence_ids=evidence_ids,
                    verification_ids=(verification_id,),
                    failure_ids=failure_ids,
                    turn=turn,
                )
            )

    def _promote_subgoals(self, task_id: str, effect_id: str, turn: int) -> None:
        state = self.session.state_store.get_task_state(task_id)
        for subgoal in state.subgoals:
            if effect_id not in subgoal.effect_ids or subgoal.status not in {
                SubgoalStatus.WAITING_FOR_EVIDENCE,
                SubgoalStatus.COMPLETED_UNVERIFIED,
            }:
                continue
            self.reducer.transition(
                SubgoalTransitionRequest(
                    task_id=task_id,
                    subgoal_id=subgoal.subgoal_id,
                    target_status=SubgoalStatus.COMPLETED_VERIFIED,
                    reason="All linked completion evidence and effects were environment-verified.",
                    turn=turn,
                )
            )


def _select_expected(expected_change: dict[str, object], spec: VerifierSpec) -> dict[str, object]:
    expected = {key: value for key, value in expected_change.items() if key not in spec.ignored_fields}
    if spec.expected_fields:
        expected = {key: expected_change[key] for key in spec.expected_fields if key in expected_change}
    return expected


def _compare(
    expected: object, observed: object, spec: VerifierSpec, path: str = "state"
) -> tuple[FieldDifference, ...]:
    differences: list[FieldDifference] = []
    if isinstance(expected, dict):
        if not isinstance(observed, dict):
            return (
                FieldDifference(path=path, kind=DifferenceKind.TYPE_MISMATCH, expected=expected, observed=observed),
            )
        for key, expected_value in expected.items():
            child = f"{path}.{key}"
            if key not in observed:
                differences.append(
                    FieldDifference(path=child, kind=DifferenceKind.MISSING, expected=expected_value, observed=None)
                )
            else:
                differences.extend(_compare(expected_value, observed[key], spec, child))
        for key in spec.forbidden_fields:
            if key in observed:
                differences.append(
                    FieldDifference(path=f"{path}.{key}", kind=DifferenceKind.UNEXPECTED, observed=observed[key])
                )
    elif type(expected) is not type(observed):
        differences.append(
            FieldDifference(path=path, kind=DifferenceKind.TYPE_MISMATCH, expected=expected, observed=observed)
        )
    elif expected != observed:
        differences.append(
            FieldDifference(path=path, kind=DifferenceKind.DIFFERENT, expected=expected, observed=observed)
        )
    return tuple(differences)


def _effect_status(status: VerificationStatus) -> EffectStatus:
    if status is VerificationStatus.VERIFIED:
        return EffectStatus.VERIFIED
    if status is VerificationStatus.MISMATCH:
        return EffectStatus.FAILED
    return EffectStatus.UNKNOWN


__all__ = ["EnvironmentVerifier", "PostActionVerifier", "VerifierRegistry"]
