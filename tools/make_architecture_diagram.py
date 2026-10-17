"""Generate figure 1: capstone architecture diagram (PNG + SVG).

The diagram reflects the code as built: the composition root in ``service.py``,
the outermost-first decorator order documented by
``decorators.py::compose_scoring_pipeline``, the strategy seam, the
infrastructure modules and the visitor output seam.

Run from the package root:

    python3 tools/make_architecture_diagram.py

Outputs ``eportfolio/figure_1_architecture.png`` and ``.svg``.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(__file__).resolve().parent.parent / "eportfolio"

INK = "#1a1a1a"
BAND_EDGE = "#9aa5b1"
BAND_FILL = "#f7f9fb"
BOX_FILL = "#ffffff"
ACCENT = "#1f4e79"
ACCENT_FILL = "#e8f0f8"
WARN_EDGE = "#b45309"
WARN_FILL = "#fff7ed"
PORT_FILL = "#eef7ee"
PORT_EDGE = "#3f7d3f"
GREY = "#5b6572"


def band(ax, x0, y0, x1, y1, title):
    ax.add_patch(
        FancyBboxPatch(
            (x0, y0),
            x1 - x0,
            y1 - y0,
            boxstyle="round,pad=0.4,rounding_size=1.2",
            linewidth=1.1,
            edgecolor=BAND_EDGE,
            facecolor=BAND_FILL,
            zorder=1,
        )
    )
    ax.text(x0 + 1.2, y1 - 1.9, title, fontsize=10.5, weight="bold", color=ACCENT, zorder=3)


def box(ax, x0, y0, x1, y1, lines, *, fill=BOX_FILL, edge=ACCENT, lw=1.2, fontsize=9.0, bold_first=True):
    ax.add_patch(
        FancyBboxPatch(
            (x0, y0),
            x1 - x0,
            y1 - y0,
            boxstyle="round,pad=0.25,rounding_size=0.9",
            linewidth=lw,
            edgecolor=edge,
            facecolor=fill,
            zorder=2,
        )
    )
    n = len(lines)
    span = y1 - y0
    for i, line in enumerate(lines):
        weight = "bold" if (bold_first and i == 0) else "normal"
        size = fontsize if i == 0 else fontsize - 1.2
        colour = INK if i == 0 else GREY
        ax.text(
            (x0 + x1) / 2,
            y1 - span * (i + 0.62) / (n + 0.25),
            line,
            ha="center",
            va="center",
            fontsize=size,
            weight=weight,
            color=colour,
            zorder=4,
        )


def arrow(ax, x0, y0, x1, y1, *, label="", style="-", colour=ACCENT, dashed=False):
    ax.add_patch(
        FancyArrowPatch(
            (x0, y0),
            (x1, y1),
            arrowstyle="-|>",
            mutation_scale=13,
            linewidth=1.3,
            linestyle="--" if dashed else "-",
            color=colour,
            zorder=3,
            shrinkA=1.5,
            shrinkB=1.5,
        )
    )
    if label:
        ax.text(
            (x0 + x1) / 2 + 1.4,
            (y0 + y1) / 2,
            label,
            fontsize=7.8,
            color=colour,
            ha="left",
            va="center",
            style="italic",
            zorder=4,
        )


def main() -> int:
    fig, ax = plt.subplots(figsize=(15.0, 10.4))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    ax.text(
        50,
        97.6,
        "Figure 1 — Capstone architecture: composition root, scoring pipeline and extension seams",
        ha="center",
        va="center",
        fontsize=14.5,
        weight="bold",
        color=INK,
    )
    ax.text(
        50,
        94.9,
        "Advanced OOP capstone artefact: 9 modules, 2,512 statements, 355 tests (+39 Unit 6 = 394), 99% statement coverage",
        ha="center",
        va="center",
        fontsize=9.2,
        color=GREY,
    )

    # ---- A. Composition root -------------------------------------------------
    band(ax, 5, 78.0, 95, 93.5, "Composition root — service.py  (Dependency Injection / Inversion of Control)")
    box(ax, 7.5, 83.4, 33, 89.0, ["build_feature_store_and_registry()", "constructs FeatureStore", "and ModelRegistry"])
    box(ax, 34.5, 83.4, 65, 89.0, ["build_risk_service()", "assembles the pipeline and wraps it", "in RiskAssessmentService"])
    box(ax, 66.5, 83.4, 92.5, 89.0, ["build_container() → ServiceContainer", "resolves and reuses the singleton", "risk_service (run section 8)"])
    ax.text(
        50,
        80.6,
        "Constructor injection only: transport · clock · context provider · scoring backend · audit sink",
        ha="center",
        va="center",
        fontsize=8.6,
        color=GREY,
    )

    arrow(ax, 50, 78.0, 50, 76.0, label="builds and configures")

    # ---- B. Scoring pipeline (decorator chain) -------------------------------
    band(ax, 5, 52.5, 95, 75.8, "Scoring pipeline — decorators.py :: compose_scoring_pipeline()   (outermost first; the order is documented in the function docstring)")
    labels = [
        ["1  RedactingDecorator", "outermost: no inner", "layer sees a raw id"],
        ["2  MetricsDecorator", "measures the whole", "chain, fallback included"],
        ["3  AuditingDecorator", "outside the fallback,", "so degraded decisions", "still reach the audit trail"],
        ["4  FallbackDecorator", "absorbs provider", "failure before the", "caller sees it"],
        ["5  CachingDecorator", "innermost: a hit skips", "the model call, not", "the governance layers"],
        ["6  StrategyComponent", "the algorithm itself,", "injected at assembly"],
    ]
    x0, x1 = 6.6, 93.4
    width = (x1 - x0 - 5 * 1.5) / 6
    centres = []
    for i, lines in enumerate(labels):
        bx0 = x0 + i * (width + 1.5)
        bx1 = bx0 + width
        centres.append((bx0 + bx1) / 2)
        is_audit = i == 2
        box(
            ax,
            bx0,
            59.4,
            bx1,
            67.4,
            lines,
            fill=WARN_FILL if is_audit else BOX_FILL,
            edge=WARN_EDGE if is_audit else ACCENT,
            fontsize=8.4,
        )
        if i < len(labels) - 1:
            arrow(ax, bx1, 63.4, bx1 + 1.5, 63.4)
    ax.text(
        50,
        57.4,
        "Governance is positioned relative to failure. AuditingDecorator wraps the fallback rather than sitting inside it:",
        ha="center",
        va="center",
        fontsize=8.4,
        color=WARN_EDGE,
    )
    ax.text(
        50,
        55.4,
        "an auditor placed inside the fallback never sees the substituted decision — the composition-order defect this design fixes.",
        ha="center",
        va="center",
        fontsize=8.4,
        color=WARN_EDGE,
    )
    ax.text(6.6, 69.4, "client →", fontsize=8.0, color=ACCENT, ha="left", va="center")
    ax.text(93.4, 69.4, "→ ScoredTransaction", fontsize=8.0, color=ACCENT, ha="right", va="center")

    arrow(ax, 50, 52.5, 50, 50.6, label="delegates to")

    # ---- C. Strategy seam ----------------------------------------------------
    band(ax, 5, 37.0, 95, 50.4, "Algorithm seam — strategies.py  (Strategy pattern)")
    for i, (title, sub) in enumerate(
        [
            ("RuleBasedScoringStrategy", "expert-set weights, rules-v1"),
            ("WeightedStatisticalStrategy", "feature-weighted scoring"),
            ("ModelBackedScoringStrategy", "hosted model reply, validated"),
        ]
    ):
        bx0 = 7.0 + i * 23.0
        box(ax, bx0, 42.6, bx0 + 21.0, 47.6, [title, sub], fontsize=9.0)
    box(ax, 76.0, 42.6, 93.0, 47.6, ["ScoringBackend (Protocol)", "5 lines, no vendor concepts"], fill=PORT_FILL, edge=PORT_EDGE, fontsize=8.6)
    ax.text(
        50,
        39.8,
        "One configuration string selects the algorithm; DecisionPolicy maps the numeric score to ALLOW / REVIEW / BLOCK.",
        ha="center",
        va="center",
        fontsize=8.4,
        color=GREY,
    )

    arrow(ax, 50, 37.0, 50, 35.4, label="reads features and model version", dashed=True)

    # ---- D. Infrastructure and lifecycle ------------------------------------
    band(ax, 5, 20.0, 95, 35.0, "Infrastructure and lifecycle — feature_store.py · model_registry.py · providers.py")
    box(
        ax,
        7.0,
        21.8,
        34.0,
        31.4,
        ["FeatureStore", "13 derived features", "amount_zscore · velocity ·", "foreign_flag · channel_risk", "", "train/serve skew controlled by", "one derivation path"],
        fontsize=8.8,
    )
    box(
        ax,
        35.5,
        21.8,
        63.5,
        31.4,
        ["ModelRegistry", "lifecycle state machine", "DEV → STAGING → PRODUCTION", "→ ARCHIVED (terminal)", "", "illegal transitions are refused;", "promotion is persisted"],
        fontsize=8.8,
    )
    box(
        ax,
        65.0,
        21.8,
        93.0,
        31.4,
        ["Abstract Factory (providers.py)", "AiProviderFactory ⇒", "LocalHeuristicProviderFactory /", "HostedLlmProviderFactory", "", "ExplanationGenerator ⇒", "template / LLM narrative"],
        fontsize=8.8,
    )

    arrow(ax, 50, 20.0, 50, 18.4, label="renders", dashed=True)

    # ---- E. Output seam -----------------------------------------------------
    band(ax, 5, 6.5, 95, 18.0, "Output seam — reporting.py  (Visitor pattern)")
    for i, (title, sub) in enumerate(
        [
            ("RiskSummaryVisitor", "batch totals and split"),
            ("BlockedTransactionVisitor", "blocked items with rationale"),
            ("JsonExportVisitor", "machine-readable export"),
            ("ChannelRiskVisitor", "added last, touched no other file"),
        ]
    ):
        bx0 = 6.8 + i * 21.8
        box(ax, bx0, 9.4, bx0 + 19.8, 14.4, [title, sub], fontsize=8.4)
    ax.text(
        50,
        7.7,
        "Artefact D: a new report is a new class over the same ScoredBatch, with no change to the visitable data structures.",
        ha="center",
        va="center",
        fontsize=8.4,
        color=GREY,
    )

    # ---- Footnotes ----------------------------------------------------------
    ax.text(
        5,
        4.2,
        "Domain objects (domain.py, frozen slotted dataclasses, fail-fast construction): Transaction · RiskScore · ScoredTransaction · ScoredBatch · RiskDecision",
        fontsize=8.2,
        color=GREY,
        ha="left",
        va="center",
    )
    ax.text(
        5,
        1.6,
        "Ports and seams (replaceable at the composition root): ScoringBackend · ContextProvider · transport · clock · audit sink · report visitor",
        fontsize=8.2,
        color=GREY,
        ha="left",
        va="center",
    )

    fig.tight_layout()
    OUT.mkdir(parents=True, exist_ok=True)
    for suffix in ("png", "svg"):
        target = OUT / f"figure_1_architecture.{suffix}"
        fig.savefig(target, dpi=200, bbox_inches="tight", facecolor="white")
        print(f"wrote {target} ({target.stat().st_size} bytes)")
    plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
