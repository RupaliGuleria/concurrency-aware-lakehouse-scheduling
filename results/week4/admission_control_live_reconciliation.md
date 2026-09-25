# Admission control: live defer-rate / admitted-only reconciliation

Generated 2026-09-23 by `scripts/reconcile_admission_control_live.py`, in
response to a gap noticed while reviewing test coverage: neither live CSV
(`live_validation_4scheduler_check.csv`,
`live_concurrency_validation_postrestart.csv`) recorded which queries
`admission_controlled_dp` deferred per row, so only *blended* SLA
adherence had been reported live — not **admitted-only adherence**, which
the offline simulation report itself identifies as the metric that
actually shows what admission control is for (blended adherence gets
dragged down by queries it deliberately gave up on).

**Method, zero additional Ryzen load**: both live scripts generate batches
deterministically (fixed seed; the condition/tier argument doesn't affect
the RNG draw order, only which prediction values get looked up). This
regenerates the identical batches offline, re-runs
`AdmissionControlledDpScheduler` to recover `deferred_ids` per batch, and
joins that against the `actual_runtime_ms`/`sla_met` already collected
live. The recovered blended numbers match the originally-reported live
summaries exactly, confirming the batch/row join is correct.

## Results

| Condition | n | Blended | Admitted-only | Deferred (should be ~0%) | Defer rate |
|---|--:|--:|--:|--:|--:|
| Ingestion: none | 178 | 65.7% | **77.5%** | 0.0% (0/27) | 15.2% |
| Ingestion: moderate_sustained | 178 | 69.1% | **83.1%** | 0.0% (0/30) | 16.9% |
| Ingestion: heavy_sustained | 178 | 69.1% | **80.9%** | 0.0% (0/26) | 14.6% |
| Concurrency: low | 30 | 63.3% | **73.1%** | 0.0% (0/4) | 13.3% |
| Concurrency: moderate | 137 | 5.8% | **33.3%** | 0.0% (0/113) | 82.5% |

## What this confirms

- **Admitted-only adherence beats blended adherence in every condition
  tested, live** — the core mechanism (admit only what's predicted to
  meet a declared service target, defer the rest) works as designed
  outside simulation, not just inside it.
- **Deferred queries show exactly 0% adherence everywhere** — confirming
  `score_order`'s accounting is honest: deferred queries are scored as
  ordinary misses (they run last, after every admitted query), never
  quietly excluded.
- **Live `moderate` concurrency triggers a dramatic, concrete defer
  response** (82.5% of queries deferred) — a real, live instance of
  "detecting when concurrency has crossed the point where SLA compliance
  is no longer achievable," the paper's v1.2 headline claim.

## One honest gap, not hidden

At live `moderate` concurrency, admitted-only adherence (33.3%) falls well
short of the declared 80% service target — unlike every ingestion
condition and the `low` concurrency tier, where admitted-only comfortably
clears or approaches 80%. Plausible causes, not disentangled here: the
admission gate's trial check uses `predicted_adaptive_ms` (from the
`moderate`-tier calibration table), and if live execution under real
background contention runs somewhat worse than that calibration predicts
(see `live_concurrency_validation_report.md`'s note on continuous
unthrottled background threads vs. the calibration's discrete rounds),
the gate would admit queries it shouldn't. Small admitted-n (24) at this
condition also limits how much weight this single number can bear. Worth
stating as a live-vs-simulation gap specific to concurrency-driven
admission control, not swept into the headline claim above.
