# Week 4 — ingestion-visibility live validation (2026-09-22)

Tests whether making ingested data genuinely visible in the same table the
scheduler's queries scan (fixing the metastore-partition-sync gap found
earlier this session — banking events were being produced and even
flushed to Parquet, but never registered in Trino's Hive metastore, so no
query all session had ever actually seen them) changes real SLA outcomes.
All 8 schedulers, 25 batches each condition, same seed (42) for both so
batch composition (query mix/deadlines/priorities) is identical between
conditions — only whether ~190K-1M rows of banking data were visible and
actively growing during the run differs.

## Setup

- `none`: current clean baseline (130 objects / 3 partitions, TPC-H SF 0.1
  only), no producer running.
- `ingestion_visible`: banking producer streaming continuously throughout
  (~1000 eps, self-restarting loop), with the new partition explicitly
  synced into Trino via `CALL hive.system.sync_partition_metadata(...)`
  before the run — 190,817 banking rows visible in `hive.events.raw_events`
  at sync time, growing to ~1M+ by the end of the run as the producer kept
  streaming.
- Both `predicted_static_ms` and `predicted_adaptive_ms` are identical
  between conditions (deliberately — see `run_live_multi_query_validation.py`'s
  `build_ingestion_prediction_tables`) so any adherence difference is
  attributable to real execution, not a different scheduling decision.

## A methodology issue caught and fixed mid-test

The first `none` run (done right after recovering from a local Docker
Desktop crash — see below) showed anomalously low adherence (17-25%) with
heavily inflated mean runtimes (up to 16x the median) — a classic
heavy-tailed-latency signature, not a real effect. The first
`ingestion_visible` run, collected afterward once the system had settled,
showed real runtimes matching the original session-start baseline almost
exactly. Comparing those two runs directly would have wrongly suggested
"visible ingestion helps performance," which doesn't make mechanistic
sense and was actually a **time-of-measurement confound** — `none` was
measured during a bad window, not a representative one. `none` was
re-collected once Trino's response time was confirmed stable
(~30ms vs. 2-3s+ during the bad window) so both conditions are compared
fairly, close together in time.

## Results (both conditions collected under comparable, stable system state)

| Scheduler | none | ingestion_visible |
|---|--:|--:|
| fifo | 43.3% | 43.3% |
| priority_first | 44.4% | 46.1% |
| edf | 55.6% | 55.6% |
| static_least_slack | 46.1% | 46.1% |
| adaptive_least_slack | 44.9% | 46.6% |
| dp_oracle | 70.2% | 66.9% |
| priority_sla_aware_dp | 64.6% | 68.0% |
| admission_controlled_dp | 69.7% | 70.8% |

No scheduler shows a differences bigger than ~4 points either direction —
consistent with every other test this session (Week 3.6 single-query
data, the bootstrap simulation, the earlier concurrency+ingestion combined
test, and now this one): **making ingested data genuinely visible in the
queried table does not meaningfully move scheduler-level SLA adherence**,
at this data volume (up to ~1M banking rows, roughly comparable to the
750K-row TPC-H baseline) and this hardware.

## Real runtime distributions (actual_runtime_ms)

| Query class | none median | none mean | ingestion_visible median | ingestion_visible mean |
|---|--:|--:|--:|--:|
| short | 293.7ms | 308.6ms | 288.0ms | 301.0ms |
| medium | 253.8ms | 269.1ms | 248.5ms | 269.5ms |
| long | 886.6ms | 914.7ms | 878.8ms | **3302.8ms** |

Short/medium are clean in both conditions (mean ≈ median, matching the
session-start baseline of 286.7/248.4/879.4ms almost exactly). **`long`
(the orders-lineitem join) shows a real mean/median gap in
`ingestion_visible`** — some genuine slow outliers pulling the mean to
3.7x the median, vs. `none`'s ~3% gap. Plausible mechanism: `long` scans
both TPC-H tables without a partition-level filter, so as the actively
growing banking partition gets written to during the run, Trino
occasionally hits extra split-planning/file-open overhead or a
mid-write file. This is a smaller, more mechanistically believable
secondary signal than the confounded first-pass result above, but the
sample size here (464 long-class queries) isn't enough to treat it as
more than a lead worth another look, not a finding to build on yet.

## What was fixed to make this test possible

1. **Local Docker Desktop crashed mid-session** (service stopped, no
   process running) — restarted the app, then had to restart Kafka twice
   (a stale Zookeeper ephemeral broker registration from the abrupt kill
   blocked the first attempt — resolved itself once Zookeeper's session
   timeout expired).
2. **The local ingestion service (Java, port 8080) isn't a Docker
   container** — it's a separate `mvn spring-boot:run` process
   (`adaptive-data-lake-producer/ingestion`) and needed restarting
   independently once Docker was back.
3. **Two silent misconfigurations on restart**, found only by reading the
   service's own logs, not by the (correctly-looking) `totalEvents`
   counter: it defaulted to `MINIO_BUCKET=adaptive-data-lake` (not
   `lakehouse-events`) on the first restart, then even after fixing the
   bucket name, defaulted to `MINIO_ENDPOINT=http://localhost:9000` (the
   local Docker MinIO, not the remote Ryzen box's MinIO that Trino
   actually reads from) on the second. Both needed explicit env var
   overrides (`MINIO_BUCKET`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`,
   `MINIO_SECRET_KEY`) — worth documenting in the producer README for next
   time, since neither failure was visible from the ingestion service's
   own metrics endpoint (it reported events processed correctly in both
   wrong-destination cases).
4. **`poll_ingestion=True` was silently wasting ~17x the wall-clock time**
   on every live query — `run_once()`'s per-query telemetry polling was
   never actually captured in this harness's CSV schema, so it was pure
   overhead. Now `poll_ingestion=False` in `run_live_multi_query_validation.py`.

## Cleanup

All producer/service processes stopped or left in their normal idle
state; MinIO/Trino partition state fully restored to baseline (130
objects / 3 partitions, verified via `minio_partition_guard.py check`).
Raw data: `results/week4/live_validation_ingestion_visibility.csv`.
