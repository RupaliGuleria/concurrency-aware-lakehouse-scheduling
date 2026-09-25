"""Live Trino validation for multi-query schedulers, keyed on REAL query
CONCURRENCY - the actual paper pivot (week4_scheduler_design.md's v1.1
addendum), unlike run_live_multi_query_validation.py which is still
ingestion-condition-keyed (see week4_next_steps.md item 2: "needs a
genuinely new design: real background concurrent load running during the
scheduler's own batch execution, not just a different prediction-table
lookup").

Design, per the 2026-09-23 plan (research-plan/week4_paper_framing.md):
background concurrent load is generated as IN-PROCESS daemon threads (a
threading.Event stop flag), not an external subprocess - after today's
producer-process cleanup incident (an external `timeout ... bash -c
'while true...'` loop survived TaskStop and kept respawning for several
minutes), an in-process design has no orphan-process risk at all: the
threads live and die with this script's own lifetime.

predicted_adaptive_ms comes from the SAME concurrency-tier tables as
simulate_multi_query_scheduling.py (build_prediction_tables /
load_concurrency_pool), not the ingestion-condition tables the older live
script uses - this is what actually tests the concurrency-aware claim
live, not just a live sanity check under a relabeled condition.

Usage:
  # low tier - no background load, directly comparable to concurrency=1
  .venv/Scripts/python.exe run_live_concurrency_validation.py --tier low --n-batches 5

  # moderate/high - background threads fire continuously during each
  # batch's serial scheduler execution
  .venv/Scripts/python.exe run_live_concurrency_validation.py --tier moderate --n-batches 5
  .venv/Scripts/python.exe run_live_concurrency_validation.py --tier high --n-batches 5
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys
import threading
import time
from dataclasses import replace

sys.path.insert(0, os.path.dirname(__file__))

import trino  # noqa: E402

from query_timing_harness import CATALOG, HOST, PORT, QUERIES, QUERY_CLASS, USER  # noqa: E402
from multi_query_schedulers import ALL_SCHEDULERS, MultiQuery, deadline_ms, score_order  # noqa: E402
from simulate_multi_query_scheduling import (  # noqa: E402
    CONCURRENCY_TIERS, QUERY_CLASSES, build_prediction_tables, load_concurrency_pool, load_pools,
)
from ryzen_health_check import DEFAULT_DRIFT_TOLERANCE, check_baseline_drift, check_health, print_baseline_drift, wait_until_healthy  # noqa: E402

CLASS_TO_QUERY_ID = {v: k for k, v in QUERY_CLASS.items()}
QUERY_IDS = list(QUERIES.keys())

SLA_TIERS = ("relaxed", "moderate", "tight")
PRIORITIES = (1, 2, 3)

DEFAULT_MODERATE_THREADS = 6
DEFAULT_HIGH_THREADS = 15

CSV_FIELDS = [
    "batch_id", "scheduler", "tier", "background_threads", "position", "query_id",
    "query_class", "sla_tier", "priority", "deadline_ms",
    "predicted_static_ms", "predicted_adaptive_ms", "actual_runtime_ms",
    "cumulative_elapsed_ms", "sla_met", "run_id", "start_time", "end_time",
    "error",
]


class BackgroundLoad:
    """Continuous background query load as in-process daemon threads - no
    external subprocess, so nothing can outlive this script the way
    today's producer loop did. Each thread owns one Trino connection for
    its whole life (reconnects on error), firing a random query
    class/id in a tight loop until stop() is set."""

    def __init__(self, n_threads: int):
        self.n_threads = n_threads
        self._stop = threading.Event()
        self._threads = []
        self._query_count = 0
        self._lock = threading.Lock()

    def _worker(self):
        conn = None
        while not self._stop.is_set():
            try:
                if conn is None:
                    conn = trino.dbapi.connect(host=HOST, port=PORT, user=USER, catalog=CATALOG, http_scheme="http")
                qid = random.choice(QUERY_IDS)
                cur = conn.cursor()
                cur.execute(QUERIES[qid])
                cur.fetchall()
                with self._lock:
                    self._query_count += 1
            except Exception:  # noqa: BLE001 - a background query failing shouldn't kill the thread or the run
                conn = None  # force reconnect next iteration
                time.sleep(0.2)

    def start(self):
        if self.n_threads <= 0:
            return
        self._stop.clear()
        self._threads = [threading.Thread(target=self._worker, daemon=True) for _ in range(self.n_threads)]
        for t in self._threads:
            t.start()

    def stop(self, join_timeout: float = 5.0) -> int:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=join_timeout)
        self._threads = []
        return self._query_count


TIER_TO_THREADS = {}  # populated in main() from CLI args


def generate_live_batch(rng, n_range, predicted_static, predicted_adaptive, tier) -> list:
    n = rng.randint(*n_range)
    queries = []
    for i in range(n):
        cls = rng.choice(QUERY_CLASSES)
        sla_tier = rng.choice(SLA_TIERS)
        priority = rng.choice(PRIORITIES)
        queries.append(MultiQuery(
            query_id=f"q{i}",
            query_class=cls,
            sla_tier=sla_tier,
            priority=priority,
            arrival_index=i,
            deadline_ms=deadline_ms(cls, sla_tier),
            predicted_static_ms=predicted_static[cls],
            predicted_adaptive_ms=predicted_adaptive[(cls, tier)],
            actual_runtime_ms=0.0,  # unknown until live execution
        ))
    return queries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tier", required=True, choices=CONCURRENCY_TIERS)
    parser.add_argument("--n-batches", type=int, default=5)
    parser.add_argument("--batch-size-min", type=int, default=4)
    parser.add_argument("--batch-size-max", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--moderate-threads", type=int, default=DEFAULT_MODERATE_THREADS)
    parser.add_argument("--high-threads", type=int, default=DEFAULT_HIGH_THREADS)
    parser.add_argument("--schedulers", nargs="+",
                         default=["static_least_slack", "adaptive_least_slack", "dp_oracle", "admission_controlled_dp"],
                         help="Restrict to these scheduler names.")
    parser.add_argument("--skip-batches", type=int, default=0)
    parser.add_argument("--sleep-between-runs", type=float, default=1.0,
                         help="Pause between scheduler runs / batches, NOT between queries within a run")
    parser.add_argument("--cooldown-between-batches", type=float, default=5.0,
                         help="Extra pause after stopping background load, before the next batch starts")
    parser.add_argument("--health-check-every", type=int, default=3,
                         help="Re-check Ryzen health every N batches, abort remaining batches if unhealthy")
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "results", "week4", "live_concurrency_validation.csv"))
    parser.add_argument("--skip-health-check", action="store_true")
    parser.add_argument("--skip-baseline-check", action="store_true",
                         help="Skip the pre-flight uncontended-baseline-drift check (not recommended - "
                              "see research-plan/week4_paper_framing.md's experimental-control note).")
    parser.add_argument("--baseline-drift-tolerance", type=float, default=DEFAULT_DRIFT_TOLERANCE)
    args = parser.parse_args()

    threads_for_tier = {"low": 0, "moderate": args.moderate_threads, "high": args.high_threads}
    n_threads = threads_for_tier[args.tier]

    if not args.skip_health_check:
        if not wait_until_healthy():
            raise SystemExit("Ryzen box failed the pre-flight health check - aborting.")

    pool = load_pools()
    concurrency_pool = load_concurrency_pool(pool)
    predicted_static, predicted_adaptive = build_prediction_tables(pool, concurrency_pool)

    conn = trino.dbapi.connect(host=HOST, port=PORT, user=USER, catalog=CATALOG, http_scheme="http")

    print(f"Tier={args.tier} -> {n_threads} background threads. Warm-up pass (12 throwaway queries) ...")
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
                "Uncontended baseline has drifted beyond tolerance - aborting rather than "
                "collecting data against invalid deadlines. Restart Trino, re-run a proper "
                "warm-up, and try again (or pass --skip-baseline-check to override, not recommended)."
            )

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    write_header = not os.path.exists(out_path)

    schedulers = [s for s in ALL_SCHEDULERS if s.name in args.schedulers]
    missing = set(args.schedulers) - {s.name for s in schedulers}
    if missing:
        raise SystemExit(f"Unknown scheduler name(s): {sorted(missing)}")
    print(f"Restricting to schedulers: {[s.name for s in schedulers]}")

    rng = random.Random(args.seed)
    scheduler_batch_scores = {s.name: [] for s in schedulers}

    if args.skip_batches:
        for _ in range(args.skip_batches):
            generate_live_batch(rng, (args.batch_size_min, args.batch_size_max), predicted_static, predicted_adaptive, args.tier)

    with open(out_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()

        for batch_idx in range(args.skip_batches, args.skip_batches + args.n_batches):
            if args.health_check_every and (batch_idx - args.skip_batches) % args.health_check_every == 0:
                h = check_health()
                if not h["healthy"]:
                    print(f"[health check] Ryzen unhealthy mid-run ({h['error']}) - stopping before batch {batch_idx + 1}, NOT starting more background load.")
                    break
                print(f"[health check] OK before batch {batch_idx + 1} ({h['latency_ms']}ms)")

            batch_id = f"liveconc_{args.tier}_{batch_idx}_{int(time.time())}"
            base_queries = generate_live_batch(rng, (args.batch_size_min, args.batch_size_max), predicted_static, predicted_adaptive, args.tier)

            bg = BackgroundLoad(n_threads)
            bg.start()
            try:
                for scheduler in schedulers:
                    queries = [replace(q) for q in base_queries]
                    result = scheduler.order(queries)

                    cumulative = 0.0
                    for pos, q in enumerate(result.order):
                        live_query_id = CLASS_TO_QUERY_ID[q.query_class]
                        t0 = time.perf_counter()
                        start_time = time.time()
                        error = ""
                        try:
                            cur = conn.cursor()
                            cur.execute(QUERIES[live_query_id])
                            cur.fetchall()
                        except Exception as exc:  # noqa: BLE001
                            error = str(exc)
                        runtime_ms = (time.perf_counter() - t0) * 1000
                        end_time = time.time()

                        q.actual_runtime_ms = runtime_ms
                        cumulative += runtime_ms
                        sla_met = cumulative <= q.deadline_ms

                        writer.writerow({
                            "batch_id": batch_id,
                            "scheduler": scheduler.name,
                            "tier": args.tier,
                            "background_threads": n_threads,
                            "position": pos,
                            "query_id": q.query_id,
                            "query_class": q.query_class,
                            "sla_tier": q.sla_tier,
                            "priority": q.priority,
                            "deadline_ms": q.deadline_ms,
                            "predicted_static_ms": q.predicted_static_ms,
                            "predicted_adaptive_ms": q.predicted_adaptive_ms,
                            "actual_runtime_ms": round(runtime_ms, 2),
                            "cumulative_elapsed_ms": round(cumulative, 2),
                            "sla_met": sla_met,
                            "run_id": f"{live_query_id}_{scheduler.name}_{batch_id}_{pos}",
                            "start_time": start_time,
                            "end_time": end_time,
                            "error": error,
                        })
                        f.flush()

                    scored = score_order(result.order)
                    scheduler_batch_scores[scheduler.name].append(scored)
                    print(
                        f"[batch {batch_idx + 1}/{args.skip_batches + args.n_batches}] {scheduler.name} "
                        f"(tier={args.tier}, bg_threads={n_threads}): "
                        f"{scored['n_met']}/{scored['n']} met, weighted={scored['weighted_score']}/{scored['max_possible_weighted']}"
                    )
                    if args.sleep_between_runs:
                        time.sleep(args.sleep_between_runs)
            finally:
                bg_queries_run = bg.stop()
                print(f"  (background load stopped - {bg_queries_run} background queries ran during this batch)")

            if args.cooldown_between_batches:
                time.sleep(args.cooldown_between_batches)

    print(f"\nWrote per-query rows to {out_path}")
    print(f"\n=== Summary (tier={args.tier} only) ===")
    for scheduler in schedulers:
        batches = scheduler_batch_scores[scheduler.name]
        if not batches:
            continue
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
