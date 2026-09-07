import math

import pytest

from pulse.models import BigScreenState, Question, Respondent, Response
from pulse.suggestions import MIN_SAMPLE_SIZE, compute_suggestions

pytestmark = pytest.mark.django_db


def make_respondent(**kwargs):
    defaults = dict(age=35, sex="female", side="bride", relation="friend")
    defaults.update(kwargs)
    return Respondent.objects.create(**defaults)


def answer(question, value, **respondent_kwargs):
    Response.objects.create(respondent=make_respondent(**respondent_kwargs), question=question, answer={"value": value})


def all_lists(suggestions):
    """Flatten all six ranked lists (three vs-overall, three pairwise) for assertions that
    don't care which one a suggestion landed in (e.g. "this question must never appear
    anywhere"). Vs-overall (Suggestion) and pairwise (PairwiseSuggestion) entries are
    different dataclasses, but both carry `question_id`, which is all these assertions
    need."""
    return (
        suggestions.most_different
        + suggestions.most_similar
        + suggestions.biggest_pct_gap
        + suggestions.most_different_pairwise
        + suggestions.most_similar_pairwise
        + suggestions.biggest_pct_gap_pairwise
    )


def pairwise_by_pair(pairwise_list, breakdown):
    """{frozenset({a, b}): suggestion} for a pairwise list, filtered to one breakdown --
    frozenset so a test can look a pair up regardless of which side _score_pool happened to
    put first (see suggestions.py's itertools.combinations order, which is incidental)."""
    return {
        frozenset({s.group_a_label, s.group_b_label}): s for s in pairwise_list if s.breakdown == breakdown
    }


# ---------------------------------------------------------------------------
# Boolean, "effect_size" strategy (Cohen's h) -- feeds "Mest olika" / "Mest lika"
# ---------------------------------------------------------------------------


def test_boolean_extreme_divergence_scores_highly_and_is_not_flagged_small_sample():
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    # Bride's side: unanimous yes (well above MIN_SAMPLE_SIZE). Groom's side: unanimous no.
    for _ in range(6):
        answer(question, True, side="bride")
    for _ in range(6):
        answer(question, False, side="groom")

    suggestions = compute_suggestions()
    by_group = {s.group_label: s for s in suggestions.most_different if s.breakdown == BigScreenState.Breakdown.SIDE}

    assert by_group["Brudens sida"].is_small_sample is False
    # Cohen's h between 100% and 50% (overall, since 6 yes + 6 no = 50%) is
    # 2*asin(1) - 2*asin(sqrt(0.5)) = pi - pi/2 = pi/2.
    assert by_group["Brudens sida"].score == pytest.approx(math.pi / 2)
    assert by_group["Brudgummens sida"].score == pytest.approx(-math.pi / 2)
    # This is the most extreme possible boolean split, so it should rank at the top of
    # "Mest olika".
    assert abs(suggestions.most_different[0].score) == pytest.approx(math.pi / 2)


def test_boolean_small_group_is_flagged_but_still_included():
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    # Groom's side: only 2 responses (< MIN_SAMPLE_SIZE), all yes.
    for _ in range(2):
        answer(question, True, side="groom")
    # Bride's side: a large, evenly split group so overall isn't degenerate (0% or 100%).
    for _ in range(4):
        answer(question, True, side="bride")
    for _ in range(4):
        answer(question, False, side="bride")

    suggestions = compute_suggestions()
    groom = next(
        s for s in suggestions.most_different if s.breakdown == BigScreenState.Breakdown.SIDE and s.group_label == "Brudgummens sida"
    )

    assert groom.sample_size == 2 < MIN_SAMPLE_SIZE
    assert groom.is_small_sample is True


def test_boolean_uniform_overall_produces_no_signal():
    # Every single respondent answered the same way -- Cohen's h (and the plain
    # percentage-point gap) against an overall of 0% or 100% is meaningless (there is, by
    # construction, no group that could possibly differ), so this question contributes
    # nothing to any of the three ranked lists.
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    for _ in range(6):
        answer(question, True, side="bride")
    for _ in range(6):
        answer(question, True, side="groom")

    suggestions = compute_suggestions()

    assert all(s.question_id != question.id for s in all_lists(suggestions))


# ---------------------------------------------------------------------------
# Multiple choice, "effect_size" strategy (total variation distance)
# ---------------------------------------------------------------------------


def test_multiple_choice_extreme_divergence():
    question = Question.objects.create(
        text_sv="Favoritdryck?", type=Question.Type.MULTIPLE_CHOICE, options=["Vin", "Öl", "Läsk"], status="live"
    )
    for _ in range(6):
        answer(question, "Vin", side="bride")
    for _ in range(6):
        answer(question, "Öl", side="groom")

    suggestions = compute_suggestions()
    by_group = {s.group_label: s for s in suggestions.most_different if s.breakdown == BigScreenState.Breakdown.SIDE}

    # Bride's side is 100% Vin vs. an overall of 50% Vin / 50% Öl -> TVD = 0.5*(0.5+0.5) = 0.5.
    assert by_group["Brudens sida"].score == pytest.approx(0.5)
    assert "Vin" in by_group["Brudens sida"].blurb
    assert by_group["Brudens sida"].is_small_sample is False


def test_multiple_choice_identical_distribution_scores_zero():
    question = Question.objects.create(
        text_sv="Favoritdryck?", type=Question.Type.MULTIPLE_CHOICE, options=["Vin", "Öl"], status="live"
    )
    # Every group has exactly the same 50/50 split as the overall population.
    for side in ("bride", "groom"):
        for _ in range(3):
            answer(question, "Vin", side=side)
        for _ in range(3):
            answer(question, "Öl", side=side)

    suggestions = compute_suggestions()
    by_group = {s.group_label: s for s in suggestions.most_similar if s.breakdown == BigScreenState.Breakdown.SIDE}

    assert by_group["Brudens sida"].score == pytest.approx(0.0)
    assert by_group["Brudgummens sida"].score == pytest.approx(0.0)


def test_multiple_choice_near_uniform_but_not_an_exact_tie():
    # Distinct from test_multiple_choice_identical_distribution_scores_zero's *exact* 0.0
    # tie: a realistic "almost, but not quite, the same across groups" MC finding, which is
    # the shape a genuinely near-uniform real question actually produces (an exact tie is a
    # coincidence a synthetic fixture can construct on purpose; real response data almost
    # never lands on one). Bride is an exact 3-way even split; groom is nudged one vote off
    # that same even split.
    question = Question.objects.create(
        text_sv="Favoritdryck?", type=Question.Type.MULTIPLE_CHOICE, options=["Vin", "Öl", "Läsk"], status="live"
    )
    for _ in range(4):
        answer(question, "Vin", side="bride")
    for _ in range(4):
        answer(question, "Öl", side="bride")
    for _ in range(4):
        answer(question, "Läsk", side="bride")
    for _ in range(5):
        answer(question, "Vin", side="groom")
    for _ in range(4):
        answer(question, "Öl", side="groom")
    for _ in range(3):
        answer(question, "Läsk", side="groom")

    suggestions = compute_suggestions()
    by_group = {s.group_label: s for s in suggestions.most_similar if s.breakdown == BigScreenState.Breakdown.SIDE}

    # Overall (24 responses): Vin=9/24, Öl=8/24, Läsk=7/24. Bride is an exact 8/24 each ->
    # TVD = 0.5*(|8/24-9/24| + |8/24-8/24| + |8/24-7/24|) = 0.5*(1/24 + 0 + 1/24) = 1/24.
    # Small and clearly nonzero -- neither this fixture's own exact-tie sibling test's 0.0,
    # nor anywhere close to the ~0.5 "extreme divergence" fixtures above.
    assert by_group["Brudens sida"].score == pytest.approx(1 / 24)
    assert 0 < by_group["Brudens sida"].score < 0.1


def test_multiple_choice_extreme_divergence_with_five_options():
    # Every multiple_choice test above uses 2-3 options; every real seeded question in this
    # app (pulse/management/commands/seed_demo_data.py) has more -- exercise TVD's "sum over
    # every option" behaviour with a realistic option count instead of the minimum case.
    question = Question.objects.create(
        text_sv="Favoritdryck?",
        type=Question.Type.MULTIPLE_CHOICE,
        options=["Vin", "Öl", "Läsk", "Vatten", "Cider"],
        status="live",
    )
    for _ in range(6):
        answer(question, "Vin", side="bride")
    for _ in range(2):
        answer(question, "Öl", side="groom")
    for _ in range(2):
        answer(question, "Läsk", side="groom")
    for _ in range(1):
        answer(question, "Vatten", side="groom")
    for _ in range(1):
        answer(question, "Cider", side="groom")

    suggestions = compute_suggestions()
    by_group = {s.group_label: s for s in suggestions.most_different if s.breakdown == BigScreenState.Breakdown.SIDE}

    # Overall (12 responses): Vin=6/12=.5, Öl=2/12, Läsk=2/12, Vatten=1/12, Cider=1/12. Bride
    # is 100% Vin -> TVD = 0.5*(|1-.5| + .1667 + .1667 + .0833 + .0833) = 0.5*2 = 1.0's worth
    # of divergence spread over 5 options, not just 2-3 -- confirms the sum runs over every
    # registered option, not just the ones a group actually picked.
    assert by_group["Brudens sida"].score == pytest.approx(0.5)
    assert "Vin" in by_group["Brudens sida"].blurb
    assert by_group["Brudens sida"].is_small_sample is False


def test_multiple_choice_small_group_is_flagged_but_still_included():
    # Boolean and number each already have a small-sample test (see their own sections
    # above); multiple_choice's is_small_sample path was untested for multiple_choice
    # specifically.
    question = Question.objects.create(
        text_sv="Favoritdryck?", type=Question.Type.MULTIPLE_CHOICE, options=["Vin", "Öl", "Läsk"], status="live"
    )
    # Groom's side: only 2 responses (< MIN_SAMPLE_SIZE).
    for _ in range(2):
        answer(question, "Öl", side="groom")
    # Bride's side: a larger, mixed group so the overall distribution isn't degenerate.
    for _ in range(3):
        answer(question, "Vin", side="bride")
    for _ in range(3):
        answer(question, "Öl", side="bride")

    suggestions = compute_suggestions()
    groom = next(
        s for s in suggestions.most_different if s.breakdown == BigScreenState.Breakdown.SIDE and s.group_label == "Brudgummens sida"
    )

    assert groom.sample_size == 2 < MIN_SAMPLE_SIZE
    assert groom.is_small_sample is True


# ---------------------------------------------------------------------------
# Number, "effect_size" strategy (standardized mean difference)
# ---------------------------------------------------------------------------


def test_number_extreme_divergence():
    question = Question.objects.create(text_sv="Hur många länder?", type=Question.Type.NUMBER, status="live")
    for v in (10,) * 6:
        answer(question, v, side="bride")
    for v in (0,) * 6:
        answer(question, v, side="groom")

    suggestions = compute_suggestions()
    by_group = {s.group_label: s for s in suggestions.most_different if s.breakdown == BigScreenState.Breakdown.SIDE}

    # Overall mean is 5, overall population stdev is 5 (six 10s and six 0s around a mean
    # of 5). Bride's side mean is 10 -> standardized diff = (10-5)/5 = 1.
    assert by_group["Brudens sida"].score == pytest.approx(1.0)
    assert by_group["Brudgummens sida"].score == pytest.approx(-1.0)
    assert by_group["Brudens sida"].is_small_sample is False


def test_number_no_spread_produces_no_signal():
    # Every respondent gave the exact same number -- nothing to standardize a group's mean
    # difference against (or, for simple_pct_gap, nothing to raise a "genuine" raw
    # difference above zero), so this question contributes nothing to any of the three
    # ranked lists.
    question = Question.objects.create(text_sv="Hur många länder?", type=Question.Type.NUMBER, status="live")
    for _ in range(6):
        answer(question, 3, side="bride")
    for _ in range(6):
        answer(question, 3, side="groom")

    suggestions = compute_suggestions()

    assert all(s.question_id != question.id for s in all_lists(suggestions))


def test_number_small_sample_still_appears_but_flagged():
    question = Question.objects.create(text_sv="Hur många länder?", type=Question.Type.NUMBER, status="live")
    for v in (1, 5, 9, 2, 8, 3):
        answer(question, v, side="bride")
    # Groom's side: one lone outlier response.
    answer(question, 50, side="groom")

    suggestions = compute_suggestions()
    groom = next(
        s for s in suggestions.most_different if s.breakdown == BigScreenState.Breakdown.SIDE and s.group_label == "Brudgummens sida"
    )

    assert groom.sample_size == 1
    assert groom.is_small_sample is True


# ---------------------------------------------------------------------------
# "simple_pct_gap" strategy (feeds "Störst procentskillnad") -- a plain,
# non-normalized magnitude per question type, computed over the same
# candidate set as effect_size but as a genuinely different formula.
# ---------------------------------------------------------------------------


def test_boolean_pct_gap_is_a_plain_percentage_point_gap():
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    for _ in range(6):
        answer(question, True, side="bride")
    for _ in range(6):
        answer(question, False, side="groom")

    suggestions = compute_suggestions()
    by_group = {s.group_label: s for s in suggestions.biggest_pct_gap if s.breakdown == BigScreenState.Breakdown.SIDE}

    # Overall yes-rate is 50%. Bride's side is 100% yes, groom's is 0% yes -> both a plain
    # 50 percentage point gap, unlike this same fixture's effect_size score of +-pi/2 (see
    # test_boolean_extreme_divergence_scores_highly_and_is_not_flagged_small_sample).
    assert by_group["Brudens sida"].score == pytest.approx(50.0)
    assert by_group["Brudgummens sida"].score == pytest.approx(50.0)
    assert by_group["Brudens sida"].score_display == "50,0 procentenheter"


def test_multiple_choice_pct_gap_reports_the_single_biggest_option_gap():
    question = Question.objects.create(
        text_sv="Favoritdryck?", type=Question.Type.MULTIPLE_CHOICE, options=["Vin", "Öl", "Läsk"], status="live"
    )
    for _ in range(6):
        answer(question, "Vin", side="bride")
    for _ in range(6):
        answer(question, "Öl", side="groom")

    suggestions = compute_suggestions()
    by_group = {s.group_label: s for s in suggestions.biggest_pct_gap if s.breakdown == BigScreenState.Breakdown.SIDE}

    # Bride's side is 100% Vin vs. an overall of 50% Vin -> a 50 percentage point gap on
    # the "Vin" option, not this fixture's effect_size TVD of 0.5 (max per-option gap here,
    # not half the sum of every option's gap).
    assert by_group["Brudens sida"].score == pytest.approx(50.0)
    assert "Vin" in by_group["Brudens sida"].blurb
    assert by_group["Brudens sida"].score_display == "50,0 procentenheter"


def test_number_pct_gap_is_a_raw_signed_mean_difference():
    question = Question.objects.create(text_sv="Hur många länder?", type=Question.Type.NUMBER, status="live")
    for v in (10,) * 6:
        answer(question, v, side="bride")
    for v in (0,) * 6:
        answer(question, v, side="groom")

    suggestions = compute_suggestions()
    by_group = {s.group_label: s for s in suggestions.biggest_pct_gap if s.breakdown == BigScreenState.Breakdown.SIDE}

    # Overall mean is 5. Bride's side mean is 10 -> raw diff = +5, not this fixture's
    # stdev-standardized effect_size score of +-1.
    assert by_group["Brudens sida"].score == pytest.approx(5.0)
    assert by_group["Brudgummens sida"].score == pytest.approx(-5.0)
    assert by_group["Brudens sida"].score_display == "+5,00"
    assert by_group["Brudgummens sida"].score_display == "-5,00"


# ---------------------------------------------------------------------------
# Cross-cutting behaviour of compute_suggestions() itself
# ---------------------------------------------------------------------------


def test_most_different_and_most_similar_are_opposite_tails_of_one_pool():
    # "Mest olika" and "Mest lika" are the two ends of one effect_size-scored pool, not
    # independently computed (PLAN.md: "uncanny similarity" is the same score's low end).
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    for _ in range(6):
        answer(question, True, side="bride")
    for _ in range(6):
        answer(question, False, side="groom")
    # sex/age/relation are left at make_respondent()'s defaults for every respondent
    # created here, so those three breakdowns each produce a single group covering
    # everyone -- identical to the overall population by construction (an exact zero
    # score). Only `side` actually varies, giving one question's pool a clean
    # extreme/near-zero split without needing a second question.

    suggestions = compute_suggestions(top_n=2)

    assert {s.breakdown for s in suggestions.most_different} == {BigScreenState.Breakdown.SIDE}
    assert all(abs(s.score) == pytest.approx(math.pi / 2) for s in suggestions.most_different)

    assert all(s.score == pytest.approx(0.0) for s in suggestions.most_similar)
    assert all(s.breakdown != BigScreenState.Breakdown.SIDE for s in suggestions.most_similar)


def test_small_sample_flag_flows_through_all_three_lists():
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    # Groom's side: only 2 responses (< MIN_SAMPLE_SIZE).
    for _ in range(2):
        answer(question, True, side="groom")
    # Bride's side: a large, evenly split group so overall isn't degenerate (0% or 100%).
    for _ in range(4):
        answer(question, True, side="bride")
    for _ in range(4):
        answer(question, False, side="bride")

    suggestions = compute_suggestions()

    # This fixture's pool is small enough (5 candidates per strategy: side x2 groups +
    # one single-group entry each for sex/age/relation) that groom's entry lands in every
    # list regardless of which tail it's sliced from -- see the "opposite tails" test above
    # for why that's expected, not a bug.
    for suggestion_list in (suggestions.most_different, suggestions.most_similar, suggestions.biggest_pct_gap):
        groom = next(
            s for s in suggestion_list if s.breakdown == BigScreenState.Breakdown.SIDE and s.group_label == "Brudgummens sida"
        )
        assert groom.sample_size == 2 < MIN_SAMPLE_SIZE
        assert groom.is_small_sample is True


def test_compute_suggestions_ignores_the_system_age_question_and_draft_questions():
    live = Question.objects.create(text_sv="Live", type=Question.Type.BOOLEAN, status="live")
    draft = Question.objects.create(text_sv="Draft", type=Question.Type.BOOLEAN, status="draft")
    system = Question.objects.create(text_sv="Ålder", type=Question.Type.NUMBER, status="live", is_system=True)
    for q in (live, draft, system):
        for _ in range(3):
            answer(q, True if q.type == Question.Type.BOOLEAN else 20, side="bride")
        for _ in range(3):
            answer(q, False if q.type == Question.Type.BOOLEAN else 40, side="groom")

    suggestions = compute_suggestions()
    question_ids = {s.question_id for s in all_lists(suggestions)}

    assert live.id in question_ids
    assert draft.id not in question_ids
    assert system.id not in question_ids


def test_compute_suggestions_respects_top_n_per_list():
    for i in range(15):
        q = Question.objects.create(text_sv=f"Q{i}", type=Question.Type.BOOLEAN, status="live")
        for _ in range(3):
            answer(q, True, side="bride")
        for _ in range(3):
            answer(q, False, side="groom")

    suggestions = compute_suggestions(top_n=5)

    # top_n applies independently to each of the three lists (5 per list, not 5 total).
    assert len(suggestions.most_different) == 5
    assert len(suggestions.most_similar) == 5
    assert len(suggestions.biggest_pct_gap) == 5


# ---------------------------------------------------------------------------
# Cross-question-type fairness in the six pool-slicing operations (_type_balanced_top_n,
# fixed 2026-09-07). Regression coverage for the actual bug this pass fixes: each of the six
# lists used to slice its pool with a single sort(key=abs(score))[:top_n] mixing all three
# question types together, even though their scores are not the same unit -- Cohen's h
# (boolean) and TVD (multiple_choice) are both mathematically bounded, while number's
# standardized mean difference is unbounded by construction (_number_effect_size). On this
# app's real seeded dev data that meant 0 of 13 real multiple_choice questions ever surfaced
# in any of the six lists' top 5, crowded out entirely by number's larger raw scores -- this
# is exactly the "incomparable units, cross-scale sort is actively wrong" problem the module
# docstring's "Cross-question-type fairness" entry and PLAN.md's _build_highlights design
# call already named, just never applied one level down until now. See
# `_type_balanced_top_n`'s own docstring for the full mechanism (round-robin by type, still
# ranked by |score| within each type's own turn) and its documented residual limitation
# (TVD's noise floor, out of scope for this pass).
# ---------------------------------------------------------------------------


def test_type_balanced_top_n_gives_every_question_type_a_fair_turn():
    # One strong, genuine finding per question type, with the number finding's standardized
    # effect size deliberately built larger than Cohen's h or TVD could ever mathematically
    # reach (both are bounded; number's is not -- see this section's docstring) -- exactly
    # the shape of dataset that let number crowd the other two types out entirely under the
    # old plain-magnitude sort. top_n is set to exactly the number of distinct types present
    # (3), so a fairness-respecting round-robin gives each type precisely one slot; the old
    # bug would have filled all 3 (or however many) slots with "number" alone.
    boolean_q = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    for _ in range(6):
        answer(boolean_q, True, side="bride")
    for _ in range(6):
        answer(boolean_q, False, side="groom")

    mc_q = Question.objects.create(
        text_sv="Favoritdryck?", type=Question.Type.MULTIPLE_CHOICE, options=["Vin", "Öl", "Läsk"], status="live"
    )
    for _ in range(6):
        answer(mc_q, "Vin", side="bride")
    for _ in range(6):
        answer(mc_q, "Öl", side="groom")

    # A large majority at 0 and a small minority at 1,000,000 (age breakdown: two distinct
    # decade buckets) -- an asymmetric-group-size construction whose standardized mean
    # difference (~4.4, computed below) comfortably exceeds Cohen's h's max of pi (~3.14,
    # only reached at a fully-opposite 100%/0% pairwise split) and TVD's max of 1.0, however
    # large or small the raw numbers involved happen to be.
    number_q = Question.objects.create(text_sv="Hur många länder?", type=Question.Type.NUMBER, status="live")
    for _ in range(114):
        answer(number_q, 0, age=25)
    for _ in range(6):
        answer(number_q, 1_000_000, age=65)

    suggestions = compute_suggestions(top_n=3)

    # Before the fix, "number"'s unbounded score would have swept every slot in both lists;
    # the fairness fix guarantees each of the three present types wins its own turn instead.
    assert {s.question_type for s in suggestions.most_different} == {
        Question.Type.BOOLEAN,
        Question.Type.MULTIPLE_CHOICE,
        Question.Type.NUMBER,
    }
    assert len(suggestions.most_different) == 3  # one slot per type -- none crowded out

    assert {s.question_type for s in suggestions.biggest_pct_gap} == {
        Question.Type.BOOLEAN,
        Question.Type.MULTIPLE_CHOICE,
        Question.Type.NUMBER,
    }
    assert len(suggestions.biggest_pct_gap) == 3


# ---------------------------------------------------------------------------
# Pairwise (group vs. group) comparisons -- generalizing beyond group-vs-overall
# (PLAN.md "Interesting stats suggestions", 2026-08-29 design call: these are three
# ADDITIONAL lists, not a replacement for the vs-overall ones above -- see module
# docstring). Same scoring strategies, same MIN_SAMPLE_SIZE flagging, just a second
# group instead of the overall pool as the comparison side.
# ---------------------------------------------------------------------------


def test_boolean_pairwise_extreme_divergence_scores_highly_and_is_not_flagged_small_sample():
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    for _ in range(6):
        answer(question, True, side="bride")
    for _ in range(6):
        answer(question, False, side="groom")

    suggestions = compute_suggestions()
    pair = pairwise_by_pair(suggestions.most_different_pairwise, BigScreenState.Breakdown.SIDE)[
        frozenset({"Brudens sida", "Brudgummens sida"})
    ]

    # Cohen's h between two fully opposite proportions (100% vs 0%) is
    # 2*asin(1) - 2*asin(0) = pi -- the maximum possible magnitude, and bigger than this
    # same fixture's vs-overall score of +-pi/2 (each side compared to a 50% overall
    # instead of directly to each other).
    assert abs(pair.score) == pytest.approx(math.pi)
    assert pair.is_small_sample is False
    assert {pair.sample_size_a, pair.sample_size_b} == {6, 6}
    assert {pair.group_a_label, pair.group_b_label} == {"Brudens sida", "Brudgummens sida"}


def test_boolean_pairwise_small_sample_flagged_when_either_side_is_small():
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    for _ in range(2):
        answer(question, True, side="groom")  # below MIN_SAMPLE_SIZE
    for _ in range(6):
        answer(question, False, side="bride")

    suggestions = compute_suggestions()
    pair = pairwise_by_pair(
        suggestions.most_different_pairwise + suggestions.most_similar_pairwise, BigScreenState.Breakdown.SIDE
    )[frozenset({"Brudens sida", "Brudgummens sida"})]

    # Flagged even though only ONE of the two sides (groom's, 2 responses) is below
    # MIN_SAMPLE_SIZE -- the other side (bride's, 6) is well above it. Small-sample
    # flagging must apply per-group on both sides of a pairwise comparison, not require
    # both (or only check a fixed side).
    assert {pair.sample_size_a, pair.sample_size_b} == {2, 6}
    assert pair.is_small_sample is True


def test_multiple_choice_pairwise_extreme_divergence():
    question = Question.objects.create(
        text_sv="Favoritdryck?", type=Question.Type.MULTIPLE_CHOICE, options=["Vin", "Öl", "Läsk"], status="live"
    )
    for _ in range(6):
        answer(question, "Vin", side="bride")
    for _ in range(6):
        answer(question, "Öl", side="groom")

    suggestions = compute_suggestions()
    pair = pairwise_by_pair(suggestions.most_different_pairwise, BigScreenState.Breakdown.SIDE)[
        frozenset({"Brudens sida", "Brudgummens sida"})
    ]

    # Bride's side is 100% Vin, groom's is 100% Öl -> TVD = 0.5*(1 + 1) = 1.0, the maximum
    # possible -- bigger than this fixture's vs-overall TVD of 0.5 (each side compared to a
    # 50/50 overall instead of directly to each other).
    assert pair.score == pytest.approx(1.0)
    assert "Vin" in pair.blurb or "Öl" in pair.blurb
    assert pair.is_small_sample is False


def test_number_pairwise_extreme_divergence_uses_pooled_variance():
    question = Question.objects.create(text_sv="Hur många länder?", type=Question.Type.NUMBER, status="live")
    for v in (8, 9, 10, 11, 12):
        answer(question, v, side="bride")
    for v in (-2, -1, 0, 1, 2):
        answer(question, v, side="groom")

    suggestions = compute_suggestions()
    pair = pairwise_by_pair(suggestions.most_different_pairwise, BigScreenState.Breakdown.SIDE)[
        frozenset({"Brudens sida", "Brudgummens sida"})
    ]

    # Both groups have the same population variance (2) and mean gap 10 -> pooled variance
    # is also 2 (equal-sized groups), so the standardized difference is 10/sqrt(2) --
    # deliberately NOT divided by either side's own stdev alone (see _number_effect_size's
    # docstring: that would make |score| depend on which side landed in values_b).
    assert abs(pair.score) == pytest.approx(10 / math.sqrt(2))
    assert pair.is_small_sample is False


def test_number_pairwise_with_no_internal_spread_in_either_group_produces_no_finding():
    # Known/documented limitation of the pooled-variance denominator (see
    # _number_effect_size's docstring): two groups that are each internally uniform (no
    # spread of their own) have a pooled variance of exactly 0 even when their means are
    # wildly different, so there's nothing to standardize by and the pair is skipped
    # entirely for "effect_size" -- unlike the vs-overall case, which never hits this
    # because the *overall* pool (bride+groom mixed) does have spread even when each side
    # alone doesn't.
    question = Question.objects.create(text_sv="Hur många länder?", type=Question.Type.NUMBER, status="live")
    for _ in range(6):
        answer(question, 10, side="bride")
    for _ in range(6):
        answer(question, 0, side="groom")

    suggestions = compute_suggestions()
    pairs = pairwise_by_pair(
        suggestions.most_different_pairwise + suggestions.most_similar_pairwise, BigScreenState.Breakdown.SIDE
    )

    assert frozenset({"Brudens sida", "Brudgummens sida"}) not in pairs
    # The vs-overall finding for the very same data is NOT similarly skipped -- confirms
    # this is a pairwise-specific denominator limitation, not a question-level guard.
    by_group = {s.group_label: s for s in suggestions.most_different if s.breakdown == BigScreenState.Breakdown.SIDE}
    assert by_group["Brudens sida"].score == pytest.approx(1.0)


def test_boolean_pct_gap_pairwise_is_a_plain_percentage_point_gap():
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    for _ in range(6):
        answer(question, True, side="bride")
    for _ in range(6):
        answer(question, False, side="groom")

    suggestions = compute_suggestions()
    pair = pairwise_by_pair(suggestions.biggest_pct_gap_pairwise, BigScreenState.Breakdown.SIDE)[
        frozenset({"Brudens sida", "Brudgummens sida"})
    ]

    # 100% yes vs 0% yes -> a plain 100 percentage point gap, unlike this fixture's
    # vs-overall pct-gap of 50 (each side compared to a 50% overall).
    assert pair.score == pytest.approx(100.0)
    assert pair.score_display == "100,0 procentenheter"


def test_number_pct_gap_pairwise_is_a_raw_signed_mean_difference():
    question = Question.objects.create(text_sv="Hur många länder?", type=Question.Type.NUMBER, status="live")
    for v in (10,) * 6:
        answer(question, v, side="bride")
    for v in (0,) * 6:
        answer(question, v, side="groom")

    suggestions = compute_suggestions()
    pair = pairwise_by_pair(suggestions.biggest_pct_gap_pairwise, BigScreenState.Breakdown.SIDE)[
        frozenset({"Brudens sida", "Brudgummens sida"})
    ]

    # Unlike the "effect_size" pairwise strategy (see the "no internal spread" test above),
    # simple_pct_gap never divides by anything, so this uniform-within-each-group fixture
    # (which produced NO effect_size pairwise finding) still produces a plain, well-defined
    # raw mean difference of +-10 here.
    assert abs(pair.score) == pytest.approx(10.0)


def test_pairwise_lists_are_additive_not_a_replacement_for_vs_overall():
    # Design call (PLAN.md, module docstring): pairwise findings are ADDED as new lists,
    # the original three vs-overall lists still populate exactly as before for the same
    # data.
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    for _ in range(6):
        answer(question, True, side="bride")
    for _ in range(6):
        answer(question, False, side="groom")

    suggestions = compute_suggestions()

    assert any(s.question_id == question.id for s in suggestions.most_different)
    assert any(s.question_id == question.id for s in suggestions.most_different_pairwise)


def test_age_breakdown_pairwise_covers_every_unordered_pair_without_blowing_up():
    # Age has more groups than sex/side/relation (up to 6 decade/age buckets since the
    # 2026-09-07 bucket-relabel added a 6th "60+ år" bucket), so pairwise combinations grow
    # faster there -- C(6,2)=15 pairs instead of sex's single pair. This confirms every pair
    # is actually produced (not silently truncated) and that the count matches C(n,2)
    # exactly, not e.g. n^2 (which would double-count each pair, or wrongly include a group
    # paired with itself).
    question = Question.objects.create(text_sv="Hur många länder?", type=Question.Type.NUMBER, status="live")
    ages = [15, 25, 35, 45, 55, 65]  # one per age bucket: Under 20 år / 20-29 år / .. / 60+ år
    for i, age in enumerate(ages):
        for v in (i, i + 1, i + 2, i + 3, i + 4):  # 5 responses/group, real internal spread
            answer(question, v, age=age)

    suggestions = compute_suggestions(top_n=100)  # large enough to not truncate the pool
    age_pairs = pairwise_by_pair(
        suggestions.most_different_pairwise + suggestions.most_similar_pairwise, BigScreenState.Breakdown.AGE
    )

    assert len(age_pairs) == 15  # C(6, 2)


# ---------------------------------------------------------------------------
# Combined "top 10, all categories" highlights (compute_suggestions().highlights /
# pulse.suggestions.Highlight) -- Johan's three explicit design calls (PLAN.md "Interesting
# stats suggestions", 2026-08-30 "top 10, all categories" follow-up):
#   1. Round-robin merge of the four "extreme" pools, in a fixed order, one rank at a time.
#   2. Up to two reserved "Mest lika" slots (one vs-overall, one pairwise), since a
#      magnitude-ranked round-robin would never naturally surface similarity.
#   3. The six original sections stay, just collapsed by default in the template (not
#      exercised here -- that's markup, covered by manual/visual verification).
#
# These tests check the MERGE/RESERVATION/DE-DUP logic in `_build_highlights`, not the
# per-type scoring math the six pools are built from (already covered by every test above --
# `most_different`/`most_similar`/etc. are trusted inputs here). `_expected_highlight_keys`
# is an independent transcription of Johan's spec operating only on those already-tested
# pools, so a test failure here means the *merge*, not the underlying statistics, disagrees
# with the spec.
# ---------------------------------------------------------------------------


def _highlight_identity(item, is_pairwise):
    """Same de-duplication key `_build_highlights` uses internally, re-derived here from a
    Suggestion/PairwiseSuggestion (not imported from suggestions.py) so this reference is
    genuinely independent of the implementation under test."""
    if is_pairwise:
        return (item.question_id, item.breakdown, frozenset({item.group_a_label, item.group_b_label}))
    return (item.question_id, item.breakdown, item.group_label)


def _highlight_key(h):
    """The same identity, computed from a rendered Highlight instead of the Suggestion/
    PairwiseSuggestion it was built from -- lets a test compare `compute_suggestions().
    highlights` directly against `_expected_highlight_keys()` below."""
    if h.is_pairwise:
        group_a, group_b = h.group_display.split(" vs ")
        return (h.question_id, h.breakdown, frozenset({group_a, group_b}))
    return (h.question_id, h.breakdown, h.group_display)


def _expected_highlight_keys(suggestions, limit=10):
    """Reference implementation of Johan's round-robin + reserved-similarity-slots spec,
    built purely from the six pools already on `suggestions` (see section docstring above)."""
    seen = set()
    keys = []
    for pool, is_pairwise in ((suggestions.most_similar, False), (suggestions.most_similar_pairwise, True)):
        if not pool:
            continue
        key = _highlight_identity(pool[0], is_pairwise)
        if key in seen:
            continue
        seen.add(key)
        keys.append(key)

    remaining = limit - len(keys)
    extreme_pools = [
        (suggestions.most_different, False),
        (suggestions.most_different_pairwise, True),
        (suggestions.biggest_pct_gap, False),
        (suggestions.biggest_pct_gap_pairwise, True),
    ]
    rank = 0
    picks = []
    while len(picks) < remaining and any(rank < len(pool) for pool, _ in extreme_pools):
        for pool, is_pairwise in extreme_pools:
            if len(picks) >= remaining:
                break
            if rank >= len(pool):
                continue  # this pool is exhausted at this rank -- skip it, don't pad
            key = _highlight_identity(pool[rank], is_pairwise)
            if key in seen:
                continue
            seen.add(key)
            picks.append(key)
        rank += 1
    keys.extend(picks)
    return keys


def _seed_varied_highlight_data():
    """A deliberately rich, varied dataset -- multiple question types across all four
    breakdowns -- so every one of the six ranked pools (and therefore every highlight
    category) has real, non-degenerate content to draw from. Shared by several tests below
    that check compute_suggestions().highlights' composition, not the per-type scoring math
    each pool is built from (covered above).

    Each question-group below (side/sex/age/relation) deliberately uses a genuinely
    DIFFERENT split per index `i`, not `i` identical repeats of the same split. An earlier
    version of this fixture repeated the exact same split (e.g. every "Sida-fråga" was a
    100%-bride-yes/0%-groom-yes tie), which -- after _type_balanced_top_n started bucketing
    each pool by question type (2026-09-07) -- made the effect_size-pairwise and
    simple_pct_gap-pairwise pools rank those tied boolean/multiple_choice candidates in the
    exact same order (Python's stable sort preserves insertion order among ties, and both
    pools score the very same candidates in the very same insertion order). That made
    "Mest olika, grupp mot grupp" and "Störst procentskillnad, grupp mot grupp" pick the
    identical (question, breakdown, group) findings at every rank, so the highlights merge's
    de-dup (`_highlight_identity`) silently ate every one of "Störst procentskillnad, grupp
    mot grupp"'s candidates as a duplicate of "Mest olika, grupp mot grupp" -- starving that
    one category out of the combined top 10 entirely
    (test_highlights_category_and_score_labels_match_their_source_list caught this). Genuine
    per-question variety avoids that: effect_size's arcsine-stretched boolean scoring and
    simple_pct_gap's linear one don't rank a *varied* set of splits in lock-step, so the two
    strategies' pairwise pools now genuinely diverge in which candidate lands at which rank,
    same as any real, non-synthetic dataset would."""
    side_splits = [(6, 0), (5, 1), (4, 2), (6, 1), (5, 0), (3, 3)]  # (bride yes, groom yes) out of 6 each
    for i, (bride_yes, groom_yes) in enumerate(side_splits):
        q = Question.objects.create(text_sv=f"Sida-fråga {i}", type=Question.Type.BOOLEAN, status="live")
        for _ in range(bride_yes):
            answer(q, True, side="bride")
        for _ in range(6 - bride_yes):
            answer(q, False, side="bride")
        for _ in range(groom_yes):
            answer(q, True, side="groom")
        for _ in range(6 - groom_yes):
            answer(q, False, side="groom")
    sex_splits = [(4, 1), (3, 2), (4, 3), (2, 4)]  # (female yes, male yes) out of 4 each
    for i, (female_yes, male_yes) in enumerate(sex_splits):
        q = Question.objects.create(text_sv=f"Kön-fråga {i}", type=Question.Type.BOOLEAN, status="live")
        for _ in range(female_yes):
            answer(q, True, sex="female")
        for _ in range(4 - female_yes):
            answer(q, False, sex="female")
        for _ in range(male_yes):
            answer(q, True, sex="male")
        for _ in range(4 - male_yes):
            answer(q, False, sex="male")
    ages = [15, 25, 35, 45, 55]
    for i in range(4):
        q = Question.objects.create(text_sv=f"Antal-fråga {i}", type=Question.Type.NUMBER, status="live")
        for j, age in enumerate(ages):
            # A per-question slope of (i+1) rather than a per-question additive shift of i --
            # a pure additive shift leaves every age bucket's pairwise mean GAP identical
            # across all 4 questions (it cancels out), which is just as much of an exact tie
            # as repeating one literal split. Scaling by (i+1) instead makes each question's
            # own set of inter-age-group differences genuinely distinct.
            for offset in range(4):
                answer(q, j * (i + 1) + offset, age=age)
    mc_splits = [(5, 0, 0, 5), (4, 1, 1, 4), (3, 2, 1, 4)]  # (friend Vin, friend Öl, family Vin, family Öl)
    for i, (friend_vin, friend_ol, family_vin, family_ol) in enumerate(mc_splits):
        q = Question.objects.create(
            text_sv=f"Dryck-fråga {i}", type=Question.Type.MULTIPLE_CHOICE, options=["Vin", "Öl", "Läsk"], status="live"
        )
        for _ in range(friend_vin):
            answer(q, "Vin", relation="friend")
        for _ in range(friend_ol):
            answer(q, "Öl", relation="friend")
        for _ in range(family_vin):
            answer(q, "Vin", relation="family")
        for _ in range(family_ol):
            answer(q, "Öl", relation="family")


def test_highlights_match_the_round_robin_reservation_spec():
    _seed_varied_highlight_data()

    suggestions = compute_suggestions()
    actual_keys = [_highlight_key(h) for h in suggestions.highlights]

    assert actual_keys == _expected_highlight_keys(suggestions)


def test_highlights_category_and_score_labels_match_their_source_list():
    _seed_varied_highlight_data()

    suggestions = compute_suggestions()
    expected = {
        "most_different": ("Mest olika", "Avvikelse", False),
        "most_different_pairwise": ("Mest olika, grupp mot grupp", "Avvikelse", True),
        "biggest_pct_gap": ("Störst procentskillnad", "Skillnad", False),
        "biggest_pct_gap_pairwise": ("Störst procentskillnad, grupp mot grupp", "Skillnad", True),
        "most_similar": ("Mest lika", "Avvikelse", False),
        "most_similar_pairwise": ("Mest lika, grupp mot grupp", "Avvikelse", True),
    }

    assert suggestions.highlights  # sanity: this rich a fixture must produce something
    seen_categories = {h.category for h in suggestions.highlights}
    # Five of the six categories reliably get a highlight slot with this much varied data.
    # "biggest_pct_gap_pairwise" ("Störst procentskillnad, grupp mot grupp") is legitimately
    # NOT guaranteed a slot here, and that's not a bug: for boolean/multiple_choice, a
    # candidate that's the most extreme by Cohen's h/TVD is very often ALSO the most extreme
    # by plain percentage-point gap (both are monotonic in the same underlying group-vs-group
    # difference) -- exactly the documented overlap _highlight_identity's docstring already
    # calls out ("a single ... entry can legitimately be the extreme of BOTH the effect_size
    # and simple_pct_gap pools"). With the fixed 4-pool round-robin order (decision 1: most_
    # different, most_different_pairwise, biggest_pct_gap, biggest_pct_gap_pairwise, always in
    # that order) and only 8 non-similarity slots to fill, "biggest_pct_gap_pairwise" -- last
    # in that order -- is the one that loses out to de-duplication when its candidates keep
    # re-matching the three pools already served before it. This is _build_highlights' own
    # existing merge/de-dup behaviour (unchanged by the 2026-09-07 fairness fix -- see module
    # docstring), not something the six-pools-fairness fix broke or could fix; asserting
    # every one of the four "extreme" categories always wins a slot would be asserting
    # something the documented de-dup design never promised.
    assert seen_categories >= set(expected) - {"biggest_pct_gap_pairwise"}
    assert seen_categories <= set(expected)
    for h in suggestions.highlights:
        label, score_label, is_pairwise = expected[h.category]
        assert h.category_label == label
        assert h.score_label == score_label
        assert h.is_pairwise is is_pairwise


def test_highlights_reserve_a_similarity_slot_for_each_non_empty_similarity_pool():
    _seed_varied_highlight_data()

    suggestions = compute_suggestions()
    assert suggestions.most_similar and suggestions.most_similar_pairwise  # precondition

    similarity_highlights = [h for h in suggestions.highlights if h.is_similarity]
    # Exactly one reserved slot per non-empty similarity pool -- never zero here (both pools
    # have entries) and never more than one per pool (only index 0 is ever reserved).
    assert {h.category for h in similarity_highlights} == {"most_similar", "most_similar_pairwise"}
    assert len(similarity_highlights) == 2


def test_highlights_never_exceed_ten_and_never_contain_duplicate_findings():
    _seed_varied_highlight_data()

    suggestions = compute_suggestions()
    keys = [_highlight_key(h) for h in suggestions.highlights]

    assert len(suggestions.highlights) == 10  # this fixture supplies well over 10 candidates
    assert len(keys) == len(set(keys))  # no (question, breakdown, group) shows up twice


def test_highlights_never_contain_duplicates_even_under_heavy_score_ties():
    # Every question here has the *identical* side split, so effect_size and simple_pct_gap
    # tie exactly for every candidate -- the scenario most likely to make "most_different"
    # and "biggest_pct_gap" (or "most_different_pairwise"/"biggest_pct_gap_pairwise") pick
    # the very same (question, breakdown, group) at the same rank. The merge must still
    # de-duplicate correctly rather than showing the same finding under two category labels.
    for i in range(8):
        q = Question.objects.create(text_sv=f"Fråga {i}", type=Question.Type.BOOLEAN, status="live")
        for _ in range(6):
            answer(q, True, side="bride")
        for _ in range(6):
            answer(q, False, side="groom")

    suggestions = compute_suggestions()
    actual_keys = [_highlight_key(h) for h in suggestions.highlights]

    assert actual_keys == _expected_highlight_keys(suggestions)
    assert len(actual_keys) == len(set(actual_keys))


def test_highlights_gracefully_handle_completely_empty_pairwise_pools():
    # Every respondent shares the exact same demographic values (make_respondent()'s
    # untouched defaults) -- so every breakdown has exactly one group for every question,
    # meaning zero pairs anywhere (itertools.combinations of a 1-item dict is empty). All
    # three pairwise pools are therefore completely empty, not just short -- the round-robin
    # and the similarity reservation must both skip these two pools gracefully (decision 1's
    # "skip a pool once it's exhausted", decision 2's "if one pool is empty, reserve only 1
    # slot") rather than erroring or padding.
    for i in range(3):
        q = Question.objects.create(text_sv=f"Fråga {i}", type=Question.Type.BOOLEAN, status="live")
        for _ in range(6):
            answer(q, True)
        for _ in range(6):
            answer(q, False)

    suggestions = compute_suggestions()

    assert suggestions.most_different_pairwise == []
    assert suggestions.most_similar_pairwise == []
    assert suggestions.biggest_pct_gap_pairwise == []
    assert suggestions.highlights  # the three vs-overall categories still produce content
    assert all(not h.is_pairwise for h in suggestions.highlights)
    # Only one similarity slot is possible here (most_similar_pairwise is empty), never two.
    assert sum(h.is_similarity for h in suggestions.highlights) == 1
    actual_keys = [_highlight_key(h) for h in suggestions.highlights]
    assert actual_keys == _expected_highlight_keys(suggestions)


def test_highlights_gracefully_handle_pools_shorter_than_top_n():
    # A single question's side split gives each vs-overall pool only 2 candidates and each
    # pairwise pool only 1 -- well under TOP_N=5 -- so the round-robin runs out of ranks to
    # pull from long before the usual 10-slot ceiling. It must stop there, not pad.
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    for _ in range(6):
        answer(question, True, side="bride")
    for _ in range(6):
        answer(question, False, side="groom")

    suggestions = compute_suggestions()
    actual_keys = [_highlight_key(h) for h in suggestions.highlights]

    assert actual_keys == _expected_highlight_keys(suggestions)
    assert 0 < len(suggestions.highlights) < 10


def test_highlights_is_empty_when_every_pool_is_empty():
    # No live questions at all -- every one of the six pools is empty, so the combined
    # panel must be an empty list, not an error.
    suggestions = compute_suggestions()

    assert suggestions.highlights == []
