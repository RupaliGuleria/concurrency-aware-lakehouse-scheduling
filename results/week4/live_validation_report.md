# Week 4 — live Trino multi-query scheduler validation

Generated 2026-09-20 by `scripts/summarize_live_validation.py` from `results/week4/live_validation.csv`, produced by `scripts/run_live_multi_query_validation.py` — design doc item 4 ("live Trino validation stage ... once scheduler behavior is stable"), run against the live Ryzen box (YOUR_TRINO_HOST).

**Caveat, unlike the bootstrap simulation**: each scheduler's execution of "the same" batch (same query_class/sla_tier/priority/deadline) is a SEPARATE live run against Trino, so each scheduler sees its own fresh `actual_runtime_ms` sample rather than all 7 schedulers sharing one ground-truth sample the way `results/week4/simulation_report.md` does. This is a live sanity check that scheduler behavior transfers off simulation, not a controlled paired comparison — read the numbers with that in mind.

## Test setup — data volume & ingestion

**Synthetic batches**: 75 total (25 none, 25 moderate_sustained, 25 heavy_sustained), batch size 4-10 queries (mean 7.1). Each batch executed once per scheduler (525 batch-scheduler runs), for **3738 live query executions against Trino** total.

**Underlying TPC-H data queried** (SF 0.1, `hive.events.raw_events`, verified live via `SELECT count(*) ... GROUP BY schema_id` this session): `tpch_orders_v1` 150,000 rows, `tpch_lineitem_v1` 600,572 rows (750,572 combined). Fixed for the whole session — no TPC-H data was added or removed between conditions. The three query templates (`scripts/query_timing_harness.py`) map 1:1 to `query_class`: `short` = point lookup on `tpch_orders_v1` (single `o_orderkey`), `medium` = full aggregation over `tpch_orders_v1`, `long` = a join across both tables with per-row JSON field extraction — the JSON parsing cost, not table size, is why `long` is the slow class despite querying a fixed, modest row count.

**Ingestion load** (`banking_producer`, TPC-H-unrelated banking transaction events, `ingestion-pipeline/run-producers.ps1 -Profile banking`, one CSV cycle = 550,000 rows replayed through local Kafka + the local ingestion service):

| Condition | Target eps | Achieved eps (observed) | Approx. events sent |
|---|--:|--:|--:|
| none | 0 | 0 (no producer running) | 0 |
| moderate_sustained | 1,000 | ~1,000-1,020, stable | ~960,000 (1 partial + 1 full CSV cycle) |
| heavy_sustained | 1,700 | ~1,620-1,720 after reducing local CPU load (first attempt capped at ~1,200-1,250 under CPU contention and was discarded) | ~1,810,000 (2 full CSV cycles + partial 3rd, self-restarting producer loop) |

**Why the achieved rate lagged target for heavy_sustained at first**: local CPU load was 66-77% (Zoom, Creative Cloud, and other background apps competing with the producer process and local Docker/Kafka for CPU) — the same failure mode `research-plan/local_machine_preflight_checklist.md` documented in an earlier session. Closing background apps dropped CPU to 15-30% and the producer reached target.

**Important scope note — this is contention load, not scan-volume growth**: verified live this session that `hive.events.raw_events` has **zero** `banking_transaction_v1` rows (`SELECT count(*) ... WHERE schema_id = 'banking_transaction_v1'` returns 0). The banking events are real Kafka/ingestion-service/MinIO write traffic — the producer, Kafka broker, and ingestion service genuinely process 1,000-1,700 events/sec — but they land in a separate data path from the TPC-H tables the scheduler's queries scan, and `scan_scope_frozen` (the harness's default) means Trino's view of `raw_events` doesn't change mid-run regardless. So `ingestion_condition` here specifically measures **shared-infrastructure resource contention** (CPU, I/O, network) during query execution, not "more data to scan." That's a deliberate v1 methodology choice (isolates contention from volume growth as separate variables), not an accident — but it's also very likely why the condition's effect on runtime looks small in both this live data and the simulation: at this hardware's headroom, 1,000-1,700 events/sec of writes to an unrelated table isn't enough contention to meaningfully slow down reads against a fixed 750,572-row table. This is the same conclusion `results/week3_6_counterbalanced_findings.md` reached from single-query timing data (no significant ingestion effect once session position is controlled for) and `results/week4/simulation_report.md`'s diagnostic reached from bootstrap replay (2-21ms predicted-runtime shifts, dwarfed by hundreds-to-thousands of ms of slack spread between queries) — this live run is a third, independent confirmation of the same finding, now with a concrete architectural reason why.

## Main comparison (all conditions pooled)

| Scheduler | SLA adherence | Weighted SLA adherence | n queries |
|---|--:|--:|--:|
| fifo | 44.2% | 45.2% | 534 |
| priority_first | 47.9% | 58.2% | 534 |
| edf | 56.0% | 55.9% | 534 |
| static_least_slack | 47.6% | 48.2% | 534 |
| adaptive_least_slack | 50.2% | 51.6% | 534 |
| dp_oracle | 66.7% | 73.8% | 534 |
| priority_sla_aware_dp | 65.0% | 70.7% | 534 |

## By ingestion condition

| Scheduler | Condition | n batches | n queries | SLA adherence | Weighted SLA adherence |
|---|---|--:|--:|--:|--:|
| fifo | none | 25 | 178 | 43.8% | 44.5% |
| fifo | moderate_sustained | 25 | 178 | 44.9% | 46.2% |
| fifo | heavy_sustained | 25 | 178 | 43.8% | 45.0% |
| priority_first | none | 25 | 178 | 48.9% | 59.2% |
| priority_first | moderate_sustained | 25 | 178 | 48.3% | 58.4% |
| priority_first | heavy_sustained | 25 | 178 | 46.6% | 56.9% |
| edf | none | 25 | 178 | 59.0% | 59.2% |
| edf | moderate_sustained | 25 | 178 | 56.7% | 56.7% |
| edf | heavy_sustained | 25 | 178 | 52.2% | 51.8% |
| static_least_slack | none | 25 | 178 | 50.6% | 52.4% |
| static_least_slack | moderate_sustained | 25 | 178 | 46.6% | 46.7% |
| static_least_slack | heavy_sustained | 25 | 178 | 45.5% | 45.3% |
| adaptive_least_slack | none | 25 | 178 | 51.7% | 52.7% |
| adaptive_least_slack | moderate_sustained | 25 | 178 | 49.4% | 51.3% |
| adaptive_least_slack | heavy_sustained | 25 | 178 | 49.4% | 50.7% |
| dp_oracle | none | 25 | 178 | 70.8% | 77.6% |
| dp_oracle | moderate_sustained | 25 | 178 | 63.5% | 72.0% |
| dp_oracle | heavy_sustained | 25 | 178 | 65.7% | 72.0% |
| priority_sla_aware_dp | none | 25 | 178 | 63.5% | 68.8% |
| priority_sla_aware_dp | moderate_sustained | 25 | 178 | 64.6% | 71.4% |
| priority_sla_aware_dp | heavy_sustained | 25 | 178 | 66.9% | 72.0% |

## Breakdown by query class

| Scheduler | Class | n | SLA adherence |
|---|---|--:|--:|
| fifo | short | 213 | 37.1% |
| fifo | medium | 147 | 38.1% |
| fifo | long | 174 | 58.0% |
| priority_first | short | 213 | 39.9% |
| priority_first | medium | 147 | 40.1% |
| priority_first | long | 174 | 64.4% |
| edf | short | 213 | 57.7% |
| edf | medium | 147 | 74.8% |
| edf | long | 174 | 37.9% |
| static_least_slack | short | 213 | 39.0% |
| static_least_slack | medium | 147 | 53.7% |
| static_least_slack | long | 174 | 52.9% |
| adaptive_least_slack | short | 213 | 40.4% |
| adaptive_least_slack | medium | 147 | 54.4% |
| adaptive_least_slack | long | 174 | 58.6% |
| dp_oracle | short | 213 | 72.8% |
| dp_oracle | medium | 147 | 68.7% |
| dp_oracle | long | 174 | 57.5% |
| priority_sla_aware_dp | short | 213 | 70.4% |
| priority_sla_aware_dp | medium | 147 | 73.5% |
| priority_sla_aware_dp | long | 174 | 51.1% |

## Breakdown by SLA tier

| Scheduler | Tier | n | SLA adherence |
|---|---|--:|--:|
| fifo | relaxed | 180 | 67.8% |
| fifo | moderate | 195 | 43.6% |
| fifo | tight | 159 | 18.2% |
| priority_first | relaxed | 180 | 71.1% |
| priority_first | moderate | 195 | 46.2% |
| priority_first | tight | 159 | 23.9% |
| edf | relaxed | 180 | 63.9% |
| edf | moderate | 195 | 46.2% |
| edf | tight | 159 | 59.1% |
| static_least_slack | relaxed | 180 | 43.9% |
| static_least_slack | moderate | 195 | 36.9% |
| static_least_slack | tight | 159 | 64.8% |
| adaptive_least_slack | relaxed | 180 | 48.9% |
| adaptive_least_slack | moderate | 195 | 39.5% |
| adaptive_least_slack | tight | 159 | 64.8% |
| dp_oracle | relaxed | 180 | 83.9% |
| dp_oracle | moderate | 195 | 68.7% |
| dp_oracle | tight | 159 | 44.7% |
| priority_sla_aware_dp | relaxed | 180 | 87.2% |
| priority_sla_aware_dp | moderate | 195 | 61.5% |
| priority_sla_aware_dp | tight | 159 | 44.0% |

## Breakdown by priority

| Scheduler | Priority | n | SLA adherence |
|---|--:|--:|--:|
| fifo | 1 | 198 | 48.5% |
| fifo | 2 | 129 | 39.5% |
| fifo | 3 | 207 | 43.0% |
| priority_first | 1 | 198 | 78.8% |
| priority_first | 2 | 129 | 37.2% |
| priority_first | 3 | 207 | 25.1% |
| edf | 1 | 198 | 55.6% |
| edf | 2 | 129 | 56.6% |
| edf | 3 | 207 | 56.0% |
| static_least_slack | 1 | 198 | 48.0% |
| static_least_slack | 2 | 129 | 51.2% |
| static_least_slack | 3 | 207 | 44.9% |
| adaptive_least_slack | 1 | 198 | 52.5% |
| adaptive_least_slack | 2 | 129 | 54.3% |
| adaptive_least_slack | 3 | 207 | 45.4% |
| dp_oracle | 1 | 198 | 83.3% |
| dp_oracle | 2 | 129 | 74.4% |
| dp_oracle | 3 | 207 | 45.9% |
| priority_sla_aware_dp | 1 | 198 | 78.8% |
| priority_sla_aware_dp | 2 | 129 | 69.8% |
| priority_sla_aware_dp | 3 | 207 | 48.8% |

