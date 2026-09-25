# Week 3.6 reproducibility attempt #2 (run 3) — clean execution, inconclusive result

A second, fully clean reproducibility attempt: pre-flight checks passed
(moderate CPU, Ryzen fully reachable, Docker/Kafka healthy, no stale data),
ingestion service restarted fresh, and all three conditions
(none/moderate_sustained/heavy_sustained × short/medium/long, 315 queries)
ran back-to-back with **zero interruptions** — no crash, no CPU-contention
failure, no service restart mid-sequence. One `moderate_sustained` warm-up
near-miss (850 vs. 900-1100 band) occurred *before* any queries ran and was
resolved by a clean retry with a fresh producer suffix, per your call to
just retry rather than change the tolerance. Raw data:
`results/week3_6/dataset_r3.csv`.

**This is the valid, clean test the invalidated `week3_6_reproducibility_findings.md`
attempt (r2) was supposed to be.** It still doesn't give a usable
reproducibility answer, but for a different and more informative reason.

## TL;DR

- **Operationally, this was a clean run** — no technical failures, no
  contamination, TPC-H verified unchanged throughout, all cleanup
  confirmed.
- **The result itself is not usable for a reproducibility judgment**,
  because the `none` block (run first, immediately after the fresh service
  restart) came out anomalously slow and erratic across **all three query
  classes simultaneously** — not one or two outliers, but the whole block's
  variance (stdev 224-228ms) roughly 3-15x higher than the same-run
  sustained-condition blocks measured minutes later (stdev 15-95ms).
- **Every comparison in this run shows `none` slower than both sustained
  conditions** (short -48%/-47%, medium -32%/-31%, long -22%/-21%, all
  p≈1.0 in the "sustained is slower" direction) — the opposite pattern from
  the original run, and implausible as a real ingestion effect (there's no
  mechanism by which adding write load would make queries faster).
- **Conclusion: this is very likely another instance of session/baseline
  drift, not a real effect and not evidence against the original finding.**
  Combined with the discarded r2 attempt (which showed the same "early
  block runs slow" pattern for `long`/`none` specifically), this is now the
  **second** clean-or-near-clean attempt where the `none` baseline came out
  systematically different from later-measured conditions in the same
  session.

## Evidence this is a whole-block artifact, not outlier noise

Checked the raw `none` timings for all three classes for a trend (cache
warming, monotonic improvement) — found none; values are erratic
throughout with no clear pattern (`short/none`: alternates between
~245-340ms and ~480-1204ms runs unpredictably across the whole 30-run
block). This isn't a warm-up curve or a couple of bad outliers — the whole
~100-second block ran with elevated, noisy latency, then the
`moderate_sustained` and `heavy_sustained` blocks measured immediately
afterward were both tight and consistent (e.g. `medium/moderate_sustained`:
208-272ms range across all 30 runs, no spread at all by comparison).

## What this means

This is the **second** time in two attempts that `none`, run first, has
come out anomalously elevated relative to conditions measured later in the
same session — once for `long` specifically (the invalidated r2 attempt,
disrupted by a crash) and now for all three classes at once (this attempt,
undisrupted). The original Week 3.6 run also ran `none` first and did
*not* show this pattern — its `none` baselines were unremarkable and its
sustained-condition effects (where found) were in the expected direction.

Two live interpretations:

1. **This system has a real, recurring "first-block-in-a-session" latency
   penalty** (JDBC/HTTP connection warm-up, Trino-side query compilation
   caching, TCP/TLS handshake costs, or something else specific to the
   first several queries after a period of inactivity) that inflates
   whichever condition happens to run first — regardless of which
   condition that is. If true, **every Week 3.6 `none`-vs-sustained
   comparison run so far, including the original, is potentially biased by
   which condition ran first**, not just this attempt.
2. **This system simply has enough baseline session-to-session (or
   block-to-block) latency variance that a single un-interleaved run,
   however cleanly executed, isn't a reliable way to detect effects of the
   size in question** (tens of ms) — sometimes that variance happens to
   land favorably (the original run) and sometimes it swamps everything
   (both repeat attempts).

Either way, the implication is the same: **a single none-then-sustained
sequence, run once, is not a sufficient experimental design to trust a
significant/non-significant verdict on its own** — this project has now
seen three attempts (original + 2 repeats) produce three different
qualitative pictures, and two of the three are explainable by baseline
drift rather than the ingestion mechanism itself.

## What this doesn't tell us

- It does **not** confirm the original `long`/`moderate_sustained` finding
  was wrong.
- It does **not** confirm sustained ingestion makes queries faster (the
  mechanism doesn't support that direction).
- It does **not**, on its own, mean the experimental method (rate-limited
  producer, live overlap telemetry, warm-up gating) is broken — those
  components worked correctly and as designed in every attempt. The issue
  is specifically the reliability of a single `none` baseline block as a
  reference point.

## Recommended next step

Before trusting any `none`-vs-sustained comparison from this pipeline, the
project likely needs one of:
- **Randomize/interleave condition order** across repetitions rather than
  always running `none` first, so a systematic first-block penalty (if real)
  gets distributed across all conditions rather than concentrated in one.
- **An ABA design** (like Checkpoint 1's corrected pass) — `none`, then
  sustained, then `none` again — so a genuine baseline shift during the
  session shows up as a discrepancy between the two `none` blocks instead
  of masquerading as a condition effect.
- **More repetitions of the whole none-sustained-none cycle**, accepting
  that any single pass is noisy, and pooling across passes for the real
  comparison.

Not implemented here — flagged as the actual blocker to a trustworthy
reproducibility answer, rather than attempting a fourth ad hoc repeat.
