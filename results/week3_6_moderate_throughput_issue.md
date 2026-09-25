# Week 3.6 reproducibility, run 4 — paused on a real, unresolved infra issue

Status: **paused, not completed**, per your call. Environment left clean
(verified: TPC-H exactly 150,000/600,572, MinIO exactly 130 objects/3
partition prefixes — no leftover test data). This documents two real
findings from today's reproducibility attempts and the specific issue that
stopped run 4 partway through.

## Finding 1 — confirmed: a cold connection/session inflates whichever
## condition runs first (fixed)

Diagnosed directly, not inferred: ran a 12-query throwaway warm-up pass
against the *existing* TPC-H data (same data, same queries used all
session), then measured 15 fresh `short`-class queries immediately after.

| | n | median | mean | stdev |
|---|--:|--:|--:|--:|
| Warm-up pass (cold, first queries of the session) | 12 | 282.1ms | 426.8ms | 260.3 |
| Test block (right after warm-up) | 15 | 235.3ms | 257.5ms | **45.7** |
| Prior `none` block, no warm-up (run 3) | 30 | 480.8ms | 508.2ms | 223.6 |

Same data, same query text in both the erratic warm-up pass and the stable
test block — the only variable was connection/session freshness. This
ruled out both of the hypotheses raised for investigation (dataset-specific
caching, query-specific caching) in favor of a connection/session
warm-up effect (plausibly Tailscale relay-to-direct path negotiation,
though not pinned down further since the fix worked regardless of exact
cause).

**Fix applied**: `scripts/run_week3_6_condition.py` now runs a 12-query
throwaway warm-up pass (not recorded) before every condition block, not
just the first — so warm-up state itself can't become a confound between
conditions. Verified working in run 4's `none` block:
short stdev 223.6→70.2, medium →23.8, long →75.4, medians all dropping
back into the range seen in later-run sustained-condition blocks. This is
a real, validated fix — it stays in the run structure going forward.

## Finding 2 — unresolved: `moderate_sustained` (1000 eps) has failed its
## warm-up gate 3 times today, `heavy_sustained` (1700 eps) has not failed once

| Attempt | Target | Result |
|---|--:|---|
| Repeat r2, attempt 1 | 1000 eps | Failed — landed ~800 eps (high CPU load at the time, a plausible cause) |
| Repeat r3, attempt 1 | 1000 eps | Failed — landed ~850 eps (CPU was *low* this time) |
| Repeat r4, attempt 1 | 1000 eps | Failed — landed ~817 eps (CPU not checked at failure time) |
| Repeat r2/r3/r4, heavy_sustained | 1700 eps | **Succeeded every time** — 1652-1724 eps observed |

Three consecutive `moderate_sustained` failures, never once trending
toward 1000 even across the full 90-second warm-up window — this stopped
looking like ordinary variance around attempt 2. What makes it a real
open question rather than "1000 just isn't achievable": **the higher
target (1700 eps) has succeeded in every single attempt**, which is the
opposite of what a simple throughput ceiling would predict. If the
pipeline genuinely couldn't sustain 1000 eps, it shouldn't be able to
sustain 1700 eps either.

**Leading hypothesis, not yet confirmed**: `moderate_sustained` is always
the *second* condition run, immediately after starting a brand-new
producer process — it may be hitting a version of the same
connection/session warm-up cost just fixed for query latency (Finding 1),
but applied to the *producer's* Kafka throughput this time rather than the
query client. `heavy_sustained` always runs *third*, by which point the
producer/Kafka/network path may already be "warm" from `moderate_sustained`'s
own (failed) attempt. This is speculative — not tested directly.

**What would confirm or rule this out** (not yet done):
- Run `heavy_sustained` *first* (before `moderate_sustained`) in a test
  sequence — if the higher target now also struggles when it's the one
  going second, that supports the warm-up-cost hypothesis over a genuine
  moderate-tier ceiling.
- Add an explicit producer-side warm-up (stream a small throwaway batch
  unthrottled for a few seconds before pacing begins) the same way the
  query-side fix worked, and see if `moderate_sustained` starts passing
  reliably.
- Log the producer's own send-side timing more granularly (current
  `Progress` log lines are only every 500 events) to see whether the
  shortfall is pacing-loop overhead, Kafka broker backpressure, or
  something else specific to lower target rates.

## Current state

- `dataset_r4.csv` contains only the `none` block (105 rows, warm-up-fixed,
  clean) — `moderate_sustained` and `heavy_sustained` were never reached.
- Environment fully clean and idle: local ingestion service still running
  (fresh restart from earlier this session), Kafka/Docker healthy, no
  leftover MinIO objects, TPC-H counts exact.
- Nothing is mid-flight; safe to resume whenever, or leave as-is.
