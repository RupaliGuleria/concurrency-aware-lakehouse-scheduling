"""Validation script for multi_query_schedulers.py - not a new experiment.

Five checks before trusting the simulation harness:
1. Reproduces research-plan/week4_scheduler_design.md's item 5 worked
   example exactly (order, per-query met/missed, weighted score).
2. Verifies the iterative Least Slack implementation agrees with the
   one-shot static-sort shortcut the module docstring claims is
   mathematically equivalent under v1's assumptions.
3. Verifies the DP oracle against brute-force permutation search on small
   random batches.
4. Verifies PrioritySlaAwareDpScheduler against brute-force permutation
   search on small random batches, comparing the same 5-tuple
   lexicographic objective the DP itself optimizes for (predicted, not
   actual, runtime - same treatment as check 3).
5. Verifies AdmissionControlledDpScheduler's (v1.2) core invariants:
   admitted+deferred always reconstructs the full batch with nothing lost
   or duplicated, the final admitted set's predicted adherence (per
   dp_oracle) is always >= the service target it was built against, and
   the two trivial-target edge cases (target=0 admits everyone,
   target>1.0 admits no one) behave as designed.

Usage:
  .venv/Scripts/python.exe verify_multi_query_schedulers.py
"""
from __future__ import annotations

import itertools
import math
import random

from multi_query_schedulers import (
    PRIORITY_WEIGHTS,
    AdaptiveLeastSlackScheduler,
    AdmissionControlledDpScheduler,
    DpOracleScheduler,
    MultiQuery,
    PrioritySlaAwareDpScheduler,
    StaticLeastSlackScheduler,
    least_slack_one_shot_order,
    score_order,
)


def check_worked_example() -> None:
    print("=== Check 1: worked example from week4_scheduler_design.md ===")
    queries = [
        MultiQuery("Q1", "short", "tight", 1, 0, 643.8, 291.9, 291.9, 310.0),
        MultiQuery("Q2", "medium", "relaxed", 3, 1, 1891.2, 249.8, 249.8, 260.0),
        MultiQuery("Q3", "long", "tight", 1, 2, 1385.9, 872.0, 872.0, 900.0),
        MultiQuery("Q4", "short", "moderate", 2, 3, 1073.0, 291.9, 291.9, 300.0),
        MultiQuery("Q5", "medium", "tight", 2, 4, 567.4, 249.8, 249.8, 260.0),
    ]
    result = AdaptiveLeastSlackScheduler().order(queries)
    got_order = [q.query_id for q in result.order]
    expected_order = ["Q5", "Q1", "Q3", "Q4", "Q2"]
    print(f"  order: {got_order}  (expected {expected_order})")
    assert got_order == expected_order, "worked example order mismatch"

    scored = score_order(result.order)
    met_ids = {qid for qid, met in scored["per_query_met"] if met}
    expected_met = {"Q5", "Q1"}
    print(f"  met: {sorted(met_ids)}  (expected {sorted(expected_met)})")
    assert met_ids == expected_met, "worked example met/missed mismatch"

    print(f"  weighted_score: {scored['weighted_score']}  (expected 5)")
    assert scored["weighted_score"] == 5, "worked example weighted score mismatch"
    print("  PASS")
    print()


def check_static_sort_equivalence(n_trials: int = 500) -> None:
    print(f"=== Check 2: iterative vs one-shot-sort equivalence ({n_trials} random batches) ===")
    rng = random.Random(42)
    classes = ["short", "medium", "long"]
    tiers = ["relaxed", "moderate", "tight"]
    from multi_query_schedulers import deadline_ms

    mismatches = 0
    for trial in range(n_trials):
        n = rng.randint(2, 10)
        queries = []
        for i in range(n):
            cls = rng.choice(classes)
            tier = rng.choice(tiers)
            pred = rng.uniform(100, 1000)
            queries.append(MultiQuery(
                query_id=f"q{i}", query_class=cls, sla_tier=tier, priority=rng.choice([1, 2, 3]),
                arrival_index=i, deadline_ms=deadline_ms(cls, tier),
                predicted_static_ms=pred, predicted_adaptive_ms=pred,
                actual_runtime_ms=rng.uniform(50, 1200),
            ))
        iterative = [q.query_id for q in AdaptiveLeastSlackScheduler().order(queries).order]
        one_shot = [q.query_id for q in least_slack_one_shot_order(queries, "predicted_adaptive_ms")]
        if iterative != one_shot:
            mismatches += 1
            if mismatches <= 3:
                print(f"  MISMATCH trial {trial}: iterative={iterative} one_shot={one_shot}")

    print(f"  {n_trials - mismatches}/{n_trials} matched")
    assert mismatches == 0, "iterative and one-shot-sort orders disagree - equivalence claim is wrong"
    print("  PASS")
    print()


def check_dp_against_brute_force(n_trials: int = 200) -> None:
    print(f"=== Check 3: DP oracle vs brute-force permutation search ({n_trials} random small batches) ===")
    rng = random.Random(7)
    classes = ["short", "medium", "long"]
    tiers = ["relaxed", "moderate", "tight"]
    from multi_query_schedulers import deadline_ms

    for trial in range(n_trials):
        n = rng.randint(2, 7)  # small enough for brute force (7! = 5040)
        queries = []
        for i in range(n):
            cls = rng.choice(classes)
            tier = rng.choice(tiers)
            pred = rng.uniform(100, 1000)
            queries.append(MultiQuery(
                query_id=f"q{i}", query_class=cls, sla_tier=tier, priority=rng.choice([1, 2, 3]),
                arrival_index=i, deadline_ms=deadline_ms(cls, tier),
                predicted_static_ms=pred, predicted_adaptive_ms=pred,
                actual_runtime_ms=rng.uniform(50, 1200),  # irrelevant to DP itself, only used if we scored against actual
            ))

        dp_order = DpOracleScheduler().order(queries).order
        dp_score = _predicted_weighted_score(dp_order)

        best_brute = -1
        for perm in itertools.permutations(queries):
            s = _predicted_weighted_score(list(perm))
            if s > best_brute:
                best_brute = s

        if dp_score != best_brute:
            print(f"  MISMATCH trial {trial}: dp={dp_score} brute_force_best={best_brute}")
            assert False, "DP oracle did not find the optimal predicted-weighted score"

    print(f"  {n_trials}/{n_trials} matched brute force")
    print("  PASS")
    print()


def _predicted_weighted_score(ordered_queries: list) -> int:
    """Weighted score using PREDICTED runtime (what the DP itself
    optimizes for) - not actual_runtime_ms. This is a different quantity
    from score_order()'s output, which uses actual_runtime_ms."""
    current_time = 0.0
    score = 0
    for q in ordered_queries:
        current_time += q.predicted_adaptive_ms
        if current_time <= q.deadline_ms:
            score += PRIORITY_WEIGHTS[q.priority]
    return score


def _predicted_lex_score(ordered_queries: list) -> tuple:
    """The same 5-tuple lexicographic objective PrioritySlaAwareDpScheduler
    optimizes for, computed using PREDICTED runtime - not
    actual_runtime_ms, matching what the DP itself sees when choosing.
    (weighted_sla_score, priority1_sla_met, priority2_sla_met,
    total_sla_met, -total_predicted_lateness) - priority protection ranked
    above raw count, per multi_query_schedulers.py's PrioritySlaAwareDpScheduler
    docstring."""
    current_time = 0.0
    weighted = 0
    total_met = 0
    p1_met = 0
    p2_met = 0
    total_lateness = 0.0
    for q in ordered_queries:
        current_time += q.predicted_adaptive_ms
        sla_met = current_time <= q.deadline_ms
        if sla_met:
            weighted += PRIORITY_WEIGHTS[q.priority]
            total_met += 1
            if q.priority == 1:
                p1_met += 1
            elif q.priority == 2:
                p2_met += 1
        total_lateness += max(0.0, current_time - q.deadline_ms)
    return (weighted, p1_met, p2_met, total_met, -total_lateness)


def check_priority_sla_dp_against_brute_force(n_trials: int = 200) -> None:
    print(f"=== Check 4: PrioritySlaAwareDpScheduler vs brute-force permutation search ({n_trials} random small batches) ===")
    rng = random.Random(13)
    classes = ["short", "medium", "long"]
    tiers = ["relaxed", "moderate", "tight"]
    from multi_query_schedulers import deadline_ms

    for trial in range(n_trials):
        n = rng.randint(2, 7)  # small enough for brute force (7! = 5040)
        queries = []
        for i in range(n):
            cls = rng.choice(classes)
            tier = rng.choice(tiers)
            pred = rng.uniform(100, 1000)
            queries.append(MultiQuery(
                query_id=f"q{i}", query_class=cls, sla_tier=tier, priority=rng.choice([1, 2, 3]),
                arrival_index=i, deadline_ms=deadline_ms(cls, tier),
                predicted_static_ms=pred, predicted_adaptive_ms=pred,
                actual_runtime_ms=rng.uniform(50, 1200),  # irrelevant to DP itself, only used if we scored against actual
            ))

        dp_order = PrioritySlaAwareDpScheduler().order(queries).order
        dp_score = _predicted_lex_score(dp_order)

        best_brute = None
        for perm in itertools.permutations(queries):
            s = _predicted_lex_score(list(perm))
            if best_brute is None or s > best_brute:
                best_brute = s

        # Integer components must match exactly; the lateness component is a
        # float sum accumulated in a different order by the DP (mask-by-mask)
        # vs. brute force (permutation order), so compare it with tolerance -
        # floating-point addition isn't associative, this isn't a logic bug.
        ints_match = dp_score[:4] == best_brute[:4]
        lateness_match = math.isclose(dp_score[4], best_brute[4], rel_tol=1e-9, abs_tol=1e-6)
        if not (ints_match and lateness_match):
            print(f"  MISMATCH trial {trial}: dp={dp_score} brute_force_best={best_brute}")
            assert False, "PrioritySlaAwareDpScheduler did not find the optimal lexicographic score"

    print(f"  {n_trials}/{n_trials} matched brute force")
    print("  PASS")
    print()


def _predicted_adherence_rate(ordered_queries: list) -> float:
    """Independent reference implementation of the same predicted-adherence
    calculation AdmissionControlledDpScheduler uses internally - not
    imported from multi_query_schedulers.py, so this check can't share a
    bug with the code it's checking."""
    if not ordered_queries:
        return 1.0
    t = 0.0
    met = 0
    for q in ordered_queries:
        t += q.predicted_adaptive_ms
        if t <= q.deadline_ms:
            met += 1
    return met / len(ordered_queries)


def check_admission_control_invariants(n_trials: int = 200) -> None:
    print(f"=== Check 5: AdmissionControlledDpScheduler invariants ({n_trials} random batches) ===")
    rng = random.Random(29)
    classes = ["short", "medium", "long"]
    tiers = ["relaxed", "moderate", "tight"]
    from multi_query_schedulers import deadline_ms

    reconstruction_failures = 0
    target_violations = 0

    for trial in range(n_trials):
        n = rng.randint(2, 10)
        queries = []
        for i in range(n):
            cls = rng.choice(classes)
            tier = rng.choice(tiers)
            pred = rng.uniform(100, 1000)
            queries.append(MultiQuery(
                query_id=f"q{i}", query_class=cls, sla_tier=tier, priority=rng.choice([1, 2, 3]),
                arrival_index=i, deadline_ms=deadline_ms(cls, tier),
                predicted_static_ms=pred, predicted_adaptive_ms=pred,
                actual_runtime_ms=rng.uniform(50, 1200),
            ))

        target = rng.choice([0.5, 0.7, 0.8, 0.9])
        scheduler = AdmissionControlledDpScheduler(service_target_adherence=target)
        result = scheduler.order(queries)

        # Reconstruction: admitted + deferred must equal the original batch exactly.
        result_ids = {q.query_id for q in result.order}
        original_ids = {q.query_id for q in queries}
        admitted_ids = result_ids - result.deferred_ids
        if result_ids != original_ids or len(result.order) != len(queries):
            reconstruction_failures += 1
            print(f"  RECONSTRUCTION MISMATCH trial {trial}: result has {len(result.order)} queries, expected {len(queries)}")
            continue

        # The final admitted set's predicted adherence (per dp_oracle) must be >= target.
        admitted_queries = [q for q in queries if q.query_id in admitted_ids]
        if admitted_queries:
            dp_order = DpOracleScheduler().order(admitted_queries).order
            adherence = _predicted_adherence_rate(dp_order)
            if adherence < target - 1e-9:
                target_violations += 1
                print(f"  TARGET VIOLATION trial {trial}: admitted set's predicted adherence {adherence:.3f} < target {target}")

    assert reconstruction_failures == 0, "admitted+deferred did not reconstruct the original batch"
    assert target_violations == 0, "admitted set's predicted adherence fell below its own service target"
    print(f"  {n_trials}/{n_trials} reconstructed correctly, all admitted sets met their target")

    # Trivial edge cases.
    rng2 = random.Random(31)
    n = rng2.randint(4, 8)
    edge_queries = []
    for i in range(n):
        cls = rng2.choice(classes)
        tier = rng2.choice(tiers)
        pred = rng2.uniform(100, 1000)
        edge_queries.append(MultiQuery(
            query_id=f"q{i}", query_class=cls, sla_tier=tier, priority=rng2.choice([1, 2, 3]),
            arrival_index=i, deadline_ms=deadline_ms(cls, tier),
            predicted_static_ms=pred, predicted_adaptive_ms=pred,
            actual_runtime_ms=rng2.uniform(50, 1200),
        ))

    lenient = AdmissionControlledDpScheduler(service_target_adherence=0.0).order(edge_queries)
    assert len(lenient.deferred_ids) == 0, "target=0.0 should admit every query"

    impossible = AdmissionControlledDpScheduler(service_target_adherence=1.01).order(edge_queries)
    assert len(impossible.deferred_ids) == len(edge_queries), "target>1.0 should admit no query"

    print("  edge cases (target=0.0 admits all, target=1.01 admits none): PASS")
    print("  PASS")
    print()


if __name__ == "__main__":
    check_worked_example()
    check_static_sort_equivalence()
    check_dp_against_brute_force()
    check_priority_sla_dp_against_brute_force()
    check_admission_control_invariants()
    print("All checks passed.")
