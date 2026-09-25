# Week 3.5 findings — data-volume / scan-scope growth at SF 0.1

Per `research-plan/week3_5_data_volume_testing_plan.md`. Fills the gap
flagged after Week 3 completion: Checkpoint 1 proved a data-volume effect,
but at the old SF 0.01 scale, with one query, and a much larger relative
scope jump than this project now operates at. This re-tests the same
mechanism at SF 0.1, across all 3 query classes, with a clean paired
before/after design in one session.

## TL;DR

- **The data-volume effect did not clear significance at SF 0.1 scale, for
  any of the 3 query classes** — a real, honest result, not the "roughly
  doubles" effect Checkpoint 1 found at SF 0.01.
- **Most likely explanation: the effect's size is relative to the existing
  baseline scope, not a fixed absolute slowdown.** Checkpoint 1 added
  1,091,000 banking rows to a 75,175-row baseline (~15.5x growth). This
  test added 550,000 banking rows to a 750,572-row baseline (~1.73x growth
  by rows, ~1.94x by bytes) — a much smaller *relative* jump, even though
  the *absolute* amount of added data is comparable.
- **Method was clean**: paired before/after in one session (avoiding the
  cross-session cache-drift confound from Week 3's Section 3), TPC-H counts
  verified unchanged throughout, banking partition properly registered then
  properly dropped afterward (not just left empty).

## Method

1. `before`: 15 runs each of short/medium/long, `condition=before_more_data`,
   frozen scope, no ingestion.
2. Registered more data: streamed the banking producer unthrottled
   (550,000 rows, `PRODUCER_ID_SUFFIX=-w35` to avoid any dedup collision
   with the same day's other suffixed runs), then explicitly ran
   `sql/02_sync_partitions.sql` (`FULL` mode) to register it — the one
   deliberate deviation from Week 3's frozen-scope discipline, since
   growing registered scope is the entire point here.
3. `after`: 15 runs each of short/medium/long, `condition=after_more_data`,
   same session, scope now grown.
4. Cleanup: deleted the 4 new MinIO objects (partition-prefix diff, same
   `scripts/minio_partition_guard.py` tooling as Week 3), then re-ran
   `02_sync_partitions.sql` to drop the now-orphaned partition
   registration — confirmed via `SELECT "$partition" FROM raw_events` that
   only the 3 legitimate TPC-H hour-partitions remained registered
   afterward, not just that the row count looked right.

**Flush-lag finding, larger than Week 3.6's**: the ingestion service's
Parquet write buffer took **~30-40 seconds** to fully flush a full-speed
550,000-row burst to MinIO (checked repeatedly until the partition's object
count stabilized across 4 consecutive 8s-spaced checks) — notably longer
than the ~5-8s lag seen during Week 3.6's smaller/paced validation runs,
likely because 8 writer threads each independently time out on
`idle-flush-ms: 5000` after their own last write, and a full-speed burst
leaves more of them with residual buffered data at completion. Syncing
too early would have registered an incomplete scope. Worth carrying this
"poll until stable" discipline into any future full-speed burst cleanup,
not just a fixed sleep.

## Results

| Class | before p50/mean | after p50/mean | diff (mean/median) | % diff (mean) | p (mean/median) |
|---|---|---|---|--:|---|
| short | 338.4 / 356.5ms | 331.9 / 335.2ms | -21.4 / -6.5ms | -6.0% | 0.871 / 0.780 |
| medium | 276.5 / 285.2ms | 282.9 / 319.9ms | +34.7 / +6.5ms | +12.2% | 0.076 / 0.323 |
| long | 997.7 / 1012.4ms | 989.0 / 1013.4ms | +1.0 / -8.7ms | +0.1% | 0.480 / 0.677 |

n=15/class/condition, one-sided permutation test (20,000 resamples), same
method as Checkpoint 1 and Week 3.

Registered scope grew from 130 objects/131.8MB (TPC-H only) to 134
objects/255.9MB (+124.1MB banking, ~1.94x by bytes) — `medium` is the class
most directly comparable to Checkpoint 1's original test query, and its
mean effect (+12.2%) is directionally consistent with "more data slows the
scan down," but nowhere near significant at n=15 and far short of
Checkpoint 1's ~52% mean increase for a comparable query shape.

## Interpretation

1. **Not a contradiction of Checkpoint 1 — a scale-dependence finding.**
   Checkpoint 1's baseline was tiny (75,175 rows); adding ~1.09M rows was a
   ~15.5x scope jump. Here the baseline is already 10x larger (750,572
   rows); adding 550,000 rows is only a ~1.73x jump. If the effect scales
   with *relative* scope growth rather than absolute added volume, a much
   smaller relative jump producing a much smaller (here: non-significant)
   effect is exactly what this predicts — not evidence the mechanism is
   fake, evidence it doesn't scale the way a flat "data volume roughly
   doubles latency" claim would suggest.
2. **Byte growth (~1.94x) outpaced the observed latency effect by a wide
   margin.** If latency scaled linearly with scanned bytes, `medium` would
   be expected to slow by something approaching the byte-growth ratio, not
   +12%. This points toward fixed per-query/per-file overhead (query
   planning, connection setup, JSON parsing setup cost) dominating at this
   scale, with per-byte scan cost being a smaller share of total runtime
   than the SF 0.01-scale result implied.
3. **This changes what the paper can claim.** "Registering more ingested
   data slows queries down" is no longer safe to state as a fixed,
   scale-independent effect size — it needs to be framed as scope-relative,
   with SF 0.1's ~1.7-1.9x growth producing no significant effect at n=15,
   while SF 0.01's ~15.5x growth produced a large, clean, significant one.
   A genuine dose-response test (multiple scope-growth ratios, not just
   one jump) would be needed to characterize the actual relationship —
   noted as future work, not assumed.

## What this doesn't cover

Same caveat as the original plan: this is one unsynced→synced jump, not a
dose-response curve across multiple scan-scope sizes. Given the
non-significant result here, a next step worth considering (not yet
planned) is testing a *larger* relative jump at SF 0.1 scale — e.g. an
addition comparable in *relative* size to Checkpoint 1's ~15.5x, to check
whether the effect reappears at a scale-matched relative growth.
