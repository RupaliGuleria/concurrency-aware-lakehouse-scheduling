"""Demo/validation run: all four naive baseline schedulers against the
Section 3 dataset's real, already-collected runtimes.

Not a new experiment - this replays results/week3/section3_dataset.csv
through each scheduler's decide() logic to confirm they're implemented and
runnable (per week3_testing_plan.md section 7's done-criteria) and to show
what admission decisions they'd have made. sla_tier is looped over all
three values for illustration, since the actual dataset was collected with
sla_tier="none" (Section 3 didn't vary SLA tier as a factor) - runtime is
independent of which tier is being evaluated against, so this is a valid
way to preview scheduler behavior at each tier.

For the full comparison (breakdowns by query_class/condition, admit-rate
vs SLA-miss-rate trade-off, a written-up comparison table), see
evaluate_baseline_schedulers.py and
results/week3/section6_scheduler_baseline_comparison.md - this script is
just the smoke test.

Usage:
  .venv/Scripts/python.exe run_baseline_schedulers_demo.py
"""
from __future__ import annotations

import csv
from collections import defaultdict

from baseline_schedulers import (
    FifoScheduler,
    PointEstimateFixedMarginScheduler,
    PointEstimatePerClassMarginScheduler,
    PointEstimateScheduler,
    Query,
    simulate,
    summarize,
)

IN_PATH = "../results/week3/section3_dataset.csv"


def main() -> None:
    by_condition = defaultdict(list)
    actual_runtime_ms = {}
    with open(IN_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["error"]:
                continue
            by_condition[row["condition"]].append(row)
            actual_runtime_ms[row["run_id"]] = float(row["runtime_ms"])

    schedulers = (
        FifoScheduler(),
        PointEstimateScheduler(),
        PointEstimateFixedMarginScheduler(),
        PointEstimatePerClassMarginScheduler(),
    )

    for condition, rows in sorted(by_condition.items()):
        print(f"=== condition={condition} (n={len(rows)}) ===")
        for sla_tier in ("relaxed", "moderate", "tight"):
            queries = [
                Query(
                    query_id=row["run_id"],
                    query_class=row["query_class"],
                    sla_tier=sla_tier,
                    arrival_index=i,
                    condition=row["condition"],
                )
                for i, row in enumerate(rows)
            ]
            for scheduler in schedulers:
                records = simulate(scheduler, queries, actual_runtime_ms)
                summary = summarize(records)[0]
                print(f"  sla_tier={sla_tier:8s} {scheduler.name:16s} "
                      f"admitted={summary['admitted']:3d} "
                      f"(met_deadline={summary['admitted_and_met_deadline']:3d}, "
                      f"missed_deadline={summary['admitted_but_missed_deadline']:3d})  "
                      f"rejected={summary['rejected']:3d} "
                      f"(would_have_met={summary['rejected_would_have_met_deadline']:3d})")
        print()


if __name__ == "__main__":
    main()
