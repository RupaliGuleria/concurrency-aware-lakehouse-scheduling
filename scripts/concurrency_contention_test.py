"""Stage 1 of the ingestion-impact investigation (see chat/research-plan
notes from 2026-09-21): tests whether CONCURRENT query execution alone
creates measurable contention on the single-node Ryzen Trino/MinIO setup -
independent of ingestion entirely. No ingestion producer running during
this test; isolates "does this hardware even have a contention ceiling
worth caring about" as its own clean question before combining with
partition-visible ingestion (stage 3).

At each concurrency level C, fires C queries at Trino SIMULTANEOUS (via a
thread pool, one Trino connection per thread - trino.dbapi connections
aren't safely shared across threads), repeated for several rounds, and
records real wall-clock runtime_ms per query. If concurrency alone
degrades latency noticeably, that's a genuine, ingestion-independent lever
for the multi-query scheduler's "why does order/scheduling matter" story.

Usage:
  .venv/Scripts/python.exe concurrency_contention_test.py
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import trino

from query_timing_harness import CATALOG, HOST, PORT, QUERIES, QUERY_CLASS, USER
from ryzen_health_check import DEFAULT_DRIFT_TOLERANCE, check_baseline_drift, print_baseline_drift, wait_until_healthy

DEFAULT_CONCURRENCY_LEVELS = (1, 2, 4, 8, 16)
DEFAULT_ROUNDS_PER_LEVEL = 4
DEFAULT_WARMUP_ROUNDS = 3
DEFAULT_COOLDOWN_S = 5.0  # pause between concurrency levels, so a high level doesn't run flat into the next
QUERY_IDS_CYCLE = list(QUERIES.keys())  # short, medium, long - cycled to mix classes each round


def warmup(rounds: int) -> None:
    """Runs and discards each query class sequentially before the timed
    sweep starts - added 2026-09-22 after a held-out collection attempt
    showed a clear cold-start curve (3122ms -> 1788ms -> 546ms -> 695ms ->
    867ms across 5 sequential single-query runs on an idle system) that had
    nothing to do with resource contention. The block/margin-tuning
    collection this project's calibration data comes from was already
    "warm-up-fixed" (see week4_scheduler_design.md's data source note);
    this script wasn't, which is a real gap when the system has been idle
    before the sweep starts (e.g. a fresh held-out collection run)."""
    print(f"=== Warm-up ({rounds} rounds per query class, discarded) ===")
    for _ in range(rounds):
        for qid in QUERY_IDS_CYCLE:
            result = run_one(qid)
            if result["error"]:
                print(f"  warm-up error on {qid}: {result['error']}")
    print("=== Warm-up done ===\n")


def run_one(query_id: str) -> dict:
    conn = trino.dbapi.connect(host=HOST, port=PORT, user=USER, catalog=CATALOG)
    cur = conn.cursor()
    t0 = time.perf_counter()
    error = ""
    rows = 0
    try:
        cur.execute(QUERIES[query_id])
        rows = len(cur.fetchall())
    except Exception as exc:  # noqa: BLE001 - capture, don't crash the sweep
        error = str(exc)
    runtime_ms = (time.perf_counter() - t0) * 1000
    return {"query_id": query_id, "query_class": QUERY_CLASS[query_id], "runtime_ms": round(runtime_ms, 2), "rows": rows, "error": error}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "results", "week4", "concurrency_contention.csv"))
    parser.add_argument("--levels", type=int, nargs="+", default=list(DEFAULT_CONCURRENCY_LEVELS))
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS_PER_LEVEL)
    parser.add_argument("--label", default="no_ingestion", help="Recorded as a 'label' column, e.g. no_ingestion / combined_with_ingestion")
    parser.add_argument("--warmup-rounds", type=int, default=DEFAULT_WARMUP_ROUNDS, help="Discarded rounds run before the timed sweep, to avoid cold-cache/cold-connection bias on an idle system. 0 to disable.")
    parser.add_argument("--cooldown", type=float, default=DEFAULT_COOLDOWN_S, help="Seconds to pause between concurrency levels, so the box gets a breather before the next escalation. 0 to disable.")
    parser.add_argument("--skip-health-check", action="store_true", help="Skip the pre-flight Ryzen health check (not recommended - added 2026-09-23 after repeated back-to-back sweeps likely contributed to the box going down).")
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

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    if args.warmup_rounds > 0:
        warmup(args.warmup_rounds)

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

    rows_out = []
    print(f"=== Concurrency contention sweep (label={args.label}) ===\n")

    for level_idx, level in enumerate(args.levels):
        if level_idx > 0 and args.cooldown > 0:
            print(f"(cooldown {args.cooldown:.0f}s before concurrency={level})")
            time.sleep(args.cooldown)
        level_runtimes = {qid: [] for qid in QUERY_IDS_CYCLE}
        wall_clock_per_round = []
        for round_idx in range(args.rounds):
            query_ids_this_round = [QUERY_IDS_CYCLE[i % len(QUERY_IDS_CYCLE)] for i in range(level)]
            round_start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=level) as pool:
                futures = [pool.submit(run_one, qid) for qid in query_ids_this_round]
                for fut in as_completed(futures):
                    result = fut.result()
                    result["concurrency"] = level
                    result["round"] = round_idx
                    result["label"] = args.label
                    result["timestamp"] = datetime.now(timezone.utc).isoformat()
                    rows_out.append(result)
                    if not result["error"]:
                        level_runtimes[result["query_id"]].append(result["runtime_ms"])
            wall_clock_per_round.append((time.perf_counter() - round_start) * 1000)

        print(f"concurrency={level}:")
        for qid in QUERY_IDS_CYCLE:
            vals = level_runtimes[qid]
            if vals:
                print(f"  {qid:28s} n={len(vals):3d}  mean={statistics.mean(vals):7.1f}ms  "
                      f"median={statistics.median(vals):7.1f}ms  max={max(vals):7.1f}ms")
        print(f"  wall-clock per round (all {level} concurrent): mean={statistics.mean(wall_clock_per_round):.1f}ms  "
              f"max={max(wall_clock_per_round):.1f}ms")
        print()

    fieldnames = ["label", "concurrency", "round", "query_id", "query_class", "runtime_ms", "rows", "error", "timestamp"]
    write_header = not os.path.exists(out_path)
    with open(out_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(rows_out)
    print(f"Wrote {len(rows_out)} rows to {out_path}")


if __name__ == "__main__":
    main()
