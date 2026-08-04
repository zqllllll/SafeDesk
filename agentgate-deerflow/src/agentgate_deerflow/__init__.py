"""DeerFlow integration adapters for the SafeDesk AgentGate core."""

from importlib.metadata import PackageNotFoundError, version

from agentgate_deerflow.builtin_profiles import core_sandbox_tool_profiles
from agentgate_deerflow.tool_adapter import (
    DeerFlowActionContext,
    DeerFlowToolCallAdapter,
    DeerFlowToolCallError,
    NormalizedDeerFlowToolCall,
    ToolCallErrorCode,
    build_deerflow_tool_catalog,
    extract_deerflow_input_schema,
)
from agentgate_deerflow.tool_profile import ArgumentProjection, DeerFlowToolProfile, ExpectedChangeBinding

try:
    __version__ = version("agentgate-deerflow")
except PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = [
    "DeerFlowActionContext",
    "DeerFlowToolCallAdapter",
    "DeerFlowToolCallError",
    "DeerFlowToolProfile",
    "ArgumentProjection",
    "ExpectedChangeBinding",
    "NormalizedDeerFlowToolCall",
    "ToolCallErrorCode",
    "__version__",
    "build_deerflow_tool_catalog",
    "core_sandbox_tool_profiles",
    "extract_deerflow_input_schema",
]
