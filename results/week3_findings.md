# Week 3 findings — prediction dataset generation

Operationalizes `research-plan/week3_testing_plan.md` (A1-A4 confirmed in
`research-plan/week3_task_list.md`). This is the actual-execution and
results counterpart to that plan — the plan describes what was designed;
this documents what was run, against real data on the Ryzen box
(`YOUR_TRINO_HOST`), and what came back. Same structure/rigor as
`results/checkpoint1_findings.md`.

## TL;DR

- **SF 0.1 data verified present and clean** before any testing started —
  `tpch_orders_v1`=150,000, `tpch_lineitem_v1`=600,572, no contamination,
  checked directly against MinIO (not just Trino's registered-partition
  view).
- **Section 2 baselines recorded** — the fixed p50 reference point for SLA
  tiers going forward. Surprise: `short` (point lookup) is *slower* than
  `medium` (full scan), not faster as originally proposed — no partition
  pruning means the "short" query pays the same full-scan cost as `medium`
  plus an extra predicate, with nothing to short-circuit on.
- **Section 3 dataset-generation pass (180 runs) shows a real,
  statistically significant concurrent-write contention effect for `short`
  and `medium`** at n=30 (p=0.007 and p<0.0001 respectively) — clearing the
  significance bar Checkpoint 1 narrowly missed at n=15 (p≈0.09-0.13).
  `long` shows no significant effect, flagged as an open question rather
  than resolved (high baseline variance may be masking it).
- **A new MinIO cleanup discipline was built and used successfully** —
  partition-snapshot-diff cleanup (`scripts/minio_partition_guard.py`) —
  because the pipeline's `source` partition field is hardcoded identical
  across all producers, so no object path can identify "which producer
  wrote this file" by name. Worked correctly: burst objects landed in
  `hour=16`/`17`, cleanly separate from TPC-H's `hour=05/06/07`, deleted
  with zero impact on the research dataset (verified before and after).

## What was built (infrastructure, not results)

- **`scripts/query_timing_harness.py`** extended: `short` (point lookup)
  and `long` (orders ⋈ lineitem join) query classes added alongside the
  existing `medium`, all validated against real SF 0.1 data before use.
  Telemetry schema extended with `query_class`, `sla_tier`,
  `scan_scope_frozen` columns per the testing plan's section 5.
- **`scripts/minio_partition_guard.py`** (new): `check` / `snapshot` /
  `cleanup` subcommands for the partition-snapshot-diff cleanup mechanism —
  see "Deferred / blocks Week 5" in `research-plan/week3_task_list.md` for
  why this workaround is needed and why it stops working once concurrent
  multi-producer ingestion starts (Week 5).

## Section 2 — clean-baseline p50 per query class

Full detail: `results/week3/section2_baselines.md`. Raw data:
`results/week3/baseline_timing.csv`. 15 runs/class, `condition=none`,
frozen scope.

| Class | p50 | mean | stdev |
|---|--:|--:|--:|
| short (point lookup) | 536.5ms | 542.4ms | 31.1ms |
| medium (single-table scan) | 472.8ms | 480.0ms | 21.6ms |
| long (orders ⋈ lineitem join) | 1154.9ms | 1180.4ms | 74.0ms |

**These are the fixed SLA reference point for the rest of the project** —
not to be recalculated later. Derived SLA thresholds (A2 multipliers) are
in the linked doc.

## Section 3 — dataset-generation pass (none vs. heavy_burst, n=30/cell)

Full detail: `results/week3/section3_dataset_findings.md`. Raw data:
`results/week3/section3_dataset.csv`.

| Class | none p50/mean | heavy_burst p50/mean | diff (mean/median) | p (mean/median) |
|---|---|---|---|---|
| short | 344.0 / 392.8ms | 419.1 / 441.2ms | +48.4 / +75.1ms | 0.027 / 0.007 |
| medium | 289.3 / 291.4ms | 378.0 / 379.4ms | +87.9 / +88.7ms | 0.0000 / 0.0000 |
| long | 1016.6 / 1055.2ms | 1000.4 / 1017.1ms | -38.1 / -16.2ms | 0.79 / 0.75 |

Known limitation carried over honestly: cache state isn't independently
controlled this phase (per the testing plan's own flagged gap), and `none`
ran before `heavy_burst` in the same session — but that bias would make
`heavy_burst` look faster, not slower, so it doesn't explain away the
positive effects found for short/medium.

## Status against week3_testing_plan.md section 7 ("done" criteria)

- [x] SF 0.1 data ingested and partitions synced
- [x] Section 2 baselines recorded
- [x] Section 3's 180+45 timed runs collected into a structured dataset
- [x] FIFO and point-estimate baselines implemented and runnable
- [x] First quantile-regression model fit on the collected dataset

**Week 3 is functionally complete** — the safety-margin question (below)
was resolved by evaluating both candidates rather than picking one, per
`results/week3/section6_scheduler_baseline_comparison.md`. Not a blocker
for Week 4.

## Baseline schedulers

`scripts/baseline_schedulers.py` — `FifoScheduler`, `PointEstimateScheduler`,
`PointEstimateFixedMarginScheduler`, `PointEstimatePerClassMarginScheduler`.
Validated by replaying Section 3's real collected runtimes through each
(`scripts/run_baseline_schedulers_demo.py` for a smoke test,
`scripts/evaluate_baseline_schedulers.py` for the full comparison).

**Finding: `point_estimate` is behaviorally identical to `fifo` at
`relaxed`/`moderate` SLA tiers** — its predicted runtime is always the
fixed class p50, and the deadline is always that same p50 times a
multiplier ≥ 2x, so admission is guaranteed by construction regardless of
what actually happens. The only tier where either naive baseline can miss
a deadline is `tight` (1.2x): **2 of 90 admitted queries missed it**, in
both `none` and `heavy_burst` conditions equally. This is exactly the
failure mode an ingestion-aware scheduler needs to catch that these two
baselines structurally can't — a concrete argument for why the paper's
scheduler needs to react to more than a fixed point estimate.

**Rather than deciding between a fixed global margin and a per-class
margin upfront, both are implemented as independent baseline schedulers
and evaluated empirically** — first pass in
`results/week3/section6_scheduler_baseline_comparison.md`, superseded by a
properly counterbalanced tune/held-out protocol in
`results/week3_margin_tuning/margin_tuning_report.md` (the first pass's
dataset predated the warm-up fix and likely carried the same session-order
confound Week 3.6 found elsewhere, so a fresh, mirrored-order collection
was made specifically for this).

**Headline result, confirmed by both passes and mechanistically explained
by the second**: no margin above the `tight` SLA tier's 1.2x multiplier
can ever admit anything (`predicted > deadline` by construction). The
tuned protocol goes further — grid-searching margins from 1.00x to 1.40x
in 0.02 increments shows the admit rate and SLA miss rate are **completely
flat across the entire workable range** (1.00x-1.20x), then collapse to 0%
admission above it. This isn't about the specific margin value being
wrong; it's structural: admission is a boolean gate, and whether an
admitted query meets its deadline depends only on its own actual runtime,
never on which margin was used to admit it. **Per-class tuning converges
to exactly the same value (1.20x) as global tuning for every class**, so
`FixedMargin` and `PerClassMargin` are provably identical on held-out
data — not just empirically tied, but mechanistically guaranteed to be,
since the binding constraint (the tier's own multiplier) is
class-independent. This is a concrete, mechanistic argument for why the
paper's adaptive scheduler needs to reason per-query rather than gate by
class: a fixed margin has zero ability to discriminate risk within a
class once it decides to admit that class at all.

## First prediction model — empirical per-cell quantiles

`scripts/fit_quantile_baseline.py` → `results/week3/quantile_baseline.json`.
With only `query_class` × `ingestion_rate` sampled as factors this phase
(per section 3's scoped-down design), a saturated model over those two
categories is the same thing as empirical per-cell quantiles — deliberately
the simplest honest first pass, not a placeholder. Real features (system
state, ingestion signal, continuous predictors) are Week 4+ per the task
list.

| query_class | ingestion_rate | n | p50 | p90 | p95 | mean |
|---|---|--:|--:|--:|--:|--:|
| short | none | 30 | 343.6ms | 519.5ms | 605.4ms | 392.8ms |
| short | heavy_burst | 30 | 416.8ms | 541.7ms | 654.3ms | 441.2ms |
| medium | none | 30 | 289.2ms | 309.7ms | 314.6ms | 291.4ms |
| medium | heavy_burst | 30 | 376.0ms | 470.4ms | 497.8ms | 379.4ms |
| long | none | 30 | 1012.5ms | 1107.2ms | 1177.0ms | 1055.2ms |
| long | heavy_burst | 30 | 993.2ms | 1080.6ms | 1143.3ms | 1017.1ms |

## Open items carried forward

- **Safety-margin question fully resolved, not just for this pass but
  structurally** — `results/week3_margin_tuning/margin_tuning_report.md`.
  No margin above 1.20x can ever admit anything at `tight`; within the
  workable range the specific value is irrelevant (flat admit/miss
  frontier); global and per-class tuning provably converge to the same
  answer. Nothing left open on this specific question — the natural next
  step is the adaptive scheduler (Week 4), which this result gives a
  concrete, mechanistic reason to expect will actually add value.
- **`long` query class shows no measurable concurrency effect** — worth
  revisiting with more samples or outlier-robust methods before concluding
  it's genuinely insensitive to write contention (see
  `results/week3/section3_dataset_findings.md`'s interpretation section).
- **Backlog, blocks Week 5**: producer `source` field must become
  per-producer before any phase with concurrent multi-producer ingestion —
  see `research-plan/week3_task_list.md`.
- **`ingestion-pipeline/run-producers.ps1` has a parser bug** under
  PowerShell 5.1 (an em-dash in a `throw` string breaks tokenization,
  likely a missing UTF-8 BOM) — worked around by invoking the producer
  directly for this session; the script itself still needs the fix.
