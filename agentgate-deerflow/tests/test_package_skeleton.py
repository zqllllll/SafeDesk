from __future__ import annotations

import importlib

import agentgate_core
import agentgate_deerflow

ADAPTER_MODULES = (
    "middleware",
    "state_adapter",
    "tool_adapter",
    "journal_adapter",
)


def test_adapter_and_core_packages_import() -> None:
    assert agentgate_core.__version__ == "0.1.0"
    assert agentgate_deerflow.__version__ == "0.1.0"


def test_adapter_modules_import_without_loading_deerflow() -> None:
    for module_name in ADAPTER_MODULES:
        imported = importlib.import_module(f"agentgate_deerflow.{module_name}")
        assert imported.__name__ == f"agentgate_deerflow.{module_name}"
