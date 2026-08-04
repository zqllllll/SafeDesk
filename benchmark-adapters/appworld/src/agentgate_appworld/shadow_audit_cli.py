"""CLI for conservative State & Verification audits of converted AppWorld bundles."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from agentgate_appworld.state_verification_audit import AppWorldStateVerificationAuditor
from agentgate_appworld.trace_converter import ConversionBundle


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit converted AppWorld traces with conservative completion rules.")
    parser.add_argument("--input", type=Path, required=True, help="Converted bundle file or directory.")
    parser.add_argument("--output", type=Path, required=True, help="Audit output file or directory.")
    return parser


def run(args: argparse.Namespace) -> int:
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    files = [input_path] if input_path.is_file() else sorted(input_path.rglob("*.agentgate.json"))
    auditor = AppWorldStateVerificationAuditor()
    counts: Counter[str] = Counter()
    failures: list[dict[str, str]] = []
    for source in files:
        try:
            bundle = ConversionBundle.model_validate_json(source.read_text(encoding="utf-8"))
            audit = auditor.audit(bundle)
            target = (
                output_path
                if input_path.is_file() and output_path.suffix.lower() == ".json"
                else output_path / source.relative_to(input_path).with_suffix(".shadow.json")
            )
            _write_json(target, audit.model_dump(mode="json"))
            counts.update(
                {
                    "tasks": 1,
                    "completion_attempts": audit.completion_attempt_count,
                    "would_allow": audit.would_allow_count,
                    "would_block": audit.would_block_count,
                    "no_completion_attempt": int(audit.no_completion_attempt),
                }
            )
            counts.update({f"blocker:{key}": value for key, value in audit.blocker_counts.items()})
        except Exception as exc:
            failures.append({"path": str(source), "error_type": type(exc).__name__, "message": str(exc)})
    metadata_root = output_path.parent if output_path.suffix.lower() == ".json" else output_path
    summary = {
        "coverage": "conservative_without_task_contract",
        "input": str(input_path),
        "output": str(output_path),
        "files": len(files),
        "counts": dict(sorted(counts.items())),
        "failed": len(failures),
        "failures": failures,
    }
    _write_json(metadata_root / "shadow_audit_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def main() -> None:
    raise SystemExit(run(_parser().parse_args()))


if __name__ == "__main__":
    main()
