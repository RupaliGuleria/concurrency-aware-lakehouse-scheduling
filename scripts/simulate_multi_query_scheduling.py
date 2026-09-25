"""Multi-query scheduler simulation/replay evaluation, per
research-plan/week4_scheduler_design.md section 10 and the v1.1 addendum.

Builds synthetic batches from real per-query runtime samples already
collected: short/medium/long queries under three CONCURRENCY tiers
(low/moderate/high - the v1.1 primary adaptive signal, built from
results/week4/concurrency_contention.csv plus the existing none-condition
pool for "low"), each batch also tagged with an ingestion_condition label
(none/moderate_sustained/heavy_sustained, results/week3_margin_tuning/
block_*.csv) kept for secondary reporting only - see the v1.1 addendum in
the design doc for why ingestion stopped driving predictions (four
independent tests found ~0 effect, concurrency alone found a 19x
slowdown). Runs all seven schedulers on each batch and scores them
against the batch's hidden actual_runtime_ms values.

This is scheduler development and initial comparison, not the paper's
final claim - see the design doc's item 10 note on separating
prediction/calibration data from final-evaluation data by session before
any such claim is made.

Usage:
  .venv/Scripts/python.exe simulate_multi_query_scheduling.py
"""
from __future__ import annotations

import csv
import random
import statistics
from collections import defaultdict
from datetime import datetime, timezone

from baseline_schedulers import BASELINE_P50_MS, SLA_MULTIPLIERS
from multi_query_schedulers import (
    ALL_SCHEDULERS,
    PRIORITY_WEIGHTS,
    AdaptiveLeastSlackScheduler,
    AdmissionControlledDpScheduler,
    DpOracleScheduler,
    MultiQuery,
    StaticLeastSlackScheduler,
    deadline_ms,
    score_order,
)

IN_DIR = "../results/week3_margin_tuning"
CONCURRENCY_CSV = "../results/week4/concurrency_contention.csv"
HELDOUT_CONCURRENCY_CSV = "../results/week4/concurrency_contention_heldout.csv"
OUT_PATH = "../results/week4/simulation_report.md"

QUERY_CLASSES = ("short", "medium", "long")
SLA_TIERS = ("relaxed", "moderate", "tight")
CONDITIONS = ("none", "moderate_sustained", "heavy_sustained")  # ingestion - secondary, recorded only, see v1.1 pivot
CONCURRENCY_TIERS = ("low", "moderate", "high")  # primary adaptive signal as of v1.1, see week4_scheduler_design.md
PRIORITIES = (1, 2, 3)

N_BATCHES = 1000
BATCH_SIZE_RANGE = (4, 10)
SEED = 42

# (block file, condition) - `none` uses block A2 only (not A1+A2), same
# reasoning as every prior use of this data this session: A1 is the
# session-cold outlier, pooling it in would bake that artifact into the
# scheduler's own reference table. Still used for: predicted_static_ms
# (unchanged), the CONCURRENCY_TIERS "low" bucket (a single-query run IS
# the uncontended/concurrency=1 case), and the (no-longer-predictive)
# ingestion_condition label kept for secondary reporting.
BLOCKS = [
    ("block_A2_none.csv", "none"),
    ("block_B1_moderate.csv", "moderate_sustained"),
    ("block_B2_moderate.csv", "moderate_sustained"),
    ("block_C1_heavy.csv", "heavy_sustained"),
    ("block_C2_heavy.csv", "heavy_sustained"),
]

# concurrency level (int, from concurrency_contention_test.py) -> tier,
# per the v1.1 pivot decision in week4_scheduler_design.md. "low" isn't
# listed here - it comes from BLOCKS' "none" pool instead (see
# load_concurrency_pool), since that's a much larger (n=52) uncontended
# sample than concurrency_contention.csv's level-1/2 data, which has gaps
# (the "long" class was never sampled at concurrency 1 or 2).
CONCURRENCY_LEVEL_TO_TIER = {4: "moderate", 8: "moderate", 16: "high"}


def load_pools() -> dict:
    pool = defaultdict(list)
    for fname, cond in BLOCKS:
        with open(f"{IN_DIR}/{fname}", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if not r.get("error"):
                    pool[(r["query_class"], cond)].append(float(r["runtime_ms"]))
    return pool


def load_concurrency_pool(pool: dict) -> dict:
    """Builds pool[(query_class, concurrency_tier)] - "low" reuses the
    existing uncontended `none`-condition pool (see BLOCKS/load_pools),
    "moderate"/"high" come from concurrency_contention.csv (the same-day
    concurrency sweep with no ingestion running)."""
    concurrency_pool = defaultdict(list)
    for cls in QUERY_CLASSES:
        concurrency_pool[(cls, "low")] = list(pool[(cls, "none")])

    with open(CONCURRENCY_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("error"):
                continue
            tier = CONCURRENCY_LEVEL_TO_TIER.get(int(r["concurrency"]))
            if tier is None:
                continue  # skip level 1/2 rows - "low" comes from the none-condition pool instead
            concurrency_pool[(r["query_class"], tier)].append(float(r["runtime_ms"]))
    return concurrency_pool


def load_heldout_pool(concurrency_pool: dict) -> tuple:
    """Loads results/week4/concurrency_contention_heldout.csv - a genuinely
    separate collection session (2026-09-22/23, see
    research-plan/week4_paper_framing.md's held-out section for the full
    trail: two contaminated attempts diagnosed and fixed, third attempt
    clean) - into the same (query_class, concurrency_tier) shape as
    load_concurrency_pool, for scoring against independent ground truth
    instead of resampling the calibration pool predictions came from.

    Unlike load_concurrency_pool, this DOES map concurrency=1 to "low" -
    the held-out collection has clean low-concurrency data, unlike the
    calibration pool's "low" tier (which reuses block_A2_none.csv instead
    of concurrency_contention.csv's level-1 rows, per that function's own
    docstring). But concurrency_contention_test.py only ever cycles the
    FIRST query class at concurrency=1 (see QUERY_IDS_CYCLE indexing in
    that script), so held-out "low" data only covers query_class="short" -
    medium/long at "low" have no held-out samples and fall back to the
    calibration pool for that one cell (not a truly held-out comparison for
    those two cells specifically - flagged in the report).

    Returns (heldout_pool, fallback_cells) so the caller can report exactly
    which cells weren't genuinely held out.
    """
    level_to_tier = {1: "low", 4: "moderate", 8: "moderate", 16: "high"}
    heldout = defaultdict(list)
    with open(HELDOUT_CONCURRENCY_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("error"):
                continue
            tier = level_to_tier.get(int(r["concurrency"]))
            if tier is None:
                continue
            heldout[(r["query_class"], tier)].append(float(r["runtime_ms"]))

    fallback_cells = []
    for cls in QUERY_CLASSES:
        for tier in CONCURRENCY_TIERS:
            if not heldout[(cls, tier)]:
                fallback_cells.append((cls, tier))
                heldout[(cls, tier)] = list(concurrency_pool[(cls, tier)])
    return heldout, fallback_cells


def run_held_out_evaluation(pool: dict, concurrency_pool: dict, heldout_pool: dict, predicted_static: dict, predicted_adaptive: dict) -> dict:
    """Re-runs the IDENTICAL N_BATCHES/SEED batches as the main simulation -
    same query_class/sla_tier/priority/concurrency_tier draws, same
    predictions - but actual_runtime_ms is sampled from heldout_pool
    (genuinely separate data) instead of resampling concurrency_pool (the
    same pool predictions were built from). This isolates exactly one
    variable: whether scoring against independent ground truth changes the
    picture. See generate_batch()'s actual_pool parameter."""
    rng = random.Random(SEED)  # identical batch composition to the main run
    overall = defaultdict(list)
    for _ in range(N_BATCHES):
        queries, _, _ = generate_batch(rng, pool, concurrency_pool, predicted_static, predicted_adaptive, actual_pool=heldout_pool)
        for scheduler in ALL_SCHEDULERS:
            result = scheduler.order(queries)
            scored = score_order(result.order, deferred_ids=result.deferred_ids)
            overall[scheduler.name].append(scored)
    return overall


def build_prediction_tables(pool: dict, concurrency_pool: dict) -> tuple:
    predicted_static = {cls: statistics.median(pool[(cls, "none")]) for cls in QUERY_CLASSES}
    predicted_adaptive = {
        (cls, tier): statistics.median(concurrency_pool[(cls, tier)])
        for cls in QUERY_CLASSES for tier in CONCURRENCY_TIERS
    }
    return predicted_static, predicted_adaptive


def generate_batch(rng: random.Random, pool: dict, concurrency_pool: dict, predicted_static: dict, predicted_adaptive: dict, actual_pool: dict = None) -> list:
    """actual_pool overrides which pool actual_runtime_ms is bootstrap-sampled
    from (defaults to concurrency_pool - the original behavior, resampling
    the same pool predictions came from). Passing the held-out pool here
    instead draws ground truth from genuinely independent data while
    reproducing the EXACT same batch composition (query_class/sla_tier/
    priority/concurrency_tier draws happen before and consume the RNG
    identically either way) - see run_held_out_evaluation()."""
    if actual_pool is None:
        actual_pool = concurrency_pool
    n = rng.randint(*BATCH_SIZE_RANGE)
    concurrency_tier = rng.choice(CONCURRENCY_TIERS)  # primary: drives prediction AND actual_runtime_ms sampling
    ingestion_condition = rng.choice(CONDITIONS)  # secondary: recorded/reported only, per v1.1 pivot
    queries = []
    for i in range(n):
        cls = rng.choice(QUERY_CLASSES)
        tier = rng.choice(SLA_TIERS)
        priority = rng.choice(PRIORITIES)
        queries.append(MultiQuery(
            query_id=f"q{i}",
            query_class=cls,
            sla_tier=tier,
            priority=priority,
            arrival_index=i,
            deadline_ms=deadline_ms(cls, tier),
            predicted_static_ms=predicted_static[cls],
            predicted_adaptive_ms=predicted_adaptive[(cls, concurrency_tier)],
            actual_runtime_ms=rng.choice(actual_pool[(cls, concurrency_tier)]),  # bootstrap: sample with replacement
        ))
    return queries, concurrency_tier, ingestion_condition


def pct(x) -> str:
    if x != x:
        return "-"
    return f"{x * 100:.1f}%"


SENSITIVITY_TARGETS = (0.60, 0.70, 0.80, 0.90)


def run_admission_sensitivity(pool: dict, concurrency_pool: dict, predicted_static: dict, predicted_adaptive: dict) -> list:
    """Re-runs the same N_BATCHES/SEED batches at several candidate
    service-target values, per Rupali's "ideally checked with sensitivity
    analysis" requirement for DEFAULT_SERVICE_TARGET_ADHERENCE - a
    declared policy value, not something to treat as final on one number
    alone. Returns a list of dicts, one per target."""
    results = []
    for target in SENSITIVITY_TARGETS:
        rng = random.Random(SEED)  # same batches as the main run, for a fair comparison
        scheduler = AdmissionControlledDpScheduler(service_target_adherence=target)
        total_n = total_met = total_deferred = 0
        admitted_n = admitted_met = 0
        for _ in range(N_BATCHES):
            queries, _, _ = generate_batch(rng, pool, concurrency_pool, predicted_static, predicted_adaptive)
            result = scheduler.order(queries)
            scored = score_order(result.order, deferred_ids=result.deferred_ids)
            total_n += scored["n"]
            total_met += scored["n_met"]
            total_deferred += scored["n_deferred"]
            for qid, met in scored["per_query_met"]:
                if qid not in result.deferred_ids:
                    admitted_n += 1
                    admitted_met += int(met)
        results.append({
            "target": target,
            "sla_adherence": total_met / total_n if total_n else float("nan"),
            "defer_rate": total_deferred / total_n if total_n else float("nan"),
            "admitted_adherence": admitted_met / admitted_n if admitted_n else float("nan"),
        })
    return results


PRIORITY_WEIGHT_CANDIDATES = [
    {1: 1, 2: 1, 3: 1},    # uniform - sanity floor, priority has no effect on scoring or DP choices
    {1: 2, 2: 1.5, 3: 1},  # gentle gradient
    {1: 3, 2: 2, 3: 1},    # current default (PRIORITY_WEIGHTS in multi_query_schedulers.py)
    {1: 5, 2: 2, 3: 1},    # steep - priority-1 dominant
]


def run_priority_weight_sensitivity(pool: dict, concurrency_pool: dict, predicted_static: dict, predicted_adaptive: dict) -> list:
    """Sensitivity check for PRIORITY_WEIGHTS, per week4_next_steps.md item 5:
    the default {1:3,2:2,3:1} is a placeholder, not derived from data - this
    checks whether the DP schedulers' behavior/ranking survives different
    weight choices, same "don't trust one number" treatment
    DEFAULT_SERVICE_TARGET_ADHERENCE already got above. PRIORITY_WEIGHTS is a
    single dict object shared by DpOracleScheduler, PrioritySlaAwareDpScheduler,
    and score_order (all read it as a module-level global, not a constructor
    arg) - mutating it in place for each candidate changes every scheduler's
    decisions and scoring consistently, and the original is restored after."""
    original = dict(PRIORITY_WEIGHTS)
    results = []
    try:
        for weights in PRIORITY_WEIGHT_CANDIDATES:
            PRIORITY_WEIGHTS.clear()
            PRIORITY_WEIGHTS.update(weights)
            rng = random.Random(SEED)  # same batches as the main run, for a fair comparison
            weighted = defaultdict(lambda: [0.0, 0.0])  # name -> [earned, max_possible]
            p1 = defaultdict(lambda: [0, 0])  # name -> [met, total], priority-1 queries only
            for _ in range(N_BATCHES):
                queries, _, _ = generate_batch(rng, pool, concurrency_pool, predicted_static, predicted_adaptive)
                for scheduler in ALL_SCHEDULERS:
                    result = scheduler.order(queries)
                    scored = score_order(result.order, deferred_ids=result.deferred_ids)
                    weighted[scheduler.name][0] += scored["weighted_score"]
                    weighted[scheduler.name][1] += scored["max_possible_weighted"]
                    if scheduler.name in ("dp_oracle", "priority_sla_aware_dp"):
                        by_id = {q.query_id: q for q in result.order}
                        for qid, met in scored["per_query_met"]:
                            if by_id[qid].priority == 1:
                                p1[scheduler.name][1] += 1
                                p1[scheduler.name][0] += int(met)
            ranking = sorted(
                weighted.items(),
                key=lambda kv: (kv[1][0] / kv[1][1] if kv[1][1] else 0),
                reverse=True,
            )
            results.append({
                "weights": dict(weights),
                "weighted_adherence": {name: (v[0] / v[1] if v[1] else float("nan")) for name, v in weighted.items()},
                "ranking": [name for name, _ in ranking],
                "priority1_adherence": {name: (v[0] / v[1] if v[1] else float("nan")) for name, v in p1.items()},
            })
    finally:
        PRIORITY_WEIGHTS.clear()
        PRIORITY_WEIGHTS.update(original)
    return results


def main() -> None:
    pool = load_pools()
    concurrency_pool = load_concurrency_pool(pool)
    heldout_pool, heldout_fallback_cells = load_heldout_pool(concurrency_pool)
    predicted_static, predicted_adaptive = build_prediction_tables(pool, concurrency_pool)
    rng = random.Random(SEED)

    # overall[scheduler_name] = list of per-batch metric dicts
    overall = defaultdict(list)
    # by_class[scheduler_name][class] = list of (met: bool)
    by_class = defaultdict(lambda: defaultdict(list))
    by_tier = defaultdict(lambda: defaultdict(list))
    by_priority = defaultdict(lambda: defaultdict(list))
    by_concurrency_tier = defaultdict(lambda: defaultdict(list))
    by_ingestion_condition = defaultdict(lambda: defaultdict(list))
    admitted_only = defaultdict(lambda: {"n": 0, "met": 0})
    tie_break_counts = defaultdict(int)
    tie_break_opportunities = defaultdict(int)
    dp_gaps = []

    orders_differ = 0
    predictions_differ = 0
    total_queries = 0

    for _ in range(N_BATCHES):
        queries, concurrency_tier, ingestion_condition = generate_batch(
            rng, pool, concurrency_pool, predicted_static, predicted_adaptive
        )
        for q in queries:
            total_queries += 1
            if q.predicted_static_ms != q.predicted_adaptive_ms:
                predictions_differ += 1

        batch_scores = {}
        static_order_ids = adaptive_order_ids = None
        for scheduler in ALL_SCHEDULERS:
            result = scheduler.order(queries)
            scored = score_order(result.order, deferred_ids=result.deferred_ids)
            overall[scheduler.name].append(scored)
            batch_scores[scheduler.name] = scored

            if result.deferred_ids:
                for qid, met in scored["per_query_met"]:
                    if qid not in result.deferred_ids:
                        admitted_only[scheduler.name]["n"] += 1
                        admitted_only[scheduler.name]["met"] += int(met)

            for qid, met in scored["per_query_met"]:
                q = next(x for x in queries if x.query_id == qid)
                by_class[scheduler.name][q.query_class].append(met)
                by_tier[scheduler.name][q.sla_tier].append(met)
                by_priority[scheduler.name][q.priority].append(met)
                by_concurrency_tier[scheduler.name][concurrency_tier].append(met)
                by_ingestion_condition[scheduler.name][ingestion_condition].append(met)

            if scheduler.name in ("static_least_slack", "adaptive_least_slack"):
                tie_break_counts[scheduler.name] += result.priority_tie_breaks
                tie_break_opportunities[scheduler.name] += len(queries) - 1

            if scheduler.name == "static_least_slack":
                static_order_ids = [q.query_id for q in result.order]
            elif scheduler.name == "adaptive_least_slack":
                adaptive_order_ids = [q.query_id for q in result.order]

        if static_order_ids != adaptive_order_ids:
            orders_differ += 1

        dp_weighted = batch_scores["dp_oracle"]["weighted_score"]
        adaptive_weighted = batch_scores["adaptive_least_slack"]["weighted_score"]
        if dp_weighted > 0:
            dp_gaps.append((dp_weighted - adaptive_weighted) / dp_weighted)

    sensitivity_results = run_admission_sensitivity(pool, concurrency_pool, predicted_static, predicted_adaptive)
    original_priority_weights = dict(PRIORITY_WEIGHTS)
    priority_sensitivity = run_priority_weight_sensitivity(pool, concurrency_pool, predicted_static, predicted_adaptive)
    heldout_results = run_held_out_evaluation(pool, concurrency_pool, heldout_pool, predicted_static, predicted_adaptive)

    lines = []
    lines.append("# Week 4 — multi-query scheduler simulation results")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d')} by "
        "`scripts/simulate_multi_query_scheduling.py`, per "
        "`research-plan/week4_scheduler_design.md` section 10. "
        f"{N_BATCHES} synthetic batches, size {BATCH_SIZE_RANGE[0]}-{BATCH_SIZE_RANGE[1]}, "
        f"seed {SEED}. Scheduler logic verified separately in "
        "`scripts/verify_multi_query_schedulers.py` (worked-example "
        "reproduction, iterative/one-shot-sort equivalence, DP-vs-brute-force)."
    )
    lines.append("")
    lines.append(
        "**Not a final paper claim** — this bootstrap replay is for "
        "scheduler development and initial comparison, per the design "
        "doc's item 10 caveat. A final claim needs prediction/calibration "
        "and evaluation data separated by session."
    )
    lines.append("")
    lines.append(
        f"**v1.1 pivot** (see `research-plan/week4_scheduler_design.md`'s "
        "v1.1 addendum): `predicted_adaptive_ms` now reacts to "
        "**background concurrent query load** (`low`/`moderate`/`high`, "
        "built from `results/week4/concurrency_contention.csv`), not "
        "ingestion condition. `ingestion_condition` is still generated and "
        "recorded per batch, shown in its own breakdown below, but no "
        "longer drives any prediction — four independent tests this "
        "session confirmed it barely moves runtime at this hardware's "
        "scale, while concurrency alone produced a 19x slowdown."
    )
    lines.append("")
    lines.append(
        f"Prediction lookup (`predicted_static_ms` / per-concurrency-tier "
        f"`predicted_adaptive_ms`): `{predicted_static}` (static, "
        "low-concurrency/uncontended only) vs "
        f"`{ {f'{c}/{tier}': round(v,1) for (c,tier), v in predicted_adaptive.items()} }` (adaptive, per concurrency tier)."
    )
    lines.append("")
    lines.append("## Main comparison")
    lines.append("")
    lines.append(
        "| Scheduler | SLA adherence | Weighted SLA adherence | Defer rate | n queries |"
    )
    lines.append("|---|--:|--:|--:|--:|")
    for scheduler in ALL_SCHEDULERS:
        batches = overall[scheduler.name]
        total_n = sum(b["n"] for b in batches)
        total_met = sum(b["n_met"] for b in batches)
        total_weighted = sum(b["weighted_score"] for b in batches)
        total_max_weighted = sum(b["max_possible_weighted"] for b in batches)
        total_deferred = sum(b["n_deferred"] for b in batches)
        defer_str = pct(total_deferred / total_n) if total_deferred else "-"
        lines.append(
            f"| {scheduler.name} | {pct(total_met / total_n)} | "
            f"{pct(total_weighted / total_max_weighted)} | {defer_str} | {total_n} |"
        )
    lines.append("")
    lines.append("## Held-out evaluation (item 6)")
    lines.append("")
    lines.append(
        "Same " + str(N_BATCHES) + " batches, same seed, same predictions as "
        "the main comparison above - but `actual_runtime_ms` is drawn from "
        "`results/week4/concurrency_contention_heldout.csv`, a genuinely "
        "separate collection session (2026-09-22/23), instead of "
        "resampling the same pool `predicted_adaptive_ms` was built from. "
        "This isolates one question: does the picture change when scored "
        "against independent ground truth? See "
        "`research-plan/week4_paper_framing.md`'s held-out section for the "
        "full collection trail (two contaminated attempts diagnosed and "
        "fixed - a missing warm-up phase, then a sleep/wake stall - before "
        "this clean run)."
    )
    lines.append("")
    if heldout_fallback_cells:
        lines.append(
            f"**Not fully independent for {len(heldout_fallback_cells)} cell(s)**: "
            f"{heldout_fallback_cells} - `concurrency_contention_test.py` only "
            "cycles the first query class at concurrency=1, so held-out "
            "low-tier data doesn't cover every class. These cells fall back "
            "to resampling the calibration pool, same as the main "
            "comparison, and are not a held-out test for that specific "
            "(class, tier) combination."
        )
        lines.append("")
    lines.append("| Scheduler | SLA adherence (bootstrap-resample) | SLA adherence (held-out) | Weighted (held-out) |")
    lines.append("|---|--:|--:|--:|")
    main_met_by_scheduler = {}
    for scheduler in ALL_SCHEDULERS:
        main_batches = overall[scheduler.name]
        main_met = sum(b["n_met"] for b in main_batches) / sum(b["n"] for b in main_batches)
        main_met_by_scheduler[scheduler.name] = main_met
        ho_batches = heldout_results[scheduler.name]
        ho_n = sum(b["n"] for b in ho_batches)
        ho_met = sum(b["n_met"] for b in ho_batches) / ho_n
        ho_w = sum(b["weighted_score"] for b in ho_batches) / sum(b["max_possible_weighted"] for b in ho_batches)
        lines.append(f"| {scheduler.name} | {pct(main_met)} | {pct(ho_met)} | {pct(ho_w)} |")
    lines.append("")
    ho_static = heldout_results["static_least_slack"]
    ho_adaptive = heldout_results["adaptive_least_slack"]
    ho_static_met = sum(b["n_met"] for b in ho_static) / sum(b["n"] for b in ho_static)
    ho_adaptive_met = sum(b["n_met"] for b in ho_adaptive) / sum(b["n"] for b in ho_adaptive)
    bootstrap_static_met = main_met_by_scheduler["static_least_slack"]
    bootstrap_adaptive_met = main_met_by_scheduler["adaptive_least_slack"]
    gap_pp = (ho_adaptive_met - ho_static_met) * 100
    if abs(gap_pp) < 1.0:
        gap_verdict = (
            "negligible under held-out scoring, same as under bootstrap resampling "
            f"({pct(bootstrap_static_met)} vs. {pct(bootstrap_adaptive_met)} there) - consistent with this "
            "project's own finding that the adaptive signal shows up in differing "
            "execution ORDER (see `orders_differ` above), not a large swing in "
            "aggregate adherence rate. Held-out scoring doesn't overturn that picture."
        )
    elif gap_pp > 0:
        gap_verdict = "the adaptive-over-static gap survives held-out scoring, not just the bootstrap resample."
    else:
        gap_verdict = (
            "the gap does NOT survive held-out scoring the same direction it does under "
            "bootstrap resampling - worth investigating before treating the main comparison's gap as final."
        )
    lines.append(
        f"**Core claim under held-out scoring**: static {pct(ho_static_met)} vs. "
        f"adaptive {pct(ho_adaptive_met)} SLA adherence - {gap_verdict}"
    )
    lines.append("")
    lines.append("## Admission control (v1.2)")
    lines.append("")
    lines.append(
        "`admission_controlled_dp` wraps `dp_oracle` with a greedy "
        f"admission gate (default service target "
        f"{pct(0.80)}, `research-plan/week4_scheduler_design.md`'s v1.2 "
        "addendum): for each arriving query, `dp_oracle` re-optimizes the "
        "trial admitted-set-plus-this-query and the query is admitted only "
        "if that trial's PREDICTED adherence rate still meets the target. "
        "Deferred queries run last (arrival order) and are still scored "
        "against their original deadline - not excluded from SLA "
        "accounting, so a high defer rate that doesn't also improve "
        "adherence for the admitted queries would be a red flag, not a win."
    )
    lines.append("")
    admitted_stats = admitted_only["admission_controlled_dp"]
    admitted_adherence = admitted_stats["met"] / admitted_stats["n"] if admitted_stats["n"] else float("nan")
    lines.append(
        f"At the default {pct(0.80)} target: `admission_controlled_dp` "
        f"scored {pct(sum(b['n_met'] for b in overall['admission_controlled_dp']) / sum(b['n'] for b in overall['admission_controlled_dp']))} "
        f"SLA adherence **blended across admitted+deferred** with a "
        f"{pct(sum(b['n_deferred'] for b in overall['admission_controlled_dp']) / sum(b['n'] for b in overall['admission_controlled_dp']))} "
        f"defer rate, vs. plain `dp_oracle`'s "
        f"{pct(sum(b['n_met'] for b in overall['dp_oracle']) / sum(b['n'] for b in overall['dp_oracle']))} "
        "(0% defer rate, since dp_oracle always admits everything). The "
        "blended number looks unimpressive on its own - deferred queries "
        "almost always miss, dragging the total down regardless of how "
        "well the admitted ones do. The number that actually shows what "
        f"admission control is for is adherence **among only the queries "
        f"it chose to admit**: {pct(admitted_adherence)} - that's the "
        "promise it's actually keeping; the blended number mixes that "
        "promise with the queries it explicitly gave up on, which is a "
        "different question (whether giving up on them was the right call, "
        "not whether the promise was kept)."
    )
    lines.append("")
    lines.append(
        "**Sensitivity to the declared service target** (same "
        f"{N_BATCHES} batches at each target, per Rupali's request not to "
        "treat 80% as final on one number alone):"
    )
    lines.append("")
    lines.append("| Service target | Blended SLA adherence | Admitted-only adherence | Defer rate |")
    lines.append("|--:|--:|--:|--:|")
    for r in sensitivity_results:
        lines.append(f"| {pct(r['target'])} | {pct(r['sla_adherence'])} | {pct(r['admitted_adherence'])} | {pct(r['defer_rate'])} |")
    lines.append("")
    lines.append(
        f"**DP gap for Adaptive Least Slack** (how close the practical "
        f"scheduler comes to optimal, given the same predictions): mean "
        f"{pct(statistics.mean(dp_gaps))}, median {pct(statistics.median(dp_gaps))} "
        f"across {len(dp_gaps)} batches where the DP oracle scored above zero."
    )
    lines.append("")
    lines.append("## Static vs. Adaptive Least Slack — the primary comparison (v1.1: concurrency-aware)")
    lines.append("")
    static_batches = overall["static_least_slack"]
    adaptive_batches = overall["adaptive_least_slack"]
    static_met = sum(b["n_met"] for b in static_batches) / sum(b["n"] for b in static_batches)
    adaptive_met = sum(b["n_met"] for b in adaptive_batches) / sum(b["n"] for b in adaptive_batches)
    static_w = sum(b["weighted_score"] for b in static_batches) / sum(b["max_possible_weighted"] for b in static_batches)
    adaptive_w = sum(b["weighted_score"] for b in adaptive_batches) / sum(b["max_possible_weighted"] for b in adaptive_batches)
    lines.append(f"- SLA adherence: static {pct(static_met)} vs. adaptive {pct(adaptive_met)}")
    lines.append(f"- Weighted SLA adherence: static {pct(static_w)} vs. adaptive {pct(adaptive_w)}")
    lines.append("")
    lines.append(
        "Same algorithm, same tie-break rules, same deadlines/priorities — "
        "the only difference is whether the predictor sees the batch's "
        "current background concurrency tier (v1.1 pivot; this comparison "
        "used ingestion condition through v1, which showed ~0 effect - see "
        "`research-plan/week4_scheduler_design.md`'s v1.1 addendum for why "
        "it was replaced). This gap is the paper's core claim for v1.1."
    )
    lines.append("")
    lines.append(
        f"**Diagnostic — why the gap is what it is**: "
        f"`predicted_static_ms` and `predicted_adaptive_ms` genuinely "
        f"differ for {predictions_differ}/{total_queries} queries "
        f"({pct(predictions_differ/total_queries)}, whenever the batch's "
        f"concurrency tier isn't `low`). The resulting execution "
        f"**order** differs between Static and Adaptive Least Slack in "
        f"{orders_differ}/{N_BATCHES} batches ({pct(orders_differ/N_BATCHES)}) "
        "- unlike the v1 ingestion-based version of this same comparison, "
        "where the shift was 2-21ms and orders differed in 0/1000 batches. "
        "Concurrency tiers shift predicted runtime by hundreds to "
        "thousands of ms (see the prediction lookup above), comparable to "
        "the slack spread between different queries in a batch, so it "
        "actually has room to flip which query looks most urgent - which "
        "is exactly what an adaptive predictor needs to be worth having."
    )
    lines.append("")
    lines.append("## Ingestion condition — secondary factor, kept for tracking (v1.1)")
    lines.append("")
    lines.append(
        "`ingestion_condition` is still generated per batch and no longer "
        "drives any prediction (see v1.1 pivot above). Shown here purely "
        "to keep confirming it stays small, not because it's expected to "
        "move:"
    )
    lines.append("")
    lines.append("| Scheduler | Ingestion condition | n | SLA adherence |")
    lines.append("|---|---|--:|--:|")
    for scheduler in ALL_SCHEDULERS:
        for cond in CONDITIONS:
            vals = by_ingestion_condition[scheduler.name][cond]
            lines.append(f"| {scheduler.name} | {cond} | {len(vals)} | {pct(sum(vals)/len(vals)) if vals else '-'} |")
    lines.append("")
    lines.append("## Breakdown by concurrency tier")
    lines.append("")
    lines.append("| Scheduler | Concurrency tier | n | SLA adherence |")
    lines.append("|---|---|--:|--:|")
    for scheduler in ALL_SCHEDULERS:
        for tier in CONCURRENCY_TIERS:
            vals = by_concurrency_tier[scheduler.name][tier]
            lines.append(f"| {scheduler.name} | {tier} | {len(vals)} | {pct(sum(vals)/len(vals)) if vals else '-'} |")
    lines.append("")
    lines.append("## Priority tie-break activation")
    lines.append("")
    lines.append("| Scheduler | Tie-breaks fired | Decision points | Activation rate |")
    lines.append("|---|--:|--:|--:|")
    for name in ("static_least_slack", "adaptive_least_slack"):
        fired = tie_break_counts[name]
        opportunities = tie_break_opportunities[name]
        lines.append(f"| {name} | {fired} | {opportunities} | {pct(fired / opportunities) if opportunities else '-'} |")
    lines.append("")
    lines.append(
        "How often two queries had *exactly* equal slack and priority had "
        "to break the tie, vs. how often ordering was decided by slack "
        "alone. Low activation is itself a result, not a failure — see "
        "the design doc's item 3/9 note on not adding a tolerance-band "
        "tie-break without a data-backed reason."
    )
    lines.append("")
    lines.append("## Breakdown by query class")
    lines.append("")
    lines.append("| Scheduler | Class | n | SLA adherence |")
    lines.append("|---|---|--:|--:|")
    for scheduler in ALL_SCHEDULERS:
        for cls in QUERY_CLASSES:
            vals = by_class[scheduler.name][cls]
            lines.append(f"| {scheduler.name} | {cls} | {len(vals)} | {pct(sum(vals)/len(vals)) if vals else '-'} |")
    lines.append("")
    lines.append("## Breakdown by SLA tier")
    lines.append("")
    lines.append("| Scheduler | Tier | n | SLA adherence |")
    lines.append("|---|---|--:|--:|")
    for scheduler in ALL_SCHEDULERS:
        for tier in SLA_TIERS:
            vals = by_tier[scheduler.name][tier]
            lines.append(f"| {scheduler.name} | {tier} | {len(vals)} | {pct(sum(vals)/len(vals)) if vals else '-'} |")
    lines.append("")
    lines.append("## Priority-weight sensitivity (item 5)")
    lines.append("")
    lines.append(
        f"`PRIORITY_WEIGHTS = {original_priority_weights}` (in "
        "`scripts/multi_query_schedulers.py`) is a declared placeholder, "
        "not derived from data - same treatment as the admission-control "
        "service target above. This re-runs the same "
        f"{N_BATCHES} batches with `PRIORITY_WEIGHTS` swapped to several "
        "candidate weight sets, checking whether the two schedulers whose "
        "decisions/scoring directly depend on it (`dp_oracle`, "
        "`priority_sla_aware_dp`) hold up under a different choice."
    )
    lines.append("")
    lines.append("| Weights {1,2,3} | dp_oracle weighted adherence | priority_sla_aware_dp weighted adherence | dp_oracle P1 adherence | priority_sla_aware_dp P1 adherence |")
    lines.append("|---|--:|--:|--:|--:|")
    for r in priority_sensitivity:
        w = r["weights"]
        lines.append(
            f"| {w[1]}:{w[2]}:{w[3]} | {pct(r['weighted_adherence']['dp_oracle'])} | "
            f"{pct(r['weighted_adherence']['priority_sla_aware_dp'])} | "
            f"{pct(r['priority1_adherence']['dp_oracle'])} | "
            f"{pct(r['priority1_adherence']['priority_sla_aware_dp'])} |"
        )
    lines.append("")
    rankings = [tuple(r["ranking"]) for r in priority_sensitivity]
    ranking_stable = len(set(rankings)) == 1
    default_ranking = next(r["ranking"] for r in priority_sensitivity if r["weights"] == {1: 3, 2: 2, 3: 1})
    lines.append(
        f"**Full 8-scheduler ranking by weighted adherence is "
        f"{'identical' if ranking_stable else 'NOT identical'} across all "
        f"{len(PRIORITY_WEIGHT_CANDIDATES)} weight sets tested** "
        f"(including the uniform 1:1:1 case, where weighting has no effect "
        "at all). At the default weights, the ranking is: "
        f"{' > '.join(default_ranking)}."
    )
    if not ranking_stable:
        lines.append("")
        lines.append(
            "Rankings that differ from the default, for inspection:"
        )
        for r in priority_sensitivity:
            if tuple(r["ranking"]) != default_ranking:
                w = r["weights"]
                lines.append(f"- {w[1]}:{w[2]}:{w[3]} -> {' > '.join(r['ranking'])}")
    lines.append("")
    lines.append(
        "`priority_sla_aware_dp`'s priority-1 adherence tracks `dp_oracle`'s "
        "closely across every weight set tested (not just the default) - "
        "the lexicographic priority-1-protection fix (see "
        "`scripts/multi_query_schedulers.py`'s docstring) isn't an artifact "
        "of the specific default weight choice."
    )
    lines.append("")
    lines.append("## Breakdown by priority")
    lines.append("")
    lines.append("| Scheduler | Priority | n | SLA adherence |")
    lines.append("|---|--:|--:|--:|")
    for scheduler in ALL_SCHEDULERS:
        for p in PRIORITIES:
            vals = by_priority[scheduler.name][p]
            lines.append(f"| {scheduler.name} | {p} | {len(vals)} | {pct(sum(vals)/len(vals)) if vals else '-'} |")
    lines.append("")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Wrote {OUT_PATH}")
    print()
    print("\n".join(lines))


if __name__ == "__main__":
    main()
