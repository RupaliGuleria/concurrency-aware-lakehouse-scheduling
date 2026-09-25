# Section 3 dataset-generation pass — findings

Per `research-plan/week3_testing_plan.md` sections 3-4. 30 runs per
(query class × ingestion condition) cell, 3 classes × 2 conditions = 180
timed queries. Raw data: `results/week3/section3_dataset.csv`. Harness:
`scripts/query_timing_harness.py`. Cleanup guard:
`scripts/minio_partition_guard.py`.

## Method

- **`none`**: all 3 classes (90 runs total), 1.0s pacing, no producer
  running. Ran first.
- **`heavy_burst`**: all 3 classes (90 runs total), 0.3s pacing, ran back
  to back during a single continuous banking-producer pass
  (`banking_transaction_v1`, 550,000 rows, ~215s streaming window,
  confirmed `total_events=550000` completed). Single continuous pass was
  required, not one pass per class — the ingestion service dedupes by
  `transaction_id` within a 60-minute window, so replaying the same CSV a
  second time in the same session would have been silently deduplicated
  (no real writes, no real contention) rather than producing a fresh burst.
- **Frozen scope throughout**: no `sync_partition_metadata` call during
  either block — confirmed via `minio_partition_guard.py`, which showed
  zero new partitions after `none` and exactly 2 new partitions (both
  banking-only, `hour=16`/`hour=17`, no overlap with TPC-H's registered
  `hour=05/06/07`) after `heavy_burst`. Those 2 partitions (6 objects,
  124,775,825 bytes) were deleted immediately after the pass via a
  before/after partition-prefix diff — targeted delete, not a bucket-wide
  wipe. Re-verified afterward: TPC-H counts unchanged
  (`tpch_orders_v1`=150,000, `tpch_lineitem_v1`=600,572), zero unexpected
  `schema_id`s registered.

## Known limitation: cache state not independently controlled

Per the testing plan's own flagged gap (section 3's table), cache state is
"whatever cache state naturally exists," not a controlled variable this
phase. Concretely here: `none` ran temporally before `heavy_burst` in the
same session, so the system may have been progressively warming
(OS/MinIO cache) across the whole 180-run pass independent of the ingestion
condition. That bias, if present, would work in the direction of making
`heavy_burst` (which ran later) look artificially *faster*, not slower —
so it does not explain away any positive burst effect found below; if
anything it means a true positive effect is likely understated, not
overstated.

## Results

| Class | none (n=30) | heavy_burst (n=30) | diff (mean / median) | p (mean / median) |
|---|---|---|---|---|
| short | p50=344.0ms mean=392.8ms stdev=105.9 | p50=419.1ms mean=441.2ms stdev=86.5 | +48.4ms / +75.1ms | 0.0271 / 0.0072 |
| medium | p50=289.3ms mean=291.4ms stdev=13.3 | p50=378.0ms mean=379.4ms stdev=73.9 | +87.9ms / +88.7ms | 0.0000 / 0.0000 |
| long | p50=1016.6ms mean=1055.2ms stdev=200.2 | p50=1000.4ms mean=1017.1ms stdev=62.3 | -38.1ms / -16.2ms | 0.7926 / 0.7516 |

One-sided permutation test, 20,000 resamples, `heavy_burst` vs `none`
(same method as `results/checkpoint1_findings.md`).

## Interpretation

- **short and medium: the concurrent-write contention effect clears
  significance at n=30**, unlike Checkpoint 1's n=15 pass (p≈0.09-0.13).
  This is exactly what the testing plan's section 4 was aiming for
  ("gives a real shot at the concurrency effect clearing significance this
  time"). Effect sizes (+48-88ms) are consistent with Checkpoint 1's
  ~70-80ms trend.
- **long: no significant difference, direction is even slightly negative.**
  Not interpreted as "no effect exists" — `long`'s `none` block has much
  higher variance (stdev 200.2ms, driven by outliers up to 2075ms even
  without any burst running) than the other two classes, which reduces
  the test's power to detect a real effect of similar size to short/medium.
  Worth more samples or outlier-robust analysis before concluding long is
  genuinely insensitive to write contention — flagging as open, not
  resolved.
- **Absolute magnitudes are not directly comparable to Section 2's
  baselines** (e.g. this section's `medium`/`none` p50 of 289.3ms vs.
  Section 2's 472.8ms) — later runs in the session benefited from a
  progressively warmer cache, per the limitation above. Section 2's
  baselines remain the fixed SLA reference point regardless; this section's
  `none`/`heavy_burst` comparison is a same-session, same-cache-trajectory
  comparison and should be read as internally relative, not as a
  replacement for Section 2's numbers.
