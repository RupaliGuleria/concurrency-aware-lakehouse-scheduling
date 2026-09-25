# Point-estimate-with-safety-margin: candidate margin values

**Resolved 2026-09-10, differently than this doc originally framed it**:
rather than picking Option A or Option B, both were implemented as
independent baseline schedulers (`PointEstimateFixedMarginScheduler`,
`PointEstimatePerClassMarginScheduler`) and evaluated empirically. See
`results/week3/section6_scheduler_baseline_comparison.md` for the full
comparison — headline finding is that **neither value works at the
`tight` SLA tier** (both reject 100% of queries there, since 1.31-1.40x
always exceeds the tier's own 1.2x multiplier). The candidate values below
are kept as the historical record of how they were derived, not as a
decision log entry anymore.

`week3_testing_plan.md` section 6 explicitly defers this decision: "needs
Rupali's call on the margin size once there's baseline variance data to
tune it against — flagged here rather than guessed." That data now exists
(Section 2 + Section 3). This doc lays out options; it does not decide —
`scripts/baseline_schedulers.py`'s `PointEstimateSafetyMarginScheduler`
takes `margin` as a required constructor argument for exactly this reason.

## Observed variance per class, across both sections

| Class | Section 2 (none) stdev | Section 3 none stdev | Section 3 heavy_burst stdev | worst observed |
|---|--:|--:|--:|--:|
| short | 31.1ms | 105.9ms | 86.5ms | 105.9ms |
| medium | 21.6ms | 13.3ms | 73.9ms | 73.9ms |
| long | 74.0ms | 200.2ms | 62.3ms | 200.2ms |

Stdev varies a lot depending on session/cache state (per the caveat in
`results/week3/section3_dataset_findings.md`) — there isn't yet a single
"true" variance per class, just a range of what's been observed so far.

## Candidate margins

**Option A — data-driven, per-class (mean + 2×worst-observed-stdev, expressed as a multiplier of p50):**

| Class | Margin |
|---|--:|
| short | 1.40x |
| medium | 1.31x |
| long | 1.35x |

Covers the worst variance seen so far, roughly a 2-stdev buffer. Different
per class — more precise, but the plan's wording ("pad the deadline check
by a fixed multiplier") suggests a single number was originally envisioned,
not per-class tuning.

**Option B — single fixed multiplier, round number:** 1.4x uniformly
(covers short's worst case, the largest of the three; over-conservative for
medium).

**Option C — smaller, less conservative:** 1.2x uniformly — same value as
the "tight" SLA multiplier, easy to reason about, but doesn't cover the
worst-observed variance for any class (would still admit queries that miss
deadline under high-variance conditions like `short`'s 105.9ms stdev or
`long`'s 200.2ms stdev).

## Recommendation (not a decision)

Option A is the most defensible since it's grounded in this project's own
measured variance rather than a round number picked for convenience — but
it's a genuine judgment call between simplicity (one number) and precision
(per-class), and the variance estimates themselves are still noisy (n=30,
single session, cache state uncontrolled). Needs your call before
`PointEstimateSafetyMarginScheduler` gets a real default.
