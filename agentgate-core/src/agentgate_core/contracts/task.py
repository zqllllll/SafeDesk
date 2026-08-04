"""Task contract and runtime task-state schemas."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from agentgate_core.contracts.base import (
    AwareDatetime,
    ContractModel,
    HumanText,
    Identifier,
    PositiveInt,
    VersionedContract,
    require_unique,
    utc_now,
)


class ConstraintKind(StrEnum):
    HARD = "hard"
    SOFT = "soft"
    POLICY = "policy"
    USER_CONFIRMATION = "user_confirmation"


class SubgoalStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    WAITING_FOR_EVIDENCE = "waiting_for_evidence"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    BLOCKED = "blocked"
    COMPLETED_UNVERIFIED = "completed_unverified"
    COMPLETED_VERIFIED = "completed_verified"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPhase(StrEnum):
    COLLECT = "collect"
    ACT = "act"
    VERIFY = "verify"
    REPAIR = "repair"
    COMPLETE = "complete"
    BLOCKED = "blocked"
    FAILED = "failed"


class Constraint(ContractModel):
    constraint_id: Identifier
    description: HumanText
    kind: ConstraintKind = ConstraintKind.HARD
    source_event_id: Identifier | None = None
    active: bool = True


class CompletionCondition(ContractModel):
    condition_id: Identifier
    description: HumanText
    required_evidence_ids: tuple[Identifier, ...] = ()
    required_effect_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        require_unique(self.required_evidence_ids, "required_evidence_ids")
        require_unique(self.required_effect_ids, "required_effect_ids")
        return self


class SubgoalDefinition(ContractModel):
    subgoal_id: Identifier
    description: HumanText
    dependency_ids: tuple[Identifier, ...] = ()
    completion_condition_ids: tuple[Identifier, ...] = ()
    required: bool = True

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        require_unique(self.dependency_ids, "dependency_ids")
        require_unique(self.completion_condition_ids, "completion_condition_ids")
        if self.subgoal_id in self.dependency_ids:
            raise ValueError("a subgoal cannot depend on itself")
        return self


class TaskContract(VersionedContract):
    task_id: Identifier
    original_instruction: HumanText
    normalized_goal: HumanText
    subgoals: tuple[SubgoalDefinition, ...] = Field(min_length=1)
    constraints: tuple[Constraint, ...] = ()
    completion_conditions: tuple[CompletionCondition, ...] = Field(min_length=1)
    allowed_effects: tuple[Identifier, ...] = ()
    forbidden_effects: tuple[Identifier, ...] = ()
    required_confirmations: tuple[Identifier, ...] = ()
    version: PositiveInt = 1
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        subgoal_ids = tuple(item.subgoal_id for item in self.subgoals)
        condition_ids = tuple(item.condition_id for item in self.completion_conditions)
        constraint_ids = tuple(item.constraint_id for item in self.constraints)
        require_unique(subgoal_ids, "subgoal ids")
        require_unique(condition_ids, "completion condition ids")
        require_unique(constraint_ids, "constraint ids")
        require_unique(self.allowed_effects, "allowed_effects")
        require_unique(self.forbidden_effects, "forbidden_effects")
        require_unique(self.required_confirmations, "required_confirmations")

        subgoal_id_set = set(subgoal_ids)
        condition_id_set = set(condition_ids)
        for subgoal in self.subgoals:
            unknown_dependencies = set(subgoal.dependency_ids) - subgoal_id_set
            if unknown_dependencies:
                raise ValueError(
                    f"subgoal {subgoal.subgoal_id} has unknown dependencies: {sorted(unknown_dependencies)}"
                )
            unknown_conditions = set(subgoal.completion_condition_ids) - condition_id_set
            if unknown_conditions:
                raise ValueError(
                    f"subgoal {subgoal.subgoal_id} has unknown completion conditions: {sorted(unknown_conditions)}"
                )

        dependencies_by_id = {item.subgoal_id: item.dependency_ids for item in self.subgoals}
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(subgoal_id: str) -> None:
            if subgoal_id in visiting:
                raise ValueError(f"subgoal dependency graph contains a cycle at {subgoal_id}")
            if subgoal_id in visited:
                return
            visiting.add(subgoal_id)
            for dependency_id in dependencies_by_id[subgoal_id]:
                visit(dependency_id)
            visiting.remove(subgoal_id)
            visited.add(subgoal_id)

        for subgoal_id in subgoal_ids:
            visit(subgoal_id)

        overlap = set(self.allowed_effects) & set(self.forbidden_effects)
        if overlap:
            raise ValueError(f"effects cannot be both allowed and forbidden: {sorted(overlap)}")
        return self


class Blocker(ContractModel):
    blocker_id: Identifier
    reason: HumanText
    source_failure_id: Identifier | None = None
    resolvable: bool = True


class SubgoalState(ContractModel):
    subgoal_id: Identifier
    status: SubgoalStatus = SubgoalStatus.PENDING
    evidence_ids: tuple[Identifier, ...] = ()
    effect_ids: tuple[Identifier, ...] = ()
    blocker_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        require_unique(self.evidence_ids, "evidence_ids")
        require_unique(self.effect_ids, "effect_ids")
        require_unique(self.blocker_ids, "blocker_ids")
        if self.status is SubgoalStatus.COMPLETED_VERIFIED and not (self.evidence_ids or self.effect_ids):
            raise ValueError("a verified subgoal must reference evidence or effects")
        return self


class TaskState(VersionedContract):
    task_id: Identifier
    contract_version: PositiveInt
    state_version: PositiveInt
    phase: TaskPhase = TaskPhase.COLLECT
    subgoals: tuple[SubgoalState, ...] = Field(min_length=1)
    active_subgoal_ids: tuple[Identifier, ...] = ()
    evidence_ids: tuple[Identifier, ...] = ()
    effect_ids: tuple[Identifier, ...] = ()
    verification_ids: tuple[Identifier, ...] = ()
    failure_ids: tuple[Identifier, ...] = ()
    blockers: tuple[Blocker, ...] = ()
    pending_confirmation_ids: tuple[Identifier, ...] = ()
    pending_approval_ids: tuple[Identifier, ...] = ()
    updated_at: AwareDatetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        subgoal_ids = tuple(item.subgoal_id for item in self.subgoals)
        blocker_ids = tuple(item.blocker_id for item in self.blockers)
        require_unique(subgoal_ids, "subgoal state ids")
        require_unique(blocker_ids, "blocker ids")
        for field_name in (
            "active_subgoal_ids",
            "evidence_ids",
            "effect_ids",
            "verification_ids",
            "failure_ids",
            "pending_confirmation_ids",
            "pending_approval_ids",
        ):
            require_unique(getattr(self, field_name), field_name)

        states_by_id = {item.subgoal_id: item for item in self.subgoals}
        unknown_active = set(self.active_subgoal_ids) - set(states_by_id)
        if unknown_active:
            raise ValueError(f"active subgoals are not present in state: {sorted(unknown_active)}")
        terminal_statuses = {
            SubgoalStatus.COMPLETED_VERIFIED,
            SubgoalStatus.FAILED,
            SubgoalStatus.CANCELLED,
        }
        invalid_active = [
            subgoal_id for subgoal_id in self.active_subgoal_ids if states_by_id[subgoal_id].status in terminal_statuses
        ]
        if invalid_active:
            raise ValueError(f"terminal subgoals cannot remain active: {sorted(invalid_active)}")
        if self.phase is TaskPhase.COMPLETE and self.active_subgoal_ids:
            raise ValueError("a complete task cannot have active subgoals")
        return self
