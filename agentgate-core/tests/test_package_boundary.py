from __future__ import annotations

import ast
import importlib
from pathlib import Path

import agentgate_core

CORE_PACKAGES = (
    "contracts",
    "schemas",
    "runtime",
    "state_verification",
    "tool_execution_guard",
    "recovery_controller",
    "context_manager",
    "tracing",
)
FORBIDDEN_IMPORT_ROOTS = {"appworld", "deerflow", "langchain", "tau2"}


def test_core_package_imports() -> None:
    assert agentgate_core.__version__ == "0.1.0"
    for package_name in CORE_PACKAGES:
        imported = importlib.import_module(f"agentgate_core.{package_name}")
        assert imported.__name__ == f"agentgate_core.{package_name}"


def test_core_has_no_framework_or_benchmark_imports() -> None:
    source_root = Path(__file__).parents[1] / "src" / "agentgate_core"
    violations: list[str] = []

    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported_roots: list[str] = []
            if isinstance(node, ast.Import):
                imported_roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots = [node.module.split(".", 1)[0]]

            for imported_root in imported_roots:
                if imported_root in FORBIDDEN_IMPORT_ROOTS:
                    violations.append(f"{path.relative_to(source_root)} imports {imported_root}")

    assert violations == []
