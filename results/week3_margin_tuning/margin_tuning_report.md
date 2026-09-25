# Margin tuning protocol — results

Generated 2026-09-10 by `scripts/tune_and_evaluate_margins.py`. Full methodology in `scripts/tune_and_evaluate_margins.py`'s module docstring - summary below.

## Data collection

Fresh, counterbalanced collection (not reusing `section3_dataset.csv`, which was found to predate the warm-up fix and likely carries the same session-order confound Week 3.6 diagnosed elsewhere). Mirrored ABC-CBA design: `none -> moderate_sustained -> heavy_sustained -> heavy_sustained -> moderate_sustained -> none`, each block using `run_week3_6_condition.py` (per-query warm-up pass already fixed), reduced n per block (short/medium=15, long=22) per `research-plan/week3_6_counterbalanced_retest_plan.md`.

| Block | Condition | Position | n | tuning | held-out |
|---|---|---|--:|--:|--:|
| block_A1_none.csv | none | early | 52 | 25 | 27 |
| block_B1_moderate.csv | moderate_sustained | early | 52 | 25 | 27 |
| block_C1_heavy.csv | heavy_sustained | early | 52 | 25 | 27 |
| block_C2_heavy.csv | heavy_sustained | late | 52 | 25 | 27 |
| block_B2_moderate.csv | moderate_sustained | late | 52 | 25 | 27 |
| block_A2_none.csv | none | late | 52 | 25 | 27 |

**Pooling**: each condition's early+late instances (e.g. `block_A1_none` + `block_A2_none`) are combined before use, specifically to cancel the session-position effect confirmed in this data (early `none` block: short median 512.5ms; late `none` block: short median 286.7ms - a same-condition, position-only swing of -44%, the same shape Week 3.6 found elsewhere).

**Split**: within each block, first half of each query_class's run_index range -> tuning, second half -> held-out - applied identically to all 6 blocks, so both pools stay position-balanced (neither pool is systematically all-early or all-late). Tuning pool: 150 rows. Held-out pool: 162 rows.

## Tuning-set Pareto frontier — global margin, tight tier

Grid searched on tuning data only, before touching held-out data. No acceptable-miss-rate threshold has ever been defined for this project, so the full trade-off is reported rather than picking one point silently.

| Margin | Admit rate | SLA miss rate (admitted) | Admitted+met | Admitted+missed |
|--:|--:|--:|--:|--:|
| 1.00x | 100.0% | 1.3% | 148 | 2 |
| 1.02x | 100.0% | 1.3% | 148 | 2 |
| 1.04x | 100.0% | 1.3% | 148 | 2 |
| 1.06x | 100.0% | 1.3% | 148 | 2 |
| 1.08x | 100.0% | 1.3% | 148 | 2 |
| 1.10x | 100.0% | 1.3% | 148 | 2 |
| 1.12x | 100.0% | 1.3% | 148 | 2 |
| 1.14x | 100.0% | 1.3% | 148 | 2 |
| 1.16x | 100.0% | 1.3% | 148 | 2 |
| 1.18x | 100.0% | 1.3% | 148 | 2 |
| 1.20x | 100.0% | 1.3% | 148 | 2 |
| 1.22x | 0.0% | - | 0 | 0 |
| 1.24x | 0.0% | - | 0 | 0 |
| 1.26x | 0.0% | - | 0 | 0 |
| 1.28x | 0.0% | - | 0 | 0 |
| 1.30x | 0.0% | - | 0 | 0 |
| 1.32x | 0.0% | - | 0 | 0 |
| 1.34x | 0.0% | - | 0 | 0 |
| 1.36x | 0.0% | - | 0 | 0 |
| 1.38x | 0.0% | - | 0 | 0 |
| 1.40x | 0.0% | - | 0 | 0 |

**Structural finding, not a tuning result**: every margin from 1.00x to 1.20x produces the *identical* admit rate and miss rate, then the grid collapses to 0% admission at 1.22x+. This isn't a coincidence of this data - it's how a point-estimate-plus-margin scheduler works by construction. Admission is a boolean gate (`predicted <= deadline`); once a query class is admitted, whether it individually meets its deadline depends only on its own actual runtime vs. the deadline - never on which specific margin was used to decide admission. So there is no interior trade-off to grid-search within the safe region (below the tier's own multiplier) - the only real decision is binary: operate this class under this tier at all (at its own intrinsic miss rate), or reject it outright. A margin scheduler cannot discriminate risk *within* a class the way a per-query adaptive predictor could - this is itself a concrete argument for why the paper's ingestion-aware scheduler needs to reason per-query, not per-class.

**Frozen value carried to held-out (global)**: 1.20x - the largest margin in the grid that still admits anyone. Given the flat frontier above, this is not "the best" margin in any meaningful sense (every workable value behaves identically) - it's simply a well-defined, non-arbitrary choice rather than picking an interior value with no principled reason to prefer it.

## Tuning-set Pareto frontier — per-class margin, tight tier

### short

| Margin | Admit rate | SLA miss rate (admitted) | Admitted+met | Admitted+missed |
|--:|--:|--:|--:|--:|
| 1.00x | 100.0% | 2.4% | 41 | 1 |
| 1.02x | 100.0% | 2.4% | 41 | 1 |
| 1.04x | 100.0% | 2.4% | 41 | 1 |
| 1.06x | 100.0% | 2.4% | 41 | 1 |
| 1.08x | 100.0% | 2.4% | 41 | 1 |
| 1.10x | 100.0% | 2.4% | 41 | 1 |
| 1.12x | 100.0% | 2.4% | 41 | 1 |
| 1.14x | 100.0% | 2.4% | 41 | 1 |
| 1.16x | 100.0% | 2.4% | 41 | 1 |
| 1.18x | 100.0% | 2.4% | 41 | 1 |
| 1.20x | 100.0% | 2.4% | 41 | 1 |
| 1.22x | 0.0% | - | 0 | 0 |
| 1.24x | 0.0% | - | 0 | 0 |
| 1.26x | 0.0% | - | 0 | 0 |
| 1.28x | 0.0% | - | 0 | 0 |
| 1.30x | 0.0% | - | 0 | 0 |
| 1.32x | 0.0% | - | 0 | 0 |
| 1.34x | 0.0% | - | 0 | 0 |
| 1.36x | 0.0% | - | 0 | 0 |
| 1.38x | 0.0% | - | 0 | 0 |
| 1.40x | 0.0% | - | 0 | 0 |

Flat frontier again for `short` - same structural reason as the global grid above.
**Frozen value carried to held-out (short)**: 1.20x

### medium

| Margin | Admit rate | SLA miss rate (admitted) | Admitted+met | Admitted+missed |
|--:|--:|--:|--:|--:|
| 1.00x | 100.0% | 0.0% | 42 | 0 |
| 1.02x | 100.0% | 0.0% | 42 | 0 |
| 1.04x | 100.0% | 0.0% | 42 | 0 |
| 1.06x | 100.0% | 0.0% | 42 | 0 |
| 1.08x | 100.0% | 0.0% | 42 | 0 |
| 1.10x | 100.0% | 0.0% | 42 | 0 |
| 1.12x | 100.0% | 0.0% | 42 | 0 |
| 1.14x | 100.0% | 0.0% | 42 | 0 |
| 1.16x | 100.0% | 0.0% | 42 | 0 |
| 1.18x | 100.0% | 0.0% | 42 | 0 |
| 1.20x | 100.0% | 0.0% | 42 | 0 |
| 1.22x | 0.0% | - | 0 | 0 |
| 1.24x | 0.0% | - | 0 | 0 |
| 1.26x | 0.0% | - | 0 | 0 |
| 1.28x | 0.0% | - | 0 | 0 |
| 1.30x | 0.0% | - | 0 | 0 |
| 1.32x | 0.0% | - | 0 | 0 |
| 1.34x | 0.0% | - | 0 | 0 |
| 1.36x | 0.0% | - | 0 | 0 |
| 1.38x | 0.0% | - | 0 | 0 |
| 1.40x | 0.0% | - | 0 | 0 |

Flat frontier again for `medium` - same structural reason as the global grid above.
**Frozen value carried to held-out (medium)**: 1.20x

### long

| Margin | Admit rate | SLA miss rate (admitted) | Admitted+met | Admitted+missed |
|--:|--:|--:|--:|--:|
| 1.00x | 100.0% | 1.5% | 65 | 1 |
| 1.02x | 100.0% | 1.5% | 65 | 1 |
| 1.04x | 100.0% | 1.5% | 65 | 1 |
| 1.06x | 100.0% | 1.5% | 65 | 1 |
| 1.08x | 100.0% | 1.5% | 65 | 1 |
| 1.10x | 100.0% | 1.5% | 65 | 1 |
| 1.12x | 100.0% | 1.5% | 65 | 1 |
| 1.14x | 100.0% | 1.5% | 65 | 1 |
| 1.16x | 100.0% | 1.5% | 65 | 1 |
| 1.18x | 100.0% | 1.5% | 65 | 1 |
| 1.20x | 100.0% | 1.5% | 65 | 1 |
| 1.22x | 0.0% | - | 0 | 0 |
| 1.24x | 0.0% | - | 0 | 0 |
| 1.26x | 0.0% | - | 0 | 0 |
| 1.28x | 0.0% | - | 0 | 0 |
| 1.30x | 0.0% | - | 0 | 0 |
| 1.32x | 0.0% | - | 0 | 0 |
| 1.34x | 0.0% | - | 0 | 0 |
| 1.36x | 0.0% | - | 0 | 0 |
| 1.38x | 0.0% | - | 0 | 0 |
| 1.40x | 0.0% | - | 0 | 0 |

Flat frontier again for `long` - same structural reason as the global grid above.
**Frozen value carried to held-out (long)**: 1.20x

## Held-out evaluation — frozen anchors only, not re-tuned

Frozen global margin: **1.20x**. Frozen per-class margins: **{'short': 1.2, 'medium': 1.2, 'long': 1.2}**. Evaluated once, on the held-out pool only, at every SLA tier.

| Scheduler | SLA tier | n | Admit rate | SLA miss rate (admitted) | Admitted+missed |
|---|---|--:|--:|--:|--:|
| fifo | relaxed | 162 | 100.0% | 0.0% | 0 |
| fifo | moderate | 162 | 100.0% | 0.0% | 0 |
| fifo | tight | 162 | 100.0% | 0.6% | 1 |
| point_estimate | relaxed | 162 | 100.0% | 0.0% | 0 |
| point_estimate | moderate | 162 | 100.0% | 0.0% | 0 |
| point_estimate | tight | 162 | 100.0% | 0.6% | 1 |
| fixed_margin | relaxed | 162 | 100.0% | 0.0% | 0 |
| fixed_margin | moderate | 162 | 100.0% | 0.0% | 0 |
| fixed_margin | tight | 162 | 100.0% | 0.6% | 1 |
| per_class_margin | relaxed | 162 | 100.0% | 0.0% | 0 |
| per_class_margin | moderate | 162 | 100.0% | 0.0% | 0 |
| per_class_margin | tight | 162 | 100.0% | 0.6% | 1 |

### Held-out breakdown by query class (`tight` tier)

| Scheduler | Class | n | Admit rate | SLA miss rate (admitted) |
|---|---|--:|--:|--:|
| fifo | long | 66 | 100.0% | 0.0% |
| fifo | medium | 48 | 100.0% | 2.1% |
| fifo | short | 48 | 100.0% | 0.0% |
| point_estimate | long | 66 | 100.0% | 0.0% |
| point_estimate | medium | 48 | 100.0% | 2.1% |
| point_estimate | short | 48 | 100.0% | 0.0% |
| fixed_margin | long | 66 | 100.0% | 0.0% |
| fixed_margin | medium | 48 | 100.0% | 2.1% |
| fixed_margin | short | 48 | 100.0% | 0.0% |
| per_class_margin | long | 66 | 100.0% | 0.0% |
| per_class_margin | medium | 48 | 100.0% | 2.1% |
| per_class_margin | short | 48 | 100.0% | 0.0% |

## Conclusions

**Does per-class tuning improve over one global margin? No - and not just empirically, mechanistically.** The per-class search converged to {'short': 1.2, 'medium': 1.2, 'long': 1.2} independently for each class, and the global search converged to 1.20x - the same value in every case. This isn't a coincidence: the binding constraint on every class's workable margin is the tight tier's own multiplier (1.20x), which is class-independent by definition. Per-class tuning can only ever find a *lower* ceiling than the global search if some class's own intrinsic behavior caps it below 1.20x - it never did here, so both approaches necessarily land on the same value and produce the same held-out result.

**Does adding any margin help over plain p50? No** - PointEstimate, FixedMargin, and PerClassMargin are behaviorally identical on held-out data at every tier, because the frozen margin (1.20x) sits at the same boundary p50 alone already satisfies. The margin adds padding that never changes an admission decision or a miss outcome under the current tier definitions.

**Caveat on the specific held-out miss-rate numbers** (0% short, 2.1% medium, 0% long): these are small-sample percentages (n=41-66 per class per half) where a single missed query swings the rate by 1.5-2.4 percentage points. Medium showed 0% misses on tuning data but 2.1% on held-out - exactly the kind of optimistic-tuning-set gap this split was designed to catch, not evidence that medium is intrinsically riskier than short or long. Do not read these exact percentages as precise risk estimates; read the pattern (flat frontier, global==per-class, margin==no-margin) as the real result.

**Maps to the proposal's expected outcomes**: closest to (A) global performs as well as per-class, combined with a refined version of (C) - not because variance simply "consumes headroom" as originally framed, but because this entire scheduler family has zero ability to discriminate risk *within* a class once it decides to admit that class at all. (D) - whether the adaptive, ingestion-aware scheduler beats both - remains open; it's the natural next comparison, and this result is a concrete, mechanistic argument for why it should be able to: per-query, context-aware admission can do something no fixed-margin class-level gate can, in principle.

