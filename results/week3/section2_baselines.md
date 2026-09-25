# Section 2 baselines — clean, no-ingestion p50 per query class

Per `research-plan/week3_testing_plan.md` section 2. 15 runs per class,
`condition=none`, no concurrent producer, frozen scope (no partition sync
during the run — confirmed via `scripts/minio_partition_guard.py cleanup`
immediately after all three blocks: zero new partitions since the pre-test
snapshot, so nothing wrote to MinIO during this pass). Raw data:
`results/week3/baseline_timing.csv`. SF 0.1 data verified present and
uncontaminated before starting: `tpch_orders_v1`=150,000,
`tpch_lineitem_v1`=600,572, no other schema_id registered.

| Class | n | p50 (median) | mean | stdev | min | max |
|---|--:|--:|--:|--:|--:|--:|
| short (point lookup) | 15 | 536.5ms | 542.4ms | 31.1ms | 505.9ms | 607.5ms |
| medium (single-table scan) | 15 | 472.8ms | 480.0ms | 21.6ms | 451.3ms | 522.0ms |
| long (orders ⋈ lineitem join) | 15 | 1154.9ms | 1180.4ms | 74.0ms | 1072.6ms | 1319.0ms |

**These three p50 numbers are the fixed reference point for every SLA tier
for the rest of the project** (per the testing plan's explicit instruction)
— do not recalculate later just because a run looks different.

## Unexpected result: short is slower than medium, not faster

The testing plan's proposal expected `short` to be the cheapest class
("still expected to be cheapest since it can short-circuit once matched").
That didn't hold — `short` (536.5ms p50) is consistently ~64ms slower than
`medium` (472.8ms p50) across all 15 runs each (no overlap in min/max
ranges).

Likely explanation: this is a distributed columnar full-table scan with no
partition pruning on `schema_id` and no index — Trino can't "stop early"
mid-scan the way a row-store point lookup would. `short` runs the exact same
full scan as `medium` (every row's `payload_json` gets parsed to check
`schema_id`), plus one extra `json_extract_scalar` + string comparison per
row to test `o_orderkey = '1'` — pure added work, no compensating shortcut.
So "point lookup" here is really "single-table scan plus an extra predicate,"
not an indexed lookup — worth stating plainly in the paper rather than
assuming the original short/medium/long cost ordering holds without
checking.

## Derived SLA thresholds (A2 multipliers × this section's p50)

| Class | Relaxed (4x) | Moderate (2x) | Tight (1.2x) |
|---|--:|--:|--:|
| short | 2146.0ms | 1073.0ms | 643.8ms |
| medium | 1891.2ms | 945.6ms | 567.4ms |
| long | 4619.6ms | 2309.8ms | 1385.9ms |
