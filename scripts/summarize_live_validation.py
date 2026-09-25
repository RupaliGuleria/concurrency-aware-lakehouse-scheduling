"""Aggregates results/week4/live_validation.csv (written by
run_live_multi_query_validation.py, one row per query per scheduler per
batch per condition) into a markdown report, same shape as
simulation_report.md so the two are easy to compare side by side.

Usage:
  .venv/Scripts/python.exe summarize_live_validation.py
"""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime, timezone

from multi_query_schedulers import ALL_SCHEDULERS, PRIORITY_WEIGHTS

IN_PATH = "../results/week4/live_validation.csv"
OUT_PATH = "../results/week4/live_validation_report.md"

CONDITIONS = ("none", "moderate_sustained", "heavy_sustained")
QUERY_CLASSES = ("short", "medium", "long")
SLA_TIERS = ("relaxed", "moderate", "tight")
PRIORITIES = ("1", "2", "3")


def pct(x) -> str:
    if x != x or x is None:
        return "-"
    return f"{x * 100:.1f}%"


def main() -> None:
    rows = []
    with open(IN_PATH, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("error"):
                continue
            rows.append(r)

    # overall[scheduler] -> list of rows; by_condition[scheduler][condition] -> list of rows
    overall = defaultdict(list)
    by_condition = defaultdict(lambda: defaultdict(list))
    by_class = defaultdict(lambda: defaultdict(list))
    by_tier = defaultdict(lambda: defaultdict(list))
    by_priority = defaultdict(lambda: defaultdict(list))
    n_batches_seen = defaultdict(lambda: defaultdict(set))

    for r in rows:
        sched = r["scheduler"]
        cond = r["condition"]
        overall[sched].append(r)
        by_condition[sched][cond].append(r)
        by_class[sched][r["query_class"]].append(r)
        by_tier[sched][r["sla_tier"]].append(r)
        by_priority[sched][r["priority"]].append(r)
        n_batches_seen[sched][cond].add(r["batch_id"])

    def weighted_pair(rowset):
        weighted = 0
        max_weighted = 0
        for r in rowset:
            w = PRIORITY_WEIGHTS[int(r["priority"])]
            max_weighted += w
            if r["sla_met"] == "True":
                weighted += w
        return weighted, max_weighted

    def met_pair(rowset):
        n = len(rowset)
        n_met = sum(1 for r in rowset if r["sla_met"] == "True")
        return n_met, n

    lines = []
    lines.append("# Week 4 — live Trino multi-query scheduler validation")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d')} by "
        "`scripts/summarize_live_validation.py` from "
        "`results/week4/live_validation.csv`, produced by "
        "`scripts/run_live_multi_query_validation.py` — design doc item 4 "
        "(\"live Trino validation stage ... once scheduler behavior is "
        "stable\"), run against the live Trino coordinator."
    )
    lines.append("")
    lines.append(
        "**Caveat, unlike the bootstrap simulation**: each scheduler's "
        "execution of \"the same\" batch (same query_class/sla_tier/"
        "priority/deadline) is a SEPARATE live run against Trino, so each "
        "scheduler sees its own fresh `actual_runtime_ms` sample rather "
        "than all 7 schedulers sharing one ground-truth sample the way "
        "`results/week4/simulation_report.md` does. This is a live sanity "
        "check that scheduler behavior transfers off simulation, not a "
        "controlled paired comparison — read the numbers with that in mind."
    )
    lines.append("")

    # Test-setup / data-volume stats, computed from the CSV itself so this
    # stays accurate on regeneration.
    batch_sizes = defaultdict(int)
    for r in rows:
        batch_sizes[(r["condition"], r["scheduler"], r["batch_id"])] += 1
    n_batches_per_condition = {
        cond: len({bid for (c, s, bid) in batch_sizes if c == cond and s == "fifo"})
        for cond in CONDITIONS
    }
    sizes_list = list(batch_sizes.values())
    n_live_executions = len(rows)
    n_batch_scheduler_runs = len(batch_sizes)

    lines.append("## Test setup — data volume & ingestion")
    lines.append("")
    lines.append(
        f"**Synthetic batches**: {sum(n_batches_per_condition.values())} total "
        f"({', '.join(f'{n} {cond}' for cond, n in n_batches_per_condition.items())}), "
        f"batch size {min(sizes_list)}-{max(sizes_list)} queries "
        f"(mean {sum(sizes_list) / len(sizes_list):.1f}). Each batch executed once "
        f"per scheduler ({n_batch_scheduler_runs} batch-scheduler runs), for "
        f"**{n_live_executions} live query executions against Trino** total."
    )
    lines.append("")
    lines.append(
        "**Underlying TPC-H data queried** (SF 0.1, `hive.events.raw_events`, "
        "verified live via `SELECT count(*) ... GROUP BY schema_id` this "
        "session): `tpch_orders_v1` 150,000 rows, `tpch_lineitem_v1` 600,572 "
        "rows (750,572 combined). Fixed for the whole session — no TPC-H data "
        "was added or removed between conditions. The three query templates "
        "(`scripts/query_timing_harness.py`) map 1:1 to `query_class`: "
        "`short` = point lookup on `tpch_orders_v1` (single `o_orderkey`), "
        "`medium` = full aggregation over `tpch_orders_v1`, `long` = a join "
        "across both tables with per-row JSON field extraction — the JSON "
        "parsing cost, not table size, is why `long` is the slow class "
        "despite querying a fixed, modest row count."
    )
    lines.append("")
    lines.append(
        "**Ingestion load** (`banking_producer`, TPC-H-unrelated banking "
        "transaction events, `ingestion-pipeline/run-producers.ps1 -Profile "
        "banking`, one CSV cycle = 550,000 rows replayed through local Kafka "
        "+ the local ingestion service):"
    )
    lines.append("")
    lines.append("| Condition | Target eps | Achieved eps (observed) | Approx. events sent |")
    lines.append("|---|--:|--:|--:|")
    lines.append("| none | 0 | 0 (no producer running) | 0 |")
    lines.append("| moderate_sustained | 1,000 | ~1,000-1,020, stable | ~960,000 (1 partial + 1 full CSV cycle) |")
    lines.append("| heavy_sustained | 1,700 | ~1,620-1,720 after reducing local CPU load (first attempt capped at ~1,200-1,250 under CPU contention and was discarded) | ~1,810,000 (2 full CSV cycles + partial 3rd, self-restarting producer loop) |")
    lines.append("")
    lines.append(
        "**Why the achieved rate lagged target for heavy_sustained at first**: "
        "local CPU load was 66-77% (Zoom, Creative Cloud, and other background "
        "apps competing with the producer process and local Docker/Kafka for "
        "CPU) — the same failure mode `research-plan/local_machine_"
        "preflight_checklist.md` documented in an earlier session. Closing "
        "background apps dropped CPU to 15-30% and the producer reached target."
    )
    lines.append("")
    lines.append(
        "**Important scope note — this is contention load, not scan-volume "
        "growth**: verified live this session that `hive.events.raw_events` "
        "has **zero** `banking_transaction_v1` rows (`SELECT count(*) ... "
        "WHERE schema_id = 'banking_transaction_v1'` returns 0). The banking "
        "events are real Kafka/ingestion-service/MinIO write traffic — the "
        "producer, Kafka broker, and ingestion service genuinely process "
        "1,000-1,700 events/sec — but they land in a separate data path from "
        "the TPC-H tables the scheduler's queries scan, and "
        "`scan_scope_frozen` (the harness's default) means Trino's view of "
        "`raw_events` doesn't change mid-run regardless. So `ingestion_"
        "condition` here specifically measures **shared-infrastructure "
        "resource contention** (CPU, I/O, network) during query execution, "
        "not \"more data to scan.\" That's a deliberate v1 methodology "
        "choice (isolates contention from volume growth as separate "
        "variables), not an accident — but it's also very likely why the "
        "condition's effect on runtime looks small in both this live data "
        "and the simulation: at this hardware's headroom, 1,000-1,700 "
        "events/sec of writes to an unrelated table isn't enough contention "
        "to meaningfully slow down reads against a fixed 750,572-row table. "
        "This is the same conclusion `results/week3_6_counterbalanced_"
        "findings.md` reached from single-query timing data (no significant "
        "ingestion effect once session position is controlled for) and "
        "`results/week4/simulation_report.md`'s diagnostic reached from "
        "bootstrap replay (2-21ms predicted-runtime shifts, dwarfed by "
        "hundreds-to-thousands of ms of slack spread between queries) — this "
        "live run is a third, independent confirmation of the same finding, "
        "now with a concrete architectural reason why."
    )
    lines.append("")
    lines.append("## Main comparison (all conditions pooled)")
    lines.append("")
    lines.append("| Scheduler | SLA adherence | Weighted SLA adherence | n queries |")
    lines.append("|---|--:|--:|--:|")
    for scheduler in ALL_SCHEDULERS:
        rowset = overall[scheduler.name]
        if not rowset:
            continue
        n_met, n = met_pair(rowset)
        w, max_w = weighted_pair(rowset)
        lines.append(
            f"| {scheduler.name} | {pct(n_met / n if n else float('nan'))} | "
            f"{pct(w / max_w if max_w else float('nan'))} | {n} |"
        )
    lines.append("")

    lines.append("## By ingestion condition")
    lines.append("")
    lines.append("| Scheduler | Condition | n batches | n queries | SLA adherence | Weighted SLA adherence |")
    lines.append("|---|---|--:|--:|--:|--:|")
    for scheduler in ALL_SCHEDULERS:
        for cond in CONDITIONS:
            rowset = by_condition[scheduler.name][cond]
            if not rowset:
                continue
            n_met, n = met_pair(rowset)
            w, max_w = weighted_pair(rowset)
            n_b = len(n_batches_seen[scheduler.name][cond])
            lines.append(
                f"| {scheduler.name} | {cond} | {n_b} | {n} | "
                f"{pct(n_met / n if n else float('nan'))} | "
                f"{pct(w / max_w if max_w else float('nan'))} |"
            )
    lines.append("")

    lines.append("## Breakdown by query class")
    lines.append("")
    lines.append("| Scheduler | Class | n | SLA adherence |")
    lines.append("|---|---|--:|--:|")
    for scheduler in ALL_SCHEDULERS:
        for cls in QUERY_CLASSES:
            rowset = by_class[scheduler.name][cls]
            n_met, n = met_pair(rowset)
            lines.append(f"| {scheduler.name} | {cls} | {n} | {pct(n_met/n) if n else '-'} |")
    lines.append("")

    lines.append("## Breakdown by SLA tier")
    lines.append("")
    lines.append("| Scheduler | Tier | n | SLA adherence |")
    lines.append("|---|---|--:|--:|")
    for scheduler in ALL_SCHEDULERS:
        for tier in SLA_TIERS:
            rowset = by_tier[scheduler.name][tier]
            n_met, n = met_pair(rowset)
            lines.append(f"| {scheduler.name} | {tier} | {n} | {pct(n_met/n) if n else '-'} |")
    lines.append("")

    lines.append("## Breakdown by priority")
    lines.append("")
    lines.append("| Scheduler | Priority | n | SLA adherence |")
    lines.append("|---|--:|--:|--:|")
    for scheduler in ALL_SCHEDULERS:
        for p in PRIORITIES:
            rowset = by_priority[scheduler.name][p]
            n_met, n = met_pair(rowset)
            lines.append(f"| {scheduler.name} | {p} | {n} | {pct(n_met/n) if n else '-'} |")
    lines.append("")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Wrote {OUT_PATH}")
    print()
    print("\n".join(lines))


if __name__ == "__main__":
    main()
