"""First runtime-prediction pass: empirical per-cell quantiles.

Per research-plan/week3_task_list.md's suggested order, item 6 ("First
prediction model once enough dataset rows exist") and
week3_testing_plan.md section 7's last done-criterion. This is
deliberately the simplest honest model, not a placeholder pretending to be
more: with only two features sampled this phase (query_class x
ingestion_rate, per week3_testing_plan.md section 3's scoped-down factor
list), a saturated regression over those categories IS the empirical
per-cell quantile - fitting e.g. gradient-boosted quantile regression here
would just be a slower way to recover the same 6 numbers per quantile.
Refinement (real features: system state, ingestion signal, continuous
predictors) is explicitly Week 4+ per the task list.

Input: results/week3/section3_dataset.csv (the Section 3 dataset-generation
pass - 30 runs x 3 classes x 2 ingestion conditions). Section 2's baseline
runs are deliberately excluded here - that data collapsed multiple cache
regimes into "none" (see results/week3/section3_dataset_findings.md's
limitation note) and exists to fix the SLA reference point, not to train
a predictor.

Usage:
  .venv/Scripts/python.exe fit_quantile_baseline.py
"""
from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict

IN_PATH = "../results/week3/section3_dataset.csv"
OUT_JSON = "../results/week3/quantile_baseline.json"
QUANTILES = [0.5, 0.9, 0.95]


def quantile(sorted_vals: list, q: float) -> float:
    """Nearest-rank method - no interpolation, so every reported value is
    an actually-observed runtime, not an extrapolated one (honest given
    n=30/cell)."""
    idx = max(0, min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def main() -> None:
    cells = defaultdict(list)
    with open(IN_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["error"]:
                continue
            key = (row["query_class"], row["ingestion_rate"])
            cells[key].append(float(row["runtime_ms"]))

    result = {}
    for (query_class, ingestion_rate), vals in sorted(cells.items()):
        vals_sorted = sorted(vals)
        cell_result = {
            "n": len(vals_sorted),
            "mean_ms": round(statistics.mean(vals_sorted), 1),
            "stdev_ms": round(statistics.stdev(vals_sorted), 1) if len(vals_sorted) > 1 else None,
        }
        for q in QUANTILES:
            cell_result[f"p{int(q * 100)}_ms"] = round(quantile(vals_sorted, q), 1)
        result[f"{query_class}|{ingestion_rate}"] = cell_result
        print(f"{query_class:7s} / {ingestion_rate:12s} n={cell_result['n']:3d}  "
              f"p50={cell_result['p50_ms']:7.1f}  p90={cell_result['p90_ms']:7.1f}  "
              f"p95={cell_result['p95_ms']:7.1f}  mean={cell_result['mean_ms']:7.1f}")

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=True)
    print(f"\nWrote {OUT_JSON}")


if __name__ == "__main__":
    main()
