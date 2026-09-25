"""Live Trino validation for multi-query schedulers — design doc item 4
("live Trino validation stage ... once scheduler behavior is stable").

Runs the SAME kind of synthetic batches (query_class/sla_tier/priority/
deadline, generated the same way as simulate_multi_query_scheduling.py)
against the live Ryzen box Trino instance, once per scheduler per batch,
scored against REAL measured runtime_ms instead of bootstrap-sampled ms.
predicted_static_ms/predicted_adaptive_ms still come from the historical
lookup tables (results/week3_margin_tuning/) — only actual_runtime_ms is
newly, live-measured here.

Caveat this script cannot avoid, unlike the bootstrap simulation: each
scheduler's execution of "the same" batch is a SEPARATE live run, so each
scheduler sees its own fresh actual_runtime_ms sample (Trino/cache/GC/
ingestion noise differs run to run) rather than all 7 schedulers sharing
one ground-truth sample the way the simulation does. That makes this a
live *sanity check* that scheduler behavior transfers off simulation, not
a controlled paired comparison — report this caveat alongside any numbers.

No sleep between queries WITHIN one scheduler's batch execution — the
next query starts the instant the previous finishes, which is what
score_order()'s cumulative-elapsed-time model assumes (deadline_ms is
compared against summed real runtimes with zero gap). --sleep-between-runs
only paces between separate scheduler runs/batches, to avoid hammering
Trino back-to-back.

Usage:
  # none condition — no producer needed
  .venv/Scripts/python.exe run_live_multi_query_validation.py --condition none --n-batches 25

  # moderate/heavy — start the producer externally first (PowerShell, per
  # ingestion-pipeline/run-producers.ps1 and this project's existing
  # pattern), THEN run this:
  .venv/Scripts/python.exe run_live_multi_query_validation.py --condition moderate_sustained --target-eps 1000 --n-batches 25
  .venv/Scripts/python.exe run_live_multi_query_validation.py --condition heavy_sustained --target-eps 1700 --n-batches 25
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys
import time
from dataclasses import replace

sys.path.insert(0, os.path.dirname(__file__))

import trino  # noqa: E402

from ingestion_client import wait_for_stable_rate  # noqa: E402
from query_timing_harness import CATALOG, HOST, PORT, QUERIES, QUERY_CLASS, USER, run_once  # noqa: E402
from multi_query_schedulers import ALL_SCHEDULERS, MultiQuery, deadline_ms, score_order  # noqa: E402
from simulate_multi_query_scheduling import CONDITIONS, QUERY_CLASSES, load_pools  # noqa: E402
from ryzen_health_check import DEFAULT_DRIFT_TOLERANCE, check_baseline_drift, print_baseline_drift, wait_until_healthy  # noqa: E402
import statistics  # noqa: E402

CLASS_TO_QUERY_ID = {v: k for k, v in QUERY_CLASS.items()}

SLA_TIERS = ("relaxed", "moderate", "tight")
PRIORITIES = (1, 2, 3)


def build_ingestion_prediction_tables(pool: dict) -> tuple:
    """Ingestion-condition-keyed prediction tables - kept local to this
    script rather than reusing simulate_multi_query_scheduling.py's
    build_prediction_tables(), which the v1.1 pivot repurposed to be
    concurrency-tier-keyed (see research-plan/week4_scheduler_design.md's
    v1.1 addendum). This live script is specifically testing ingestion
    visibility, not concurrency, so it needs the original ingestion-keyed
    version, not the concurrency one."""
    predicted_static = {cls: statistics.median(pool[(cls, "none")]) for cls in QUERY_CLASSES}
    predicted_adaptive = {
        (cls, cond): statistics.median(pool[(cls, cond)])
        for cls in QUERY_CLASSES for cond in CONDITIONS
    }
    # "ingestion_visible" (2026-09-22 test): schedulers get the SAME
    # predictions as "none" - nothing about the scheduling decision itself
    # should change, only the real underlying table now has the banking
    # partition synced into it. Any adherence difference this produces is
    # therefore attributable to real execution timing, not to a different
    # scheduling decision - the correct apples-to-apples design for testing
    # "does visible ingestion volume change real query performance."
    for cls in QUERY_CLASSES:
        predicted_adaptive[(cls, "ingestion_visible")] = predicted_adaptive[(cls, "none")]
    return predicted_static, predicted_adaptive

CSV_FIELDS = [
    "batch_id", "scheduler", "condition", "position", "query_id",
    "query_class", "sla_tier", "priority", "deadline_ms",
    "predicted_static_ms", "predicted_adaptive_ms", "actual_runtime_ms",
    "cumulative_elapsed_ms", "sla_met", "run_id", "start_time", "end_time",
    "error",
]


def generate_live_batch(rng, n_range, predicted_static, predicted_adaptive, condition) -> list:
    n = rng.randint(*n_range)
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
            predicted_adaptive_ms=predicted_adaptive[(cls, condition)],
            actual_runtime_ms=0.0,  # unknown until live execution
        ))
    return queries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--condition", required=True,
                         choices=["none", "moderate_sustained", "heavy_sustained", "ingestion_visible"])
    parser.add_argument("--target-eps", type=float, default=0.0)
    parser.add_argument("--eps-tolerance", type=float, default=0.10,
                         help="Fractional tolerance band for --target-eps stabilization (e.g. 0.25 = accept eps within +/-25%%). "
                              "Loosen this if the pipeline plateaus below target - record the actually-achieved rate, don't force a number it can't sustain.")
    parser.add_argument("--n-batches", type=int, default=25)
    parser.add_argument("--batch-size-min", type=int, default=4)
    parser.add_argument("--batch-size-max", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--schedulers", nargs="+", default=None,
                         help="Restrict to these scheduler names (e.g. dp_oracle priority_sla_aware_dp) "
                              "instead of running all of ALL_SCHEDULERS - keeps a focused live run faster.")
    parser.add_argument("--skip-batches", type=int, default=0,
                         help="Resume support: advance the RNG through this many batches "
                              "WITHOUT executing them (so with the same --seed, batch indices "
                              "before this many reproduce identically to a prior run that "
                              "already recorded them), then execute --n-batches starting after "
                              "the skip. Use after a partial run to continue, not restart.")
    parser.add_argument("--sleep-between-runs", type=float, default=0.5,
                         help="Pause between scheduler runs / batches, NOT between queries within a run")
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "results", "week4", "live_validation.csv"))
    parser.add_argument("--skip-health-check", action="store_true", help="Skip the pre-flight Ryzen health check (not recommended).")
    parser.add_argument("--skip-baseline-check", action="store_true", help="Skip the pre-flight uncontended-baseline-drift check (not recommended - see research-plan/week4_paper_framing.md's experimental-control note).")
    parser.add_argument("--baseline-drift-tolerance", type=float, default=DEFAULT_DRIFT_TOLERANCE)
    args = parser.parse_args()

    if not args.skip_health_check:
        if not wait_until_healthy():
            raise SystemExit(
                "Ryzen box failed the pre-flight health check - aborting rather "
                "than adding load to a box that's already struggling. Pass "
                "--skip-health-check to override (not recommended)."
            )

    if args.target_eps > 0:
        print(f"Waiting for ingestion rate to stabilize within 10% of {args.target_eps} eps ...")
        snap = wait_for_stable_rate(args.target_eps, tolerance=args.eps_tolerance, consecutive_required=3, poll_interval=1.0, timeout=90)
        print(f"Stable: rolling_eps={snap['currentRollingEps']:.1f} (target={args.target_eps})")

    pool = load_pools()
    predicted_static, predicted_adaptive = build_ingestion_prediction_tables(pool)

    conn = trino.dbapi.connect(host=HOST, port=PORT, user=USER, catalog=CATALOG)

    print("Warm-up pass (12 throwaway queries, not recorded) ...")
    for _ in range(4):
        for qid in QUERIES:
            cur = conn.cursor()
            cur.execute(QUERIES[qid])
            cur.fetchall()
    print("Warm-up complete.")

    if not args.skip_baseline_check:
        drift = check_baseline_drift(tolerance=args.baseline_drift_tolerance)
        print_baseline_drift(drift)
        if not drift["healthy"]:
            raise SystemExit(
                "Uncontended baseline has drifted beyond tolerance - aborting rather "
                "than collecting data against a stale calibration. Restart Trino, "
                "re-run a proper warm-up, and try again (or pass --skip-baseline-check "
                "to override, not recommended)."
            )

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    write_header = not os.path.exists(out_path)

    schedulers = ALL_SCHEDULERS
    if args.schedulers:
        schedulers = [s for s in ALL_SCHEDULERS if s.name in args.schedulers]
        missing = set(args.schedulers) - {s.name for s in schedulers}
        if missing:
            raise SystemExit(f"Unknown scheduler name(s): {sorted(missing)} - valid names: {[s.name for s in ALL_SCHEDULERS]}")
        print(f"Restricting to schedulers: {[s.name for s in schedulers]}")

    rng = random.Random(args.seed)
    scheduler_batch_scores = {s.name: [] for s in schedulers}

    if args.skip_batches:
        print(f"Skipping {args.skip_batches} already-recorded batches (RNG advance only, no execution) ...")
        for _ in range(args.skip_batches):
            generate_live_batch(
                rng, (args.batch_size_min, args.batch_size_max),
                predicted_static, predicted_adaptive, args.condition,
            )

    with open(out_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()

        for batch_idx in range(args.skip_batches, args.skip_batches + args.n_batches):
            batch_id = f"live_{args.condition}_{batch_idx}_{int(time.time())}"
            base_queries = generate_live_batch(
                rng, (args.batch_size_min, args.batch_size_max),
                predicted_static, predicted_adaptive, args.condition,
            )

            for scheduler in schedulers:
                # Fresh copies per scheduler so one scheduler's live
                # actual_runtime_ms never leaks into another's.
                queries = [replace(q) for q in base_queries]
                result = scheduler.order(queries)

                cumulative = 0.0
                for pos, q in enumerate(result.order):
                    live_query_id = CLASS_TO_QUERY_ID[q.query_class]
                    # poll_ingestion=False: CSV_FIELDS above never captures
                    # run_once()'s per-query overlap telemetry (that's a
                    # different schema from query_timing_harness.py's own
                    # CSV), so polling here was pure overhead - each poll
                    # blocks on a real HTTP call (up to 5s timeout each) with
                    # nothing to show for it. Diagnosed 2026-09-22: this was
                    # responsible for the bulk of an 8-scheduler run's total
                    # wall-clock time (elapsed time was ~3.5x the sum of
                    # actual_runtime_ms). If per-query overlap telemetry is
                    # ever wanted here, add the fields to CSV_FIELDS AND flip
                    # this back on - don't pay the cost without the data.
                    exec_result = run_once(conn, live_query_id, poll_ingestion=False)
                    q.actual_runtime_ms = exec_result["runtime_ms"]
                    cumulative += q.actual_runtime_ms
                    sla_met = cumulative <= q.deadline_ms

                    writer.writerow({
                        "batch_id": batch_id,
                        "scheduler": scheduler.name,
                        "condition": args.condition,
                        "position": pos,
                        "query_id": q.query_id,
                        "query_class": q.query_class,
                        "sla_tier": q.sla_tier,
                        "priority": q.priority,
                        "deadline_ms": q.deadline_ms,
                        "predicted_static_ms": q.predicted_static_ms,
                        "predicted_adaptive_ms": q.predicted_adaptive_ms,
                        "actual_runtime_ms": q.actual_runtime_ms,
                        "cumulative_elapsed_ms": round(cumulative, 2),
                        "sla_met": sla_met,
                        "run_id": f"{live_query_id}_{scheduler.name}_{batch_id}_{pos}",
                        "start_time": exec_result["start_time"],
                        "end_time": exec_result["end_time"],
                        "error": exec_result["error"],
                    })
                    f.flush()

                scored = score_order(result.order)
                scheduler_batch_scores[scheduler.name].append(scored)
                print(
                    f"[batch {batch_idx + 1}/{args.skip_batches + args.n_batches}] {scheduler.name}: "
                    f"{scored['n_met']}/{scored['n']} met, "
                    f"weighted={scored['weighted_score']}/{scored['max_possible_weighted']}"
                )

                if args.sleep_between_runs:
                    time.sleep(args.sleep_between_runs)

    print(f"\nWrote per-query rows to {out_path}")
    print(f"\n=== Summary (condition={args.condition} only) ===")
    for scheduler in schedulers:
        batches = scheduler_batch_scores[scheduler.name]
        total_n = sum(b["n"] for b in batches)
        total_met = sum(b["n_met"] for b in batches)
        total_w = sum(b["weighted_score"] for b in batches)
        total_max_w = sum(b["max_possible_weighted"] for b in batches)
        print(
            f"  {scheduler.name}: SLA adherence {total_met / total_n * 100:.1f}%  "
            f"weighted {total_w / total_max_w * 100:.1f}%  (n={total_n})"
        )


if __name__ == "__main__":
    main()
