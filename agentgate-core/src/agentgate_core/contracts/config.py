"""Versioned feature configuration for AgentGate runtime behavior."""

from __future__ import annotations

import hashlib
import json

from agentgate_core.contracts.base import VersionedContract
from agentgate_core.contracts.decision import FeatureMode, FeatureName


class AgentGateFeatureConfig(VersionedContract):
    state_verification: FeatureMode = FeatureMode.OFF
    tool_execution_guard: FeatureMode = FeatureMode.OFF
    recovery_controller: FeatureMode = FeatureMode.OFF
    context_manager: FeatureMode = FeatureMode.OFF
    allow_read_on_trace_failure: bool = False

    def mode_for(self, feature: FeatureName) -> FeatureMode:
        return {
            FeatureName.STATE_VERIFICATION: self.state_verification,
            FeatureName.TOOL_EXECUTION_GUARD: self.tool_execution_guard,
            FeatureName.RECOVERY_CONTROLLER: self.recovery_controller,
            FeatureName.CONTEXT_MANAGER: self.context_manager,
        }[feature]

    @property
    def configuration_hash(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


__all__ = ["AgentGateFeatureConfig"]
