import pytest

from pulse.aggregations import compute_breakdown
from pulse.models import BigScreenState, Question, Respondent, Response, age_bucket_label

pytestmark = pytest.mark.django_db


def make_respondent(**kwargs):
    # age=35 lands in the "30-talet" bucket, matching what the old age_bucket="30s"
    # default used to mean, so existing tests below keep grouping the way they always did.
    defaults = dict(age=35, sex="female", side="bride", relation="friend")
    defaults.update(kwargs)
    return Respondent.objects.create(**defaults)


def test_boolean_overall_breakdown():
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    Response.objects.create(respondent=make_respondent(), question=question, answer={"value": True})
    Response.objects.create(respondent=make_respondent(), question=question, answer={"value": False})

    result = compute_breakdown(question, BigScreenState.Breakdown.OVERALL)

    assert result == {"Alla": {"count": 2, "yes_pct": 50.0}}


def test_boolean_grouped_by_age():
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    Response.objects.create(respondent=make_respondent(age=25), question=question, answer={"value": True})
    Response.objects.create(respondent=make_respondent(age=45), question=question, answer={"value": False})

    result = compute_breakdown(question, BigScreenState.Breakdown.AGE)

    # Age has no stored `choices` (unlike sex/side/relation) -- _group() special-cases
    # it to bucket by decade via age_bucket_label() instead of get_<field>_display().
    assert result["20-talet"]["yes_pct"] == 100.0
    assert result["40-talet"]["yes_pct"] == 0.0


@pytest.mark.parametrize(
    "age,expected_label",
    [
        (1, "Under 20"),
        (19, "Under 20"),
        (20, "20-talet"),
        (29, "20-talet"),
        (30, "30-talet"),
        (39, "30-talet"),
        (40, "40-talet"),
        (49, "40-talet"),
        (50, "50 eller äldre"),
        (119, "50 eller äldre"),
    ],
)
def test_age_bucket_label_boundaries(age, expected_label):
    # The one genuinely new piece of logic here is the decade-boundary math (age //
    # 10 * 10) -- exercise every boundary explicitly rather than trusting the
    # generalized formula on a couple of mid-decade examples.
    assert age_bucket_label(age) == expected_label


def test_boolean_grouped_by_side():
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    Response.objects.create(respondent=make_respondent(side="bride"), question=question, answer={"value": True})
    Response.objects.create(respondent=make_respondent(side="groom"), question=question, answer={"value": False})

    result = compute_breakdown(question, BigScreenState.Breakdown.SIDE)

    # Grouped by Swedish display label, not the raw stored value — see
    # aggregations._group.
    assert result["Brudens sida"]["yes_pct"] == 100.0
    assert result["Brudgummens sida"]["yes_pct"] == 0.0


def test_multiple_choice_breakdown():
    question = Question.objects.create(
        text_sv="Favoritdryck?",
        type=Question.Type.MULTIPLE_CHOICE,
        options=["Vin", "Öl", "Läsk"],
        status="live",
    )
    Response.objects.create(respondent=make_respondent(), question=question, answer={"value": "Vin"})
    Response.objects.create(respondent=make_respondent(), question=question, answer={"value": "Vin"})
    Response.objects.create(respondent=make_respondent(), question=question, answer={"value": "Öl"})

    result = compute_breakdown(question, BigScreenState.Breakdown.OVERALL)

    assert result["Alla"]["options_pct"] == {"Vin": pytest.approx(66.7, abs=0.1), "Öl": pytest.approx(33.3, abs=0.1)}


def test_number_aggregations():
    question = Question.objects.create(text_sv="Hur många länder?", type=Question.Type.NUMBER, status="live")
    for v in (2, 4, 10):
        Response.objects.create(respondent=make_respondent(), question=question, answer={"value": v})

    avg = compute_breakdown(question, BigScreenState.Breakdown.OVERALL, aggregation=BigScreenState.Aggregation.AVG)
    median = compute_breakdown(question, BigScreenState.Breakdown.OVERALL, aggregation=BigScreenState.Aggregation.MEDIAN)
    above = compute_breakdown(
        question,
        BigScreenState.Breakdown.OVERALL,
        aggregation=BigScreenState.Aggregation.COUNT_ABOVE_THRESHOLD,
        threshold=3,
    )

    assert avg["Alla"]["value"] == pytest.approx(16 / 3)
    assert median["Alla"]["value"] == 4
    assert above["Alla"]["value"] == 2


def test_breakdown_with_no_responses_does_not_crash():
    question = Question.objects.create(text_sv="Tom fråga", type=Question.Type.BOOLEAN, status="live")

    result = compute_breakdown(question, BigScreenState.Breakdown.OVERALL)

    assert result == {"Alla": {"count": 0, "yes_pct": None}}


def test_grouped_breakdown_with_no_responses_has_no_groups():
    question = Question.objects.create(text_sv="Tom fråga", type=Question.Type.BOOLEAN, status="live")

    result = compute_breakdown(question, BigScreenState.Breakdown.SIDE)

    assert result == {}


def test_response_unique_per_respondent_and_question():
    question = Question.objects.create(text_sv="Q", type=Question.Type.BOOLEAN, status="live")
    respondent = make_respondent()
    Response.objects.create(respondent=respondent, question=question, answer={"value": True})

    with pytest.raises(Exception):
        Response.objects.create(respondent=respondent, question=question, answer={"value": False})
