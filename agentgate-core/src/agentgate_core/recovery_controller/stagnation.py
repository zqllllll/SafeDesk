"""Configurable detection of repeated calls, repeated failures, and no progress."""

from __future__ import annotations

from collections import Counter, defaultdict, deque

from agentgate_core.contracts.decision import DecisionOutcome
from agentgate_core.contracts.failure import FailureType
from agentgate_core.contracts.recovery import (
    ProgressSignal,
    StagnationAssessment,
    StagnationConfig,
    ToolCallFingerprint,
)


class StagnationDetector:
    def __init__(self, config: StagnationConfig) -> None:
        self.config = config
        self._progress: dict[str, deque[ProgressSignal]] = defaultdict(lambda: deque(maxlen=config.stagnation_window))
        self._fingerprints: dict[str, Counter[str]] = defaultdict(Counter)
        self._failures: dict[str, Counter[FailureType]] = defaultdict(Counter)

    def observe(
        self,
        task_id: str,
        progress: ProgressSignal,
        *,
        fingerprint: ToolCallFingerprint | None = None,
        failure_type: FailureType | None = None,
    ) -> StagnationAssessment:
        if progress.task_id != task_id:
            raise ValueError("progress signal does not belong to the task")
        self._progress[task_id].append(progress)
        if fingerprint is not None:
            self._fingerprints[task_id][fingerprint.fingerprint] += 1
        if failure_type is not None:
            self._failures[task_id][failure_type] += 1
        recent = self._progress[task_id]
        no_progress = 0
        for signal in reversed(recent):
            if signal.score != 0:
                break
            no_progress += 1
        identical = max(self._fingerprints[task_id].values(), default=0)
        same_failure = max(self._failures[task_id].values(), default=0)
        reasons: list[str] = []
        if len(recent) >= self.config.stagnation_window and no_progress >= self.config.stagnation_window:
            reasons.append("stagnation_window_no_progress")
        if identical >= self.config.max_identical_calls:
            reasons.append("identical_call_limit")
        if same_failure >= self.config.max_same_failure:
            reasons.append("same_failure_limit")
        if recent and all(signal.score == 0 and signal.token_growth > 0 for signal in recent):
            if len(recent) >= self.config.stagnation_window:
                reasons.append("token_growth_without_progress")
        return StagnationAssessment(
            task_id=task_id,
            stagnant=bool(reasons),
            reason_codes=tuple(reasons),
            consecutive_no_progress_turns=no_progress,
            identical_call_count=identical,
            same_failure_count=same_failure,
            recommended_outcome=DecisionOutcome.REPLAN if reasons else DecisionOutcome.ALLOW,
        )

    def reset_after_progress(self, task_id: str) -> None:
        self._fingerprints.pop(task_id, None)
        self._failures.pop(task_id, None)


__all__ = ["StagnationDetector"]
