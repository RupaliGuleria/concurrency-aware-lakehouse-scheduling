# Week 3.6 — counterbalanced result (closes the open question)

Written 2026-09-10. Resolves the open item left by
`research-plan/week3_6_next_steps.md`: does sustained ingestion genuinely
slow down `long` queries, or was the original finding a session-order
artifact? Uses the mirrored ABC-CBA data collected in
`results/week3_margin_tuning/` for the margin-tuning protocol — same
warm-up-fixed harness (`run_week3_6_condition.py`), same mirrored order
(`none -> moderate -> heavy -> heavy -> moderate -> none`) the
counterbalanced plan called for, so it doubles as this test with no new
live testing needed. Analysis: `scripts/week3_6_counterbalanced_long_test.py`.

## TL;DR

**The original headline finding does not reproduce.** Once compared
fairly (same session position, same warm-up opportunity), `long` shows no
significant difference between `none`, `moderate_sustained`, and
`heavy_sustained`. The original result (`long` significantly slower under
`moderate_sustained`, p=0.0002, `results/week3_6_findings.md`) is best
explained as a session-order artifact, not a real ingestion effect.
**Treat that original finding as refuted, not just unconfirmed.**

## Raw per-block stats (`long` class, n=22 each)

| Block | Condition | Position | Median | Mean | Stdev |
|---|---|---|--:|--:|--:|
| A1 | none | 1st (session start) | 1170.0ms | 1165.0ms | 126.6ms |
| B1 | moderate_sustained | 2nd | 877.0ms | 877.7ms | 23.4ms |
| C1 | heavy_sustained | 3rd | 848.3ms | 877.5ms | 89.8ms |
| C2 | heavy_sustained | 4th | 864.1ms | 867.0ms | 34.7ms |
| B2 | moderate_sustained | 5th | 860.8ms | 862.3ms | 29.5ms |
| A2 | none | 6th (session end) | 879.4ms | 913.3ms | 99.3ms |

**A1 is a dramatic, isolated outlier** — every other block, regardless of
condition, clusters tightly in the 848-880ms range (a 32ms spread across 5
blocks). This is a one-time jump between "the very first Trino query block
of the entire session" and everything after it, not a smooth drift —
consistent with Trino/filesystem-cache warm-up that persists across
separate client processes (the same mechanism identified in the r5 session
for `short`/`medium`, here showing up in `long` too).

## Contrasts

**Early** (baseline: A1, itself the anomalous first block):

| Comparison | Diff | % | p (one-sided, slower) |
|---|--:|--:|--:|
| moderate_sustained vs none | -293.0ms | -25.0% | 1.0000 |
| heavy_sustained vs none | -321.8ms | -27.5% | 1.0000 |

**Late** (baseline: A2, on equal footing with B2/C2 — the fair comparison):

| Comparison | Diff | % | p (one-sided, slower) |
|---|--:|--:|--:|
| moderate_sustained vs none | -18.6ms | -2.1% | 0.8862 |
| heavy_sustained vs none | -15.2ms | -1.7% | 0.8932 |

**Pooled** (A1+A2 vs B1+B2 vs C1+C2 — included for completeness, but
misleading here since it mixes A1's outlier into the `none` baseline):

| Comparison | Diff | % | p (one-sided, slower) |
|---|--:|--:|--:|
| moderate_sustained vs none | -157.8ms | -15.3% | 1.0000 |
| heavy_sustained vs none | -171.1ms | -16.6% | 1.0000 |

The pooled and early numbers look dramatic only because they're dragged
down by A1. The **late contrast is the trustworthy one** — both
comparisons are null (p=0.886, p=0.893), and the effect sizes are tiny
(-1.7% to -2.1%), well within normal run-to-run noise.

## Why this settles it

Two things point the same direction:

1. Once past the first block, `none`/`moderate_sustained`/`heavy_sustained`
   are statistically indistinguishable from each other for `long`
   (848-880ms regardless of condition).
2. The one comparison that isn't contaminated by the first-block anomaly
   (late vs late) shows no significant difference in either direction.

Neither the original hypothesis (ingestion slows `long` down) nor its
mirror image (ingestion speeds `long` up, which the early/pooled numbers
would naively suggest) is supported once session position is controlled
for. The honest conclusion is **no measurable effect** on `long` query
latency from sustained ingestion at these rates (1000/1700 eps), at least
at the scan-scope and data volume tested so far.

## What this changes

- `results/week3_6_findings.md`'s original headline is superseded — see
  that file's update pointing here.
- `research-plan/week3_6_next_steps.md`'s open item is closed.
- The paper's ingestion-effect story rests on the earlier, still-valid
  Week 3 finding instead: `short`/`medium` showed a significant
  concurrent-write effect at n=30 (`results/week3_findings.md`,
  p=0.007/p<0.0001) — that result used `heavy_burst` (Section 3's own
  ingestion pattern, not the sustained rate-limited producer) and predates
  the warm-up fix, so it carries the same caveat flagged in
  `results/week3/section6_scheduler_baseline_comparison.md` and would
  benefit from the same counterbalanced treatment if it becomes
  load-bearing for the paper later — not urgent now, flagged for when it
  matters.
