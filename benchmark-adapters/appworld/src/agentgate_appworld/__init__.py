"""AppWorld adapters for SafeDesk AgentGate."""

from importlib.metadata import PackageNotFoundError, version

from agentgate_appworld.catalog_adapter import AppWorldToolCatalog
from agentgate_appworld.result_projector import AppWorldResultProjector
from agentgate_appworld.state_verification_audit import AppWorldShadowAudit, AppWorldStateVerificationAuditor
from agentgate_appworld.trace_converter import (
    AppWorldTraceConverter,
    ConversionBundle,
    ConversionDiagnostic,
    ConversionSummary,
    DiagnosticSeverity,
)
from agentgate_appworld.verifier_adapter import AppWorldEnvironmentVerifier, AppWorldReadbackProfile

try:
    __version__ = version("agentgate-appworld")
except PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = [
    "AppWorldToolCatalog",
    "AppWorldTraceConverter",
    "AppWorldEnvironmentVerifier",
    "AppWorldReadbackProfile",
    "AppWorldResultProjector",
    "AppWorldShadowAudit",
    "AppWorldStateVerificationAuditor",
    "ConversionBundle",
    "ConversionDiagnostic",
    "ConversionSummary",
    "DiagnosticSeverity",
    "__version__",
]
