"""Baseline schedulers (pure decision logic, no ML) per
research-plan/week3_testing_plan.md section 6.

Each scheduler answers one question for an incoming query: given its
query_class and sla_tier (a deadline expressed as a multiplier of that
class's Section 2 baseline p50), should it be admitted, and what's the
predicted runtime? These are the do-nothing / naive reference points the
paper's ingestion-aware scheduler needs to beat - not the final schedulers.

Baseline p50s and A2 SLA multipliers are the frozen Section 2 reference
values (results/week3/section2_baselines.md) - not recalculated here, per
that doc's explicit instruction.

Two independent safety-margin strategies are implemented side by side
(`PointEstimateFixedMarginScheduler`, `PointEstimatePerClassMarginScheduler`)
rather than picking one upfront - see
results/week3/section6_scheduler_baseline_comparison.md for why, and for
which one actually performs better on the data. There is deliberately no
logic anywhere in this file that switches between the two at runtime; they
are evaluated as separate baselines so the comparison stays interpretable.

The eventual ingestion-aware ("adaptive") scheduler is a different kind of
thing, not a third margin strategy - it will predict runtime/risk from
query_class + actual ingestion rate + system state + ingestion/query
overlap, not from a fixed or per-class multiplier on the static p50. Keep
it in its own class when it's built, so the paper's comparison
(FIFO -> PointEstimate -> FixedMargin -> PerClassMargin -> Adaptive) stays a
fair, legible ladder rather than the margin schedulers quietly growing
adaptive logic.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

# Fixed reference point from Section 2 (results/week3/section2_baselines.md).
BASELINE_P50_MS = {
    "short": 536.5,
    "medium": 472.8,
    "long": 1154.9,
}

# A2, confirmed 2026-08-20 (research-plan/week3_task_list.md).
SLA_MULTIPLIERS = {
    "relaxed": 4.0,
    "moderate": 2.0,
    "tight": 1.2,
}

# Candidate margin values from results/week3/scheduler_margin_options.md
# (Option B and Option A respectively), computed from Section 2 + Section 3
# observed variance. These are initial data-driven benchmark values to
# evaluate against each other and against plain point-estimate - NOT proven
# optimal. Week 3.6 later found session drift, extreme outliers, and
# workload-dependent behavior in this same kind of variance measurement, so
# treat both as a starting point, not a final answer. If either is ever
# re-tuned, do it on a held-out subset - never on the same data used to
# compare scheduler performance (see the module docstring above and
# results/week3/section6_scheduler_baseline_comparison.md).
GLOBAL_FIXED_MARGIN = 1.40

PER_CLASS_MARGINS = {
    "short": 1.40,
    "medium": 1.31,
    "long": 1.35,
}


def sla_deadline_ms(query_class: str, sla_tier: str) -> float:
    return BASELINE_P50_MS[query_class] * SLA_MULTIPLIERS[sla_tier]


@dataclass
class Query:
    query_id: str
    query_class: str    # short/medium/long
    sla_tier: str        # relaxed/moderate/tight
    arrival_index: int   # arrival order, for FIFO
    condition: str = "unknown"  # ingestion condition at collection time, for breakdown reporting only


@dataclass
class Decision:
    query_id: str
    admit: bool
    predicted_runtime_ms: Optional[float]
    deadline_ms: float
    reason: str


class FifoScheduler:
    """The do-nothing reference point: arrival order, always admit, no
    runtime estimate, no reaction to deadline or ingestion state."""

    name = "fifo"

    def decide(self, query: Query) -> Decision:
        return Decision(
            query_id=query.query_id,
            admit=True,
            predicted_runtime_ms=None,
            deadline_ms=sla_deadline_ms(query.query_class, query.sla_tier),
            reason="fifo: always admit, arrival order only",
        )


class PointEstimateScheduler:
    """Admits if the query class's known mean/median runtime fits the
    deadline. No uncertainty modeling, no reaction to current ingestion
    state - per week3_testing_plan.md section 6."""

    name = "point_estimate"

    def __init__(self, class_estimate_ms: Optional[dict] = None):
        self.class_estimate_ms = dict(class_estimate_ms or BASELINE_P50_MS)

    def decide(self, query: Query) -> Decision:
        predicted = self.class_estimate_ms[query.query_class]
        deadline = sla_deadline_ms(query.query_class, query.sla_tier)
        return Decision(
            query_id=query.query_id,
            admit=predicted <= deadline,
            predicted_runtime_ms=predicted,
            deadline_ms=deadline,
            reason=f"point estimate {predicted:.1f}ms vs deadline {deadline:.1f}ms",
        )


class PointEstimateFixedMarginScheduler(PointEstimateScheduler):
    """Baseline B: point estimate padded by ONE global safety margin, same
    multiplier for every query class.

    Research question this baseline tests: can a single conservative safety
    factor already improve SLA decisions without using ingestion or
    system-state information? The default (1.40, Option B in
    results/week3/scheduler_margin_options.md) is a conservative value
    derived from the worst early observed variance (short's Section 3
    stdev) - documented as a benchmark to evaluate, not claimed optimal.
    """

    name = "fixed_margin"

    def __init__(self, margin: float = GLOBAL_FIXED_MARGIN, class_estimate_ms: Optional[dict] = None):
        super().__init__(class_estimate_ms)
        self.margin = margin

    def decide(self, query: Query) -> Decision:
        predicted = self.class_estimate_ms[query.query_class] * self.margin
        deadline = sla_deadline_ms(query.query_class, query.sla_tier)
        return Decision(
            query_id=query.query_id,
            admit=predicted <= deadline,
            predicted_runtime_ms=predicted,
            deadline_ms=deadline,
            reason=f"point estimate x{self.margin} = {predicted:.1f}ms vs deadline {deadline:.1f}ms",
        )


class PointEstimatePerClassMarginScheduler(PointEstimateScheduler):
    """Baseline C: point estimate padded by a QUERY-CLASS-SPECIFIC safety
    margin.

    Research question this baseline tests: does tuning the safety margin by
    query class materially improve admission/SLA performance over one
    global margin? Defaults (Option A in
    results/week3/scheduler_margin_options.md: short=1.40, medium=1.31,
    long=1.35) come from mean + 2x worst-observed-stdev per class -
    documented as a benchmark to evaluate, not claimed optimal. Note these
    candidates were partly derived from the same Section 3 dataset used to
    evaluate this scheduler - see the module docstring's leakage caveat
    before treating a "per-class wins" result as fully clean.
    """

    name = "per_class_margin"

    def __init__(self, class_margins: Optional[dict] = None, class_estimate_ms: Optional[dict] = None):
        super().__init__(class_estimate_ms)
        self.class_margins = dict(class_margins or PER_CLASS_MARGINS)

    def decide(self, query: Query) -> Decision:
        margin = self.class_margins[query.query_class]
        predicted = self.class_estimate_ms[query.query_class] * margin
        deadline = sla_deadline_ms(query.query_class, query.sla_tier)
        return Decision(
            query_id=query.query_id,
            admit=predicted <= deadline,
            predicted_runtime_ms=predicted,
            deadline_ms=deadline,
            reason=f"point estimate x{margin} ({query.query_class}) = {predicted:.1f}ms vs deadline {deadline:.1f}ms",
        )


def simulate(scheduler, queries: list, actual_runtime_ms: dict) -> list:
    """Runs a scheduler over a list of Query objects with known actual
    runtimes (e.g. from results/week3/section3_dataset.csv) and returns one
    record per query (decision + outcome). Deliberately returns granular
    records rather than a pre-aggregated summary, so callers can group by
    whatever they need (overall, by query_class, by sla_tier, by ingestion
    condition) via `summarize()` without re-running the simulation."""
    records = []
    for q in queries:
        decision = scheduler.decide(q)
        actual = actual_runtime_ms[q.query_id]
        met_deadline = actual <= decision.deadline_ms
        records.append({
            "scheduler": scheduler.name,
            "query_id": q.query_id,
            "query_class": q.query_class,
            "sla_tier": q.sla_tier,
            "condition": q.condition,
            "admit": decision.admit,
            "met_deadline": met_deadline,
            "predicted_runtime_ms": decision.predicted_runtime_ms,
            "deadline_ms": decision.deadline_ms,
            "actual_runtime_ms": actual,
        })
    return records


def summarize(records: list, group_by: Optional[list] = None) -> list:
    """Aggregates simulate() records into rate-based summary rows, optionally
    grouped by any of query_class/sla_tier/condition (pass group_by=None for
    one overall row across whatever records were passed in - callers should
    already have segmented records by scheduler before calling this).

    Reports both sides of the trade-off on purpose: reliability (SLA miss
    rate among admitted queries) and admission efficiency (admit rate,
    unnecessary-rejection rate). A scheduler can trivially reach zero SLA
    misses by rejecting almost everything, so neither number alone is a fair
    comparison - see results/week3/section6_scheduler_baseline_comparison.md.
    """
    keys = group_by or []
    groups = defaultdict(list)
    for r in records:
        groups[tuple(r[k] for k in keys)].append(r)

    out = []
    for key, rows in sorted(groups.items()):
        n = len(rows)
        admitted = [r for r in rows if r["admit"]]
        rejected = [r for r in rows if not r["admit"]]
        admitted_missed = [r for r in admitted if not r["met_deadline"]]
        rejected_would_have_met = [r for r in rejected if r["met_deadline"]]
        out.append({
            **dict(zip(keys, key)),
            "n": n,
            "admitted": len(admitted),
            "admit_rate": (len(admitted) / n) if n else float("nan"),
            "admitted_and_met_deadline": len(admitted) - len(admitted_missed),
            "admitted_but_missed_deadline": len(admitted_missed),
            "sla_miss_rate_among_admitted": (len(admitted_missed) / len(admitted)) if admitted else float("nan"),
            "rejected": len(rejected),
            "rejected_would_have_met_deadline": len(rejected_would_have_met),
        })
    return out
