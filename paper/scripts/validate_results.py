"""Validate manuscript result macros and publication-integrity invariants."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path


PAPER = Path(__file__).resolve().parents[1]
MACROS_PATH = PAPER / "results_macros.tex"
MACRO_RE = re.compile(r"\\newcommand\{\\([^}]+)\}\{([^\n]*)\}")


def load_macros() -> dict[str, str]:
    return dict(MACRO_RE.findall(MACROS_PATH.read_text(encoding="utf-8")))


def number(macros: dict[str, str], name: str) -> float:
    raw = macros[name]
    raw = raw.replace("{,}", "").replace("\\%", "").replace("%", "")
    raw = raw.replace("pp", "").replace("+", "").strip()
    return float(raw)


def assert_rate(macros: dict[str, str], numerator: str, denominator: str,
                value: str, tolerance: float = 0.011) -> None:
    expected = 100.0 * number(macros, numerator) / number(macros, denominator)
    actual = number(macros, value)
    if not math.isclose(expected, actual, abs_tol=tolerance):
        raise AssertionError(
            f"{value}: expected {expected:.4f} from {numerator}/{denominator}, got {actual}"
        )


def main() -> None:
    macros = load_macros()

    rate_checks = [
        ("AWBaseSuccessCount", "AWTaskCount", "AWBaseTGCValue"),
        ("AWBaseScenarioSuccessCount", "AWScenarioCount", "AWBaseSGCValue"),
        ("AWBaseEvaluatorPassed", "AWBaseEvaluatorTotal", "AWBaseEvaluatorPassValue"),
        ("AWBaseProposedFalseCompletionCount", "AWBaseCompletionTaskCount", "AWBaseProposedFalseCompletionValue"),
        ("AWBaseSystemAcceptedFailureCount", "AWBaseSystemAcceptedCompletionCount", "AWBaseSystemAcceptedFailureValue"),
        ("AWBaseNoCompletionCount", "AWTaskCount", "AWBaseNoCompletionValue"),
        ("AWBaseMaxTurnCount", "AWTaskCount", "AWBaseMaxTurnValue"),
        ("AWBaseInvalidCount", "AWBaseProposedCallCount", "AWBaseInvalidValue"),
        ("AWBaseOutOfSchemaCount", "AWBaseProposedCallCount", "AWBaseOutOfSchemaValue"),
        ("AWBaseDuplicateCallCount", "AWBaseExecutedCallCount", "AWBaseDuplicateCallValue"),
        ("AWBaseDuplicateWriteCount", "AWBaseWriteCallCount", "AWBaseDuplicateWriteValue"),
        ("AWBaseOutOfSchemaAffectedTaskCount", "AWTaskCount", "AWBaseOutOfSchemaAffectedTaskValue"),
        ("AWBaseDuplicateAffectedTaskCount", "AWTaskCount", "AWBaseDuplicateAffectedTaskValue"),
        ("AWBaseDuplicateWriteAffectedTaskCount", "AWTaskCount", "AWBaseDuplicateWriteAffectedTaskValue"),
        ("AWDiffOneSuccess", "AWDiffOneCount", "AWBaseDiffOneTGCValue"),
        ("AWDiffTwoSuccess", "AWDiffTwoCount", "AWBaseDiffTwoTGCValue"),
        ("AWDiffThreeSuccess", "AWDiffThreeCount", "AWBaseDiffThreeTGCValue"),
    ]
    for check in rate_checks:
        assert_rate(macros, *check)

    if number(macros, "AWBaseOutOfSchemaCount") > number(macros, "AWBaseInvalidCount"):
        raise AssertionError("Out-of-schema calls must be a subset of invalid calls")
    if number(macros, "AWBaseReadCallCount") + number(macros, "AWBaseWriteCallCount") != number(macros, "AWBaseExecutedCallCount"):
        raise AssertionError("Executed calls must equal read plus write calls")
    if number(macros, "AWBasePredictorTokens") + number(macros, "AWBaseAgentTokens") != number(macros, "AWBaseTotalTokens"):
        raise AssertionError("Predictor and agent tokens must sum to total tokens")
    if round(number(macros, "AWBaseTotalTokens") / number(macros, "AWTaskCount")) != number(macros, "AWBaseMeanTokens"):
        raise AssertionError("Mean token macro is inconsistent with total tokens")
    if number(macros, "AWBaseSuccessCount") + number(macros, "AWBaseFailureCount") != number(macros, "AWTaskCount"):
        raise AssertionError("Successful and failed task counts must cover the task set")

    clustered = json.loads((PAPER / "data/appworld_clustered_intervals.json").read_text(encoding="utf-8"))
    if clustered["method"] != "scenario-clustered percentile bootstrap":
        raise AssertionError("Unexpected clustered interval method")
    if clustered["samples"] != int(number(macros, "AWBootstrapSamples")):
        raise AssertionError("Bootstrap sample count does not match manuscript macro")
    ci_pairs = [
        (clustered["overall"], "AWBaseTGCClusterCILow", "AWBaseTGCClusterCIHigh"),
        (clustered["difficulty"]["1"], "AWBaseDiffOneCILow", "AWBaseDiffOneCIHigh"),
        (clustered["difficulty"]["2"], "AWBaseDiffTwoCILow", "AWBaseDiffTwoCIHigh"),
        (clustered["difficulty"]["3"], "AWBaseDiffThreeCILow", "AWBaseDiffThreeCIHigh"),
    ]
    for observed, low_name, high_name in ci_pairs:
        if not math.isclose(observed["ci95_low"], number(macros, low_name), abs_tol=0.001):
            raise AssertionError(f"{low_name} does not match clustered export")
        if not math.isclose(observed["ci95_high"], number(macros, high_name), abs_tol=0.001):
            raise AssertionError(f"{high_name} does not match clustered export")

    required_files = [
        "main.tex", "results_macros.tex", "references.bib",
        "figures/system_architecture.pdf", "figures/appworld_results.pdf",
        "figures/reliability_metrics.pdf", "figures/token_distribution.pdf",
        "tables/appworld_main.tex", "tables/tau2_main.tex", "tables/ablations.tex",
        "tables/baseline_diagnostics.tex", "tables/tau2_diagnostics.tex",
        "tables/related_work_comparison.tex", "tables/trace_example.tex",
        "appendix/metric_definitions.tex", "appendix/reproducibility.tex",
        "appendix/prespecified_evaluation.tex",
        "data/appworld_clustered_intervals.json",
        "CHANGELOG.md", "TODO_EXPERIMENTS.md",
    ]
    missing = [path for path in required_files if not (PAPER / path).is_file()]
    if missing:
        raise AssertionError(f"Missing required files: {', '.join(missing)}")

    publication_sources = [PAPER / "main.tex", *sorted((PAPER / "tables").glob("*.tex")),
                           *sorted((PAPER / "appendix").glob("*.tex"))]
    publication_text = "\n".join(path.read_text(encoding="utf-8") for path in publication_sources)
    forbidden = [
        "ProjectedAW", "ProjectedTau", "Authors withheld", "Affiliation to be inserted",
        "61.39", "46.76", "19.66", "17.27", "17.42", "280{,}198", "+6.05", "+12.14",
    ]
    found = [item for item in forbidden if item in publication_text]
    if found:
        raise AssertionError(f"Projection or placeholder leaked into publication text: {found}")
    if "\\SafeResultsAvailablefalse" not in MACROS_PATH.read_text(encoding="utf-8"):
        raise AssertionError("SafeDesk result switch must remain false until matched results exist")

    main_text = (PAPER / "main.tex").read_text(encoding="utf-8")
    main_forbidden = [
        "\\ResultPending", "\\input{tables/appworld_main}",
        "\\input{tables/tau2_main}", "\\input{tables/ablations}",
        "AWBaseCostLow", "AWBaseCostHigh", "SafeDesk (pending)",
    ]
    leaked = [item for item in main_forbidden if item in main_text]
    if leaked:
        raise AssertionError(f"Unmeasured result marker leaked into main manuscript: {leaked}")

    figure_source = (PAPER / "scripts/build_figures.py").read_text(encoding="utf-8")
    if "Pending" in figure_source or "SafeDesk distribution" in figure_source:
        raise AssertionError("Unmeasured SafeDesk markers must not appear in diagnostic figures")
    if "retain their tool_call_id" in publication_text:
        raise AssertionError("Deferred calls must close the old ID and require a new proposal ID")
    if "AllowComplete}(H_t,E_t,L_t,P,\\widehat W_t)" not in main_text:
        raise AssertionError("Completion Gate must include the observable world projection")

    print(f"Validated {len(rate_checks)} rates, clustered intervals, and publication-integrity checks.")


if __name__ == "__main__":
    main()
