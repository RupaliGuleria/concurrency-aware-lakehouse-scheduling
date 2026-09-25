# Section 6 — baseline scheduler comparison (margin strategies evaluated, not picked)

**Superseded by a more rigorous follow-up**: this doc's dataset
(`section3_dataset.csv`) predates the warm-up fix and likely carries the
same session-order confound Week 3.6 found elsewhere (see the "Known
confound" note below, which was already flagging this before it was
confirmed). A properly counterbalanced re-run with a real tune/held-out
split lives in
`results/week3_margin_tuning/margin_tuning_report.md` — it confirms this
doc's core structural finding (any margin above the tight tier's 1.2x
multiplier admits nothing) and goes further: within the workable region,
the specific margin value turns out not to matter at all (a flat
admit-rate/miss-rate frontier), which is why global and per-class tuning
converge to the identical answer. Treat that doc as the current result;
this one is kept for the historical record of how the question was first
approached.

Generated 2026-09-09 by `scripts/evaluate_baseline_schedulers.py`. Answers the open item left in `research-plan/week3_task_list.md` and `results/week3/scheduler_margin_options.md` - rather than picking Option A (per-class margin) or Option B (fixed global margin) upfront, both are implemented as independent baseline schedulers and evaluated empirically, side by side, against the same replay dataset and SLA definitions.

## Methodology

Replay dataset: `results/week3/section3_dataset.csv` (180 real collected query runs, `condition` in {none, heavy_burst}, `query_class` in {short, medium, long}, 30 runs per class per condition, 0 errors). Same dataset used for every scheduler and every SLA tier - required for a fair comparison (point 6 of the request this doc answers).

SLA deadlines: `class_baseline_p50 x SLA_MULTIPLIERS[tier]` using the frozen Section 2 p50s (`BASELINE_P50_MS` = {'short': 536.5, 'medium': 472.8, 'long': 1154.9}) and A2 multipliers (`SLA_MULTIPLIERS` = {'relaxed': 4.0, 'moderate': 2.0, 'tight': 1.2}).

Four schedulers evaluated (see `scripts/baseline_schedulers.py`):

- **FIFO** — always admit, no runtime estimate.
- **PointEstimate** — admit if `class_p50 <= deadline`.
- **FixedMargin** — admit if `class_p50 x 1.4 <= deadline`, same multiplier for every class.
- **PerClassMargin** — admit if `class_p50 x class_margin <= deadline`, `class_margin` = {'short': 1.4, 'medium': 1.31, 'long': 1.35}.

**Leakage caveat** (per the instruction that raised this): the FixedMargin and PerClassMargin values above were originally derived in part from this same Section 3 dataset's observed variance (`results/week3/scheduler_margin_options.md`). They are used here as fixed, already-decided candidate values - nothing in this script re-tunes them against this data - but that means this is not a fully clean held-out test of "is per-class tuning better"; it's an evaluation of two specific pre-existing candidates. Any future re-tuning of the margin values themselves must use a held-out subset, not this evaluation set.

**Known confound this dataset predates**: Week 3.6 later found a session-order effect (Trino server-side cache warm-up, and a separate session-time drift) that can masquerade as a real ingestion effect if conditions always run in the same fixed order within one session - see `research-plan/week3_6_counterbalanced_retest_plan.md`. This dataset's `none`/`heavy_burst` blocks were not counterbalanced either. It doesn't invalidate the scheduler-comparison logic below (all four schedulers see the identical rows, so the confound - if present - hits every scheduler equally), but the `none` vs `heavy_burst` breakdown further down should not be over-read as a clean measurement of ingestion's effect on its own.

## Comparison table (all SLA tiers)

| Scheduler | Margin strategy | SLA tier | n | Admit rate | SLA miss rate (admitted) | Admitted+missed | Unnecessary rejections |
|---|---|---|--:|--:|--:|--:|--:|
| fifo | none | relaxed | 180 | 100.0% | 0.0% | 0 | 0 |
| fifo | none | moderate | 180 | 100.0% | 0.0% | 0 | 0 |
| fifo | none | tight | 180 | 100.0% | 2.2% | 4 | 0 |
| point_estimate | none (p50 only) | relaxed | 180 | 100.0% | 0.0% | 0 | 0 |
| point_estimate | none (p50 only) | moderate | 180 | 100.0% | 0.0% | 0 | 0 |
| point_estimate | none (p50 only) | tight | 180 | 100.0% | 2.2% | 4 | 0 |
| fixed_margin | 1.4x global | relaxed | 180 | 100.0% | 0.0% | 0 | 0 |
| fixed_margin | 1.4x global | moderate | 180 | 100.0% | 0.0% | 0 | 0 |
| fixed_margin | 1.4x global | tight | 180 | 0.0% | - | 0 | 176 |
| per_class_margin | short 1.40 / medium 1.31 / long 1.35 | relaxed | 180 | 100.0% | 0.0% | 0 | 0 |
| per_class_margin | short 1.40 / medium 1.31 / long 1.35 | moderate | 180 | 100.0% | 0.0% | 0 | 0 |
| per_class_margin | short 1.40 / medium 1.31 / long 1.35 | tight | 180 | 0.0% | - | 0 | 176 |
| adaptive (ingestion-aware) | learned/context-aware | - | - | - | - | - | - *(not yet implemented - Week 4)* |

**Headline structural finding**: at the `tight` tier (1.2x p50 deadline), both margin schedulers admit **zero** queries in every class and every condition. Their margins (1.31-1.40x) always exceed the tight deadline's 1.2x multiplier, so `predicted > deadline` by construction regardless of actual runtime variance - not a data-driven rejection, a structural one. This is exactly the failure mode point 7 warned about: trivially zero SLA misses by rejecting everything. At `relaxed`/`moderate` (4x/2x), all four schedulers admit everything and neither margin strategy changes anything, since even p50 alone clears those deadlines by a wide margin - the only tier where any of these baselines actually differ from each other is `tight`.

## Reliability vs. admission efficiency, `tight` tier only (the only tier with real differentiation)

| Scheduler | Admit rate | SLA miss rate (admitted) | Reading |
|---|--:|--:|---|
| fifo | 100.0% | 2.2% | admits everything, 2/180 misses slip through uncaught |
| point_estimate | 100.0% | 2.2% | identical to FIFO at this tier - p50 alone already clears 1.2x for every class, so it never rejects either |
| fixed_margin | 0.0% | - | perfectly reliable (no admits = no misses) but useless - rejects 100% of real, mostly-on-time queries |
| per_class_margin | 0.0% | - | same failure as fixed_margin - being class-specific doesn't help once the margin itself exceeds the tier multiplier |

## Breakdown by query class (`tight` tier)

| Scheduler | Class | n | Admit rate | SLA miss rate (admitted) |
|---|---|--:|--:|--:|
| fifo | long | 60 | 100.0% | 1.7% |
| fifo | medium | 60 | 100.0% | 0.0% |
| fifo | short | 60 | 100.0% | 5.0% |
| point_estimate | long | 60 | 100.0% | 1.7% |
| point_estimate | medium | 60 | 100.0% | 0.0% |
| point_estimate | short | 60 | 100.0% | 5.0% |
| fixed_margin | long | 60 | 0.0% | - |
| fixed_margin | medium | 60 | 0.0% | - |
| fixed_margin | short | 60 | 0.0% | - |
| per_class_margin | long | 60 | 0.0% | - |
| per_class_margin | medium | 60 | 0.0% | - |
| per_class_margin | short | 60 | 0.0% | - |

## Breakdown by ingestion condition (`tight` tier)

| Scheduler | Condition | n | Admit rate | SLA miss rate (admitted) |
|---|---|--:|--:|--:|
| fifo | heavy_burst | 90 | 100.0% | 2.2% |
| fifo | none | 90 | 100.0% | 2.2% |
| point_estimate | heavy_burst | 90 | 100.0% | 2.2% |
| point_estimate | none | 90 | 100.0% | 2.2% |
| fixed_margin | heavy_burst | 90 | 0.0% | - |
| fixed_margin | none | 90 | 0.0% | - |
| per_class_margin | heavy_burst | 90 | 0.0% | - |
| per_class_margin | none | 90 | 0.0% | - |

## Answers to the research questions this evaluation can answer

1. **Does adding any safety margin help over plain p50?** Not with these candidate values - both margin strategies are strictly worse than plain PointEstimate at `tight` (0% admit vs 100% admit with only a 2.2% miss rate) and identical to it at `relaxed`/`moderate` (all clear those deadlines regardless). A margin only helps if it's smaller than the tightest SLA multiplier it needs to operate under - these margins (1.31-1.40x) are larger than the tight tier's own multiplier (1.2x), so they can never admit anything at that tier.
2. **Does per-class tuning improve over one global margin?** No measurable difference in this evaluation - both reject 100% of `tight`-tier queries and behave identically at the other two tiers, because every class's margin (1.31-1.40x) exceeds the tight multiplier (1.2x) regardless of which specific value is used. The per-class values would need to be re-derived with the tight-tier ceiling as an explicit constraint before this question is meaningfully testable.
3. **Does the future ingestion-aware scheduler add value beyond both?** Not yet answerable - not implemented (Week 4).

## Implication for the margin values themselves

Neither candidate margin (Option A per-class, Option B fixed 1.40x) is usable as-is for the `tight` SLA tier - both need to be re-derived so the margin multiplier stays below 1.2x, or the tight tier's own multiplier needs revisiting, before either margin scheduler can do anything at that tier besides reject everything. This is a concrete, data-backed reason to treat the `scheduler_margin_options.md` values as initial benchmarks rather than final ones, independent of the separate session-drift caveat raised in Week 3.6. Re-deriving them should use a held-out subset of the data, not this evaluation set (see the leakage caveat above).

