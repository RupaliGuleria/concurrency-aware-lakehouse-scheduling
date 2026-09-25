# Week 3.6 reproducibility attempt — INVALIDATED, not a valid test

**Status: invalidated by Rupali (2026-08-25). Do not cite the numbers below
as evidence about reproducibility, in either direction.** The attempt is
kept here only as a record that it was tried and why it doesn't count —
not as a data point. Raw data (`results/week3_6/dataset_r2.csv`) should be
treated the same way: not usable for analysis.

**Why it's invalid:** the repeat's `none` block ran cleanly and early, but
the sustained-condition blocks were pushed 30-50+ minutes later by a local
system crash mid-run (which forced discarding and redoing the
`moderate_sustained` block) and two ingestion-service restarts. That's a
fundamentally different measurement setup from the original run's tight,
uninterrupted back-to-back sequence — the "did it reproduce?" question
needs all three conditions run under comparable conditions to mean
anything, and this attempt didn't achieve that. See the sequence-of-events
and analysis below for what was observed, but treat it as "this specific
attempt failed to be a valid test," not as "the original finding didn't
reproduce."

A real reproducibility check is still an open item — needs a clean,
uninterrupted repeat, not a patch on this one.

---

*Original writeup follows, preserved for the record:*

A full repeat of Week 3.6 (none/moderate_sustained/heavy_sustained ×
short/medium/long, 315 queries) was run to check whether the headline
finding — `long` significant under `moderate_sustained` (p=0.0002) but not
`heavy_sustained` (p=0.207) — reproduces. Raw data:
`results/week3_6/dataset_r2.csv` (repeat) vs `results/week3_6/dataset.csv`
(original).

## TL;DR

- **The original result did not reproduce as measured** — the repeat shows
  every sustained condition *faster* than `none`, opposite direction from
  the original, with no significant differences.
- **This is not read as evidence the original finding was wrong.** The
  repeat's own timeline was disrupted by real events between its `none`
  block and its sustained-condition blocks: a local system crash mid-run
  (contaminating and forcing a full redo of the first `moderate_sustained`
  attempt) and two ingestion-service restarts. `none` ended up measured in
  an early, isolated ~90-second window; the sustained conditions were
  measured 30-50+ minutes later, after significant intervening system
  disruption — breaking the "all three conditions close together in one
  session" design the original run and this repeat both intended.
- **Conclusion: reproducibility is an open question, not resolved either
  way by this attempt.** A valid test needs all three conditions run
  back-to-back without interruption, which this attempt failed to do for
  reasons outside the experiment design itself.

## What happened, in sequence

1. `none` (repeat): ran cleanly, 105 queries, ~18:25-18:29 UTC.
2. `moderate_sustained` (repeat), attempt 1: warm-up gate timed out
   (system CPU spiked to 61-71%, dominated by `com.docker.backend`) —
   caught correctly, no data written.
3. Freed up CPU (closed background apps), retried `moderate_sustained` —
   warm-up succeeded, but run 43/45 of `long` hit a 58,945ms outlier with
   ingestion eps crashing to 16.9 (target 1000) — a **local system crash**
   during the run, confirmed independently. Discarded the entire
   contaminated `moderate_sustained` block per your call, rather than
   patching around 2-3 bad rows.
4. Reran `moderate_sustained` cleanly — no anomalies, normal runtime
   ranges throughout.
5. `heavy_sustained` (repeat), attempt 1: warm-up gate timed out again —
   this time CPU was *low* (27-34%), but achievable throughput had
   genuinely dropped to ~1200-1270 eps against a 1700 target, for reasons
   not fully diagnosed (plausibly the ingestion service JVM after many
   hours of continuous operation across all of today's testing).
6. Restarted the ingestion service for a clean slate, retried
   `heavy_sustained` — stabilized immediately at 1693.7 eps, ran cleanly.

Every one of these interruptions was caught and handled correctly (warm-up
gate refused instability, the crash was identified and its data discarded,
cleanup verified TPC-H untouched throughout) — but collectively they
stretched what was meant to be one continuous ~15-20 minute session (like
the original run) into one spanning 50+ minutes with a crash and two
service restarts in the middle.

## Results comparison

| Class | Condition | Original median (n) | Original p | Repeat median (n) | Repeat p |
|---|---|--:|--:|--:|--:|
| short | none | 282.6ms (30) | — | 319.1ms (30) | — |
| short | moderate_sustained | 300.6ms | 0.243 | 281.4ms | 0.984 |
| short | heavy_sustained | 255.7ms | 0.975 | 276.0ms | 0.996 |
| medium | none | 235.6ms (30) | — | 260.5ms (30) | — |
| medium | moderate_sustained | 225.5ms | 0.978 | 231.0ms | 0.999 |
| medium | heavy_sustained | 231.7ms | 0.691 | 249.7ms | 0.992 |
| long | none | 775.3ms (45) | — | 963.8ms (45) | — |
| long | moderate_sustained | 845.8ms | **0.0002** | 785.8ms | 1.000 |
| long | heavy_sustained | 790.0ms | 0.207 | 873.9ms | 0.994 |

The repeat's `long`/`none` baseline (963.8ms) is **24% higher** than the
original's (775.3ms) — and higher than either of the repeat's *own*
sustained-condition medians, measured later in the session. Checked the
raw `none`/`long` timings for within-block drift (a cache-warming trend
that might explain the elevated baseline) — found none; runtimes bounce
between ~700-1400ms throughout the block with no trend. This points to a
block-to-block (session-level) shift, not a within-block artifact: this
particular `none` block happened to run during a slower overall system
state than the (much later, post-crash, post-restart) sustained blocks.

## Why this matters methodologically

This is the same class of confound already flagged in
`results/week3/section3_dataset_findings.md` (Section 3's `none` running
before `heavy_burst` in one session, with possible cache-warming drift) —
except here it's far larger, because real infrastructure events (not just
ordinary session drift) separated the conditions by tens of minutes
instead of running back-to-back. It reinforces a pattern this project keeps
finding: **absolute latency numbers are not stable across sessions or even
across widely-spaced blocks within a session**, and any comparison between
conditions is only as trustworthy as how tightly those conditions were run
together in time. The original Week 3.6 run's conditions were run close
together (~15-20 minutes total, no interruptions); this repeat's were not.

## What this doesn't tell us

- It does **not** show the original finding was a false positive — the
  repeat isn't a clean enough test to conclude that either way.
- It does **not** show sustained ingestion makes queries faster — the
  negative diffs are much more plausibly a `none`-baseline timing artifact
  than a real effect, given the magnitude (24% baseline shift with no
  mechanistic story) and the lack of a within-block trend to explain it
  another way.

## Recommended next step (not yet done)

A genuine reproducibility check needs all three conditions run back-to-back
in one uninterrupted session, the same way the original run was — ideally
immediately after confirming the environment is stable (CPU load normal,
ingestion service freshly started, no pending infrastructure issues) so
there's no need to stop mid-sequence. Worth scheduling deliberately rather
than attempting opportunistically.
