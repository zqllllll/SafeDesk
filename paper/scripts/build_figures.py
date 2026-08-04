from __future__ import annotations

import re
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle


PAPER = Path(__file__).resolve().parents[1]
MACROS = PAPER / "results_macros.tex"
OUT = PAPER / "figures"


def read_macros() -> dict[str, str]:
    values: dict[str, str] = {}
    pattern = re.compile(r"^\\newcommand\{\\([A-Za-z][A-Za-z0-9]*)\}\{(.*)\}\s*$")
    for line in MACROS.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line.strip())
        if match:
            values[match.group(1)] = match.group(2)
    return values


def number(values: dict[str, str], name: str) -> float:
    raw = values[name].replace("{,}", "").replace(",", "")
    raw = raw.replace("\\%", "").replace("+", "")
    match = re.search(r"-?\d+(?:\.\d+)?", raw)
    if not match:
        raise ValueError(f"Macro {name} is not numeric: {values[name]}")
    return float(match.group(0))


def save(fig: plt.Figure, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        OUT / name,
        format="pdf",
        bbox_inches="tight",
        pad_inches=0.04,
        metadata={"Creator": "SafeDesk paper/scripts/build_figures.py"},
    )
    plt.close(fig)


def architecture() -> None:
    # The canvas matches the final page width; no label is scaled below body size.
    fig, ax = plt.subplots(figsize=(7.2, 3.85))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 8)
    ax.axis("off")

    styles = {
        "agent": ("#f4f4f4", "#333333"),
        "guard": ("#fde2dc", "#b84a35"),
        "state": ("#dbeafe", "#276fbf"),
        "recovery": ("#fff0c7", "#a66a00"),
        "context": ("#d9f0ec", "#16766b"),
        "trace": ("#eeeeee", "#555555"),
    }

    def box(x: float, y: float, w: float, h: float, text: str, style: str, *, bold: bool = False) -> None:
        face, edge = styles[style]
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.05,rounding_size=0.08",
                                    facecolor=face, edgecolor=edge, linewidth=1.45))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=8.5,
                weight=("bold" if bold else "normal"))

    def arrow(start, end, *, dashed: bool = False, color: str = "#333333") -> None:
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=11,
                                    linewidth=1.35, color=color,
                                    linestyle=(0, (4, 3)) if dashed else "solid",
                                    shrinkA=3, shrinkB=3))

    # Normal execution path: the recovery controller is deliberately not on this path.
    box(0.35, 5.90, 2.85, 0.84, "M4  Context Manager\nbuild context", "context", bold=True)
    box(3.55, 5.90, 1.35, 0.84, "LLM\nproposal", "agent", bold=True)
    box(5.25, 5.90, 2.70, 0.84, "M2  Tool Guard\nvalidate / schedule", "guard", bold=True)
    box(8.30, 5.90, 2.20, 0.84, "Tool\nEnvironment", "agent", bold=True)
    arrow((3.20, 6.32), (3.55, 6.32))
    arrow((4.90, 6.32), (5.25, 6.32))
    arrow((7.95, 6.32), (8.30, 6.32))

    # M1 contains state reduction, verification, completion, and grounded response.
    face, edge = styles["state"]
    ax.add_patch(Rectangle((10.85, 3.15), 2.75, 3.58, facecolor=face, edgecolor=edge, linewidth=1.55))
    ax.text(12.23, 6.38, "M1  State &\nVerification", ha="center", va="center", fontsize=9.3,
            weight="bold", color="#174f86")
    box(11.10, 5.05, 2.25, 0.62, "Reduce + verify", "state")
    box(11.10, 4.18, 2.25, 0.62, "Completion Gate", "state")
    # The terminal response remains inside M1 so the diagram has no ambiguous
    # arrow or label outside the module boundary.
    box(11.10, 3.34, 2.25, 0.62, "Ground response\nfinal response", "state")
    arrow((10.50, 6.32), (11.10, 5.36))
    arrow((12.23, 5.05), (12.23, 4.80))
    arrow((12.23, 4.18), (12.23, 3.96))

    # Authoritative state feeds the next context; only failure/block enters recovery.
    box(0.35, 3.55, 2.85, 0.78, "Authoritative runtime state\n$H_t,E_t,L_t,F_t$", "trace", bold=True)
    arrow((11.10, 5.05), (3.20, 3.94))
    arrow((1.78, 4.33), (1.78, 5.90))
    box(4.35, 2.25, 3.30, 0.84, "M3  Recovery Controller\nclassify / recover / safe stop", "recovery", bold=True)
    arrow((11.10, 4.18), (7.65, 2.67))
    arrow((4.35, 2.67), (3.20, 3.55))

    # A single, uncluttered trace channel uses only dashed downward events.
    box(5.05, 0.45, 3.20, 0.64, "Trace Recorder", "trace", bold=True)
    for x0, y0 in ((3.55, 5.90), (5.25, 5.90), (8.30, 5.90), (10.85, 3.15), (7.65, 2.25)):
        arrow((x0, y0), (x0, 1.09), dashed=True, color="#666666")

    ax.plot([0.55, 1.05], [0.85, 0.85], color="#333333", linewidth=1.35)
    ax.text(1.15, 0.85, "control flow", va="center", fontsize=8.7)
    ax.plot([2.75, 3.25], [0.85, 0.85], color="#666666", linewidth=1.35, linestyle=(0, (4, 3)))
    ax.text(3.35, 0.85, "trace event", va="center", fontsize=8.7)
    save(fig, "system_architecture.pdf")


def appworld_difficulty(values: dict[str, str]) -> None:
    labels = ["Difficulty 1", "Difficulty 2", "Difficulty 3"]
    baseline = [number(values, f"AWBaseDiff{x}TGCValue") for x in ("One", "Two", "Three")]
    low = [number(values, f"AWBaseDiff{x}CILow") for x in ("One", "Two", "Three")]
    high = [number(values, f"AWBaseDiff{x}CIHigh") for x in ("One", "Two", "Three")]
    ns = [int(number(values, f"AWDiff{x}Count")) for x in ("One", "Two", "Three")]

    fig, ax = plt.subplots(figsize=(7.2, 3.5))
    x = range(len(labels))
    errors = [[b - l for b, l in zip(baseline, low)], [h - b for h, b in zip(high, baseline)]]
    bars = ax.bar(list(x), baseline, width=0.52, color="#4f81bd", edgecolor="black", hatch="///", label="Measured baseline")
    ax.errorbar(list(x), baseline, yerr=errors, fmt="none", ecolor="black", capsize=3, linewidth=1)
    for bar, value, n in zip(bars, baseline, ns):
        ax.text(bar.get_x() + bar.get_width() / 2, value + 2.2, f"{value:.2f}%\nn={n}", ha="center", fontsize=8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 92)
    ax.set_ylabel("Task Goal Completion (%)")
    ax.grid(axis="y", color="#dddddd", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, loc="upper right")
    save(fig, "appworld_results.pdf")


def reliability(values: dict[str, str]) -> None:
    labels = ["Invalid", "Out-of-\nschema", "Duplicate\ncall", "Duplicate\nwrite", "Max-turn"]
    baseline = [
        number(values, "AWBaseInvalidValue"),
        number(values, "AWBaseOutOfSchemaValue"),
        number(values, "AWBaseDuplicateCallValue"),
        number(values, "AWBaseDuplicateWriteValue"),
        number(values, "AWBaseMaxTurnValue"),
    ]
    fig, ax = plt.subplots(figsize=(7.2, 3.5))
    x = range(len(labels))
    bars = ax.bar(list(x), baseline, width=0.52, color="#777777", edgecolor="black", hatch="///", label="Measured baseline")
    for bar, value in zip(bars, baseline):
        ax.text(bar.get_x() + bar.get_width() / 2, value + 0.65, f"{value:.2f}", ha="center", fontsize=8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 26)
    ax.set_ylabel("Rate (%)")
    ax.grid(axis="y", color="#dddddd", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, loc="upper left")
    save(fig, "reliability_metrics.pdf")


def token_distribution(values: dict[str, str]) -> None:
    groups = ["Successful tasks", "Failed tasks"]
    medians = [number(values, "AWBaseSuccessMedianTokens"), number(values, "AWBaseFailureMedianTokens")]
    p95s = [number(values, "AWBaseSuccessPNinetyFiveTokens"), number(values, "AWBaseFailurePNinetyFiveTokens")]
    maxima = [number(values, "AWBaseSuccessMaxTokens"), number(values, "AWBaseFailureMaxTokens")]

    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    x = [0, 1]
    ax.scatter(x, medians, marker="o", s=48, color="#222222", label="Median", zorder=3)
    ax.scatter(x, p95s, marker="s", s=48, facecolors="white", edgecolors="#222222", label="P95", zorder=3)
    ax.scatter(x, maxima, marker="^", s=55, color="#777777", label="Maximum", zorder=3)
    for i, (m, p, mx) in enumerate(zip(medians, p95s, maxima)):
        ax.vlines(i, m, mx, color="#999999", linewidth=1)
        ax.text(i + 0.05, m, f"{int(m):,}", fontsize=7, va="center")
        ax.text(i + 0.05, p, f"{int(p):,}", fontsize=7, va="center")
        ax.text(i + 0.05, mx, f"{int(mx):,}", fontsize=7, va="center")
    ax.set_xlim(-0.35, 1.55)
    ax.set_yscale("log")
    ax.set_ylim(5e4, 1.1e7)
    ax.set_xticks(x)
    ax.set_xticklabels(groups)
    ax.set_ylabel("Tokens per task (log scale)")
    ax.grid(axis="y", which="both", color="#dddddd", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, ncol=3, loc="upper left")
    save(fig, "token_distribution.pdf")


def main() -> None:
    values = read_macros()
    architecture()
    appworld_difficulty(values)
    reliability(values)
    token_distribution(values)


if __name__ == "__main__":
    main()
