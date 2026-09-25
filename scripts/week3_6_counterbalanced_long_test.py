"""Closes out Week 3.6's original research question using the mirrored
ABC-CBA data already collected in results/week3_margin_tuning/ (collected
for the margin-tuning protocol, but structurally identical to what
research-plan/week3_6_counterbalanced_retest_plan.md called for - same
warm-up-fixed harness, same mirrored order, same conditions).

Tests whether `long` queries are genuinely slower under moderate_sustained
/ heavy_sustained ingestion, using two independent contrasts:

  early contrast: block_B1_moderate / block_C1_heavy vs block_A1_none
  late contrast:  block_B2_moderate / block_C2_heavy vs block_A2_none

A real ingestion effect should show up in BOTH contrasts, same direction.
An effect that only appears in one position slot (or reverses) is a
session-order artifact, not evidence of a real slowdown - exactly the
distinction the original fixed-order design couldn't make.

One-sided permutation test on medians, 20,000 resamples - same method as
Checkpoint 1, Week 3, and the original (now-unconfirmed) Week 3.6 result.

Usage:
  .venv/Scripts/python.exe week3_6_counterbalanced_long_test.py
"""
from __future__ import annotations

import csv
import random
import statistics

random.seed(42)

IN_DIR = "../results/week3_margin_tuning"
N_PERM = 20000


def load_long_runtimes(path: str) -> list:
    with open(path, newline="", encoding="utf-8") as f:
        return [
            float(r["runtime_ms"])
            for r in csv.DictReader(f)
            if not r.get("error") and r["query_class"] == "long"
        ]


def perm_test_median(a: list, b: list, n_perm: int = N_PERM):
    """One-sided: is median(a) > median(b)? Returns (obs_diff, p)."""
    obs_diff = statistics.median(a) - statistics.median(b)
    pooled = a + b
    na = len(a)
    count = 0
    for _ in range(n_perm):
        random.shuffle(pooled)
        pa = pooled[:na]
        pb = pooled[na:]
        if statistics.median(pa) - statistics.median(pb) >= obs_diff:
            count += 1
    p = (count + 1) / (n_perm + 1)
    return obs_diff, p


def describe(name: str, vals: list) -> str:
    return (f"{name}: n={len(vals)} median={statistics.median(vals):.1f}ms "
            f"mean={statistics.mean(vals):.1f}ms stdev={statistics.stdev(vals):.1f}ms")


def main() -> None:
    a1 = load_long_runtimes(f"{IN_DIR}/block_A1_none.csv")
    b1 = load_long_runtimes(f"{IN_DIR}/block_B1_moderate.csv")
    c1 = load_long_runtimes(f"{IN_DIR}/block_C1_heavy.csv")
    c2 = load_long_runtimes(f"{IN_DIR}/block_C2_heavy.csv")
    b2 = load_long_runtimes(f"{IN_DIR}/block_B2_moderate.csv")
    a2 = load_long_runtimes(f"{IN_DIR}/block_A2_none.csv")

    print("=== Raw long-class stats per block ===")
    for name, vals in (("A1_none", a1), ("B1_moderate", b1), ("C1_heavy", c1),
                        ("C2_heavy", c2), ("B2_moderate", b2), ("A2_none", a2)):
        print(describe(name, vals))
    print()

    print("=== Early contrast (baseline: A1_none) ===")
    diff, p = perm_test_median(b1, a1)
    print(f"moderate_sustained (B1) vs none (A1): +{diff:.1f}ms ({diff/statistics.median(a1)*100:+.1f}%)  p={p:.4f}")
    diff, p = perm_test_median(c1, a1)
    print(f"heavy_sustained (C1) vs none (A1): +{diff:.1f}ms ({diff/statistics.median(a1)*100:+.1f}%)  p={p:.4f}")
    print()

    print("=== Late contrast (baseline: A2_none) ===")
    diff, p = perm_test_median(b2, a2)
    print(f"moderate_sustained (B2) vs none (A2): +{diff:.1f}ms ({diff/statistics.median(a2)*100:+.1f}%)  p={p:.4f}")
    diff, p = perm_test_median(c2, a2)
    print(f"heavy_sustained (C2) vs none (A2): +{diff:.1f}ms ({diff/statistics.median(a2)*100:+.1f}%)  p={p:.4f}")
    print()

    print("=== Pooled contrast (both instances combined per condition, n=44 each) ===")
    none_pooled = a1 + a2
    moderate_pooled = b1 + b2
    heavy_pooled = c1 + c2
    diff, p = perm_test_median(moderate_pooled, none_pooled)
    print(f"moderate_sustained (pooled) vs none (pooled): +{diff:.1f}ms ({diff/statistics.median(none_pooled)*100:+.1f}%)  p={p:.4f}")
    diff, p = perm_test_median(heavy_pooled, none_pooled)
    print(f"heavy_sustained (pooled) vs none (pooled): +{diff:.1f}ms ({diff/statistics.median(none_pooled)*100:+.1f}%)  p={p:.4f}")


if __name__ == "__main__":
    main()
