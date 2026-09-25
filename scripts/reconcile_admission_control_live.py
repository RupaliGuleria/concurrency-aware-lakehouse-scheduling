"""Offline reconciliation of admission_controlled_dp's live-collected data
with its own defer decisions - closes a gap noticed 2026-09-23: neither
live CSV (live_validation_4scheduler_check.csv,
live_concurrency_validation_postrestart.csv) recorded which queries were
deferred per row, so blended SLA adherence was reported live but not
admitted-only adherence / defer rate - the more diagnostic metric per the
offline simulation's own framing ("the number that actually shows what
admission control is for is adherence among only the queries it chose to
admit").

Zero additional Ryzen load: batches were generated deterministically (fixed
seed, and tier/condition doesn't affect the RNG draw order in either live
script - only which prediction values get looked up), so this regenerates
the identical batches offline, re-runs AdmissionControlledDpScheduler to
recover deferred_ids, and joins that against the actual_runtime_ms /
sla_met already collected live - no new live queries needed.

Usage:
  .venv/Scripts/python.exe reconcile_admission_control_live.py
"""
from __future__ import annotations

import csv
import random
from collections import defaultdict

from multi_query_schedulers import AdmissionControlledDpScheduler
from run_live_multi_query_validation import build_ingestion_prediction_tables, generate_live_batch as generate_ingestion_batch
from run_live_concurrency_validation import generate_live_batch as generate_concurrency_batch
from simulate_multi_query_scheduling import build_prediction_tables, load_concurrency_pool, load_pools


def reconcile(csv_path: str, batch_id_prefix: str, tier_or_condition_field: str, condition_value: str,
              batches_by_idx: dict, label: str) -> None:
    """batches_by_idx: {batch_idx: [MultiQuery, ...]} for this condition,
    already regenerated with the correct predictions. Recovers deferred_ids
    per batch via AdmissionControlledDpScheduler, then joins against the
    live CSV's actual sla_met for admission_controlled_dp rows."""
    deferred_by_batch = {}
    for batch_idx, queries in batches_by_idx.items():
        result = AdmissionControlledDpScheduler().order(queries)
        deferred_by_batch[batch_idx] = result.deferred_ids

    admitted_n = admitted_met = 0
    deferred_n = deferred_met = 0
    blended_n = blended_met = 0
    unmatched = 0

    with open(csv_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["scheduler"] != "admission_controlled_dp":
                continue
            if r.get(tier_or_condition_field) != condition_value:
                continue
            parts = r["batch_id"].split("_")
            # batch_id = f"{prefix}_{condition}_{batch_idx}_{timestamp}" - condition itself may contain
            # underscores (e.g. "heavy_sustained"), so take the second-to-last part as batch_idx.
            try:
                batch_idx = int(parts[-2])
            except (ValueError, IndexError):
                unmatched += 1
                continue
            if batch_idx not in deferred_by_batch:
                unmatched += 1
                continue
            met = r["sla_met"].strip().lower() == "true"
            blended_n += 1
            blended_met += met
            if r["query_id"] in deferred_by_batch[batch_idx]:
                deferred_n += 1
                deferred_met += met
            else:
                admitted_n += 1
                admitted_met += met

    print(f"=== {label} ===")
    print(f"  blended:      {blended_met}/{blended_n} = {blended_met/blended_n*100:.1f}%" if blended_n else "  blended: n/a")
    print(f"  admitted-only: {admitted_met}/{admitted_n} = {admitted_met/admitted_n*100:.1f}%" if admitted_n else "  admitted-only: n/a")
    print(f"  deferred:      {deferred_met}/{deferred_n} = {deferred_met/deferred_n*100:.1f}%" if deferred_n else "  deferred: n/a (none deferred)")
    print(f"  defer rate:    {deferred_n}/{blended_n} = {deferred_n/blended_n*100:.1f}%" if blended_n else "")
    if unmatched:
        print(f"  ({unmatched} rows unmatched - unexpected, check batch_id parsing)")
    print()


def main() -> None:
    pool = load_pools()
    concurrency_pool = load_concurrency_pool(pool)

    # --- ingestion-condition live validation (live_validation_4scheduler_check.csv) ---
    predicted_static_ing, predicted_adaptive_ing = build_ingestion_prediction_tables(pool)
    ing_csv = "../results/week4/live_validation_4scheduler_check.csv"

    # none: seed=42, batches 0-9 (first pilot) + 10-24 (extension) = 0-24
    rng = random.Random(42)
    none_batches = {i: generate_ingestion_batch(rng, (4, 10), predicted_static_ing, predicted_adaptive_ing, "none") for i in range(25)}
    reconcile(ing_csv, "live", "condition", "none", none_batches, "Ingestion condition: none (25 batches)")

    # moderate_sustained: seed=42, batches 0-24 (single 25-batch run)
    rng = random.Random(42)
    mod_batches = {i: generate_ingestion_batch(rng, (4, 10), predicted_static_ing, predicted_adaptive_ing, "moderate_sustained") for i in range(25)}
    reconcile(ing_csv, "live", "condition", "moderate_sustained", mod_batches, "Ingestion condition: moderate_sustained (25 batches)")

    # heavy_sustained: seed=42, batches 0-24 (single 25-batch run)
    rng = random.Random(42)
    heavy_batches = {i: generate_ingestion_batch(rng, (4, 10), predicted_static_ing, predicted_adaptive_ing, "heavy_sustained") for i in range(25)}
    reconcile(ing_csv, "live", "condition", "heavy_sustained", heavy_batches, "Ingestion condition: heavy_sustained (25 batches)")

    # --- concurrency-tier live validation (live_concurrency_validation_postrestart.csv) ---
    predicted_static_conc, predicted_adaptive_conc = build_prediction_tables(pool, concurrency_pool)
    conc_csv = "../results/week4/live_concurrency_validation_postrestart.csv"

    # low: seed=43, batches 0-4 (single 5-batch run)
    rng = random.Random(43)
    low_batches = {i: generate_concurrency_batch(rng, (4, 10), predicted_static_conc, predicted_adaptive_conc, "low") for i in range(5)}
    reconcile(conc_csv, "liveconc", "tier", "low", low_batches, "Concurrency tier: low (5 batches)")

    # moderate: seed=43, batches 0-4 (first pilot) + 5-19 (scale-up) = 0-19
    rng = random.Random(43)
    mod_conc_batches = {i: generate_concurrency_batch(rng, (4, 10), predicted_static_conc, predicted_adaptive_conc, "moderate") for i in range(20)}
    reconcile(conc_csv, "liveconc", "tier", "moderate", mod_conc_batches, "Concurrency tier: moderate (20 batches)")


if __name__ == "__main__":
    main()
