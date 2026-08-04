"""Deterministic token estimation and priority-based context budgeting."""

from __future__ import annotations

import json
import re

from agentgate_core.contracts.context import ContextBudgetStatus, ContextPriority, ContextSection
from agentgate_core.contracts.context_management import TokenBudgetReport


class HeuristicTokenEstimator:
    """Stable tokenizer-independent estimate used for preflight budgeting."""

    def estimate(self, value: object) -> int:
        text = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        )
        cjk = len(re.findall(r"[\u3400-\u9fff]", text))
        non_cjk = len(text) - cjk
        return max(1, cjk + (non_cjk + 3) // 4)


class ContextBudgetAllocator:
    def __init__(self, estimator: HeuristicTokenEstimator) -> None:
        self.estimator = estimator

    def allocate(
        self,
        sections: tuple[ContextSection, ...],
        *,
        soft_limit: int,
        hard_limit: int,
        reserved_output_tokens: int = 0,
    ) -> tuple[tuple[ContextSection, ...], TokenBudgetReport]:
        if soft_limit > hard_limit:
            raise ValueError("soft_limit cannot exceed hard_limit")
        available = max(1, hard_limit - reserved_output_tokens)
        kept = list(sections)
        dropped: list[str] = []
        for priority in (ContextPriority.P4, ContextPriority.P3, ContextPriority.P2):
            for section in tuple(reversed(kept)):
                if sum(item.estimated_tokens for item in kept) <= available:
                    break
                if section.priority is priority and section.compressible:
                    kept.remove(section)
                    dropped.append(section.section_id)
        estimated = sum(item.estimated_tokens for item in kept)
        total_reserved = estimated + reserved_output_tokens
        if total_reserved <= soft_limit:
            status = ContextBudgetStatus.WITHIN_BUDGET
        elif total_reserved <= hard_limit:
            status = ContextBudgetStatus.SOFT_EXCEEDED
        else:
            status = ContextBudgetStatus.HARD_EXCEEDED
        return tuple(kept), TokenBudgetReport(
            soft_limit=soft_limit,
            hard_limit=hard_limit,
            estimated_input_tokens=estimated,
            reserved_output_tokens=reserved_output_tokens,
            section_tokens={item.section_id: item.estimated_tokens for item in kept},
            dropped_section_ids=tuple(dropped),
            projected_section_ids=tuple(item.section_id for item in kept if item.raw_reference is not None),
            status=status,
        )


__all__ = ["ContextBudgetAllocator", "HeuristicTokenEstimator"]
