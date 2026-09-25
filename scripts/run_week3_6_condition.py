"""Week 3.6 condition driver: warm-up gate (if target_eps > 0) + all 3
query-class blocks for one ingestion condition, in a single process.

The producer itself is started/stopped externally (PowerShell, per the
project's existing pattern) - this script only waits for it to stabilize
and then runs the timed query blocks, so it can't accidentally leave a
producer process orphaned if it errors out.

Usage:
  # none condition - no producer, no warm-up gate
  .venv/Scripts/python.exe run_week3_6_condition.py --condition none --target-eps 0

  # sustained conditions - start the producer first (see plan doc), then:
  .venv/Scripts/python.exe run_week3_6_condition.py --condition moderate_sustained --target-eps 1000
  .venv/Scripts/python.exe run_week3_6_condition.py --condition heavy_sustained --target-eps 1700
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from ingestion_client import wait_for_stable_rate  # noqa: E402
from query_timing_harness import CATALOG, HOST, PORT, QUERIES, QUERY_CLASS, USER, run_once  # noqa: E402
import csv  # noqa: E402
import time  # noqa: E402

import trino  # noqa: E402

QUERY_CLASS_SAMPLE_SIZES = {
    "tpch_point_lookup": 30,   # short
    "tpch_orders_sum": 30,     # medium
    "tpch_orders_lineitem_join": 45,  # long - Week 3 showed highest variance
}

CSV_FIELDS = [
    "run_id", "query_id", "query_class", "condition", "run_index",
    "start_time", "end_time", "runtime_ms", "rows_returned",
    "cache_state", "ingestion_rate", "sla_tier", "scan_scope_frozen",
    "ingestion_target_eps", "ingestion_actual_eps", "ingestion_events_during_query",
    "ingestion_active_at_query_start", "ingestion_active_at_query_end",
    "query_ingestion_overlap_pct", "error",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--condition", required=True, choices=["none", "moderate_sustained", "heavy_sustained"])
    parser.add_argument("--target-eps", type=float, required=True)
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "results", "week3_6", "dataset.csv"))
    parser.add_argument("--n-short", type=int, default=QUERY_CLASS_SAMPLE_SIZES["tpch_point_lookup"])
    parser.add_argument("--n-medium", type=int, default=QUERY_CLASS_SAMPLE_SIZES["tpch_orders_sum"])
    parser.add_argument("--n-long", type=int, default=QUERY_CLASS_SAMPLE_SIZES["tpch_orders_lineitem_join"])
    args = parser.parse_args()

    sample_sizes = {
        "tpch_point_lookup": args.n_short,
        "tpch_orders_sum": args.n_medium,
        "tpch_orders_lineitem_join": args.n_long,
    }

    if args.target_eps > 0:
        print(f"Waiting for ingestion rate to stabilize within 10% of {args.target_eps} eps ...")
        snap = wait_for_stable_rate(args.target_eps, tolerance=0.10, consecutive_required=3, poll_interval=1.0, timeout=90)
        print(f"Stable: rolling_eps={snap['currentRollingEps']:.1f} (target={args.target_eps})")

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    write_header = not os.path.exists(out_path)

    conn = trino.dbapi.connect(host=HOST, port=PORT, user=USER, catalog=CATALOG)

    # Warm-up pass: throwaway, not recorded. Diagnosed 2026-08-25 - a cold
    # connection/session produces erratic, elevated latency for whichever
    # condition runs first in a session (confirmed: same data, same query,
    # only difference is warm vs cold connection - warmed stdev 45.7 vs
    # unwarmed stdev 223.6 on an identical block). Every condition gets one,
    # not just the first, so warm-up state can't itself become a confound
    # between conditions.
    print("Warm-up pass (12 throwaway queries, not recorded) ...")
    for _ in range(4):
        for qid in sample_sizes:
            cur = conn.cursor()
            cur.execute(QUERIES[qid])
            cur.fetchall()
    print("Warm-up complete.")

    overlap_warnings = 0
    total_runs = 0

    with open(out_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()

        for query_id, n_runs in sample_sizes.items():
            print(f"\n=== {QUERY_CLASS[query_id]} ({query_id}), n={n_runs} ===")
            for i in range(1, n_runs + 1):
                # Always poll, even for target_eps=0 - a measured "zero ingestion
                # activity" is a stronger claim than an assumed one, and the
                # smoke test showed polling adds no measurable timing bias.
                result = run_once(conn, query_id, poll_ingestion=True)
                total_runs += 1
                run_id = f"{query_id}_{args.condition}_{i}_{int(time.time())}"
                row = {
                    "run_id": run_id,
                    "query_id": query_id,
                    "query_class": QUERY_CLASS[query_id],
                    "condition": args.condition,
                    "run_index": i,
                    "cache_state": "unknown",
                    "ingestion_rate": args.condition,
                    "sla_tier": "none",
                    "scan_scope_frozen": True,
                    "ingestion_target_eps": args.target_eps,
                    **result,
                }
                writer.writerow(row)
                f.flush()

                status = "ERROR" if row["error"] else f"{row['runtime_ms']} ms"
                overlap = row.get("query_ingestion_overlap_pct", "")
                if args.target_eps > 0 and overlap != "" and overlap < 90:
                    overlap_warnings += 1
                    print(f"[{i}/{n_runs}] {status}  overlap={overlap}%  <-- LOW OVERLAP")
                else:
                    print(f"[{i}/{n_runs}] {status}" + (f"  overlap={overlap}%" if overlap != "" else ""))

                if i < n_runs:
                    time.sleep(args.sleep)

    print(f"\nWrote {total_runs} rows to {out_path}")
    if args.target_eps > 0:
        print(f"Runs with <90% ingestion overlap: {overlap_warnings}/{total_runs}")


if __name__ == "__main__":
    main()
