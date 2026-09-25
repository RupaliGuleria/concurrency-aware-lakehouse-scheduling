"""Week 3.6, open item 1: validate current pipeline capacity before locking
moderate/heavy sustained-ingestion tiers.

Streams the banking CSV unthrottled (PRODUCER_TARGET_EVENTS_PER_SEC unset)
while polling the ingestion service's new live metrics endpoint
(IngestionMetricsController, /internal/metrics/ingestion-snapshot) for
current_rolling_eps, and reports the observed steady-state max. Section 3's
heavy_burst gave one estimate (~2,500-2,800 eps) from producer-side log
timestamps alone; this gets an independent, ingestion-service-side reading
via the new endpoint - a real measurement, not the same number recomputed
a different way.

Usage:
  .venv/Scripts/python.exe capacity_validation.py --poll-interval 1.0
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request

INGESTION_SNAPSHOT_URL = "http://localhost:8080/internal/metrics/ingestion-snapshot"


def poll_snapshot() -> dict:
    with urllib.request.urlopen(INGESTION_SNAPSHOT_URL, timeout=5) as resp:
        return json.loads(resp.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--duration", type=float, default=240.0,
                         help="Max seconds to poll for (safety cap; stops early if ingestion goes idle)")
    args = parser.parse_args()

    readings = []
    start = time.monotonic()
    idle_polls = 0
    ever_started = False
    print(f"Polling {INGESTION_SNAPSHOT_URL} every {args.poll_interval}s ...")
    while time.monotonic() - start < args.duration:
        try:
            snap = poll_snapshot()
        except Exception as exc:  # noqa: BLE001
            print(f"  poll failed: {exc}")
            time.sleep(args.poll_interval)
            continue

        eps = snap["currentRollingEps"]
        active = snap["ingestionActive"]
        readings.append(eps)
        print(f"  total={snap['totalEvents']:7d}  rolling_eps={eps:7.1f}  active={active}")

        if eps > 5 or snap["totalEvents"] > 0:
            ever_started = True

        # Only treat sustained zero as "finished" once we've actually seen
        # ingestion running - otherwise this fires during producer startup
        # (CSV load, Kafka connect) before the first event is even sent.
        if ever_started and not active and max(readings[-5:] or [0]) < 5:
            idle_polls += 1
            if idle_polls >= 3:
                print("Ingestion appears idle after having been active - stopping poll loop.")
                break
        else:
            idle_polls = 0

        time.sleep(args.poll_interval)

    if not readings:
        print("No readings collected.")
        return

    # Steady-state = readings once rate is clearly non-zero and has stabilized
    # (ignore the ramp-up at the very start).
    nonzero = [r for r in readings if r > 0]
    if len(nonzero) >= 5:
        steady = nonzero[len(nonzero) // 4:]  # drop first quarter as ramp-up
    else:
        steady = nonzero

    if steady:
        steady_mean = sum(steady) / len(steady)
        print(f"\nObserved max rolling_eps: {max(readings):.1f}")
        print(f"Observed steady-state mean rolling_eps (post-ramp-up): {steady_mean:.1f}")
        print(f"\nProposed tiers from this measurement:")
        print(f"  moderate (45-55%): {0.45*steady_mean:.0f}-{0.55*steady_mean:.0f} eps")
        print(f"  heavy (80-90%):    {0.80*steady_mean:.0f}-{0.90*steady_mean:.0f} eps")
    else:
        print("No non-zero readings - could not compute steady state.")


if __name__ == "__main__":
    main()
