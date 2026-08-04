"""Structured context construction, budgeting, compression, and retrieval."""

from agentgate_core.context_manager.builder import ContextBuilder, ContextBuildError, ContextFreshnessStage
from agentgate_core.context_manager.invariants import ContextInvariantValidator
from agentgate_core.context_manager.projector import (
    GenericToolResultProjector,
    ToolResultProjector,
    ToolResultProjectorRegistry,
)
from agentgate_core.context_manager.retrieval import RawTraceRetriever
from agentgate_core.context_manager.summary import StructuredHistorySummarizer
from agentgate_core.context_manager.token_budget import ContextBudgetAllocator, HeuristicTokenEstimator

__all__ = [
    "ContextBudgetAllocator",
    "ContextBuildError",
    "ContextBuilder",
    "ContextFreshnessStage",
    "ContextInvariantValidator",
    "GenericToolResultProjector",
    "HeuristicTokenEstimator",
    "RawTraceRetriever",
    "StructuredHistorySummarizer",
    "ToolResultProjector",
    "ToolResultProjectorRegistry",
]
