"""Conservative State & Verification shadow audit for converted AppWorld traces."""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, ConfigDict, Field

from agentgate_appworld.trace_converter import ConversionBundle
from agentgate_core.contracts import EffectStatus, EvidenceStatus

_COMPLETION_TOOLS = {"supervisor__complete_task", "supervisor.complete_task", "complete_task"}


class ShadowCompletionAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str
    turn: int = Field(ge=0)
    would_allow: bool
    blocker_codes: tuple[str, ...]
    unverified_effect_ids: tuple[str, ...]
    verified_evidence_ids: tuple[str, ...]


class AppWorldShadowAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    run_id: str
    coverage: str
    completion_attempts: tuple[ShadowCompletionAttempt, ...]
    completion_attempt_count: int = Field(ge=0)
    would_allow_count: int = Field(ge=0)
    would_block_count: int = Field(ge=0)
    no_completion_attempt: bool
    blocker_counts: dict[str, int]
    note: str


class AppWorldStateVerificationAuditor:
    """Audit observable records only; absence of TaskContract is an explicit blocker."""

    def audit(self, bundle: ConversionBundle) -> AppWorldShadowAudit:
        completion_actions = [action for action in bundle.actions if action.tool_name in _COMPLETION_TOOLS]
        action_turns = {action.action_id: action.source_turn for action in bundle.actions}
        attempts: list[ShadowCompletionAttempt] = []
        blocker_counts: Counter[str] = Counter()
        for action in completion_actions:
            preceding_effects = [
                effect
                for effect in bundle.effects
                if effect.action_id != action.action_id and action_turns.get(effect.action_id, 0) <= action.source_turn
            ]
            unverified_effect_ids = tuple(
                effect.effect_id for effect in preceding_effects if effect.status is not EffectStatus.VERIFIED
            )
            verified_evidence_ids = tuple(
                item.evidence_id for item in bundle.evidence if item.status is EvidenceStatus.VERIFIED
            )
            blockers = ["task_contract_unavailable"]
            if unverified_effect_ids:
                blockers.append("effect_not_verified")
            if not verified_evidence_ids:
                blockers.append("verified_evidence_unavailable")
            blocker_counts.update(blockers)
            attempts.append(
                ShadowCompletionAttempt(
                    action_id=action.action_id,
                    turn=action.source_turn,
                    would_allow=False,
                    blocker_codes=tuple(blockers),
                    unverified_effect_ids=unverified_effect_ids,
                    verified_evidence_ids=verified_evidence_ids,
                )
            )
        return AppWorldShadowAudit(
            task_id=bundle.task_id,
            run_id=bundle.run_id,
            coverage="conservative_without_task_contract",
            completion_attempts=tuple(attempts),
            completion_attempt_count=len(attempts),
            would_allow_count=0,
            would_block_count=len(attempts),
            no_completion_attempt=not attempts,
            blocker_counts=dict(sorted(blocker_counts.items())),
            note=(
                "This offline audit uses only public runtime actions/effects/evidence. It does not read AppWorld "
                "evaluator tests or hidden ground truth, and cannot prove missing subgoals without a TaskContract."
            ),
        )


__all__ = ["AppWorldShadowAudit", "AppWorldStateVerificationAuditor", "ShadowCompletionAttempt"]
