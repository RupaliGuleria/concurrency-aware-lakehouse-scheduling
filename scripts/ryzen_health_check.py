"""Shared health-check + exponential-backoff helper for any script that
hits the live Ryzen-box Trino deployment. Added 2026-09-23 after repeated
back-to-back concurrency_contention_test.py sweeps (three full 16-way
attempts within about an hour, while chasing an unrelated cold-start bug)
likely contributed to the box going down (CPU/OOM, per Rupali). Every live
test from here on should check health before starting and back off between
retries instead of immediately re-hammering the box.

Usage:
  from ryzen_health_check import check_health, wait_until_healthy

  if not wait_until_healthy():
      raise SystemExit("Ryzen box not healthy - aborting rather than adding load")
"""
from __future__ import annotations

import statistics
import time

import trino

from baseline_schedulers import BASELINE_P50_MS
from query_timing_harness import CATALOG, HOST, PORT, QUERIES, QUERY_CLASS, USER


def check_health(timeout_s: float = 10.0) -> dict:
    """One-shot health check: a single cheap query with a strict timeout.
    Does not retry - see wait_until_healthy for that. Returns a dict, never
    raises."""
    t0 = time.perf_counter()
    try:
        conn = trino.dbapi.connect(
            host=HOST, port=PORT, user=USER, catalog=CATALOG,
            http_scheme="http", request_timeout=timeout_s, max_attempts=1,
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchall()
        return {"healthy": True, "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "error": None}
    except Exception as exc:  # noqa: BLE001 - report, don't crash the caller
        return {"healthy": False, "latency_ms": None, "error": repr(exc)}


def wait_until_healthy(max_attempts: int = 5, base_delay_s: float = 15.0, max_delay_s: float = 300.0) -> bool:
    """Exponential backoff health check loop: base_delay_s, 2x, 4x, ... capped
    at max_delay_s between attempts. Prints status each attempt so it's
    visible in a live run, not just a silent hang. Returns True as soon as
    one check succeeds, False if every attempt in max_attempts fails."""
    for attempt in range(1, max_attempts + 1):
        result = check_health()
        if result["healthy"]:
            print(f"[health check] OK (attempt {attempt}/{max_attempts}, {result['latency_ms']}ms)")
            return True
        delay = min(base_delay_s * (2 ** (attempt - 1)), max_delay_s)
        print(f"[health check] FAILED (attempt {attempt}/{max_attempts}): {result['error']}")
        if attempt < max_attempts:
            print(f"[health check] backing off {delay:.0f}s before retry...")
            time.sleep(delay)
    print(f"[health check] giving up after {max_attempts} attempts - Ryzen box not healthy.")
    return False


CLASS_TO_QUERY_ID = {v: k for k, v in QUERY_CLASS.items()}

DEFAULT_DRIFT_TOLERANCE = 0.5  # accept up to 50% above BASELINE_P50_MS before flagging drift


def check_baseline_drift(n_samples: int = 5, tolerance: float = DEFAULT_DRIFT_TOLERANCE) -> dict:
    """EXPERIMENTAL CONTROL, not part of the paper's main contribution -
    added 2026-09-23 after a live concurrency-validation session found the
    box's live uncontended performance had drifted well above the 9/21
    calibration baseline (BASELINE_P50_MS), silently invalidating every
    deadline_ms in that session (deadline_ms = BASELINE_P50_MS x
    SLA_MULTIPLIER) until a Trino restart + warm-up brought it back in
    line. Single-node home hardware can drift session to session for
    reasons not fully diagnosed (JVM/plan-cache state, host-level
    variability) - this check catches that BEFORE a live session runs,
    rather than discovering it after collecting an hour of now-suspect
    data. Not a resource-contention check (see check_health/wait_until_healthy
    for that) - this specifically asks "does uncontended performance still
    match what deadlines were calibrated against."

    Runs n_samples queries per class (short/medium/long), takes the
    median, and compares against BASELINE_P50_MS. Returns a dict with
    per-class actual/baseline/drift_ratio and an overall "healthy" flag
    (False if any class exceeds tolerance above baseline - running under
    baseline is never flagged, only running slower than expected)."""
    conn = trino.dbapi.connect(host=HOST, port=PORT, user=USER, catalog=CATALOG, http_scheme="http")
    per_class = {}
    healthy = True
    for cls, baseline_ms in BASELINE_P50_MS.items():
        query_id = CLASS_TO_QUERY_ID[cls]
        samples = []
        for _ in range(n_samples):
            t0 = time.perf_counter()
            cur = conn.cursor()
            cur.execute(QUERIES[query_id])
            cur.fetchall()
            samples.append((time.perf_counter() - t0) * 1000)
        actual_median = statistics.median(samples)
        drift_ratio = (actual_median - baseline_ms) / baseline_ms
        within_tolerance = drift_ratio <= tolerance
        healthy = healthy and within_tolerance
        per_class[cls] = {
            "actual_median_ms": round(actual_median, 1),
            "baseline_ms": baseline_ms,
            "drift_ratio": round(drift_ratio, 2),
            "within_tolerance": within_tolerance,
        }
    return {"healthy": healthy, "tolerance": tolerance, "per_class": per_class}


def print_baseline_drift(result: dict) -> None:
    status = "OK" if result["healthy"] else "DRIFT DETECTED"
    print(f"[baseline drift check] {status} (tolerance +{result['tolerance']*100:.0f}%)")
    for cls, d in result["per_class"].items():
        flag = "" if d["within_tolerance"] else "  <-- EXCEEDS TOLERANCE"
        print(f"  {cls}: actual={d['actual_median_ms']}ms baseline={d['baseline_ms']}ms drift={d['drift_ratio']*100:+.0f}%{flag}")
    if not result["healthy"]:
        print(
            "[baseline drift check] Uncontended performance has drifted above "
            "the calibration baseline - deadline_ms values (computed from "
            "BASELINE_P50_MS) may no longer reflect reality. Consider "
            "restarting Trino and re-running a proper warm-up before trusting "
            "results from this session (see research-plan/week4_paper_framing.md's "
            "experimental-control note)."
        )


if __name__ == "__main__":
    ok = wait_until_healthy()
    print("HEALTHY" if ok else "UNHEALTHY")
    if ok:
        print_baseline_drift(check_baseline_drift())
