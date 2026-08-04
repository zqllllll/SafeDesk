"""Validate the SafeDesk paper package and its measured-data claims."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "paper"
DATA = PAPER / "data"


def load_csv(name: str) -> list[dict[str, str]]:
    with (DATA / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def digest(path: Path) -> str:
    value = hashlib.sha256()
    value.update(path.read_bytes())
    return value.hexdigest()


def close(actual: float, expected: float, tolerance: float = 1e-6) -> bool:
    return abs(actual - expected) <= tolerance


def check(condition: bool, name: str, details: Any, checks: list[dict[str, Any]]) -> None:
    checks.append({"name": name, "passed": bool(condition), "details": details})


def main() -> None:
    checks: list[dict[str, Any]] = []

    primary = load_csv("appworld_primary_metrics.csv")[0]
    check(int(primary["tasks"]) == 417, "appworld_task_count", primary["tasks"], checks)
    check(int(primary["task_successes"]) == 174, "appworld_success_count", primary["task_successes"], checks)
    check(close(float(primary["tgc"]), 174 / 417), "appworld_tgc", primary["tgc"], checks)
    check(
        int(primary["scenario_successes"]) == 41,
        "appworld_scenario_success_count",
        primary["scenario_successes"],
        checks,
    )
    check(close(float(primary["sgc"]), 41 / 139), "appworld_sgc", primary["sgc"], checks)
    check(
        int(primary["model_proposed_tool_calls"]) == 15066,
        "appworld_proposed_calls",
        primary["model_proposed_tool_calls"],
        checks,
    )
    check(int(primary["total_tokens"]) == 201_973_864, "appworld_total_tokens", primary["total_tokens"], checks)

    task_rows = load_csv("appworld_task_level_derived.csv")
    check(len(task_rows) == 417, "appworld_task_level_rows", len(task_rows), checks)
    check(
        sum(row["success"] == "True" for row in task_rows) == 174,
        "appworld_task_level_successes",
        "expected 174",
        checks,
    )
    check(
        sum(int(row["total_tokens"]) for row in task_rows) == 201_973_864,
        "appworld_task_token_sum",
        "expected 201973864",
        checks,
    )
    check(
        all(row["state_persisted_before_evaluation"] == "True" for row in task_rows),
        "appworld_state_persistence",
        "all 417 rows",
        checks,
    )

    difficulty = load_csv("appworld_difficulty.csv")
    difficulty_counts = {int(row["difficulty"]): int(row["tasks"]) for row in difficulty}
    difficulty_passed = {int(row["difficulty"]): int(row["passed"]) for row in difficulty}
    check(difficulty_counts == {1: 72, 2: 150, 3: 195}, "difficulty_counts", difficulty_counts, checks)
    check(difficulty_passed == {1: 51, 2: 60, 3: 63}, "difficulty_successes", difficulty_passed, checks)

    tau = load_csv("tau2_baseline_metrics.csv")
    tau_overall = next(row for row in tau if row["domain"] == "overall")
    check(int(tau_overall["tasks"]) == 278, "tau2_task_count", tau_overall["tasks"], checks)
    check(int(tau_overall["passed"]) == 236, "tau2_success_count", tau_overall["passed"], checks)
    check(int(tau_overall["total_tokens"]) == 41_225_715, "tau2_token_sum", tau_overall["total_tokens"], checks)
    check(len(load_csv("tau2_task_level_derived.csv")) == 278, "tau2_task_level_rows", "expected 278", checks)

    manifest = json.loads((DATA / "data_manifest.json").read_text(encoding="utf-8"))
    source_results = []
    for item in manifest["sources"]:
        path = ROOT / item["path"]
        source_results.append(path.exists() and digest(path) == item["sha256"])
    generated_results = []
    for item in manifest["generated_files"]:
        path = ROOT / item["path"]
        generated_results.append(path.exists() and digest(path) == item["sha256"])
    check(all(source_results), "manifest_source_hashes", f"{sum(source_results)}/{len(source_results)}", checks)
    check(
        all(generated_results),
        "manifest_generated_hashes",
        f"{sum(generated_results)}/{len(generated_results)}",
        checks,
    )

    report = (PAPER / "SafeDesk_arXiv_论文初稿.md").read_text(encoding="utf-8")
    bib = (PAPER / "references.bib").read_text(encoding="utf-8")
    citation_keys = set(re.findall(r"\[@([A-Za-z0-9_:-]+)\]", report))
    bib_keys = set(re.findall(r"^@\w+\{([^,]+),", bib, re.MULTILINE))
    missing_citations = sorted(citation_keys - bib_keys)
    check(not missing_citations, "citation_keys", missing_citations or "all resolved", checks)

    paper_files = [path for path in PAPER.rglob("*") if path.is_file()]
    secret_hits = []
    secret_pattern = re.compile(r"(?:sk-[A-Za-z0-9_.-]{16,}|api[_-]?key\s*[:=]\s*['\"][^'\"]+)", re.I)
    for path in paper_files:
        if path.suffix.lower() not in {".md", ".py", ".csv", ".json", ".bib"}:
            continue
        text = path.read_text(encoding="utf-8-sig")
        if secret_pattern.search(text):
            secret_hits.append(path.relative_to(ROOT).as_posix())
    check(not secret_hits, "secret_scan", secret_hits or "no key-like strings", checks)

    claims = load_csv("claims_registry.csv")
    prohibited_pending = [
        row["claim_id"]
        for row in claims
        if row["publication_rule"] == "prohibited_until_measured" and row["evidence_status"] != "pending"
    ]
    check(not prohibited_pending, "claims_registry_pending_guards", prohibited_pending or "intact", checks)

    report_paths = [
        PAPER / "SafeDesk_arXiv_论文初稿.md",
        PAPER / "实验执行与统计计划.md",
        PAPER / "README.md",
        PAPER / "references.bib",
        DATA / "data_manifest.json",
    ]
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "status": "passed" if all(item["passed"] for item in checks) else "failed",
        "checks": checks,
        "paper_artifacts": [
            {
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": digest(path),
                "bytes": path.stat().st_size,
            }
            for path in report_paths
        ],
    }
    (PAPER / "validation_report.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if output["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
