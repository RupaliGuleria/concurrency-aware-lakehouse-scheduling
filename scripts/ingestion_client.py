"""Shared client for the ingestion service's live metrics endpoint
(IngestionMetricsController, /internal/metrics/ingestion-snapshot), used by
both capacity_validation.py and the Week 3.6 harness extension
(week3_6_testing_plan.md sections 2, 5, 9).
"""
from __future__ import annotations

import json
import time
import urllib.request

INGESTION_SNAPSHOT_URL = "http://localhost:8080/internal/metrics/ingestion-snapshot"


def poll_snapshot() -> dict:
    with urllib.request.urlopen(INGESTION_SNAPSHOT_URL, timeout=5) as resp:
        return json.loads(resp.read())


def wait_for_stable_rate(
    target_eps: float,
    tolerance: float = 0.10,
    consecutive_required: int = 3,
    poll_interval: float = 1.0,
    timeout: float = 90.0,
) -> dict:
    """Blocks until rolling_eps stays within +/-tolerance of target_eps for
    consecutive_required consecutive polls, per week3_6's warm-up-gating
    requirement (section 9: "based on telemetry rather than an arbitrary
    long sleep"). Returns the final stabilizing snapshot. Raises
    TimeoutError if it never stabilizes within `timeout` seconds - callers
    should treat that as a real failure to investigate, not paper over it
    with a longer sleep.
    """
    lo, hi = target_eps * (1 - tolerance), target_eps * (1 + tolerance)
    consecutive = 0
    start = time.monotonic()
    last_snapshot = None
    while time.monotonic() - start < timeout:
        snap = poll_snapshot()
        last_snapshot = snap
        eps = snap["currentRollingEps"]
        if lo <= eps <= hi:
            consecutive += 1
            if consecutive >= consecutive_required:
                return snap
        else:
            consecutive = 0
        time.sleep(poll_interval)
    raise TimeoutError(
        f"Ingestion rate did not stabilize within {tolerance*100:.0f}% of "
        f"{target_eps} eps after {timeout}s (last snapshot: {last_snapshot})"
    )
