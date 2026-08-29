"""Generic stats/breakdown engine.

Parameterized by (question, grouping dimension, aggregation function) so new
aggregations (histograms, "closest guess wins" leaderboards) can be added
later without restructuring — see PLAN.md. No per-question-type branching
outside `_aggregate_group`.
"""

import statistics
from collections import Counter, defaultdict

from .models import BigScreenState, Question, Response

_BREAKDOWN_FIELD = {
    BigScreenState.Breakdown.SEX: "sex",
    BigScreenState.Breakdown.SIDE: "side",
    BigScreenState.Breakdown.RELATION: "relation",
}


def compute_breakdown(question: Question, breakdown: str, aggregation: str | None = None, threshold: float | None = None):
    """Returns {group_label: aggregate_dict}, ordered by group_label."""
    responses = Response.objects.filter(question=question).select_related("respondent")
    groups = _group(responses, breakdown)
    return {
        label: _aggregate_group(question.type, group_responses, aggregation, threshold)
        for label, group_responses in sorted(groups.items())
    }


def _group(responses, breakdown: str) -> dict[str, list[Response]]:
    if breakdown == BigScreenState.Breakdown.OVERALL:
        return {"Alla": list(responses)}
    groups: dict[str, list[Response]] = defaultdict(list)
    if breakdown == BigScreenState.Breakdown.AGE:
        # Age has no Django `choices` to drive the generic get_<field>_display()
        # trick below — Respondent stores an exact age (see pulse/models.py), and
        # the decade bucket is computed from it at read time instead.
        for r in responses:
            groups[r.respondent.age_bucket_label].append(r)
        return dict(groups)
    attr = _BREAKDOWN_FIELD[breakdown]
    # Group by the Swedish display label (e.g. "Brudens sida"), not the raw
    # stored value ("bride") — this is guest/host-facing, entirely Swedish.
    for r in responses:
        label = getattr(r.respondent, f"get_{attr}_display")()
        groups[label].append(r)
    return dict(groups)


def _aggregate_group(question_type: str, responses: list[Response], aggregation: str | None, threshold: float | None) -> dict:
    values = [r.answer["value"] for r in responses]
    count = len(values)

    if question_type == Question.Type.BOOLEAN:
        yes_pct = round(100 * sum(1 for v in values if v) / count, 1) if count else None
        return {"count": count, "yes_pct": yes_pct}

    if question_type == Question.Type.MULTIPLE_CHOICE:
        counts = Counter(values)
        options_pct = {opt: round(100 * n / count, 1) for opt, n in counts.items()} if count else {}
        return {"count": count, "options_pct": options_pct}

    if question_type == Question.Type.NUMBER:
        agg = aggregation or BigScreenState.Aggregation.AVG
        value = None
        if count:
            if agg == BigScreenState.Aggregation.AVG:
                value = statistics.mean(values)
            elif agg == BigScreenState.Aggregation.MEDIAN:
                value = statistics.median(values)
            elif agg == BigScreenState.Aggregation.MIN:
                value = min(values)
            elif agg == BigScreenState.Aggregation.MAX:
                value = max(values)
            elif agg == BigScreenState.Aggregation.COUNT_ABOVE_THRESHOLD:
                value = sum(1 for v in values if v > (threshold or 0))
        return {"count": count, "aggregation": agg, "value": value}

    raise ValueError(f"unknown question type: {question_type}")
