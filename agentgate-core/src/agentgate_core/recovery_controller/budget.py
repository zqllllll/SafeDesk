"""Task-local and failure-type recovery budgets."""

from __future__ import annotations

from threading import RLock

from agentgate_core.contracts.failure import FailureType
from agentgate_core.contracts.recovery import RecoveryBudgetState, StagnationConfig


class RecoveryBudgetManager:
    def __init__(self, config: StagnationConfig) -> None:
        self.config = config
        self._states: dict[str, RecoveryBudgetState] = {}
        self._lock = RLock()

    def get(self, task_id: str) -> RecoveryBudgetState:
        with self._lock:
            state = self._states.get(task_id)
            if state is None:
                state = RecoveryBudgetState(
                    task_id=task_id,
                    max_total_attempts=self.config.max_total_recovery_attempts,
                    max_attempts_per_type=self.config.max_recovery_attempts_per_type,
                    max_tokens=self.config.max_recovery_tokens,
                )
                self._states[task_id] = state
            return state.model_copy(deep=True)

    def consume(
        self, task_id: str, failure_type: FailureType, *, estimated_tokens: int = 0
    ) -> RecoveryBudgetState | None:
        if estimated_tokens < 0:
            raise ValueError("estimated_tokens cannot be negative")
        with self._lock:
            current = self.get(task_id)
            used_for_type = current.attempts_by_type.get(failure_type, 0)
            if current.exhausted or used_for_type >= current.max_attempts_per_type:
                return None
            if current.tokens_used + estimated_tokens > current.max_tokens:
                return None
            attempts = dict(current.attempts_by_type)
            attempts[failure_type] = used_for_type + 1
            updated = current.model_copy(
                update={
                    "total_attempts_used": current.total_attempts_used + 1,
                    "attempts_by_type": attempts,
                    "tokens_used": current.tokens_used + estimated_tokens,
                }
            )
            self._states[task_id] = updated
            return updated.model_copy(deep=True)

    def restore(self, state: RecoveryBudgetState) -> RecoveryBudgetState:
        """Restore a task budget from a checkpoint after validating configured limits."""

        with self._lock:
            if (
                state.max_total_attempts != self.config.max_total_recovery_attempts
                or state.max_attempts_per_type != self.config.max_recovery_attempts_per_type
                or state.max_tokens != self.config.max_recovery_tokens
            ):
                raise ValueError("recovery budget checkpoint does not match the active configuration")
            self._states[state.task_id] = state.model_copy(deep=True)
            return state.model_copy(deep=True)

    def remaining_for_type(self, task_id: str, failure_type: FailureType) -> int:
        state = self.get(task_id)
        return max(0, state.max_attempts_per_type - state.attempts_by_type.get(failure_type, 0))


__all__ = ["RecoveryBudgetManager"]
