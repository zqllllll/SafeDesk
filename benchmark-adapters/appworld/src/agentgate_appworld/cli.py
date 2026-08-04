"""Command-line entry point for offline AppWorld trace conversion."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from agentgate_appworld.catalog_adapter import AppWorldToolCatalog
from agentgate_appworld.trace_converter import CONVERTER_VERSION, AppWorldTraceConverter


def _atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _source_sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _trace_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    if not input_path.is_dir():
        raise FileNotFoundError(f"Trace input does not exist: {input_path}")
    candidates = sorted(input_path.rglob("*.json"))
    if input_path.name == "traces":
        return candidates
    nested_traces = [path for path in candidates if "traces" in path.relative_to(input_path).parts]
    return nested_traces or candidates


def _output_path(input_root: Path, output_root: Path, trace_path: Path) -> Path:
    if input_root.is_file():
        if output_root.suffix.lower() == ".json":
            return output_root
        return output_root / f"{trace_path.stem}.agentgate.json"
    relative = trace_path.relative_to(input_root)
    return output_root / relative.with_suffix(".agentgate.json")


def _is_current(output_path: Path, source_sha256: str, catalog_version: str) -> bool:
    if not output_path.is_file():
        return False
    try:
        existing = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        existing.get("source_sha256") == source_sha256
        and existing.get("converter_version") == CONVERTER_VERSION
        and existing.get("catalog_version") == catalog_version
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert AppWorld runtime traces into AgentGate Action, Effect, and Evidence records."
    )
    parser.add_argument("--input", type=Path, required=True, help="Trace JSON file or directory to scan.")
    parser.add_argument("--output", type=Path, required=True, help="Output JSON file or directory.")
    parser.add_argument("--api-docs", type=Path, required=True, help="Public AppWorld function_calling API docs.")
    parser.add_argument("--run-id", required=True, help="Experiment/run identifier recorded in every bundle.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Reconvert current outputs. Stale outputs are always refreshed.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    input_root = args.input.resolve()
    output_root = args.output.resolve()
    catalog = AppWorldToolCatalog.from_api_docs(args.api_docs.resolve())
    converter = AppWorldTraceConverter(catalog)
    traces = _trace_files(input_root)
    if not traces:
        raise ValueError(f"No JSON trace files found under {input_root}")

    converted = 0
    skipped = 0
    failures: list[dict[str, str]] = []
    aggregate = Counter()
    for trace_path in traces:
        target = _output_path(input_root, output_root, trace_path)
        source_sha256 = _source_sha256(trace_path)
        if not args.overwrite and _is_current(target, source_sha256, catalog.snapshot.catalog_version):
            skipped += 1
            continue
        try:
            bundle = converter.convert_file(trace_path, run_id=args.run_id)
            payload = bundle.model_dump(mode="json")
            _atomic_json_write(target, payload)
            converted += 1
            aggregate.update(
                {
                    "actions": bundle.summary.actions,
                    "read_actions": bundle.summary.read_actions,
                    "write_actions": bundle.summary.write_actions,
                    "effects": bundle.summary.effects,
                    "evidence_items": bundle.summary.evidence_items,
                    "diagnostics": len(bundle.diagnostics),
                    "unknown_tools": bundle.summary.unknown_tools,
                    "missing_tool_results": bundle.summary.missing_tool_results,
                    "orphan_tool_results": bundle.summary.orphan_tool_results,
                    "redacted_values": bundle.summary.redacted_values,
                }
            )
        except Exception as exc:  # Keep an offline batch resumable after one malformed trace.
            failures.append({"trace_path": str(trace_path), "error_type": type(exc).__name__, "message": str(exc)})

    metadata_root = (
        output_root.parent if input_root.is_file() and output_root.suffix.lower() == ".json" else output_root
    )
    _atomic_json_write(
        metadata_root / "catalog_snapshot.json",
        catalog.snapshot.model_dump(mode="json"),
    )
    summary = {
        "converter_version": CONVERTER_VERSION,
        "run_id": args.run_id,
        "input": str(input_root),
        "output": str(output_root),
        "catalog_version": catalog.snapshot.catalog_version,
        "catalog_tool_count": len(catalog),
        "trace_files": len(traces),
        "converted": converted,
        "skipped_current": skipped,
        "failed": len(failures),
        "aggregate": dict(sorted(aggregate.items())),
        "failures": failures,
    }
    _atomic_json_write(metadata_root / "conversion_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def main() -> None:
    raise SystemExit(run(_parser().parse_args()))


if __name__ == "__main__":
    main()
