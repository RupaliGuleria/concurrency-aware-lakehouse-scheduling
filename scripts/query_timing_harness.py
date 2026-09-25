"""Checkpoint-1 harness: run one fixed query N times against Trino and log
per-run timing to CSV. Set TRINO_HOST to point at your own Trino coordinator
(default: localhost).

Used two ways:
  1. Smoke test — same query, 10x, everything else held constant. Establishes
     the natural runtime noise floor before any factor comparison means
     anything (see research-plan/scheduler_scope_and_infra_notes.md).
  2. Checkpoint 1 — run once with --condition none (nothing else running),
     then again with --condition heavy_burst while a producer profile is
     ingesting (`ingestion-pipeline/run-producers.ps1`). Compare runtime_ms
     between the two condition labels in the output CSV.

--cache-state and --ingestion-rate are manually-attested labels for now
(no automated telemetry yet — that's a later task); they exist so the CSV
schema doesn't need to change when automated collection is added.

Usage:
  .venv/Scripts/python.exe query_timing_harness.py --condition none --runs 10
  .venv/Scripts/python.exe query_timing_harness.py --condition heavy_burst --runs 10 --ingestion-rate heavy_burst
"""
from __future__ import annotations

import argparse
import csv
import os
import threading
import time
from datetime import datetime, timezone

import trino

from ingestion_client import poll_snapshot

HOST = os.environ.get("TRINO_HOST", "localhost")
PORT = 8080
USER = "lakehouse-scheduler-research"
CATALOG = "hive"

QUERIES = {
    "tpch_point_lookup": """
        SELECT
            count(*) AS n_orders,
            sum(CAST(json_extract_scalar(payload_json, '$.o_totalprice') AS DOUBLE)) AS total_price
        FROM hive.events.raw_events
        WHERE schema_id = 'tpch_orders_v1'
          AND json_extract_scalar(payload_json, '$.o_orderkey') = '1'
    """,
    "tpch_orders_sum": """
        SELECT
            count(*) AS n_orders,
            sum(CAST(json_extract_scalar(payload_json, '$.o_totalprice') AS DOUBLE)) AS total_price
        FROM hive.events.raw_events
        WHERE schema_id = 'tpch_orders_v1'
    """,
    "tpch_orders_lineitem_join": """
        SELECT
            count(*) AS n_lines,
            sum(l.l_extendedprice * (1 - l.l_discount)) AS total_revenue
        FROM
          (SELECT
               json_extract_scalar(payload_json, '$.o_orderkey') AS o_orderkey
           FROM hive.events.raw_events
           WHERE schema_id = 'tpch_orders_v1') o
        JOIN
          (SELECT
               json_extract_scalar(payload_json, '$.l_orderkey') AS l_orderkey,
               CAST(json_extract_scalar(payload_json, '$.l_extendedprice') AS DOUBLE) AS l_extendedprice,
               CAST(json_extract_scalar(payload_json, '$.l_discount') AS DOUBLE) AS l_discount
           FROM hive.events.raw_events
           WHERE schema_id = 'tpch_lineitem_v1') l
          ON o.o_orderkey = l.l_orderkey
    """,
}

# query_id -> query_class, per research-plan/week3_testing_plan.md section 1 (A1).
QUERY_CLASS = {
    "tpch_point_lookup": "short",
    "tpch_orders_sum": "medium",
    "tpch_orders_lineitem_join": "long",
}

CSV_FIELDS = [
    "run_id",
    "query_id",
    "query_class",
    "condition",
    "run_index",
    "start_time",
    "end_time",
    "runtime_ms",
    "rows_returned",
    "cache_state",
    "ingestion_rate",
    "sla_tier",
    "scan_scope_frozen",
    "ingestion_target_eps",
    "ingestion_actual_eps",
    "ingestion_events_during_query",
    "ingestion_active_at_query_start",
    "ingestion_active_at_query_end",
    "query_ingestion_overlap_pct",
    "error",
]


def _poll_during_query(samples: list, stop_event: threading.Event, poll_interval_s: float = 0.2) -> None:
    """Background sampler for week3_6's overlap telemetry (section 5/9's
    "sample the endpoint every few hundred ms while the query is running").
    Runs in its own thread since the main thread is blocked on the Trino
    call for the query's whole duration."""
    while not stop_event.is_set():
        try:
            samples.append(poll_snapshot())
        except Exception:  # noqa: BLE001 - a missed sample just thins the overlap estimate, not fatal
            pass
        stop_event.wait(poll_interval_s)


def run_once(conn, query_id: str, poll_ingestion: bool = False) -> dict:
    ingestion_start_snap = None
    mid_samples: list = []
    stop_event = None
    poll_thread = None

    if poll_ingestion:
        try:
            ingestion_start_snap = poll_snapshot()
        except Exception:  # noqa: BLE001
            ingestion_start_snap = None
        stop_event = threading.Event()
        poll_thread = threading.Thread(target=_poll_during_query, args=(mid_samples, stop_event), daemon=True)
        poll_thread.start()

    start = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    error = ""
    rows_returned = 0
    try:
        cur = conn.cursor()
        cur.execute(QUERIES[query_id])
        rows = cur.fetchall()
        rows_returned = len(rows)
    except Exception as exc:  # noqa: BLE001 - want any failure captured in the CSV, not a crash
        error = str(exc)
    runtime_ms = (time.perf_counter() - t0) * 1000
    end = datetime.now(timezone.utc)

    result = {
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "runtime_ms": round(runtime_ms, 2),
        "rows_returned": rows_returned,
        "error": error,
    }

    if poll_ingestion:
        stop_event.set()
        poll_thread.join(timeout=1.0)
        try:
            ingestion_end_snap = poll_snapshot()
        except Exception:  # noqa: BLE001
            ingestion_end_snap = None

        events_during = actual_eps = active_start = active_end = overlap_pct = ""
        if ingestion_start_snap and ingestion_end_snap:
            events_during = ingestion_end_snap["totalEvents"] - ingestion_start_snap["totalEvents"]
            elapsed_s = runtime_ms / 1000.0
            actual_eps = round(events_during / elapsed_s, 1) if elapsed_s > 0 else ""
            active_start = ingestion_start_snap["ingestionActive"]
            active_end = ingestion_end_snap["ingestionActive"]

        all_samples = (
            ([ingestion_start_snap] if ingestion_start_snap else [])
            + mid_samples
            + ([ingestion_end_snap] if ingestion_end_snap else [])
        )
        if all_samples:
            active_count = sum(1 for s in all_samples if s.get("ingestionActive"))
            overlap_pct = round(100.0 * active_count / len(all_samples), 1)

        result.update({
            "ingestion_actual_eps": actual_eps,
            "ingestion_events_during_query": events_during,
            "ingestion_active_at_query_start": active_start,
            "ingestion_active_at_query_end": active_end,
            "query_ingestion_overlap_pct": overlap_pct,
        })

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--condition", required=True, help="Free-text label, e.g. none, heavy_burst")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--query-id", default="tpch_orders_sum", choices=list(QUERIES))
    parser.add_argument("--cache-state", default="unknown", help="Manually-attested for now: warm/cold/unknown")
    parser.add_argument("--ingestion-rate", default="", help="Manually-attested for now, e.g. none/heavy_burst")
    parser.add_argument("--sla-tier", default="none", choices=["none", "relaxed", "moderate", "tight"],
                         help="Which SLA deadline this run is evaluated against (none until baselines exist)")
    parser.add_argument("--scan-scope-frozen", dest="scan_scope_frozen", action="store_true", default=True,
                         help="No partition sync during this run (default: true, per Checkpoint 1 discipline)")
    parser.add_argument("--scope-not-frozen", dest="scan_scope_frozen", action="store_false",
                         help="Set if a partition sync happened during this run's window")
    parser.add_argument("--sleep", type=float, default=1.0, help="Seconds between runs")
    parser.add_argument("--poll-ingestion", action="store_true",
                         help="Poll the live ingestion metrics endpoint before/during/after each query "
                              "(week3_6_sustained_ingestion_testing_plan.md sections 5/9) - requires the "
                              "local ingestion service's /internal/metrics/ingestion-snapshot endpoint")
    parser.add_argument("--target-eps", type=float, default=0.0,
                         help="Recorded as ingestion_target_eps - the producer's configured target, not measured")
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "results", "checkpoint1_timing.csv"))
    args = parser.parse_args()

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    write_header = not os.path.exists(out_path)

    conn = trino.dbapi.connect(host=HOST, port=PORT, user=USER, catalog=CATALOG)

    with open(out_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()

        for i in range(1, args.runs + 1):
            result = run_once(conn, args.query_id, poll_ingestion=args.poll_ingestion)
            run_id = f"{args.query_id}_{args.condition}_{i}_{int(time.time())}"
            row = {
                "run_id": run_id,
                "query_id": args.query_id,
                "query_class": QUERY_CLASS[args.query_id],
                "condition": args.condition,
                "run_index": i,
                "cache_state": args.cache_state,
                "ingestion_rate": args.ingestion_rate or args.condition,
                "sla_tier": args.sla_tier,
                "scan_scope_frozen": args.scan_scope_frozen,
                "ingestion_target_eps": args.target_eps if args.poll_ingestion else "",
                **result,
            }
            writer.writerow(row)
            f.flush()
            status = "ERROR" if row["error"] else f"{row['runtime_ms']} ms, {row['rows_returned']} rows"
            print(f"[{i}/{args.runs}] condition={args.condition} -> {status}")

            if i < args.runs:
                time.sleep(args.sleep)

    print(f"\nWrote results to {out_path}")


if __name__ == "__main__":
    main()
