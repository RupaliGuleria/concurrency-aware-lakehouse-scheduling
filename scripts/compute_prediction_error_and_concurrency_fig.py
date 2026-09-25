"""Computes held-out prediction error (MAE/MAPE per concurrency tier) and
builds the concurrency-vs-runtime figure for the paper.

Reuses the exact same data-loading and prediction functions as
simulate_multi_query_scheduling.py (load_pools, load_concurrency_pool,
load_heldout_pool, build_prediction_tables) so these numbers are computed
from the same predicted_adaptive[(class, tier)] values the schedulers
themselves use, and scored against the same held-out data
run_held_out_evaluation() uses for the SLA-adherence held-out comparison -
this is a different metric (runtime prediction error, not adherence) on
the identical inputs, not a separately invented computation.

Respects load_heldout_pool's fallback_cells: cells where the held-out CSV
has no genuinely independent samples (medium/long at "low" tier - see that
function's docstring) fall back to resampling the calibration pool, and
are excluded from the per-tier aggregate so the aggregate is not silently
diluted by non-independent data.

Usage (run from scripts/):
  python compute_prediction_error_and_concurrency_fig.py
"""
from __future__ import annotations

import csv
import os
import statistics
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from simulate_multi_query_scheduling import (
    QUERY_CLASSES,
    CONCURRENCY_TIERS,
    CONCURRENCY_CSV,
    load_pools,
    load_concurrency_pool,
    load_heldout_pool,
    build_prediction_tables,
)

OUT_FIG_DIR = os.path.join(os.path.dirname(__file__), "..", "results", "week4", "figures")
OUT_REPORT = os.path.join(os.path.dirname(__file__), "..", "results", "week4", "held_out_prediction_error.md")

# Palette matching generate_paper_figures.py (dataviz-skill categorical slots).
COLOR_SHORT = "#2a78d6"   # slot 1 blue
COLOR_MEDIUM = "#eb6834"  # slot 2 orange
COLOR_LONG = "#1baf7a"    # slot 3 aqua
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"


def style_axes(ax, ylabel):
    ax.set_facecolor("#fcfcfb")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(BASELINE)
    ax.spines["bottom"].set_color(BASELINE)
    ax.yaxis.grid(True, color=GRIDLINE, linewidth=1, zorder=0)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK_SECONDARY, labelsize=10)
    ax.set_ylabel(ylabel, color=INK_PRIMARY, fontsize=11)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_linewidth(1)


def compute_prediction_error():
    pool = load_pools()
    concurrency_pool = load_concurrency_pool(pool)
    heldout_pool, fallback_cells = load_heldout_pool(concurrency_pool)
    _, predicted_adaptive = build_prediction_tables(pool, concurrency_pool)
    fallback_set = set(fallback_cells)

    rows = []
    for tier in CONCURRENCY_TIERS:
        for cls in QUERY_CLASSES:
            actuals = heldout_pool[(cls, tier)]
            pred = predicted_adaptive[(cls, tier)]
            abs_errs = [abs(a - pred) for a in actuals]
            pct_errs = [abs(a - pred) / a * 100 for a in actuals if a > 0]
            rows.append({
                "class": cls,
                "tier": tier,
                "n": len(actuals),
                "predicted_ms": pred,
                "mae_ms": statistics.median(abs_errs) if abs_errs else float("nan"),
                "mape_pct": statistics.median(pct_errs) if pct_errs else float("nan"),
                "held_out": (cls, tier) not in fallback_set,
            })

    tier_agg = {}
    for tier in CONCURRENCY_TIERS:
        pooled_abs, pooled_pct, n = [], [], 0
        for cls in QUERY_CLASSES:
            if (cls, tier) in fallback_set:
                continue
            actuals = heldout_pool[(cls, tier)]
            pred = predicted_adaptive[(cls, tier)]
            pooled_abs.extend(abs(a - pred) for a in actuals)
            pooled_pct.extend(abs(a - pred) / a * 100 for a in actuals if a > 0)
            n += len(actuals)
        tier_agg[tier] = {
            "n": n,
            "mae_ms": statistics.median(pooled_abs) if pooled_abs else float("nan"),
            "mape_pct": statistics.median(pooled_pct) if pooled_pct else float("nan"),
        }
    return rows, tier_agg, fallback_cells


def write_report(rows, tier_agg, fallback_cells):
    lines = [
        "# Held-out prediction error (Adaptive predictor)",
        "",
        "Predicted runtime is `predicted_adaptive[(class, tier)]`, the median "
        "of the calibration pool for that cell -- the same value "
        "Adaptive/DP/Admission-Controlled DP use. Actual runtime is drawn "
        "from `concurrency_contention_heldout.csv`, a separately collected "
        "round (see `load_heldout_pool` in simulate_multi_query_scheduling.py). "
        "MAE/MAPE are the median absolute error / median absolute percentage "
        "error across held-out samples in each cell.",
        "",
        "| Class | Tier | n | Predicted (ms) | MAE (ms) | MAPE (%) | Genuinely held out |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['class']} | {r['tier']} | {r['n']} | {r['predicted_ms']:.1f} | "
            f"{r['mae_ms']:.1f} | {r['mape_pct']:.1f} | "
            f"{'yes' if r['held_out'] else 'no (fallback to calibration pool)'} |"
        )
    lines += ["", "## Per-tier aggregate (genuinely held-out cells only, pooled)", "",
              "| Tier | n | MAE (ms) | MAPE (%) |", "|---|---:|---:|---:|"]
    for tier in CONCURRENCY_TIERS:
        a = tier_agg[tier]
        lines.append(f"| {tier} | {a['n']} | {a['mae_ms']:.1f} | {a['mape_pct']:.1f} |")
    if fallback_cells:
        lines.append("")
        lines.append(
            f"Fallback cells (not genuinely held out -- `concurrency_contention_test.py` "
            f"only cycles the first query class at concurrency=1, so held-out low-tier "
            f"data doesn't cover every class; these resample the calibration pool instead "
            f"and are excluded from the aggregate above): {fallback_cells}"
        )
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote {OUT_REPORT}")


def fig_concurrency_runtime():
    """Median runtime (IQR band) vs. concurrency level, per query class, from
    the raw calibration sweep (concurrency_contention.csv: levels 1,2,4,8,16,
    no ingestion running)."""
    by_level_class = defaultdict(list)
    with open(CONCURRENCY_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("error"):
                continue
            by_level_class[(int(r["concurrency"]), r["query_class"])].append(float(r["runtime_ms"]))

    levels = sorted({lvl for lvl, _cls in by_level_class})
    colors = {"short": COLOR_SHORT, "medium": COLOR_MEDIUM, "long": COLOR_LONG}
    labels = {"short": "short (point lookup)", "medium": "medium (aggregation)", "long": "long (join)"}

    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=300)
    for cls in QUERY_CLASSES:
        xs, meds, p25s, p75s = [], [], [], []
        for lvl in levels:
            vals = by_level_class.get((lvl, cls))
            if not vals:
                continue
            vals_sorted = sorted(vals)
            xs.append(lvl)
            meds.append(statistics.median(vals_sorted))
            if len(vals_sorted) >= 4:
                q = statistics.quantiles(vals_sorted, n=4)
                p25s.append(q[0])
                p75s.append(q[2])
            else:
                p25s.append(min(vals_sorted))
                p75s.append(max(vals_sorted))
        ax.plot(xs, meds, marker="o", markersize=6, linewidth=2, color=colors[cls],
                 label=labels[cls], zorder=3)
        ax.fill_between(xs, p25s, p75s, color=colors[cls], alpha=0.15, zorder=2, linewidth=0)

    ax.set_xscale("log", base=2)
    ax.set_xticks(levels)
    ax.set_xticklabels([str(l) for l in levels])
    ax.set_xlabel("Concurrent queries (C)", fontsize=11, color=INK_PRIMARY)
    ax.set_yscale("log")
    style_axes(ax, ylabel="Median runtime, ms (log scale)")
    ax.set_title("Query runtime rises sharply with concurrency\n(shaded band: interquartile range)",
                 fontsize=12, color=INK_PRIMARY, pad=12)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUT_FIG_DIR, f"fig_concurrency_runtime.{ext}"))
    plt.close(fig)
    print("Wrote fig_concurrency_runtime.{pdf,png}")

    short_1 = statistics.median(by_level_class.get((1, "short"), []))
    short_16 = statistics.median(by_level_class.get((16, "short"), []))
    return short_1, short_16


def main():
    os.makedirs(OUT_FIG_DIR, exist_ok=True)
    rows, tier_agg, fallback_cells = compute_prediction_error()
    write_report(rows, tier_agg, fallback_cells)
    short_1, short_16 = fig_concurrency_runtime()
    print(
        f"\nCross-check vs. paper's '19x' / '296ms to 5,717ms' claim: "
        f"short-class median at C=1 = {short_1:.2f} ms, at C=16 = {short_16:.2f} ms, "
        f"ratio = {short_16 / short_1:.2f}x"
    )


if __name__ == "__main__":
    main()
