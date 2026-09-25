# Week 4 — multi-query scheduler simulation results

Generated 2026-09-23 by `scripts/simulate_multi_query_scheduling.py`, per `research-plan/week4_scheduler_design.md` section 10. 1000 synthetic batches, size 4-10, seed 42. Scheduler logic verified separately in `scripts/verify_multi_query_schedulers.py` (worked-example reproduction, iterative/one-shot-sort equivalence, DP-vs-brute-force).

**Not a final paper claim** — this bootstrap replay is for scheduler development and initial comparison, per the design doc's item 10 caveat. A final claim needs prediction/calibration and evaluation data separated by session.

**v1.1 pivot** (see `research-plan/week4_scheduler_design.md`'s v1.1 addendum): `predicted_adaptive_ms` now reacts to **background concurrent query load** (`low`/`moderate`/`high`, built from `results/week4/concurrency_contention.csv`), not ingestion condition. `ingestion_condition` is still generated and recorded per batch, shown in its own breakdown below, but no longer drives any prediction — four independent tests this session confirmed it barely moves runtime at this hardware's scale, while concurrency alone produced a 19x slowdown.

Prediction lookup (`predicted_static_ms` / per-concurrency-tier `predicted_adaptive_ms`): `{'short': 286.71, 'medium': 248.42, 'long': 879.375}` (static, low-concurrency/uncontended only) vs `{'short/low': 286.7, 'short/moderate': 1630.6, 'short/high': 5771.7, 'medium/low': 248.4, 'medium/moderate': 2487.1, 'medium/high': 5694.8, 'long/low': 879.4, 'long/moderate': 2637.4, 'long/high': 5963.1}` (adaptive, per concurrency tier).

## Main comparison

| Scheduler | SLA adherence | Weighted SLA adherence | Defer rate | n queries |
|---|--:|--:|--:|--:|
| fifo | 16.3% | 16.5% | - | 6943 |
| priority_first | 16.6% | 20.9% | - | 6943 |
| edf | 20.2% | 20.3% | - | 6943 |
| static_least_slack | 16.9% | 17.5% | - | 6943 |
| adaptive_least_slack | 17.0% | 17.5% | - | 6943 |
| dp_oracle | 28.8% | 31.1% | - | 6943 |
| priority_sla_aware_dp | 28.1% | 30.6% | - | 6943 |
| admission_controlled_dp | 28.4% | 29.6% | 65.2% | 6943 |

## Held-out evaluation (item 6)

Same 1000 batches, same seed, same predictions as the main comparison above - but `actual_runtime_ms` is drawn from `results/week4/concurrency_contention_heldout.csv`, a genuinely separate collection session (2026-09-22/23), instead of resampling the same pool `predicted_adaptive_ms` was built from. This isolates one question: does the picture change when scored against independent ground truth? See `research-plan/week4_paper_framing.md`'s held-out section for the full collection trail (two contaminated attempts diagnosed and fixed - a missing warm-up phase, then a sleep/wake stall - before this clean run).

**Not fully independent for 2 cell(s)**: [('medium', 'low'), ('long', 'low')] - `concurrency_contention_test.py` only cycles the first query class at concurrency=1, so held-out low-tier data doesn't cover every class. These cells fall back to resampling the calibration pool, same as the main comparison, and are not a held-out test for that specific (class, tier) combination.

| Scheduler | SLA adherence (bootstrap-resample) | SLA adherence (held-out) | Weighted (held-out) |
|---|--:|--:|--:|
| fifo | 16.3% | 14.7% | 14.8% |
| priority_first | 16.6% | 14.5% | 18.4% |
| edf | 20.2% | 16.7% | 16.7% |
| static_least_slack | 16.9% | 14.3% | 15.0% |
| adaptive_least_slack | 17.0% | 14.4% | 15.0% |
| dp_oracle | 28.8% | 24.5% | 26.5% |
| priority_sla_aware_dp | 28.1% | 23.8% | 25.8% |
| admission_controlled_dp | 28.4% | 24.3% | 24.8% |

**Core claim under held-out scoring**: static 14.3% vs. adaptive 14.4% SLA adherence - negligible under held-out scoring, same as under bootstrap resampling (16.9% vs. 17.0% there) - consistent with this project's own finding that the adaptive signal shows up in differing execution ORDER (see `orders_differ` above), not a large swing in aggregate adherence rate. Held-out scoring doesn't overturn that picture.

## Admission control (v1.2)

`admission_controlled_dp` wraps `dp_oracle` with a greedy admission gate (default service target 80.0%, `research-plan/week4_scheduler_design.md`'s v1.2 addendum): for each arriving query, `dp_oracle` re-optimizes the trial admitted-set-plus-this-query and the query is admitted only if that trial's PREDICTED adherence rate still meets the target. Deferred queries run last (arrival order) and are still scored against their original deadline - not excluded from SLA accounting, so a high defer rate that doesn't also improve adherence for the admitted queries would be a red flag, not a win.

At the default 80.0% target: `admission_controlled_dp` scored 28.4% SLA adherence **blended across admitted+deferred** with a 65.2% defer rate, vs. plain `dp_oracle`'s 28.8% (0% defer rate, since dp_oracle always admits everything). The blended number looks unimpressive on its own - deferred queries almost always miss, dragging the total down regardless of how well the admitted ones do. The number that actually shows what admission control is for is adherence **among only the queries it chose to admit**: 78.6% - that's the promise it's actually keeping; the blended number mixes that promise with the queries it explicitly gave up on, which is a different question (whether giving up on them was the right call, not whether the promise was kept).

**Sensitivity to the declared service target** (same 1000 batches at each target, per Rupali's request not to treat 80% as final on one number alone):

| Service target | Blended SLA adherence | Admitted-only adherence | Defer rate |
|--:|--:|--:|--:|
| 60.0% | 28.9% | 71.2% | 59.6% |
| 70.0% | 28.6% | 76.3% | 62.6% |
| 80.0% | 28.4% | 81.5% | 65.2% |
| 90.0% | 27.7% | 88.6% | 68.9% |

**DP gap for Adaptive Least Slack** (how close the practical scheduler comes to optimal, given the same predictions): mean 55.4%, median 57.1% across 531 batches where the DP oracle scored above zero.

## Static vs. Adaptive Least Slack — the primary comparison (v1.1: concurrency-aware)

- SLA adherence: static 16.9% vs. adaptive 17.0%
- Weighted SLA adherence: static 17.5% vs. adaptive 17.5%

Same algorithm, same tie-break rules, same deadlines/priorities — the only difference is whether the predictor sees the batch's current background concurrency tier (v1.1 pivot; this comparison used ingestion condition through v1, which showed ~0 effect - see `research-plan/week4_scheduler_design.md`'s v1.1 addendum for why it was replaced). This gap is the paper's core claim for v1.1.

**Diagnostic — why the gap is what it is**: `predicted_static_ms` and `predicted_adaptive_ms` genuinely differ for 4478/6943 queries (64.5%, whenever the batch's concurrency tier isn't `low`). The resulting execution **order** differs between Static and Adaptive Least Slack in 488/1000 batches (48.8%) - unlike the v1 ingestion-based version of this same comparison, where the shift was 2-21ms and orders differed in 0/1000 batches. Concurrency tiers shift predicted runtime by hundreds to thousands of ms (see the prediction lookup above), comparable to the slack spread between different queries in a batch, so it actually has room to flip which query looks most urgent - which is exactly what an adaptive predictor needs to be worth having.

## Ingestion condition — secondary factor, kept for tracking (v1.1)

`ingestion_condition` is still generated per batch and no longer drives any prediction (see v1.1 pivot above). Shown here purely to keep confirming it stays small, not because it's expected to move:

| Scheduler | Ingestion condition | n | SLA adherence |
|---|---|--:|--:|
| fifo | none | 2163 | 16.9% |
| fifo | moderate_sustained | 2470 | 15.7% |
| fifo | heavy_sustained | 2310 | 16.2% |
| priority_first | none | 2163 | 17.1% |
| priority_first | moderate_sustained | 2470 | 16.2% |
| priority_first | heavy_sustained | 2310 | 16.7% |
| edf | none | 2163 | 21.1% |
| edf | moderate_sustained | 2470 | 19.6% |
| edf | heavy_sustained | 2310 | 20.1% |
| static_least_slack | none | 2163 | 17.7% |
| static_least_slack | moderate_sustained | 2470 | 16.0% |
| static_least_slack | heavy_sustained | 2310 | 17.2% |
| adaptive_least_slack | none | 2163 | 17.8% |
| adaptive_least_slack | moderate_sustained | 2470 | 16.1% |
| adaptive_least_slack | heavy_sustained | 2310 | 17.1% |
| dp_oracle | none | 2163 | 29.3% |
| dp_oracle | moderate_sustained | 2470 | 27.9% |
| dp_oracle | heavy_sustained | 2310 | 29.3% |
| priority_sla_aware_dp | none | 2163 | 28.8% |
| priority_sla_aware_dp | moderate_sustained | 2470 | 27.2% |
| priority_sla_aware_dp | heavy_sustained | 2310 | 28.5% |
| admission_controlled_dp | none | 2163 | 29.6% |
| admission_controlled_dp | moderate_sustained | 2470 | 27.3% |
| admission_controlled_dp | heavy_sustained | 2310 | 28.5% |

## Breakdown by concurrency tier

| Scheduler | Concurrency tier | n | SLA adherence |
|---|---|--:|--:|
| fifo | low | 2465 | 42.0% |
| fifo | moderate | 2131 | 4.4% |
| fifo | high | 2347 | 0.0% |
| priority_first | low | 2465 | 42.4% |
| priority_first | moderate | 2131 | 5.2% |
| priority_first | high | 2347 | 0.0% |
| edf | low | 2465 | 56.6% |
| edf | moderate | 2131 | 0.5% |
| edf | high | 2347 | 0.0% |
| static_least_slack | low | 2465 | 47.1% |
| static_least_slack | moderate | 2131 | 0.5% |
| static_least_slack | high | 2347 | 0.0% |
| adaptive_least_slack | low | 2465 | 47.1% |
| adaptive_least_slack | moderate | 2131 | 0.7% |
| adaptive_least_slack | high | 2347 | 0.0% |
| dp_oracle | low | 2465 | 71.4% |
| dp_oracle | moderate | 2131 | 11.3% |
| dp_oracle | high | 2347 | 0.0% |
| priority_sla_aware_dp | low | 2465 | 70.1% |
| priority_sla_aware_dp | moderate | 2131 | 10.6% |
| priority_sla_aware_dp | high | 2347 | 0.0% |
| admission_controlled_dp | low | 2465 | 70.1% |
| admission_controlled_dp | moderate | 2131 | 11.5% |
| admission_controlled_dp | high | 2347 | 0.0% |

## Priority tie-break activation

| Scheduler | Tie-breaks fired | Decision points | Activation rate |
|---|--:|--:|--:|
| static_least_slack | 2050 | 5943 | 34.5% |
| adaptive_least_slack | 2050 | 5943 | 34.5% |

How often two queries had *exactly* equal slack and priority had to break the tie, vs. how often ordering was decided by slack alone. Low activation is itself a result, not a failure — see the design doc's item 3/9 note on not adding a tolerance-band tie-break without a data-backed reason.

## Breakdown by query class

| Scheduler | Class | n | SLA adherence |
|---|---|--:|--:|
| fifo | short | 2352 | 13.8% |
| fifo | medium | 2282 | 12.1% |
| fifo | long | 2309 | 22.9% |
| priority_first | short | 2352 | 14.1% |
| priority_first | medium | 2282 | 12.8% |
| priority_first | long | 2309 | 23.0% |
| edf | short | 2352 | 21.0% |
| edf | medium | 2282 | 25.9% |
| edf | long | 2309 | 13.9% |
| static_least_slack | short | 2352 | 13.6% |
| static_least_slack | medium | 2282 | 18.3% |
| static_least_slack | long | 2309 | 18.8% |
| adaptive_least_slack | short | 2352 | 13.6% |
| adaptive_least_slack | medium | 2282 | 18.5% |
| adaptive_least_slack | long | 2309 | 18.8% |
| dp_oracle | short | 2352 | 30.0% |
| dp_oracle | medium | 2282 | 29.5% |
| dp_oracle | long | 2309 | 26.9% |
| priority_sla_aware_dp | short | 2352 | 29.6% |
| priority_sla_aware_dp | medium | 2282 | 29.5% |
| priority_sla_aware_dp | long | 2309 | 25.3% |
| admission_controlled_dp | short | 2352 | 29.3% |
| admission_controlled_dp | medium | 2282 | 28.2% |
| admission_controlled_dp | long | 2309 | 27.7% |

## Breakdown by SLA tier

| Scheduler | Tier | n | SLA adherence |
|---|---|--:|--:|
| fifo | relaxed | 2351 | 27.3% |
| fifo | moderate | 2292 | 12.7% |
| fifo | tight | 2300 | 8.6% |
| priority_first | relaxed | 2351 | 27.4% |
| priority_first | moderate | 2292 | 13.2% |
| priority_first | tight | 2300 | 9.0% |
| edf | relaxed | 2351 | 22.3% |
| edf | moderate | 2292 | 17.2% |
| edf | tight | 2300 | 21.0% |
| static_least_slack | relaxed | 2351 | 15.6% |
| static_least_slack | moderate | 2292 | 11.1% |
| static_least_slack | tight | 2300 | 24.0% |
| adaptive_least_slack | relaxed | 2351 | 15.7% |
| adaptive_least_slack | moderate | 2292 | 11.2% |
| adaptive_least_slack | tight | 2300 | 24.0% |
| dp_oracle | relaxed | 2351 | 43.2% |
| dp_oracle | moderate | 2292 | 24.5% |
| dp_oracle | tight | 2300 | 18.5% |
| priority_sla_aware_dp | relaxed | 2351 | 41.9% |
| priority_sla_aware_dp | moderate | 2292 | 23.3% |
| priority_sla_aware_dp | tight | 2300 | 18.9% |
| admission_controlled_dp | relaxed | 2351 | 43.3% |
| admission_controlled_dp | moderate | 2292 | 23.3% |
| admission_controlled_dp | tight | 2300 | 18.3% |

## Priority-weight sensitivity (item 5)

`PRIORITY_WEIGHTS = {1: 3, 2: 2, 3: 1}` (in `scripts/multi_query_schedulers.py`) is a declared placeholder, not derived from data - same treatment as the admission-control service target above. This re-runs the same 1000 batches with `PRIORITY_WEIGHTS` swapped to several candidate weight sets, checking whether the two schedulers whose decisions/scoring directly depend on it (`dp_oracle`, `priority_sla_aware_dp`) hold up under a different choice.

| Weights {1,2,3} | dp_oracle weighted adherence | priority_sla_aware_dp weighted adherence | dp_oracle P1 adherence | priority_sla_aware_dp P1 adherence |
|---|--:|--:|--:|--:|
| 1:1:1 | 29.0% | 28.7% | 28.3% | 33.9% |
| 2:1.5:1 | 30.4% | 29.9% | 34.6% | 34.4% |
| 3:2:1 | 31.1% | 30.6% | 34.9% | 34.8% |
| 5:2:1 | 32.2% | 31.7% | 35.7% | 35.5% |

**Full 8-scheduler ranking by weighted adherence is NOT identical across all 4 weight sets tested** (including the uniform 1:1:1 case, where weighting has no effect at all). At the default weights, the ranking is: dp_oracle > priority_sla_aware_dp > admission_controlled_dp > priority_first > edf > adaptive_least_slack > static_least_slack > fifo.

Rankings that differ from the default, for inspection:
- 1:1:1 -> dp_oracle > priority_sla_aware_dp > admission_controlled_dp > edf > adaptive_least_slack > static_least_slack > priority_first > fifo
- 2:1.5:1 -> dp_oracle > priority_sla_aware_dp > admission_controlled_dp > edf > priority_first > adaptive_least_slack > static_least_slack > fifo
- 3:2:1 -> dp_oracle > priority_sla_aware_dp > admission_controlled_dp > priority_first > edf > adaptive_least_slack > static_least_slack > fifo
- 5:2:1 -> dp_oracle > priority_sla_aware_dp > admission_controlled_dp > priority_first > edf > adaptive_least_slack > static_least_slack > fifo

`priority_sla_aware_dp`'s priority-1 adherence tracks `dp_oracle`'s closely across every weight set tested (not just the default) - the lexicographic priority-1-protection fix (see `scripts/multi_query_schedulers.py`'s docstring) isn't an artifact of the specific default weight choice.

## Breakdown by priority

| Scheduler | Priority | n | SLA adherence |
|---|--:|--:|--:|
| fifo | 1 | 2314 | 17.3% |
| fifo | 2 | 2304 | 15.5% |
| fifo | 3 | 2325 | 16.0% |
| priority_first | 1 | 2314 | 30.9% |
| priority_first | 2 | 2304 | 13.6% |
| priority_first | 3 | 2325 | 5.4% |
| edf | 1 | 2314 | 20.5% |
| edf | 2 | 2304 | 19.9% |
| edf | 3 | 2325 | 20.3% |
| static_least_slack | 1 | 2314 | 19.0% |
| static_least_slack | 2 | 2304 | 16.1% |
| static_least_slack | 3 | 2325 | 15.6% |
| adaptive_least_slack | 1 | 2314 | 19.0% |
| adaptive_least_slack | 2 | 2304 | 16.2% |
| adaptive_least_slack | 3 | 2325 | 15.7% |
| dp_oracle | 1 | 2314 | 34.9% |
| dp_oracle | 2 | 2304 | 30.3% |
| dp_oracle | 3 | 2325 | 21.3% |
| priority_sla_aware_dp | 1 | 2314 | 34.8% |
| priority_sla_aware_dp | 2 | 2304 | 29.3% |
| priority_sla_aware_dp | 3 | 2325 | 20.3% |
| admission_controlled_dp | 1 | 2314 | 31.8% |
| admission_controlled_dp | 2 | 2304 | 28.9% |
| admission_controlled_dp | 3 | 2325 | 24.6% |

