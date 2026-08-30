"""Auto-surfaced "interesting stats" suggestions for the host console.

Scans every live question x every non-overall breakdown x every group in that breakdown,
and scores how much that group's answers diverge either from the overall respondent pool
for that question (the original "vs-overall" comparison) or from one specific other group
in the same breakdown (the newer "pairwise" comparison -- e.g. "kvinnor" vs "män" head to
head, not each only vs. the pooled average). Surfaced as six separate ranked lists on the
host console (PLAN.md "Interesting stats suggestions"):

- "Mest olika" / "Mest lika": the two tails of one "effect_size"-scored vs-overall pool
  (most extreme divergence from, and closest agreement with, the overall population).
- "Störst procentskillnad": the vs-overall pool for the separate "simple_pct_gap" strategy
  -- a deliberately plain, non-normalized magnitude (raw percentage-point gap, or raw unit
  gap for number questions) rather than a standardized effect size, for a host who'd rather
  eyeball "23 percentage points apart" than reason about Cohen's h.
- "Mest olika, grupp mot grupp" / "Mest lika, grupp mot grupp": the same two tails, but of
  a *pairwise* "effect_size"-scored pool -- every unordered pair of groups within a
  breakdown, scored against each other directly instead of against "everyone".
- "Störst procentskillnad, grupp mot grupp": the pairwise counterpart of the third list.

Design call (PLAN.md, "Interesting stats suggestions" -- generalizing to group-vs-group,
2026-08-29): pairwise findings are ADDED as three new lists alongside the original three,
not merged into them and not a replacement. The two comparison types answer different
questions ("how does this group compare to the room as a whole" vs. "how do these two
specific groups compare to each other") and a Suggestion/PairwiseSuggestion is shaped
differently (one group label vs. two) -- folding them into one ranked list would mean
either losing that distinction in the UI or branching the template per-entry anyway, so
six clearly-labeled lists reads better than one ambiguous one. Nothing stops a future pass
from re-merging them if that turns out wrong in practice.

This is a decision aid only: it never reveals anything itself, it only ranks candidates
for the host to review and load into the existing screen_control form (see the `prefill`
handling in views.screen_control) -- still requires the host's own explicit
"show"/"reveal" click, same as PLAN.md's "Reveal semantics" for the panel it sits next to.

Scoring is a pluggable strategy (see SCORING_STRATEGIES at the bottom) -- add a sibling
scoring function with the same (question, label_a, values_a, label_b, values_b) ->
(score, blurb) | None signature and register it there to add a mode; nothing else in this
module needs to change (_score_pool() feeds it both vs-overall pairs -- always with
label_b="totalt", values_b=the question's overall values -- and genuine pairwise pairs from
the same per-breakdown group scan, so compute_suggestions() decides which strategies feed
which lists, same as before)."""

import itertools
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
# For a pairwise finding this applies to EACH side independently -- a pair is flagged if
# either group is below the threshold, not only when both are (see _score_pool below).
MIN_SAMPLE_SIZE = 5

# How many entries each ranked list surfaces (5 per list, not 5 total). Recomputed on
# every host-console page load (no caching, no background job) -- see _score_pool()'s
# docstring for why that's cheap enough at this app's actual scale (PLAN.md: 39 seeded
# questions, wedding-guest-list response counts) even with pairwise comparisons added.
TOP_N = 5

_BREAKDOWNS = [
    BigScreenState.Breakdown.SEX,
    BigScreenState.Breakdown.AGE,
    BigScreenState.Breakdown.SIDE,
    BigScreenState.Breakdown.RELATION,
]

_BREAKDOWN_LABELS = dict(BigScreenState.Breakdown.choices)

# Sentinel passed as a pairwise function's `label_b` to mean "the overall respondent pool
# for this question", not an actual second group. Every vs-overall Suggestion is produced
# by the exact same scoring functions as a pairwise one, just called with this as label_b
# and the question's overall values as values_b -- see _score_pool().
_OVERALL = "totalt"


def _sv_number(value: float, decimals: int = 1) -> str:
    """Swedish decimal-comma formatting for numbers embedded in blurb/score text. Not a
    Django template render (these strings are built once, in Python, from raw floats),
    so the `|unlocalize`-for-CSS / localized-for-text split PLAN.md documents for
    _screen_state.html doesn't apply here -- this is always human-readable text, so it
    always gets the comma."""
    return f"{value:.{decimals}f}".replace(".", ",")


def _vs_phrase(label: str) -> str:
    """Swedish phrase for "compared to <label>" in a blurb. "totalt" reads naturally on its
    own ("jämfört med 40% totalt"); any real group label needs "bland" in front of it to
    read as a group rather than a stray noun ("jämfört med 40% bland män")."""
    return _OVERALL if label == _OVERALL else f"bland {label}"


def _n_phrase(label_b: str, n_a: int, n_b: int) -> str:
    """Sample-size suffix for a blurb. The vs-overall case only names the group's own n
    (matches the original single-comparison wording -- "everyone" doesn't need its own n
    called out every time); a genuine pairwise comparison names both, since either side can
    independently be a small sample (see MIN_SAMPLE_SIZE above)."""
    return f"(n={n_a})" if label_b == _OVERALL else f"(n={n_a} vs {n_b})"


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
class PairwiseSuggestion:
    """Same shape as Suggestion, but for a genuine (group_a, group_b) comparison instead of
    (group, overall) -- two group labels/sample sizes instead of one, so the template can
    render "which two groups", not just one group name. See module docstring's design call
    for why this is a separate dataclass/list family rather than folded into Suggestion."""

    question_id: int
    question_text: str
    breakdown: str
    breakdown_label: str
    group_a_label: str
    group_b_label: str
    sample_size_a: int
    sample_size_b: int
    score: float
    score_display: str
    is_small_sample: bool  # true if EITHER side is below MIN_SAMPLE_SIZE
    blurb: str


@dataclass
class Highlight:
    """Template-ready wrapper uniting a vs-overall `Suggestion` or pairwise `PairwiseSuggestion`
    into one shape, for the combined "top 10, all categories" panel added on top of the six
    existing lists (`compute_suggestions()`'s `highlights` field; see PLAN.md "Interesting
    stats suggestions" for Johan's three explicit design calls this implements: round-robin
    merge order, reserved similarity slots, six sections collapsed-not-removed). This is a
    presentation-layer composition, not a new scoring computation -- every Highlight is built
    by wrapping an entry a pool already produced, never by recomputing or re-scoring anything.
    Deliberately does NOT carry the raw `score` float: `Suggestion`/`PairwiseSuggestion` scores
    live on incomparable units across the six pools (Cohen's h, TVD, standardized mean diff,
    raw percentage points, raw number-unit diffs), which is exactly why the round-robin merge
    exists instead of a literal "sort everything by |score|" -- keeping `score` off this
    dataclass makes that impossible to do by accident later (e.g. in a future template change),
    only `score_display` (already human-formatted per-category) is exposed.

    `group_display`/`sample_size_display` fold the vs-overall/pairwise branch in here, once,
    instead of in the template: a vs-overall finding has one group and one n ("kvinnor",
    "n=6"), a pairwise one has two of each ("kvinnor vs män", "n=6 vs n=4") -- see
    `_make_highlight`. `is_pairwise` is still exposed too, for the rare bit of template
    behaviour (if any) that can't be expressed as a pre-formatted string."""

    category: str  # matches the SuggestionLists field name this came from, e.g. "most_different"
    category_label: str  # Swedish section heading, reused verbatim from screen_control.html's <h3>s
    score_label: str  # "Avvikelse" for effect_size categories, "Skillnad" for simple_pct_gap ones
    is_pairwise: bool  # True for a group-vs-group finding, False for group-vs-overall
    is_similarity: bool  # True for one of the (at most two) reserved "Mest lika" picks -- see below
    question_id: int
    question_text: str
    breakdown: str
    breakdown_label: str
    group_display: str  # "kvinnor" (vs-overall) or "kvinnor vs män" (pairwise), ready to print
    sample_size_display: str  # "n=6" or "n=6 vs n=4", ready to print
    score_display: str
    is_small_sample: bool
    blurb: str


@dataclass
class SuggestionLists:
    most_different: list[Suggestion]
    most_similar: list[Suggestion]
    biggest_pct_gap: list[Suggestion]
    most_different_pairwise: list[PairwiseSuggestion]
    most_similar_pairwise: list[PairwiseSuggestion]
    biggest_pct_gap_pairwise: list[PairwiseSuggestion]
    highlights: list[Highlight]


def _score_display(scoring: str, question_type: str, score: float) -> str:
    """Human-formatted score string for a suggestion card's score line, kept separate from
    the raw `score` field so the template never has to know which strategy produced a
    Suggestion or branch on question type itself. "effect_size" is always a unitless,
    signed number (Cohen's h / TVD / standardized mean diff). "simple_pct_gap" is a plain
    magnitude in the question's own terms: percentage points for boolean/multiple_choice
    (already non-negative -- see _pct_gap_boolean/_pct_gap_multiple_choice), or a signed
    raw-unit difference for number (no percentage points involved there). Shared as-is by
    vs-overall and pairwise findings -- the score's meaning per strategy/type doesn't
    change depending on what it's being compared against."""
    if scoring == "simple_pct_gap":
        if question_type == Question.Type.NUMBER:
            return f"{score:+.2f}".replace(".", ",")
        return f"{_sv_number(score, 1)} procentenheter"
    return _sv_number(score, 2)


def _question_has_no_signal(question: Question, overall_values: list) -> bool:
    """True when every possible comparison for this question -- vs-overall or pairwise,
    effect_size or simple_pct_gap -- is trivially zero, so the question contributes nothing
    to any ranked list. Every group's (and every pair of groups') values are a subset of
    overall_values, so if the overall pool itself has no variance (boolean: everyone
    answered the same way; number: everyone gave the same number), no group -- and no pair
    of groups -- drawn from it can differ either. Checked once per question rather than
    once per (group, strategy) or once per pair, since it's a property of the question's
    answers as a whole, not of which one or two groups are being compared -- this replaces
    what used to be a per-group guard inside each of the boolean/number scoring functions
    (multiple_choice never had one: TVD/pct-gap are well-defined, including at exactly 0,
    regardless of how uniform the distribution is)."""
    if question.type == Question.Type.BOOLEAN:
        p = sum(overall_values) / len(overall_values)
        return p in (0, 1)
    if question.type == Question.Type.NUMBER:
        return statistics.pstdev(overall_values) == 0
    return False


def _score_pool(scoring: str) -> tuple[list[Suggestion], list[PairwiseSuggestion]]:
    """The full, unsorted, unsliced candidate pools for one scoring strategy: every live,
    non-system question x every non-overall breakdown x every group (vs-overall pool), AND
    x every unordered pair of groups within that breakdown (pairwise pool) -- both scored by
    `scoring`. compute_suggestions() calls this once per strategy it needs and slices/sorts
    each of the two pools it returns.

    Pairwise piggybacks on the exact same per-question Response query and per-breakdown
    _group() call already needed for the vs-overall pool -- no extra database queries, just
    extra in-memory arithmetic over group_values, a dict already built while computing the
    vs-overall pool. At this app's real scale (PLAN.md: 39 seeded questions, single-digit
    groups per breakdown -- sex has 2 groups/1 pair, side and relation typically 2-3
    groups/1-3 pairs, age up to 5 groups/10 pairs) that's at most a few dozen extra scored
    pairs per question, not the group count squared across the *whole* app: pairs are only
    ever formed within one breakdown's own groups, and age -- the one dimension with more
    than 2-3 groups -- tops out at C(5,2)=10 pairs, cheap even run twice (once per scoring
    strategy). Measured with pulse/management/commands/seed_demo_data.py's 39-question/
    150-respondent dataset: compute_suggestions() before pairwise support and after are both
    comfortably sub-100ms (see PLAN.md for the measured numbers), i.e. pairwise support adds
    no perceptible page-load cost at this app's scale."""
    score_fn = SCORING_STRATEGIES[scoring]
    questions = Question.objects.filter(status=Question.Status.LIVE, is_system=False)
    vs_overall: list[Suggestion] = []
    pairwise: list[PairwiseSuggestion] = []
    for question in questions:
        responses = list(Response.objects.filter(question=question).select_related("respondent"))
        overall_values = [r.answer["value"] for r in responses]
        if len(overall_values) < 2:
            continue  # nothing to meaningfully compare a single group against
        if _question_has_no_signal(question, overall_values):
            continue
        for breakdown in _BREAKDOWNS:
            groups = _group(responses, breakdown)
            group_values: dict[str, list] = {}
            for group_label, group_responses in groups.items():
                values = [r.answer["value"] for r in group_responses]
                if not values:
                    continue
                group_values[group_label] = values
                scored = score_fn(question, group_label, values, _OVERALL, overall_values)
                if scored is None:
                    continue
                score, blurb = scored
                vs_overall.append(
                    Suggestion(
                        question_id=question.id,
                        question_text=question.text_sv,
                        breakdown=breakdown,
                        breakdown_label=_BREAKDOWN_LABELS[breakdown],
                        group_label=group_label,
                        sample_size=len(values),
                        score=score,
                        score_display=_score_display(scoring, question.type, score),
                        is_small_sample=len(values) < MIN_SAMPLE_SIZE,
                        blurb=blurb,
                    )
                )
            # Every unordered pair of groups actually present in this breakdown for this
            # question -- e.g. sex gives at most one pair ("kvinnor" vs "män"), age up to
            # ten (see docstring above). group_values only holds groups with >=1 response
            # (built in the loop above), so a group nobody in this breakdown answered
            # simply can't form a pair.
            for (label_a, values_a), (label_b, values_b) in itertools.combinations(group_values.items(), 2):
                scored = score_fn(question, label_a, values_a, label_b, values_b)
                if scored is None:
                    continue
                score, blurb = scored
                pairwise.append(
                    PairwiseSuggestion(
                        question_id=question.id,
                        question_text=question.text_sv,
                        breakdown=breakdown,
                        breakdown_label=_BREAKDOWN_LABELS[breakdown],
                        group_a_label=label_a,
                        group_b_label=label_b,
                        sample_size_a=len(values_a),
                        sample_size_b=len(values_b),
                        score=score,
                        score_display=_score_display(scoring, question.type, score),
                        is_small_sample=len(values_a) < MIN_SAMPLE_SIZE or len(values_b) < MIN_SAMPLE_SIZE,
                        blurb=blurb,
                    )
                )
    return vs_overall, pairwise


# How many entries the combined "top 10, all categories" panel surfaces (Johan's decision,
# PLAN.md "Interesting stats suggestions" -- fixed at 10 regardless of TOP_N/top_n, since it's
# a presentation choice about the panel, not a scoring-pool size).
HIGHLIGHTS_N = 10

# Fixed round-robin order for the four "extreme" (magnitude-ranked) pools -- Johan's decision
# 1: take rank-1 from each pool in this order, then rank-2 from each, etc., skipping a pool
# once it runs out rather than erroring or padding. Each tuple is
# (pool attribute name on SuggestionLists, Swedish category label reused verbatim from
# screen_control.html's <h3>s, score-line label matching that section's existing wording,
# is_pairwise).
_EXTREME_CATEGORIES = [
    ("most_different", "Mest olika", "Avvikelse", False),
    ("most_different_pairwise", "Mest olika, grupp mot grupp", "Avvikelse", True),
    ("biggest_pct_gap", "Störst procentskillnad", "Skillnad", False),
    ("biggest_pct_gap_pairwise", "Störst procentskillnad, grupp mot grupp", "Skillnad", True),
]

# The two "similarity" pools eligible for a reserved highlight slot -- Johan's decision 2: a
# magnitude-ranked round-robin over the four pools above would never naturally surface a "Mest
# lika" finding (it's the *low* end of a pool sorted by descending |score|), so up to one
# highlight from each is reserved separately, from index 0 (each pool's single best/lowest-
# |score| entry) rather than folded into the round-robin.
_SIMILARITY_CATEGORIES = [
    ("most_similar", "Mest lika", "Avvikelse", False),
    ("most_similar_pairwise", "Mest lika, grupp mot grupp", "Avvikelse", True),
]


def _highlight_identity(item, is_pairwise: bool):
    """De-duplication key for a Suggestion/PairwiseSuggestion: identifies "the same finding"
    regardless of which category list it was drawn from. Needed because a single (question,
    breakdown, group) vs-overall entry can legitimately be the extreme of BOTH the
    effect_size and simple_pct_gap pools (two different scores over the same candidate), and
    -- in a pool small enough that "most different" and "most similar" overlap (see
    compute_suggestions()'s docstring) -- `most_similar[0]` can literally be the same entry as
    `most_different[0]`. Either way the combined top-10 should show that finding once, not
    twice under two labels. Pairwise identity uses a frozenset of the two group labels so
    swapping which side landed in group_a/group_b (an itertools.combinations incidental, per
    _score_pool) doesn't produce a false non-duplicate."""
    if is_pairwise:
        return (item.question_id, item.breakdown, frozenset({item.group_a_label, item.group_b_label}))
    return (item.question_id, item.breakdown, item.group_label)


def _make_highlight(item, category: str, category_label: str, score_label: str, is_pairwise: bool, is_similarity: bool) -> Highlight:
    if is_pairwise:
        group_display = f"{item.group_a_label} vs {item.group_b_label}"
        sample_size_display = f"n={item.sample_size_a} vs {item.sample_size_b}"
    else:
        group_display = item.group_label
        sample_size_display = f"n={item.sample_size}"
    return Highlight(
        category=category,
        category_label=category_label,
        score_label=score_label,
        is_pairwise=is_pairwise,
        is_similarity=is_similarity,
        question_id=item.question_id,
        question_text=item.question_text,
        breakdown=item.breakdown,
        breakdown_label=item.breakdown_label,
        group_display=group_display,
        sample_size_display=sample_size_display,
        score_display=item.score_display,
        is_small_sample=item.is_small_sample,
        blurb=item.blurb,
    )


def _build_highlights(lists: SuggestionLists) -> list[Highlight]:
    """Combined "top 10, all categories" list -- Johan's three explicit design calls
    (PLAN.md "Interesting stats suggestions"), implemented as pure selection/wrapping over the
    six pools `compute_suggestions()` already sorted and sliced to top_n. Never recomputes or
    re-scores anything.

    1. Up to 2 reserved similarity highlights first (decision 2): index 0 of `most_similar`
       and of `most_similar_pairwise`, each only if that pool is non-empty -- so 0, 1, or 2
       slots are reserved depending on what's actually available, never padded.
    2. The remaining slots (10 minus however many similarity highlights landed) filled by a
       fixed-order round-robin over the four "extreme" pools (decision 1): rank-1 from each of
       most_different / most_different_pairwise / biggest_pct_gap / biggest_pct_gap_pairwise in
       that order, then rank-2 from each, etc. A pool shorter than the current rank (or
       already exhausted) is silently skipped -- never an error, never a filler entry.

    A single `seen` de-dup set spans both phases (see _highlight_identity) so the same
    underlying (question, breakdown, group) finding is never shown twice under two category
    labels -- a duplicate is simply skipped and the round-robin moves on to the next
    pool/rank, same graceful "just skip it" behaviour as an exhausted pool.

    Final order returned is similarity highlights (if any) followed by the round-robin
    picks -- this only affects on-page order (screen_control.html is free to lay these out
    differently, e.g. visually pinning the similarity card(s) instead of listing them first;
    Johan's decision left presentation to implementation), it does not affect which findings
    end up included."""
    seen: set = set()
    highlights: list[Highlight] = []

    for pool_name, category_label, score_label, is_pairwise in _SIMILARITY_CATEGORIES:
        pool = getattr(lists, pool_name)
        if not pool:
            continue
        item = pool[0]
        identity = _highlight_identity(item, is_pairwise)
        if identity in seen:
            continue
        seen.add(identity)
        highlights.append(_make_highlight(item, pool_name, category_label, score_label, is_pairwise, is_similarity=True))

    remaining = HIGHLIGHTS_N - len(highlights)
    extreme_pools = [
        (getattr(lists, pool_name), pool_name, category_label, score_label, is_pairwise)
        for pool_name, category_label, score_label, is_pairwise in _EXTREME_CATEGORIES
    ]
    extreme_picks: list[Highlight] = []
    rank = 0
    while len(extreme_picks) < remaining and any(rank < len(pool) for pool, *_rest in extreme_pools):
        for pool, pool_name, category_label, score_label, is_pairwise in extreme_pools:
            if len(extreme_picks) >= remaining:
                break
            if rank >= len(pool):
                continue  # this pool exhausted at this rank -- skip it, don't pad, move on
            item = pool[rank]
            identity = _highlight_identity(item, is_pairwise)
            if identity in seen:
                continue
            seen.add(identity)
            extreme_picks.append(_make_highlight(item, pool_name, category_label, score_label, is_pairwise, is_similarity=False))
        rank += 1

    highlights.extend(extreme_picks)
    return highlights


def compute_suggestions(top_n: int = TOP_N) -> SuggestionLists:
    """Builds the six ranked lists the host console panel renders -- see module docstring
    for what each one means. Within each of the two pool families (vs-overall,
    pairwise), "most different" and "most similar" are the two tails of one
    "effect_size"-scored pool (not independently computed), so with a small candidate pool
    they can legitimately share entries -- e.g. 6 total scored candidates means the top 5
    and bottom 5 overlap in 4 of 5 slots. That's just what "most extreme" and "least
    extreme" mean when there's barely anything to rank; nothing special-cases it, the same
    way PLAN.md's original single-list design never needed to."""
    effect_size_pool, effect_size_pairwise_pool = _score_pool("effect_size")
    effect_size_pool.sort(key=lambda s: abs(s.score), reverse=True)
    most_different = effect_size_pool[:top_n]
    most_similar = list(reversed(effect_size_pool))[:top_n]  # same pool, opposite (ascending |score|) tail

    effect_size_pairwise_pool.sort(key=lambda s: abs(s.score), reverse=True)
    most_different_pairwise = effect_size_pairwise_pool[:top_n]
    most_similar_pairwise = list(reversed(effect_size_pairwise_pool))[:top_n]

    pct_gap_pool, pct_gap_pairwise_pool = _score_pool("simple_pct_gap")
    pct_gap_pool.sort(key=lambda s: abs(s.score), reverse=True)
    biggest_pct_gap = pct_gap_pool[:top_n]

    pct_gap_pairwise_pool.sort(key=lambda s: abs(s.score), reverse=True)
    biggest_pct_gap_pairwise = pct_gap_pairwise_pool[:top_n]

    lists = SuggestionLists(
        most_different=most_different,
        most_similar=most_similar,
        biggest_pct_gap=biggest_pct_gap,
        most_different_pairwise=most_different_pairwise,
        most_similar_pairwise=most_similar_pairwise,
        biggest_pct_gap_pairwise=biggest_pct_gap_pairwise,
        highlights=[],
    )
    lists.highlights = _build_highlights(lists)
    return lists


# ---------------------------------------------------------------------------
# Scoring strategy: normalized effect size (feeds "Mest olika" / "Mest lika"
# and their pairwise counterparts). One function per question type, dispatched
# from a single entry point -- mirrors aggregations._aggregate_group's "one
# function branching on type" pattern rather than type-specific duplication or
# a class hierarchy. Each function takes two arbitrary (label, values) sides --
# label_b/values_b is the question's overall pool for a vs-overall finding
# (label_b == _OVERALL, guaranteed by _score_pool), or a genuine second group
# for a pairwise finding. Nothing here needs to know which case it's in except
# where the *math* itself must differ (see _number_effect_size) -- the blurb
# wording difference is handled once, by _vs_phrase/_n_phrase above.
# ---------------------------------------------------------------------------


def _boolean_effect_size(label_a: str, values_a: list[bool], label_b: str, values_b: list[bool]):
    """Cohen's h: the standard effect size for comparing two proportions, defined as the
    difference of their arcsine-square-root transforms. Chosen over a raw percentage-point
    gap because it's stretched at the extremes (the gap between 90% and 99% is a much
    bigger deal than the gap between 45% and 54%, which a raw-point gap can't tell apart)
    and over a z-score because it deliberately does NOT scale with group size -- sample
    size is handled separately and visibly via `is_small_sample`, not folded into the
    score itself, so a tiny group's score is directly comparable to a large group's. Well
    defined for any two proportions in [0, 1] (arcsin has no singularity there), so unlike
    the number strategy below this needs no per-pair guard -- the only "nothing to compare"
    case (the whole question is degenerate) is filtered once per question by
    _question_has_no_signal before this is ever called."""
    p_a = sum(values_a) / len(values_a)
    p_b = sum(values_b) / len(values_b)
    h = 2 * math.asin(math.sqrt(p_a)) - 2 * math.asin(math.sqrt(p_b))
    blurb = (
        f"Bland {label_a} svarade {_sv_number(100 * p_a)}% ja, "
        f"jämfört med {_sv_number(100 * p_b)}% {_vs_phrase(label_b)} {_n_phrase(label_b, len(values_a), len(values_b))}."
    )
    return h, blurb


def _multiple_choice_effect_size(question: Question, label_a: str, values_a: list[str], label_b: str, values_b: list[str]):
    """Total variation distance (half the sum of absolute per-option probability
    differences) between the two sides' answer distributions. Chosen over raw chi-square
    because TVD is bounded to [0, 1] regardless of how many options a question has, so it
    stays "normalized ... comparable across questions" (PLAN.md decision) instead of
    naturally growing with option count the way chi-square does. Symmetric in a/b (swapping
    which side is "a" only flips which option's gap gets reported as biggest in a tie, never
    changes the TVD value itself), so unlike the number strategy this needs no pairwise-vs-
    vs-overall branch at all."""
    options = question.options or sorted(set(values_a) | set(values_b))
    n_a, n_b = len(values_a), len(values_b)
    counts_a = Counter(values_a)
    counts_b = Counter(values_b)
    gaps = {opt: counts_a.get(opt, 0) / n_a - counts_b.get(opt, 0) / n_b for opt in options}
    tvd = 0.5 * sum(abs(gap) for gap in gaps.values())
    if tvd == 0:
        identical_to = "totalen" if label_b == _OVERALL else label_b
        return 0.0, f"Bland {label_a} är fördelningen identisk med {identical_to} (n={n_a})."
    # Name the single option that diverges most, for a concrete one-line blurb rather
    # than just the aggregate divergence number.
    biggest_opt = max(gaps, key=lambda opt: abs(gaps[opt]))
    pct_a = 100 * counts_a.get(biggest_opt, 0) / n_a
    pct_b = 100 * counts_b.get(biggest_opt, 0) / n_b
    blurb = (
        f"Bland {label_a} svarade {_sv_number(pct_a)}% “{biggest_opt}”, "
        f"jämfört med {_sv_number(pct_b)}% {_vs_phrase(label_b)} {_n_phrase(label_b, n_a, n_b)}."
    )
    return tvd, blurb


def _number_effect_size(label_a: str, values_a: list[float], label_b: str, values_b: list[float]):
    """Standardized mean difference: (mean_a - mean_b) / spread -- the number-question
    equivalent of Cohen's d/Glass's delta. For a vs-overall finding (label_b == _OVERALL),
    `spread` is the overall population's own stdev (statistics.pstdev, population not
    sample -- PLAN.md's anonymity model treats the guest list as the whole population being
    described): a single, stable reference regardless of which group is being examined,
    exactly the original single-comparison formula.

    For a genuine pairwise (group_a, group_b) finding there's no single natural "the"
    population to standardize against anymore, and dividing by one side's own stdev would
    make |score| -- and therefore the ranked position -- depend on which of the two groups
    happened to land in `values_b` (see _score_pool's itertools.combinations, whose pair
    order is incidental, not meaningful). So pairwise instead divides by a size-weighted
    POOLED population variance across both groups (Cohen's d's usual construction), which
    is symmetric under swapping a and b -- the ranking doesn't depend on an arbitrary
    labeling choice."""
    mean_a = statistics.mean(values_a)
    mean_b = statistics.mean(values_b)
    if label_b == _OVERALL:
        spread = statistics.pstdev(values_b)
    else:
        n_a, n_b = len(values_a), len(values_b)
        pooled_variance = (n_a * statistics.pvariance(values_a) + n_b * statistics.pvariance(values_b)) / (n_a + n_b)
        spread = math.sqrt(pooled_variance)
    if spread == 0:
        # Vs-overall never reaches this (already filtered by _question_has_no_signal since
        # values_b there IS the overall pool) -- this guards the genuinely-possible pairwise
        # case where two specific small groups both happen to have zero internal spread.
        return None
    score = (mean_a - mean_b) / spread
    blurb = (
        f"Bland {label_a} var snittet {_sv_number(mean_a)}, "
        f"jämfört med {_sv_number(mean_b)} {_vs_phrase(label_b)} {_n_phrase(label_b, len(values_a), len(values_b))}."
    )
    return score, blurb


def _effect_size_score(question: Question, label_a: str, values_a: list, label_b: str, values_b: list):
    if question.type == Question.Type.BOOLEAN:
        return _boolean_effect_size(label_a, values_a, label_b, values_b)
    if question.type == Question.Type.MULTIPLE_CHOICE:
        return _multiple_choice_effect_size(question, label_a, values_a, label_b, values_b)
    if question.type == Question.Type.NUMBER:
        return _number_effect_size(label_a, values_a, label_b, values_b)
    raise ValueError(f"unknown question type: {question.type}")


# ---------------------------------------------------------------------------
# Scoring strategy: simple % gap (feeds "Störst procentskillnad" and its
# pairwise counterpart). Deliberately the "raw, easy to eyeball" counterpart
# to effect_size above -- no arcsine stretching, no TVD normalization, no
# dividing by stdev, so (unlike effect_size's number case) the exact same
# formula applies whether label_b is the overall pool or a genuine second
# group. Same one-function-per-type dispatch pattern as effect_size.
# ---------------------------------------------------------------------------


def _pct_gap_boolean(label_a: str, values_a: list[bool], label_b: str, values_b: list[bool]):
    """Plain absolute percentage-point gap between the two sides' yes-rates -- e.g. 62% vs
    38% is reported as "24 percentage points", full stop, with no arcsine stretching at the
    extremes the way _boolean_effect_size's Cohen's h has."""
    p_a = sum(values_a) / len(values_a)
    p_b = sum(values_b) / len(values_b)
    gap = abs(100 * p_a - 100 * p_b)
    blurb = (
        f"Bland {label_a} svarade {_sv_number(100 * p_a)}% ja, "
        f"jämfört med {_sv_number(100 * p_b)}% {_vs_phrase(label_b)} {_n_phrase(label_b, len(values_a), len(values_b))}."
    )
    return gap, blurb


def _pct_gap_multiple_choice(question: Question, label_a: str, values_a: list[str], label_b: str, values_b: list[str]):
    """The single option with the largest absolute percentage-point gap between the two
    sides' shares -- e.g. "Vin: 80% vs 45%, a 35pp gap" -- rather than
    _multiple_choice_effect_size's aggregate total-variation-distance across every option at
    once. Deliberately per-option and un-normalized: this list exists so a host can eyeball
    "how many more/fewer people picked X", not reason about a distribution-wide divergence
    measure."""
    options = question.options or sorted(set(values_a) | set(values_b))
    n_a, n_b = len(values_a), len(values_b)
    counts_a = Counter(values_a)
    counts_b = Counter(values_b)
    gaps = {opt: abs(100 * counts_a.get(opt, 0) / n_a - 100 * counts_b.get(opt, 0) / n_b) for opt in options}
    biggest_opt = max(gaps, key=lambda opt: gaps[opt])
    gap = gaps[biggest_opt]
    pct_a = 100 * counts_a.get(biggest_opt, 0) / n_a
    pct_b = 100 * counts_b.get(biggest_opt, 0) / n_b
    blurb = (
        f"Bland {label_a} svarade {_sv_number(pct_a)}% “{biggest_opt}”, "
        f"jämfört med {_sv_number(pct_b)}% {_vs_phrase(label_b)} {_n_phrase(label_b, n_a, n_b)}."
    )
    return gap, blurb


def _pct_gap_number(label_a: str, values_a: list[float], label_b: str, values_b: list[float]):
    """Raw, signed difference between the two sides' means, in the question's own units --
    no division by stdev (or pooled variance) the way _number_effect_size standardizes it,
    so this one formula covers vs-overall and pairwise alike with no branch. Signed (not
    abs'd) unlike the boolean/multiple_choice gaps above: a raw unit difference has a
    natural direction ("2.3 higher/lower") that's worth keeping, whereas a percentage-point
    gap between two shares of the same 100% doesn't need one."""
    mean_a = statistics.mean(values_a)
    mean_b = statistics.mean(values_b)
    diff = mean_a - mean_b
    blurb = (
        f"Bland {label_a} var snittet {_sv_number(mean_a)}, "
        f"jämfört med {_sv_number(mean_b)} {_vs_phrase(label_b)} {_n_phrase(label_b, len(values_a), len(values_b))}."
    )
    return diff, blurb


def _simple_pct_gap_score(question: Question, label_a: str, values_a: list, label_b: str, values_b: list):
    if question.type == Question.Type.BOOLEAN:
        return _pct_gap_boolean(label_a, values_a, label_b, values_b)
    if question.type == Question.Type.MULTIPLE_CHOICE:
        return _pct_gap_multiple_choice(question, label_a, values_a, label_b, values_b)
    if question.type == Question.Type.NUMBER:
        return _pct_gap_number(label_a, values_a, label_b, values_b)
    raise ValueError(f"unknown question type: {question.type}")


# Registry of swappable scoring strategies, keyed by name and consumed by _score_pool()
# via compute_suggestions(). Both slots are shared by vs-overall and pairwise findings
# alike (PLAN.md decision to keep this pluggable): "effect_size" feeds "Mest olika"/"Mest
# lika" and their pairwise counterparts, "simple_pct_gap" feeds "Störst procentskillnad" and
# its pairwise counterpart. Add a sibling scoring function with the same (question,
# label_a, values_a, label_b, values_b) -> (score, blurb) | None signature and register it
# here to add a mode -- _score_pool() itself needs no changes.
SCORING_STRATEGIES = {
    "effect_size": _effect_size_score,
    "simple_pct_gap": _simple_pct_gap_score,
}
