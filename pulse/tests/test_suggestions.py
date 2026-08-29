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
    """Flatten the three ranked lists for assertions that don't care which one a
    suggestion landed in (e.g. "this question must never appear anywhere")."""
    return suggestions.most_different + suggestions.most_similar + suggestions.biggest_pct_gap


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
