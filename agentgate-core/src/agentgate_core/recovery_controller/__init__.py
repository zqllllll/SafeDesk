"""Typed failure classification, stagnation detection, and recovery control."""

from agentgate_core.recovery_controller.budget import RecoveryBudgetManager
from agentgate_core.recovery_controller.classifier import FailureClassifier
from agentgate_core.recovery_controller.controller import RecoveryController
from agentgate_core.recovery_controller.progress import ProgressSnapshot, ProgressTracker, fingerprint_action
from agentgate_core.recovery_controller.stagnation import StagnationDetector
from agentgate_core.recovery_controller.strategies import (
    ParameterRepairStrategy,
    RecoveryStrategy,
    RecoveryStrategyRegistry,
    ResourceRelocationStrategy,
    StaticRecoveryStrategy,
    VerificationRepairStrategy,
)

__all__ = [
    "FailureClassifier",
    "ParameterRepairStrategy",
    "ProgressSnapshot",
    "ProgressTracker",
    "RecoveryBudgetManager",
    "RecoveryController",
    "RecoveryStrategy",
    "RecoveryStrategyRegistry",
    "ResourceRelocationStrategy",
    "StagnationDetector",
    "StaticRecoveryStrategy",
    "VerificationRepairStrategy",
    "fingerprint_action",
]
