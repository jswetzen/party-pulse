"""Auto-surfaced "interesting stats" suggestions for the host console.

Scans every live question x every non-overall breakdown x every group in that breakdown,
and scores how much that group's answers diverge from the overall respondent pool for
that question. Surfaced as three separate ranked lists (PLAN.md "Interesting stats
suggestions", refined from an earlier single-list version):

- "Mest olika": the highest-|score| entries under the "effect_size" strategy (most
  extreme divergence from the overall population).
- "Mest lika": the lowest-|score| entries under that *same* "effect_size" strategy and
  *same* scored pool as "Mest olika" -- just the opposite tail of one ranking, not a
  second scan. "Uncanny similarity" is that ranking's low end (PLAN.md decision), and
  splitting it into its own list stops a handful of extreme entries from crowding
  near-zero ones out of a single combined top-N.
- "Störst procentskillnad": the highest-|score| entries under the separate
  "simple_pct_gap" strategy -- a deliberately plain, non-normalized magnitude (raw
  percentage-point gap, or raw unit gap for number questions) rather than a standardized
  effect size, for a host who'd rather eyeball "23 percentage points apart" than reason
  about Cohen's h.

This is a decision aid only: it never reveals anything itself, it only ranks candidates
for the host to review and load into the existing screen_control form (see the `prefill`
handling in views.screen_control) -- still requires the host's own explicit
"show"/"reveal" click, same as PLAN.md's "Reveal semantics" for the panel it sits next to.

Scoring is a pluggable strategy (see SCORING_STRATEGIES at the bottom) -- add a sibling
scoring function with the same (question, group_label, overall_values, group_values) ->
(score, blurb) | None signature and register it there to add a mode; nothing else in this
module needs to change (compute_suggestions() decides which strategies feed which lists).
"""

import math
import statistics
from collections import Counter
from dataclasses import dataclass

from .aggregations import _group
from .models import BigScreenState, Question, Response

# A group below this size still appears in the ranked lists -- PLAN.md's anonymity model
# ("party anonymous") trusts the host to judge live rather than silently hiding data, and
# this panel is a decision aid, not a publisher. But below ~5 responses, one guest's
# answer swings a group's percentage by 20+ points, so it's flagged `is_small_sample` for
# a caveat badge rather than presented with the same confidence as a larger group's score.
MIN_SAMPLE_SIZE = 5

# How many entries each of the three lists surfaces (5 per list, not 5 total). Recomputed
# on every host-console page load (no caching, no background job) -- see _score_pool()'s
# docstring for why that's cheap enough at this app's actual scale (PLAN.md: 39 seeded
# questions, wedding-guest-list response counts).
TOP_N = 5

_BREAKDOWNS = [
    BigScreenState.Breakdown.SEX,
    BigScreenState.Breakdown.AGE,
    BigScreenState.Breakdown.SIDE,
    BigScreenState.Breakdown.RELATION,
]

_BREAKDOWN_LABELS = dict(BigScreenState.Breakdown.choices)


def _sv_number(value: float, decimals: int = 1) -> str:
    """Swedish decimal-comma formatting for numbers embedded in blurb/score text. Not a
    Django template render (these strings are built once, in Python, from raw floats),
    so the `|unlocalize`-for-CSS / localized-for-text split PLAN.md documents for
    _screen_state.html doesn't apply here -- this is always human-readable text, so it
    always gets the comma."""
    return f"{value:.{decimals}f}".replace(".", ",")


@dataclass
class Suggestion:
    question_id: int
    question_text: str
    breakdown: str  # raw value (e.g. "side"), for building the prefill link
    breakdown_label: str  # Swedish display label (e.g. "Sida")
    group_label: str
    sample_size: int
    score: float  # raw strategy score -- meaning depends on which strategy produced this
    score_display: str  # human-formatted score for the card (e.g. "1,57" or "23,4 procentenheter")
    is_small_sample: bool
    blurb: str  # Swedish, human-readable description of the finding


@dataclass
class SuggestionLists:
    most_different: list[Suggestion]
    most_similar: list[Suggestion]
    biggest_pct_gap: list[Suggestion]


def _score_display(scoring: str, question_type: str, score: float) -> str:
    """Human-formatted score string for a suggestion card's score line, kept separate from
    the raw `score` field so the template never has to know which strategy produced a
    Suggestion or branch on question type itself. "effect_size" is always a unitless,
    signed number (Cohen's h / TVD / standardized mean diff). "simple_pct_gap" is a plain
    magnitude in the question's own terms: percentage points for boolean/multiple_choice
    (already non-negative -- see _pct_gap_boolean/_pct_gap_multiple_choice), or a signed
    raw-unit difference for number (no percentage points involved there)."""
    if scoring == "simple_pct_gap":
        if question_type == Question.Type.NUMBER:
            return f"{score:+.2f}".replace(".", ",")
        return f"{_sv_number(score, 1)} procentenheter"
    return _sv_number(score, 2)


def _score_pool(scoring: str) -> list[Suggestion]:
    """The full, unsorted, unsliced candidate pool for one scoring strategy: every live,
    non-system question x every non-overall breakdown x every group, scored by `scoring`.
    compute_suggestions() calls this once per strategy it needs and slices/sorts the
    result -- so "most different" and "most similar" share one pool (one call, two slices)
    while "biggest % gap" gets its own pool from a second call, per PLAN.md's decision that
    lists 1/2 must share a pool but list 3 is a genuinely different formula.

    Plain synchronous computation, no caching. At this app's real scale (PLAN.md: 39
    seeded questions) that's at most a few dozen questions x 4 breakdowns x a handful of
    groups each -- low hundreds of (question, breakdown, group) score computations per
    strategy per page load, each just arithmetic over a question's already-fetched
    Response rows (one query per question, reused across all 4 breakdowns via
    aggregations._group). Confirmed against this repo's actual seeded question count and
    test fixtures, not assumed. compute_suggestions() calling this twice (once per
    strategy) doubles that to a few hundred computations total, still cheap at this scale."""
    score_fn = SCORING_STRATEGIES[scoring]
    questions = Question.objects.filter(status=Question.Status.LIVE, is_system=False)
    results: list[Suggestion] = []
    for question in questions:
        responses = list(Response.objects.filter(question=question).select_related("respondent"))
        overall_values = [r.answer["value"] for r in responses]
        if len(overall_values) < 2:
            continue  # nothing to meaningfully compare a single group against
        for breakdown in _BREAKDOWNS:
            groups = _group(responses, breakdown)
            for group_label, group_responses in groups.items():
                group_values = [r.answer["value"] for r in group_responses]
                if not group_values:
                    continue
                scored = score_fn(question, group_label, overall_values, group_values)
                if scored is None:
                    continue
                score, blurb = scored
                results.append(
                    Suggestion(
                        question_id=question.id,
                        question_text=question.text_sv,
                        breakdown=breakdown,
                        breakdown_label=_BREAKDOWN_LABELS[breakdown],
                        group_label=group_label,
                        sample_size=len(group_values),
                        score=score,
                        score_display=_score_display(scoring, question.type, score),
                        is_small_sample=len(group_values) < MIN_SAMPLE_SIZE,
                        blurb=blurb,
                    )
                )
    return results


def compute_suggestions(top_n: int = TOP_N) -> SuggestionLists:
    """Builds the three ranked lists the host console panel renders -- see module
    docstring for what each one means. "Most different" and "most similar" are the two
    tails of one "effect_size"-scored pool (not independently computed), so with a small
    candidate pool they can legitimately share entries -- e.g. 6 total scored candidates
    means the top 5 and bottom 5 overlap in 4 of 5 slots. That's just what "most extreme"
    and "least extreme" mean when there's barely anything to rank; nothing special-cases
    it, the same way PLAN.md's original single-list design never needed to."""
    effect_size_pool = _score_pool("effect_size")
    effect_size_pool.sort(key=lambda s: abs(s.score), reverse=True)
    most_different = effect_size_pool[:top_n]
    most_similar = list(reversed(effect_size_pool))[:top_n]  # same pool, opposite (ascending |score|) tail

    pct_gap_pool = _score_pool("simple_pct_gap")
    pct_gap_pool.sort(key=lambda s: abs(s.score), reverse=True)
    biggest_pct_gap = pct_gap_pool[:top_n]

    return SuggestionLists(most_different=most_different, most_similar=most_similar, biggest_pct_gap=biggest_pct_gap)


# ---------------------------------------------------------------------------
# Scoring strategy: normalized effect size (feeds "Mest olika" / "Mest lika").
# One function per question type, dispatched from a single entry point --
# mirrors aggregations._aggregate_group's "one function branching on type"
# pattern rather than type-specific duplication or a class hierarchy.
# ---------------------------------------------------------------------------


def _boolean_effect_size(group_label: str, overall_values: list[bool], group_values: list[bool]):
    """Cohen's h: the standard effect size for comparing two proportions, defined as the
    difference of their arcsine-square-root transforms. Chosen over a raw percentage-point
    gap because it's stretched at the extremes (the gap between 90% and 99% is a much
    bigger deal than the gap between 45% and 54%, which a raw-point gap can't tell apart)
    and over a z-score because it deliberately does NOT scale with group size -- sample
    size is handled separately and visibly via `is_small_sample`, not folded into the
    score itself, so a tiny group's score is directly comparable to a large group's."""
    p_overall = sum(overall_values) / len(overall_values)
    p_group = sum(group_values) / len(group_values)
    if p_overall in (0, 1):
        # Every response to this question was identical -- and since `group_values` is a
        # subset of `overall_values`, the group must be identical too. Nothing to compare.
        return None
    h = 2 * math.asin(math.sqrt(p_group)) - 2 * math.asin(math.sqrt(p_overall))
    blurb = (
        f"Bland {group_label} svarade {_sv_number(100 * p_group)}% ja, "
        f"jämfört med {_sv_number(100 * p_overall)}% totalt (n={len(group_values)})."
    )
    return h, blurb


def _multiple_choice_effect_size(question: Question, group_label: str, overall_values: list[str], group_values: list[str]):
    """Total variation distance (half the sum of absolute per-option probability
    differences) between the group's answer distribution and the overall one. Chosen over
    raw chi-square because TVD is bounded to [0, 1] regardless of how many options a
    question has, so it stays "normalized ... comparable across questions" (PLAN.md
    decision) instead of naturally growing with option count the way chi-square does."""
    options = question.options or sorted(set(overall_values) | set(group_values))
    n_overall, n_group = len(overall_values), len(group_values)
    overall_counts = Counter(overall_values)
    group_counts = Counter(group_values)
    gaps = {opt: group_counts.get(opt, 0) / n_group - overall_counts.get(opt, 0) / n_overall for opt in options}
    tvd = 0.5 * sum(abs(gap) for gap in gaps.values())
    if tvd == 0:
        return 0.0, f"Bland {group_label} är fördelningen identisk med totalen (n={n_group})."
    # Name the single option that diverges most, for a concrete one-line blurb rather
    # than just the aggregate divergence number.
    biggest_opt = max(gaps, key=lambda opt: abs(gaps[opt]))
    group_pct = 100 * group_counts.get(biggest_opt, 0) / n_group
    overall_pct = 100 * overall_counts.get(biggest_opt, 0) / n_overall
    blurb = (
        f"Bland {group_label} svarade {_sv_number(group_pct)}% “{biggest_opt}”, "
        f"jämfört med {_sv_number(overall_pct)}% totalt (n={n_group})."
    )
    return tvd, blurb


def _number_effect_size(group_label: str, overall_values: list[float], group_values: list[float]):
    """Standardized mean difference (group mean minus overall mean, divided by the
    overall population's stdev) -- the number-question equivalent of Cohen's d. Uses
    population stdev (statistics.pstdev), not sample stdev: PLAN.md's anonymity model
    treats the guest list itself as the whole population being described, not a sample
    drawn from some larger one."""
    mean_overall = statistics.mean(overall_values)
    mean_group = statistics.mean(group_values)
    stdev_overall = statistics.pstdev(overall_values)
    if stdev_overall == 0:
        return None  # everyone gave the exact same answer -- no spread to standardize against
    score = (mean_group - mean_overall) / stdev_overall
    blurb = (
        f"Bland {group_label} var snittet {_sv_number(mean_group)}, "
        f"jämfört med {_sv_number(mean_overall)} totalt (n={len(group_values)})."
    )
    return score, blurb


def _effect_size_score(question: Question, group_label: str, overall_values: list, group_values: list):
    if question.type == Question.Type.BOOLEAN:
        return _boolean_effect_size(group_label, overall_values, group_values)
    if question.type == Question.Type.MULTIPLE_CHOICE:
        return _multiple_choice_effect_size(question, group_label, overall_values, group_values)
    if question.type == Question.Type.NUMBER:
        return _number_effect_size(group_label, overall_values, group_values)
    raise ValueError(f"unknown question type: {question.type}")


# ---------------------------------------------------------------------------
# Scoring strategy: simple % gap (feeds "Störst procentskillnad"). Deliberately
# the "raw, easy to eyeball" counterpart to effect_size above -- no arcsine
# stretching, no TVD normalization, no dividing by stdev. Same one-function-
# per-type dispatch pattern as effect_size, and each per-type function mirrors
# its effect_size counterpart's "nothing to compare" guard (see comments
# there) so the two strategies agree on which candidates are worth ranking at
# all, even though they disagree on *how much* a real candidate scores.
# ---------------------------------------------------------------------------


def _pct_gap_boolean(group_label: str, overall_values: list[bool], group_values: list[bool]):
    """Plain absolute percentage-point gap between the group's yes-rate and the overall
    yes-rate -- e.g. 62% vs 38% is reported as "24 percentage points", full stop, with no
    arcsine stretching at the extremes the way _boolean_effect_size's Cohen's h has."""
    p_overall = sum(overall_values) / len(overall_values)
    if p_overall in (0, 1):
        return None  # see _boolean_effect_size -- group is identical to overall by construction
    p_group = sum(group_values) / len(group_values)
    gap = abs(100 * p_group - 100 * p_overall)
    blurb = (
        f"Bland {group_label} svarade {_sv_number(100 * p_group)}% ja, "
        f"jämfört med {_sv_number(100 * p_overall)}% totalt (n={len(group_values)})."
    )
    return gap, blurb


def _pct_gap_multiple_choice(question: Question, group_label: str, overall_values: list[str], group_values: list[str]):
    """The single option with the largest absolute percentage-point gap between the
    group's share and the overall share -- e.g. "Vin: 80% vs 45%, a 35pp gap" -- rather
    than _multiple_choice_effect_size's aggregate total-variation-distance across every
    option at once. Deliberately per-option and un-normalized: this list exists so a host
    can eyeball "how many more/fewer people picked X", not reason about a distribution-
    wide divergence measure."""
    options = question.options or sorted(set(overall_values) | set(group_values))
    n_overall, n_group = len(overall_values), len(group_values)
    overall_counts = Counter(overall_values)
    group_counts = Counter(group_values)
    gaps = {
        opt: abs(100 * group_counts.get(opt, 0) / n_group - 100 * overall_counts.get(opt, 0) / n_overall)
        for opt in options
    }
    biggest_opt = max(gaps, key=lambda opt: gaps[opt])
    gap = gaps[biggest_opt]
    group_pct = 100 * group_counts.get(biggest_opt, 0) / n_group
    overall_pct = 100 * overall_counts.get(biggest_opt, 0) / n_overall
    blurb = (
        f"Bland {group_label} svarade {_sv_number(group_pct)}% “{biggest_opt}”, "
        f"jämfört med {_sv_number(overall_pct)}% totalt (n={n_group})."
    )
    return gap, blurb


def _pct_gap_number(group_label: str, overall_values: list[float], group_values: list[float]):
    """Raw, signed difference between the group's mean and the overall mean, in the
    question's own units -- no division by stdev the way _number_effect_size standardizes
    it. Signed (not abs'd) unlike the boolean/multiple_choice gaps above: a raw unit
    difference has a natural direction ("2.3 higher/lower") that's worth keeping, whereas
    a percentage-point gap between two shares of the same 100% doesn't need one."""
    stdev_overall = statistics.pstdev(overall_values)
    if stdev_overall == 0:
        return None  # see _number_effect_size -- no spread, group is identical to overall
    mean_overall = statistics.mean(overall_values)
    mean_group = statistics.mean(group_values)
    diff = mean_group - mean_overall
    blurb = (
        f"Bland {group_label} var snittet {_sv_number(mean_group)}, "
        f"jämfört med {_sv_number(mean_overall)} totalt (n={len(group_values)})."
    )
    return diff, blurb


def _simple_pct_gap_score(question: Question, group_label: str, overall_values: list, group_values: list):
    if question.type == Question.Type.BOOLEAN:
        return _pct_gap_boolean(group_label, overall_values, group_values)
    if question.type == Question.Type.MULTIPLE_CHOICE:
        return _pct_gap_multiple_choice(question, group_label, overall_values, group_values)
    if question.type == Question.Type.NUMBER:
        return _pct_gap_number(group_label, overall_values, group_values)
    raise ValueError(f"unknown question type: {question.type}")


# Registry of swappable scoring strategies, keyed by name and consumed by _score_pool()
# via compute_suggestions(). Both slots are now implemented (PLAN.md decision to keep this
# pluggable): "effect_size" feeds the "Mest olika"/"Mest lika" lists, "simple_pct_gap"
# feeds "Störst procentskillnad". Add a sibling scoring function with the same
# (question, group_label, overall_values, group_values) -> (score, blurb) | None
# signature and register it here to add a mode -- _score_pool() itself needs no changes.
SCORING_STRATEGIES = {
    "effect_size": _effect_size_score,
    "simple_pct_gap": _simple_pct_gap_score,
}
