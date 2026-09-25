# Week 3.6 findings — sustained ingestion rate vs. query latency

**Superseded 2026-09-10**: this finding does not reproduce under a fair,
counterbalanced comparison — see `results/week3_6_counterbalanced_findings.md`.
Best explained as a session-order artifact (the `none` condition always ran
first, absorbing a one-time cache-warming cost every subsequent condition
benefited from). Kept below as the historical record of the original
(now-refuted) result.

Per `research-plan/week3_6_sustained_ingestion_testing_plan.md`. Tests
whether the *rate and duration* of concurrent writing (not just its
presence) affects query latency differently from Week 3's one-shot
`heavy_burst`, specifically whether it explains why `long` showed no effect
there.

## TL;DR

- **The hypothesis is confirmed, with a twist.** Sustained ingestion does
  reveal a `long`-query effect that the original `heavy_burst` missed —
  but only at the **moderate** rate (1,000 eps target), not the heavy one
  (1,700 eps target). `long`/`moderate_sustained`: **p=0.0002, +70.5ms
  (+9.1%) median**, clearing significance decisively. `long`/`heavy_sustained`:
  p=0.207, not significant.
- **Non-monotonic, and that's a real finding, not noise** — Week 3.6's own
  plan (section 16) explicitly anticipated this as a legitimate possible
  outcome ("moderate worse than heavy... if different resource interactions
  occur") rather than assuming `none < moderate < heavy` had to hold.
- `short` and `medium` show **no significant effect** under either sustained
  condition — the opposite pattern from Week 3, where those two classes
  were the ones that cleared significance (under one-shot `heavy_burst`)
  and `long` didn't.
- **Two isolated, extreme single-run outliers** (5054ms in `short/none`,
  4343ms in `medium/heavy_sustained` — 15-18x typical) badly distorted
  mean-based comparisons; median-based comparisons (this document's
  primary metric, consistent with Checkpoint 1 and Week 3's convention)
  are robust to them, confirmed by a trim-and-recompute check.
- Overlap coverage was good: 98.0% mean overlap for `moderate_sustained`,
  96.4% for `heavy_sustained` (target: ~100%; runs below 90% flagged, not
  silently included).

## Method

Full design in the plan doc. Summary: `none` / `moderate_sustained`
(1,000 eps target) / `heavy_sustained` (1,700 eps target), each condition
run as short(n=30) + medium(n=30) + long(n=45) = 105 queries,
315 total. Tiers were locked from a dedicated capacity-validation
measurement (steady-state ~1,970 eps observed), not guessed. Overlap
telemetry came from a new live endpoint
(`IngestionMetricsController` in the ingestion service) polled before,
during (every ~200ms), and after each query — not log correlation.

**Two real bugs were caught and fixed during stage 1** (see the plan doc
for detail): a dedup-key mismatch that silently dropped every event in a
suffixed condition, and a flush-lag race in cleanup (a full-speed burst
can take 30-40s to fully flush to MinIO, not the ~8s that worked for
smaller runs — caught by polling until the partition's object count
stabilized rather than trusting a fixed sleep).

Cleanup after each sustained condition used the same partition-snapshot-diff
tooling as Week 3/3.5 (`minio_partition_guard.py`); final check confirmed
exactly the original 130 objects/3 prefixes and TPC-H counts unchanged
(150,000/600,572) after all three conditions.

## Overlap / actual rate validation

| Condition | Target eps | Mean actual eps | Mean overlap | Runs <90% overlap |
|---|--:|--:|--:|--:|
| moderate_sustained | 1,000 | 1,057.0 | 98.0% | 10/105 |
| heavy_sustained | 1,700 | 1,652.5 | 96.4% | 15/105 |

Both conditions tracked their target rate closely (within ~6% and ~3%
respectively) and maintained high overlap with query execution windows —
the core requirement section 2 of the plan set out ("ingestion must span
the query runtime").

## Results (median-based — see outlier note below for why)

| Class | none median (n) | moderate_sustained median | diff (%) | p | heavy_sustained median | diff (%) | p |
|---|---|---|---|--:|---|---|--:|
| short | 282.6ms (30) | 300.6ms | +6.4% | 0.243 | 255.7ms | -9.5% | 0.975 |
| medium | 235.6ms (30) | 225.5ms | -4.3% | 0.978 | 231.7ms | -1.7% | 0.691 |
| long | 775.3ms (45) | 845.8ms | **+9.1%** | **0.0002** | 790.0ms | +1.9% | 0.207 |

One-sided permutation test on medians, 20,000 resamples, same method as
Checkpoint 1 and Week 3.

## Outlier note — why median, not mean, is the headline metric here

Two isolated single-run spikes distorted mean-based stats badly:
`short/none` run 13 hit 5054.5ms (vs. a typical ~280ms — 18x);
`medium/heavy_sustained` run 8 hit 4343.1ms (vs. a typical ~230ms — 19x).
Both are consistent with the "occasional severe outlier" pattern Checkpoint
1 already documented for this box, just larger than what was seen there.
Trimming the single max value from each cell: `short/none` mean drops from
454.7ms to 296.0ms (stdev 870.5→55.3); `medium/heavy_sustained` mean drops
from 387.3ms to 250.9ms (stdev 751.2→79.6) — confirms these are one-run
artifacts, not a systematic mean shift, and confirms the median-based
comparisons above aren't hiding a mean-driven story that got dropped for
convenience.

## The long-query question — was this hypothesis right?

The original question (plan section "Objective"): does `long`'s null
result under Week 3's one-shot `heavy_burst` (`results/week3/section3_dataset_findings.md`:
p=0.79 mean, p=0.75 median, n=30) reflect true insensitivity, or was the
burst pattern just too bursty relative to `long`'s ~1s runtime?

**Answer: insensitivity was the wrong read — but so would "heavy sustained
ingestion always hurts long queries" be.** `moderate_sustained` (1,000 eps,
98% overlap) produces a clean, decisive effect (p=0.0002). `heavy_sustained`
(1,700 eps, 96.4% overlap) — a *faster* rate with comparably good overlap —
does not (p=0.207). Both conditions kept scan scope frozen and both
sustained real, verified concurrent writing throughout nearly the entire
query window, so the difference isn't an overlap-quality artifact by the
numbers above.

Two live hypotheses, not yet distinguished by this data:
1. **A resource-contention sweet spot.** Moderate-rate writes might
   interleave with `long`'s two-table-scan-plus-join access pattern in a
   way that maximizes lock/IO/thread contention, while heavy-rate writes
   might saturate a different resource (e.g. network or MinIO throughput)
   in a way that doesn't specifically collide with `long`'s query plan the
   same way.
2. **Statistical noise on the heavy side**, not the moderate side — long's
   `none` baseline stdev is smaller here (Week 3.6 measured lower absolute
   variance than Week 3's original pass) but n=45 per cell should still
   give reasonable power; the heavy_sustained p-value (0.207) isn't close
   enough to significance to call this likely, but isn't ruled out either.

Not resolved by this pass — flagged as the natural next question rather
than force-fit to a tidy story.

## What this means for the paper

- **"Ingestion affects query latency" needs its `long`-class story
  rewritten.** Week 3's summary ("no significant effect for the long
  query") was correct for one-shot bursts specifically, not a general
  statement about `long`'s sensitivity to concurrent ingestion — Week 3.6
  shows a real, non-trivial effect exists, just not where the original
  burst-shaped test could see it.
- **`short`/`medium` flip relative to Week 3.** They were the classes with
  clear one-shot-burst effects; under sustained rates, neither shows a
  significant effect. Worth stating plainly rather than picking whichever
  week's numbers fit a cleaner narrative — different mechanisms
  (instantaneous write contention vs. sustained resource pressure) appear
  to hit different query shapes differently.
- **The non-monotonic moderate-vs-heavy result is itself a finding worth
  keeping in the paper**, not smoothing into "sustained ingestion hurts
  long queries" — it's evidence that a real ingestion-aware scheduler needs
  more than a single "is ingestion active" binary or even a single rate
  threshold; the *rate itself*, not just its presence, matters in a
  non-obvious way for at least one query class.

## Status against the plan's checklist

Sections 2 (rate tiers), 5 (telemetry), 8 (run structure) all executed as
designed, with 2 real bugs found and fixed during stage 1 (dedup-key
mismatch, flush-lag race) and one design correction during stage 3
(telemetry made unconditional rather than skipped for `target_eps=0`, for
a stronger "measured zero" rather than an assumed one). All cleanup
verified — TPC-H exactly 150,000/600,572 throughout, no leftover objects.
