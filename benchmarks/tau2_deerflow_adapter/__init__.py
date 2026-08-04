"""DeerFlow agent and policy adapters for tau2."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .policy_adapter import Tau2PolicyAdapter

if TYPE_CHECKING:
    from .agent import DeerFlowTauAgent


def __getattr__(name: str) -> Any:
    """Load tau2-dependent agent classes only when the benchmark runtime asks for them."""

    if name in {"DeerFlowTauAgent", "create_deerflow_tau_agent"}:
        from .agent import DeerFlowTauAgent, create_deerflow_tau_agent

        return {
            "DeerFlowTauAgent": DeerFlowTauAgent,
            "create_deerflow_tau_agent": create_deerflow_tau_agent,
        }[name]
    raise AttributeError(name)


__all__ = ["DeerFlowTauAgent", "Tau2PolicyAdapter", "create_deerflow_tau_agent"]
