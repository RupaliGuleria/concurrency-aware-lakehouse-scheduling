"""Multi-query schedulers v1, per research-plan/week4_scheduler_design.md.

Eight schedulers, all strictly serial (one query in flight at a time, per
the design doc's confirmed v1 scope): FIFO, PriorityFirst, EDF,
StaticLeastSlack, AdaptiveLeastSlack (the original proposed design), an
exact single-objective DP oracle for small batches, PrioritySlaAwareDpScheduler
- a second, multi-objective DP that optimizes the whole ordering for a
lexicographic (weighted SLA, priority-1 SLA, priority-2 SLA, total SLA,
lateness) objective rather than greedily picking by slack alone - and
AdmissionControlledDpScheduler (v1.2) - wraps the DP oracle with a greedy
admission-control gate that stops admitting queries once DP itself
predicts the queue can't hit a declared service target, per the v1.2
addendum.

FIFO/PriorityFirst/EDF are implemented as a single static sort - none of
arrival_index/priority/deadline change as the batch executes, so an
iterative recompute-after-each-completion loop would produce the
identical order at O(n^2) cost for no benefit.

StaticLeastSlack/AdaptiveLeastSlack ARE implemented as the iterative
recompute-after-each-completion loop the design doc describes (matches
the worked example exactly, and stays correct if a future version
re-predicts runtime mid-batch, which would break the shortcut below) -
even though, under v1's specific assumptions (predicted_runtime fixed per
query for the whole batch), this is mathematically equivalent to a single
static sort by `deadline_ms - predicted_*_ms`: since every remaining
query's slack decreases by the same elapsed-time amount each round, their
RELATIVE order never changes. `tests/test_multi_query_schedulers.py`
checks the iterative and one-shot-sort implementations agree, as a
correctness self-check on this exact property.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from baseline_schedulers import BASELINE_P50_MS, SLA_MULTIPLIERS

# Placeholder default - flagged in the design doc as needing the same
# explicit confirm-or-adjust treatment A1-A4 got in Week 3, not a value
# derived from data.
PRIORITY_WEIGHTS = {1: 3, 2: 2, 3: 1}

# v1.2 admission control (research-plan/week4_scheduler_design.md's v1.2
# addendum): the declared service target used by AdmissionControlledDpScheduler.
# A declared policy value, not derived from data - explicitly NOT silently
# invented (same treatment PRIORITY_WEIGHTS got). Needs a sensitivity check
# across candidate values before being treated as final; see
# scripts/simulate_multi_query_scheduling.py's admission-control sensitivity
# section for that sweep.
DEFAULT_SERVICE_TARGET_ADHERENCE = 0.80


def deadline_ms(query_class: str, sla_tier: str) -> float:
    return BASELINE_P50_MS[query_class] * SLA_MULTIPLIERS[sla_tier]


@dataclass
class MultiQuery:
    query_id: str
    query_class: str              # short/medium/long
    sla_tier: str                  # relaxed/moderate/tight
    priority: int                   # 1 (highest) .. 3 (lowest)
    arrival_index: int
    deadline_ms: float
    predicted_static_ms: float        # none-condition p50 - used by StaticLeastSlack
    predicted_adaptive_ms: float      # batch's actual-condition p50 - used by AdaptiveLeastSlack + DP oracle
    actual_runtime_ms: float          # hidden from every scheduler's decision - revealed only during scoring


@dataclass
class ScheduleResult:
    order: list           # list[MultiQuery] in execution order
    priority_tie_breaks: int   # how often an exact slack tie was broken by priority (0 for schedulers where this doesn't apply)
    deferred_ids: frozenset = frozenset()  # query_ids admission control chose not to admit (empty for every scheduler except AdmissionControlledDpScheduler)


class FifoScheduler:
    name = "fifo"

    def order(self, queries: list) -> ScheduleResult:
        ordered = sorted(queries, key=lambda q: q.arrival_index)
        return ScheduleResult(order=ordered, priority_tie_breaks=0)


class PriorityFirstScheduler:
    name = "priority_first"

    def order(self, queries: list) -> ScheduleResult:
        ordered = sorted(queries, key=lambda q: (q.priority, q.arrival_index))
        return ScheduleResult(order=ordered, priority_tie_breaks=0)


class EdfScheduler:
    name = "edf"

    def order(self, queries: list) -> ScheduleResult:
        ordered = sorted(queries, key=lambda q: (q.deadline_ms, q.arrival_index))
        return ScheduleResult(order=ordered, priority_tie_breaks=0)


class _LeastSlackScheduler:
    """Shared iterative least-slack logic. Subclasses only differ in which
    predicted_runtime field feeds the slack calculation."""

    name = "least_slack_base"

    def _predicted_ms(self, q: MultiQuery) -> float:
        raise NotImplementedError

    def order(self, queries: list) -> ScheduleResult:
        remaining = list(queries)
        current_time = 0.0
        result = []
        tie_breaks = 0
        while remaining:
            def slack(q):
                return (q.deadline_ms - current_time) - self._predicted_ms(q)
            remaining.sort(key=lambda q: (slack(q), q.priority, q.arrival_index))
            if len(remaining) > 1 and slack(remaining[0]) == slack(remaining[1]):
                tie_breaks += 1
            next_q = remaining.pop(0)
            result.append(next_q)
            current_time += next_q.actual_runtime_ms
        return ScheduleResult(order=result, priority_tie_breaks=tie_breaks)


class StaticLeastSlackScheduler(_LeastSlackScheduler):
    """Condition-blind: always uses the none-condition predicted runtime,
    regardless of the batch's actual current ingestion_condition."""

    name = "static_least_slack"

    def _predicted_ms(self, q: MultiQuery) -> float:
        return q.predicted_static_ms


class AdaptiveLeastSlackScheduler(_LeastSlackScheduler):
    """This design: uses the predicted runtime for the batch's actual
    current ingestion_condition (none/moderate_sustained/heavy_sustained)."""

    name = "adaptive_least_slack"

    def _predicted_ms(self, q: MultiQuery) -> float:
        return q.predicted_adaptive_ms


def least_slack_one_shot_order(queries: list, predicted_field: str) -> list:
    """Reference implementation for the correctness self-check described
    in the module docstring: a single static sort by
    (deadline_ms - predicted_*_ms), computed once at current_time=0. Not
    used by the schedulers themselves - only to verify the iterative
    implementation above produces the identical order under v1's
    assumptions."""
    def key(q):
        predicted = getattr(q, predicted_field)
        return (q.deadline_ms - predicted, q.priority, q.arrival_index)
    return sorted(queries, key=key)


class DpOracleScheduler:
    """Exact DP oracle for small batches. Uses the SAME
    predicted_adaptive_ms as AdaptiveLeastSlack (design doc item 7's
    shared-prediction scope) - never sees actual_runtime_ms. Answers
    "what's the best possible ordering given what Adaptive Least Slack's
    predictions say," not "given perfect hindsight."

    Bitmask DP over which queries have been scheduled (`mask`). Because
    predicted_runtime is fixed per query for the whole batch, elapsed
    predicted time to reach a given mask is always exactly
    sum(predicted_adaptive_ms for j in mask), regardless of which order
    reached it - a deterministic function of mask alone, so no need to
    track elapsed time as a separate DP dimension (see the design doc's
    item 7 for why an earlier draft's "track min elapsed time" was an
    unnecessary complication, not just extra bookkeeping).

    Only tractable for small n (state space 2^n) - do not use for large
    batches. This project's batches are capped at 10 per the design.
    """

    name = "dp_oracle"

    def order(self, queries: list) -> ScheduleResult:
        n = len(queries)
        preds = [q.predicted_adaptive_ms for q in queries]
        deadlines = [q.deadline_ms for q in queries]
        weights = [PRIORITY_WEIGHTS[q.priority] for q in queries]

        full = 1 << n
        elapsed = [0.0] * full
        for mask in range(1, full):
            lsb = mask & (-mask)
            j = lsb.bit_length() - 1
            elapsed[mask] = elapsed[mask ^ lsb] + preds[j]

        dp = [-1] * full
        choice = [-1] * full
        dp[0] = 0
        for mask in range(1, full):
            best_score = -1
            best_j = -1
            for j in range(n):
                bit = 1 << j
                if not (mask & bit):
                    continue
                prev_mask = mask ^ bit
                if dp[prev_mask] < 0:
                    continue
                completion_time = elapsed[prev_mask] + preds[j]
                earns = weights[j] if completion_time <= deadlines[j] else 0
                candidate = dp[prev_mask] + earns
                if candidate > best_score:
                    best_score = candidate
                    best_j = j
            dp[mask] = best_score
            choice[mask] = best_j

        order_idx = []
        mask = full - 1
        while mask:
            j = choice[mask]
            order_idx.append(j)
            mask ^= (1 << j)
        order_idx.reverse()
        return ScheduleResult(order=[queries[i] for i in order_idx], priority_tie_breaks=0)


class PrioritySlaAwareDpScheduler:
    """Multi-objective DP: chooses the complete ordering that best protects
    SLA success for higher-priority queries, then maximizes total SLA
    adherence, then minimizes predicted lateness - not a greedy
    smallest-slack-first rule like AdaptiveLeastSlack, and not a
    single-objective weighted-score maximizer like DpOracle.

    Inputs, same restriction as every other scheduler: query_class,
    sla_tier, priority, deadline_ms, predicted_adaptive_ms, arrival_index.
    Never actual_runtime_ms - that stays hidden until score_order() replays
    the chosen order afterward. Uses the same condition-aware
    predicted_adaptive_ms as AdaptiveLeastSlack and DpOracle (same
    information, not extra).

    Slack (deadline_ms - elapsed - predicted_adaptive_ms at the point a
    query would start) informs urgency but is NOT a hard greedy ordering
    rule here, unlike _LeastSlackScheduler - the DP is free to run a
    less-urgent-by-slack query first if that produces a better overall
    outcome by the objective below.

    Objective: a lexicographic tuple, compared left to right (Python tuple
    comparison does this natively - no arbitrary scalar weighting):

        (weighted_sla_score, priority1_sla_met, priority2_sla_met,
         total_sla_met, -total_predicted_lateness)

    1. maximize weighted SLA success (priority_weight earned only for
       queries predicted to meet their deadline - no partial credit for
       misses)
    2. then prefer more priority-1 successes specifically
    3. then prefer more priority-2 successes specifically (priority-3's
       count is implied by the other three counts, since there are only
       three priority levels - no separate slot needed for the comparison
       to be well-defined)
    4. then maximize the raw count of SLA successes, as a tie-break among
       schedules already equal on priority-1/priority-2 protection
    5. then minimize total predicted lateness (`max(0, completion_time -
       deadline_ms)`, summed over every query in the schedule) as a final
       tie-break

    Priority protection (2-3) is ranked ABOVE raw count (4) deliberately:
    with the count ranked first (an earlier version of this scheduler),
    whenever two orderings tied on weighted_sla_score - which happens
    often, since weighted_sla_score already double- and triple-counts
    priority via PRIORITY_WEIGHTS - the scheduler would happily trade one
    priority-1 success for two lower-priority successes at equal weighted
    score, since that raised the raw count. Measured directly against
    DpOracleScheduler (which shares the identical weighted_sla_score
    primary objective and predicted_adaptive_ms inputs, and so ties with
    this scheduler on predicted weighted score in every batch where they
    disagree): that count-first ordering cost ~24 actual priority-1
    successes per 1000 simulated batches relative to DpOracleScheduler's
    tie-break (which isn't priority-1-aware either, but also isn't biased
    against it). Ranking priority protection above raw count closes that
    gap, at the cost of some raw/weighted adherence in the batches where
    the trade would otherwise have paid off.
    """

    name = "priority_sla_aware_dp"

    def order(self, queries: list) -> ScheduleResult:
        n = len(queries)
        preds = [q.predicted_adaptive_ms for q in queries]
        deadlines = [q.deadline_ms for q in queries]
        weights = [PRIORITY_WEIGHTS[q.priority] for q in queries]
        priorities = [q.priority for q in queries]

        full = 1 << n
        elapsed = [0.0] * full
        for mask in range(1, full):
            lsb = mask & (-mask)
            j = lsb.bit_length() - 1
            elapsed[mask] = elapsed[mask ^ lsb] + preds[j]

        # dp[mask] = (weighted_sla_score, priority1_sla_met, priority2_sla_met,
        #             total_sla_met, -total_predicted_lateness)
        NEG_INF = (-1, -1, -1, -1, float("-inf"))
        dp = [NEG_INF] * full
        choice = [-1] * full
        dp[0] = (0, 0, 0, 0, 0.0)

        for mask in range(1, full):
            best_state = NEG_INF
            best_j = -1
            for j in range(n):
                bit = 1 << j
                if not (mask & bit):
                    continue
                prev_mask = mask ^ bit
                if dp[prev_mask] == NEG_INF:
                    continue

                completion_time = elapsed[prev_mask] + preds[j]
                sla_met = completion_time <= deadlines[j]
                weighted_reward = weights[j] if sla_met else 0
                met_increment = 1 if sla_met else 0
                p1_increment = 1 if (sla_met and priorities[j] == 1) else 0
                p2_increment = 1 if (sla_met and priorities[j] == 2) else 0
                lateness = max(0.0, completion_time - deadlines[j])

                prev = dp[prev_mask]
                candidate = (
                    prev[0] + weighted_reward,
                    prev[1] + p1_increment,
                    prev[2] + p2_increment,
                    prev[3] + met_increment,
                    prev[4] - lateness,
                )
                if candidate > best_state:
                    best_state = candidate
                    best_j = j
            dp[mask] = best_state
            choice[mask] = best_j

        order_idx = []
        mask = full - 1
        while mask:
            j = choice[mask]
            order_idx.append(j)
            mask ^= (1 << j)
        order_idx.reverse()
        return ScheduleResult(order=[queries[i] for i in order_idx], priority_tie_breaks=0)


def _predicted_adherence_rate(ordered_queries: list) -> float:
    """Fraction of ordered_queries predicted to meet their deadline, using
    predicted_adaptive_ms (never actual_runtime_ms - this is an admission
    decision, made before execution). Used by AdmissionControlledDpScheduler
    as its feasibility test."""
    if not ordered_queries:
        return 1.0  # an empty admitted set trivially "meets" any target
    current_time = 0.0
    met = 0
    for q in ordered_queries:
        current_time += q.predicted_adaptive_ms
        if current_time <= q.deadline_ms:
            met += 1
    return met / len(ordered_queries)


class AdmissionControlledDpScheduler:
    """v1.2 (research-plan/week4_scheduler_design.md's v1.2 addendum):
    wraps DpOracleScheduler with a greedy admission-control gate, per the
    design discussion - "use DP to estimate the best achievable SLA under
    the current concurrency; if that expected SLA falls below a declared
    service target, stop admitting more queries."

    Processes queries in arrival order. For each arriving query, tentatively
    adds it to the admitted set and re-runs DpOracleScheduler on that
    trial set (using predicted_adaptive_ms - DpOracleScheduler is the
    single-objective, non-priority-aware oracle, matching this scheduler's
    deliberately-not-priority-aware v1 scope). If the DP-optimal ordering's
    PREDICTED adherence rate for the trial set is still >= service_target_adherence,
    the query is admitted; otherwise it's deferred. A later (e.g. shorter)
    query can still be admitted even after an earlier one was deferred -
    this is a per-candidate feasibility check, not a one-time cutoff.

    Admitted queries execute first, in DpOracleScheduler's chosen order.
    Deferred queries are NOT dropped from accounting: they execute after
    the admitted ones finish (arrival order among themselves), scored
    against their ORIGINAL deadline like any other query - by that point
    they'll almost always miss it, which is the intended, honest
    consequence of deferring (score_order's n_deferred/defer_rate makes
    this visible separately from ordinary misses).

    service_target_adherence is a DECLARED POLICY VALUE
    (DEFAULT_SERVICE_TARGET_ADHERENCE = 0.80), not derived from data -
    pass a different value to test sensitivity to that choice.
    """

    def __init__(self, service_target_adherence: float = DEFAULT_SERVICE_TARGET_ADHERENCE, name: Optional[str] = None):
        self.service_target_adherence = service_target_adherence
        self.name = name or "admission_controlled_dp"

    def order(self, queries: list) -> ScheduleResult:
        arrival_ordered = sorted(queries, key=lambda q: q.arrival_index)
        admitted: list = []
        deferred: list = []
        for q in arrival_ordered:
            trial = admitted + [q]
            trial_order = DpOracleScheduler().order(trial).order
            if _predicted_adherence_rate(trial_order) >= self.service_target_adherence:
                admitted.append(q)
            else:
                deferred.append(q)

        admitted_order = DpOracleScheduler().order(admitted).order if admitted else []
        final_order = admitted_order + deferred
        return ScheduleResult(
            order=final_order,
            priority_tie_breaks=0,
            deferred_ids=frozenset(q.query_id for q in deferred),
        )


def score_order(ordered_queries: list, deferred_ids: frozenset = frozenset()) -> dict:
    """Replays a fixed execution order through the batch's hidden
    actual_runtime_ms values - same scoring procedure for every scheduler,
    including the DP oracle (which chose its order using predictions, not
    this ground truth). deferred_ids (from AdmissionControlledDpScheduler,
    empty for every other scheduler) are still scored as ordinary misses if
    they miss - see n_deferred/defer_rate for visibility into how much of
    that is admission control's doing, not double-counted or excluded."""
    current_time = 0.0
    met = []
    for q in ordered_queries:
        current_time += q.actual_runtime_ms
        met.append(current_time <= q.deadline_ms)

    n = len(ordered_queries)
    n_met = sum(met)
    weighted_score = sum(
        PRIORITY_WEIGHTS[q.priority] for q, m in zip(ordered_queries, met) if m
    )
    max_possible_weighted = sum(PRIORITY_WEIGHTS[q.priority] for q in ordered_queries)
    n_deferred = len(deferred_ids)
    return {
        "n": n,
        "n_met": n_met,
        "sla_adherence_rate": n_met / n if n else float("nan"),
        "weighted_score": weighted_score,
        "max_possible_weighted": max_possible_weighted,
        "per_query_met": list(zip((q.query_id for q in ordered_queries), met)),
        "n_deferred": n_deferred,
        "defer_rate": n_deferred / n if n else float("nan"),
    }


ALL_SCHEDULERS = [
    FifoScheduler(),
    PriorityFirstScheduler(),
    EdfScheduler(),
    StaticLeastSlackScheduler(),
    AdaptiveLeastSlackScheduler(),
    DpOracleScheduler(),
    PrioritySlaAwareDpScheduler(),
    AdmissionControlledDpScheduler(),
]
