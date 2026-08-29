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


# ---------------------------------------------------------------------------
# Boolean: Cohen's h effect size
# ---------------------------------------------------------------------------


def test_boolean_extreme_divergence_scores_highly_and_is_not_flagged_small_sample():
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    # Bride's side: unanimous yes (well above MIN_SAMPLE_SIZE). Groom's side: unanimous no.
    for _ in range(6):
        answer(question, True, side="bride")
    for _ in range(6):
        answer(question, False, side="groom")

    suggestions = compute_suggestions()
    by_group = {s.group_label: s for s in suggestions if s.breakdown == BigScreenState.Breakdown.SIDE}

    assert by_group["Brudens sida"].is_small_sample is False
    # Cohen's h between 100% and 50% (overall, since 6 yes + 6 no = 50%) is
    # 2*asin(1) - 2*asin(sqrt(0.5)) = pi - pi/2 = pi/2.
    assert by_group["Brudens sida"].score == pytest.approx(math.pi / 2)
    assert by_group["Brudgummens sida"].score == pytest.approx(-math.pi / 2)
    # This is the most extreme possible boolean split, so it should rank at the top.
    assert abs(suggestions[0].score) == pytest.approx(math.pi / 2)


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
    groom = next(s for s in suggestions if s.breakdown == BigScreenState.Breakdown.SIDE and s.group_label == "Brudgummens sida")

    assert groom.sample_size == 2 < MIN_SAMPLE_SIZE
    assert groom.is_small_sample is True


def test_boolean_uniform_overall_produces_no_signal():
    # Every single respondent answered the same way -- Cohen's h against an overall of
    # 0% or 100% is meaningless (there is, by construction, no group that could possibly
    # differ), so this question contributes nothing to the ranked list.
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    for _ in range(6):
        answer(question, True, side="bride")
    for _ in range(6):
        answer(question, True, side="groom")

    suggestions = compute_suggestions()

    assert all(s.question_id != question.id for s in suggestions)


# ---------------------------------------------------------------------------
# Multiple choice: total variation distance
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
    by_group = {s.group_label: s for s in suggestions if s.breakdown == BigScreenState.Breakdown.SIDE}

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
    by_group = {s.group_label: s for s in suggestions if s.breakdown == BigScreenState.Breakdown.SIDE}

    assert by_group["Brudens sida"].score == pytest.approx(0.0)
    assert by_group["Brudgummens sida"].score == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Number: standardized mean difference
# ---------------------------------------------------------------------------


def test_number_extreme_divergence():
    question = Question.objects.create(text_sv="Hur många länder?", type=Question.Type.NUMBER, status="live")
    for v in (10,) * 6:
        answer(question, v, side="bride")
    for v in (0,) * 6:
        answer(question, v, side="groom")

    suggestions = compute_suggestions()
    by_group = {s.group_label: s for s in suggestions if s.breakdown == BigScreenState.Breakdown.SIDE}

    # Overall mean is 5, overall population stdev is 5 (six 10s and six 0s around a mean
    # of 5). Bride's side mean is 10 -> standardized diff = (10-5)/5 = 1.
    assert by_group["Brudens sida"].score == pytest.approx(1.0)
    assert by_group["Brudgummens sida"].score == pytest.approx(-1.0)
    assert by_group["Brudens sida"].is_small_sample is False


def test_number_no_spread_produces_no_signal():
    # Every respondent gave the exact same number -- nothing to standardize a group's
    # mean difference against, so this question contributes nothing to the ranked list.
    question = Question.objects.create(text_sv="Hur många länder?", type=Question.Type.NUMBER, status="live")
    for _ in range(6):
        answer(question, 3, side="bride")
    for _ in range(6):
        answer(question, 3, side="groom")

    suggestions = compute_suggestions()

    assert all(s.question_id != question.id for s in suggestions)


def test_number_small_sample_still_appears_but_flagged():
    question = Question.objects.create(text_sv="Hur många länder?", type=Question.Type.NUMBER, status="live")
    for v in (1, 5, 9, 2, 8, 3):
        answer(question, v, side="bride")
    # Groom's side: one lone outlier response.
    answer(question, 50, side="groom")

    suggestions = compute_suggestions()
    groom = next(s for s in suggestions if s.breakdown == BigScreenState.Breakdown.SIDE and s.group_label == "Brudgummens sida")

    assert groom.sample_size == 1
    assert groom.is_small_sample is True


# ---------------------------------------------------------------------------
# Cross-cutting behaviour of compute_suggestions() itself
# ---------------------------------------------------------------------------


def test_similar_groups_score_near_zero_lower_than_divergent_ones():
    # A "genuinely similar" case: two groups that essentially agree with the overall
    # population should land near the bottom of |score|, well below a genuinely
    # divergent question's score -- this is the "uncanny similarity" end of the same
    # ranking (PLAN.md decision), not a separately computed thing.
    similar_q = Question.objects.create(text_sv="Gillar ni tårta?", type=Question.Type.BOOLEAN, status="live")
    for side in ("bride", "groom"):
        for _ in range(3):
            answer(similar_q, True, side=side)
        for _ in range(3):
            answer(similar_q, False, side=side)

    divergent_q = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    for _ in range(6):
        answer(divergent_q, True, side="bride")
    for _ in range(6):
        answer(divergent_q, False, side="groom")

    suggestions = compute_suggestions()
    similar_scores = [abs(s.score) for s in suggestions if s.question_id == similar_q.id]
    divergent_scores = [abs(s.score) for s in suggestions if s.question_id == divergent_q.id]

    assert similar_scores, "identical 50/50 groups still produce a (zero) score, not silence"
    assert max(similar_scores) == pytest.approx(0.0)
    assert max(divergent_scores) > max(similar_scores)


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

    question_ids = {s.question_id for s in suggestions}
    assert live.id in question_ids
    assert draft.id not in question_ids
    assert system.id not in question_ids


def test_compute_suggestions_respects_top_n():
    for i in range(15):
        q = Question.objects.create(text_sv=f"Q{i}", type=Question.Type.BOOLEAN, status="live")
        for _ in range(3):
            answer(q, True, side="bride")
        for _ in range(3):
            answer(q, False, side="groom")

    suggestions = compute_suggestions(top_n=5)

    assert len(suggestions) == 5
