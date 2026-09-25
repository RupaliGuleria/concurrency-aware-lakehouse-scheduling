"""Generates the paper's figures as static PNGs into results/week4/figures/.

Pulls data directly from the underlying scripts/CSVs (re-derives numbers
via the same functions the reports use) rather than hardcoding report
numbers, so figures stay correct if the underlying data is ever
regenerated. Colors follow the dataviz skill's validated categorical
palette (fixed slot order, CVD-safe): slot 1 blue (static_least_slack),
slot 2 orange (adaptive_least_slack), slot 3 aqua (dp_oracle), slot 7
violet (admission_controlled_dp) - slots 4/5 (yellow/magenta) skipped for
static print figures since they sit below 3:1 contrast on a light surface.

Usage:
  .venv/Scripts/python.exe generate_paper_figures.py
"""
from __future__ import annotations

import csv
import os
import random
import statistics
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from multi_query_schedulers import AdmissionControlledDpScheduler, ALL_SCHEDULERS, score_order
from simulate_multi_query_scheduling import (
    BATCH_SIZE_RANGE, CONCURRENCY_TIERS, N_BATCHES, SEED,
    build_prediction_tables, generate_batch, load_concurrency_pool, load_pools,
    run_admission_sensitivity,
)
from run_live_multi_query_validation import build_ingestion_prediction_tables, generate_live_batch as generate_ingestion_batch
from run_live_concurrency_validation import generate_live_batch as generate_concurrency_batch

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "results", "week4", "figures")

# Validated categorical palette (references/palette.md), fixed slot order.
COLOR_STATIC = "#2a78d6"      # slot 1 blue
COLOR_ADAPTIVE = "#eb6834"    # slot 2 orange
COLOR_DP_ORACLE = "#1baf7a"   # slot 3 aqua
COLOR_ADMISSION = "#4a3aa7"   # slot 7 violet (skipping 4/5 yellow/magenta - low contrast on light surface)
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"

SCHEDULER_LABELS = {
    "static_least_slack": "Static Least Slack",
    "adaptive_least_slack": "Adaptive Least Slack",
    "dp_oracle": "DP Oracle",
    "admission_controlled_dp": "Admission-Controlled DP",
}
SCHEDULER_COLORS = {
    "static_least_slack": COLOR_STATIC,
    "adaptive_least_slack": COLOR_ADAPTIVE,
    "dp_oracle": COLOR_DP_ORACLE,
    "admission_controlled_dp": COLOR_ADMISSION,
}
SCHED_ORDER = ["static_least_slack", "adaptive_least_slack", "dp_oracle", "admission_controlled_dp"]


def style_axes(ax, ylabel="SLA adherence (%)"):
    ax.set_facecolor("#fcfcfb")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(BASELINE)
    ax.spines["bottom"].set_color(BASELINE)
    ax.yaxis.grid(True, color=GRIDLINE, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK_SECONDARY, labelsize=10)
    ax.set_ylabel(ylabel, color=INK_PRIMARY, fontsize=11)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_linewidth(1)


def bar_labels(ax, bars, fmt="{:.1f}%"):
    for b in bars:
        h = b.get_height()
        ax.annotate(fmt.format(h), (b.get_x() + b.get_width() / 2, h),
                    xytext=(0, 3), textcoords="offset points", ha="center",
                    va="bottom", fontsize=8.5, color=INK_SECONDARY)


def fig1_offline_per_tier(pool, concurrency_pool, predicted_static, predicted_adaptive):
    """Grouped bar: SLA adherence by scheduler, across low/moderate/high tiers (offline bootstrap simulation)."""
    rng = random.Random(SEED)
    by_tier = defaultdict(lambda: defaultdict(list))
    for _ in range(N_BATCHES):
        queries, concurrency_tier, _ = generate_batch(rng, pool, concurrency_pool, predicted_static, predicted_adaptive)
        for scheduler in ALL_SCHEDULERS:
            if scheduler.name not in SCHED_ORDER:
                continue
            result = scheduler.order(queries)
            scored = score_order(result.order, deferred_ids=result.deferred_ids)
            by_tier[concurrency_tier][scheduler.name].append(scored)

    fig, ax = plt.subplots(figsize=(7.5, 4.5), dpi=300)
    n_sched = len(SCHED_ORDER)
    width = 0.8 / n_sched
    x = range(len(CONCURRENCY_TIERS))
    for i, name in enumerate(SCHED_ORDER):
        heights = []
        for tier in CONCURRENCY_TIERS:
            batches = by_tier[tier][name]
            met = sum(b["n_met"] for b in batches)
            n = sum(b["n"] for b in batches)
            heights.append(met / n * 100 if n else 0)
        offsets = [xi + (i - n_sched / 2 + 0.5) * width for xi in x]
        bars = ax.bar(offsets, heights, width=width * 0.92, color=SCHEDULER_COLORS[name],
                       label=SCHEDULER_LABELS[name], zorder=3)
        bar_labels(ax, bars)
    ax.set_xticks(list(x))
    ax.set_xticklabels([t.capitalize() for t in CONCURRENCY_TIERS], fontsize=11, color=INK_PRIMARY)
    ax.set_ylim(0, 100)
    style_axes(ax)
    ax.set_title("Offline simulation: SLA adherence by concurrency tier\n(1000-batch bootstrap, isolated per tier)",
                 fontsize=12, color=INK_PRIMARY, pad=12)
    ax.legend(frameon=False, fontsize=9, loc="upper right", ncol=1)
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUT_DIR, f"fig1_offline_per_tier.{ext}"))
    plt.close(fig)
    print("Wrote fig1_offline_per_tier.{pdf,png}")


def fig2_offline_vs_live(pool, concurrency_pool, predicted_static, predicted_adaptive):
    """Grouped bar: offline (bootstrap) vs held-out vs live-concurrency (low+moderate blended),
    per scheduler - validates the ranking transfers across evaluation methods."""
    from simulate_multi_query_scheduling import load_heldout_pool, run_held_out_evaluation

    rng = random.Random(SEED)
    overall = defaultdict(list)
    for _ in range(N_BATCHES):
        queries, _, _ = generate_batch(rng, pool, concurrency_pool, predicted_static, predicted_adaptive)
        for scheduler in ALL_SCHEDULERS:
            if scheduler.name not in SCHED_ORDER:
                continue
            result = scheduler.order(queries)
            scored = score_order(result.order, deferred_ids=result.deferred_ids)
            overall[scheduler.name].append(scored)

    heldout_pool, _ = load_heldout_pool(concurrency_pool)
    heldout_results = run_held_out_evaluation(pool, concurrency_pool, heldout_pool, predicted_static, predicted_adaptive)

    live_csv = "../results/week4/live_concurrency_validation_postrestart.csv"
    live_stats = defaultdict(lambda: {"n": 0, "met": 0})
    with open(live_csv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["scheduler"] not in SCHED_ORDER:
                continue
            s = live_stats[r["scheduler"]]
            s["n"] += 1
            s["met"] += r["sla_met"].strip().lower() == "true"

    methods = ["Bootstrap\n(offline)", "Held-out\n(offline)", "Live\n(low+moderate)"]
    fig, ax = plt.subplots(figsize=(7.5, 4.5), dpi=300)
    n_sched = len(SCHED_ORDER)
    width = 0.8 / n_sched
    x = range(len(methods))
    for i, name in enumerate(SCHED_ORDER):
        bs = overall[name]
        bs_pct = sum(b["n_met"] for b in bs) / sum(b["n"] for b in bs) * 100
        ho = heldout_results[name]
        ho_pct = sum(b["n_met"] for b in ho) / sum(b["n"] for b in ho) * 100
        live_pct = live_stats[name]["met"] / live_stats[name]["n"] * 100 if live_stats[name]["n"] else 0
        heights = [bs_pct, ho_pct, live_pct]
        offsets = [xi + (i - n_sched / 2 + 0.5) * width for xi in x]
        bars = ax.bar(offsets, heights, width=width * 0.92, color=SCHEDULER_COLORS[name],
                       label=SCHEDULER_LABELS[name], zorder=3)
        bar_labels(ax, bars)
    ax.set_xticks(list(x))
    ax.set_xticklabels(methods, fontsize=10, color=INK_PRIMARY)
    ax.set_ylim(0, 100)
    style_axes(ax)
    ax.set_title("Scheduler ranking is consistent across evaluation methods",
                 fontsize=12, color=INK_PRIMARY, pad=12)
    ax.legend(frameon=False, fontsize=9, loc="upper right")
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUT_DIR, f"fig2_offline_vs_live.{ext}"))
    plt.close(fig)
    print("Wrote fig2_offline_vs_live.{pdf,png}")


def fig3_admission_control_live_reconciliation():
    """Grouped bar: blended vs admitted-only adherence, across all 5 live conditions tested."""
    pool = load_pools()
    concurrency_pool = load_concurrency_pool(pool)
    predicted_static_conc, predicted_adaptive_conc = build_prediction_tables(pool, concurrency_pool)
    predicted_static_ing, predicted_adaptive_ing = build_ingestion_prediction_tables(pool)

    def deferred_by_batch(gen_fn, seed, n_batches, predicted_static, predicted_adaptive, cond):
        rng = random.Random(seed)
        out = {}
        for i in range(n_batches):
            queries = gen_fn(rng, (4, 10), predicted_static, predicted_adaptive, cond)
            result = AdmissionControlledDpScheduler().order(queries)
            out[i] = result.deferred_ids
        return out

    def reconcile(csv_path, field, value, deferred_map):
        admitted_n = admitted_met = blended_n = blended_met = 0
        with open(csv_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["scheduler"] != "admission_controlled_dp" or r.get(field) != value:
                    continue
                parts = r["batch_id"].split("_")
                try:
                    batch_idx = int(parts[-2])
                except (ValueError, IndexError):
                    continue
                if batch_idx not in deferred_map:
                    continue
                met = r["sla_met"].strip().lower() == "true"
                blended_n += 1
                blended_met += met
                if r["query_id"] not in deferred_map[batch_idx]:
                    admitted_n += 1
                    admitted_met += met
        blended = blended_met / blended_n * 100 if blended_n else 0
        admitted = admitted_met / admitted_n * 100 if admitted_n else 0
        return blended, admitted

    ing_csv = "../results/week4/live_validation_4scheduler_check.csv"
    conc_csv = "../results/week4/live_concurrency_validation_postrestart.csv"

    conditions = [
        ("Ingestion:\nnone", ing_csv, "condition", "none",
         deferred_by_batch(generate_ingestion_batch, 42, 25, predicted_static_ing, predicted_adaptive_ing, "none")),
        ("Ingestion:\nmoderate", ing_csv, "condition", "moderate_sustained",
         deferred_by_batch(generate_ingestion_batch, 42, 25, predicted_static_ing, predicted_adaptive_ing, "moderate_sustained")),
        ("Ingestion:\nheavy", ing_csv, "condition", "heavy_sustained",
         deferred_by_batch(generate_ingestion_batch, 42, 25, predicted_static_ing, predicted_adaptive_ing, "heavy_sustained")),
        ("Concurrency:\nlow", conc_csv, "tier", "low",
         deferred_by_batch(generate_concurrency_batch, 43, 5, predicted_static_conc, predicted_adaptive_conc, "low")),
        ("Concurrency:\nmoderate", conc_csv, "tier", "moderate",
         deferred_by_batch(generate_concurrency_batch, 43, 20, predicted_static_conc, predicted_adaptive_conc, "moderate")),
    ]

    labels, blended_vals, admitted_vals = [], [], []
    for label, path, field, value, dmap in conditions:
        blended, admitted = reconcile(path, field, value, dmap)
        labels.append(label)
        blended_vals.append(blended)
        admitted_vals.append(admitted)

    fig, ax = plt.subplots(figsize=(8, 4.8), dpi=300)
    x = range(len(labels))
    width = 0.36
    b1 = ax.bar([xi - width / 2 for xi in x], blended_vals, width=width * 0.92,
                color=COLOR_STATIC, label="Blended (admitted + deferred)", zorder=3)
    b2 = ax.bar([xi + width / 2 for xi in x], admitted_vals, width=width * 0.92,
                color=COLOR_ADMISSION, label="Admitted-only", zorder=3)
    bar_labels(ax, b1)
    bar_labels(ax, b2)
    ax.axhline(80, color=INK_MUTED, linewidth=1, linestyle="--", zorder=2)
    ax.annotate("declared 80% service target", (len(labels) - 1, 80), xytext=(0, 5),
                textcoords="offset points", ha="right", fontsize=8.5, color=INK_MUTED)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=9.5, color=INK_PRIMARY)
    ax.set_ylim(0, 100)
    style_axes(ax)
    ax.set_title("Admission control: admitted-only adherence beats blended,\nlive, in every condition tested",
                 fontsize=12, color=INK_PRIMARY, pad=12)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUT_DIR, f"fig3_admission_control_live.{ext}"))
    plt.close(fig)
    print("Wrote fig3_admission_control_live.{pdf,png}")


def fig4_admission_sensitivity(pool, concurrency_pool, predicted_static, predicted_adaptive):
    """Line chart: admitted-only adherence and defer rate vs. declared service target (offline sweep)."""
    results = run_admission_sensitivity(pool, concurrency_pool, predicted_static, predicted_adaptive)
    targets = [r["target"] * 100 for r in results]
    admitted = [r["admitted_adherence"] * 100 for r in results]
    defer = [r["defer_rate"] * 100 for r in results]

    fig, ax1 = plt.subplots(figsize=(7, 4.5), dpi=300)
    ax1.plot(targets, admitted, marker="o", markersize=7, linewidth=2, color=COLOR_ADMISSION,
             label="Admitted-only adherence", zorder=3)
    ax1.plot(targets, defer, marker="s", markersize=7, linewidth=2, color=COLOR_ADAPTIVE,
             label="Defer rate", zorder=3)
    for tx, ty in zip(targets, admitted):
        ax1.annotate(f"{ty:.1f}%", (tx, ty), xytext=(0, 8), textcoords="offset points",
                     ha="center", fontsize=8.5, color=INK_SECONDARY)
    for tx, ty in zip(targets, defer):
        ax1.annotate(f"{ty:.1f}%", (tx, ty), xytext=(0, -14), textcoords="offset points",
                     ha="center", fontsize=8.5, color=INK_SECONDARY)
    ax1.set_xlabel("Declared service target (%)", fontsize=11, color=INK_PRIMARY)
    ax1.set_xticks(targets)
    style_axes(ax1, ylabel="Percent")
    ax1.set_ylim(0, 100)
    ax1.set_title("Admission control tracks its declared service target\n(offline sensitivity sweep, 1000 batches per target)",
                  fontsize=12, color=INK_PRIMARY, pad=12)
    ax1.legend(frameon=False, fontsize=9, loc="center right")
    fig.tight_layout()
    for ext in ("pdf", "png"):
        fig.savefig(os.path.join(OUT_DIR, f"fig4_admission_sensitivity.{ext}"))
    plt.close(fig)
    print("Wrote fig4_admission_sensitivity.{pdf,png}")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    pool = load_pools()
    concurrency_pool = load_concurrency_pool(pool)
    predicted_static, predicted_adaptive = build_prediction_tables(pool, concurrency_pool)

    fig1_offline_per_tier(pool, concurrency_pool, predicted_static, predicted_adaptive)
    fig2_offline_vs_live(pool, concurrency_pool, predicted_static, predicted_adaptive)
    fig3_admission_control_live_reconciliation()
    fig4_admission_sensitivity(pool, concurrency_pool, predicted_static, predicted_adaptive)
    print(f"\nAll figures written to {os.path.abspath(OUT_DIR)}")


if __name__ == "__main__":
    main()
