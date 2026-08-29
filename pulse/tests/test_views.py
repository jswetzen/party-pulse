import uuid

import pytest

from pulse.aggregations import compute_breakdown
from pulse.models import BigScreenState, Question, Respondent, Response

pytestmark = pytest.mark.django_db


def test_questionnaire_redirects_when_respondent_missing(client):
    missing_id = uuid.uuid4()

    response = client.get(f"/r/{missing_id}/")

    assert response.status_code == 302
    assert response.url == f"/?stale={missing_id}"


def test_questionnaire_redirect_target_is_reachable(client):
    missing_id = uuid.uuid4()

    response = client.get(f"/r/{missing_id}/", follow=True)

    assert response.status_code == 200
    assert response.redirect_chain == [(f"/?stale={missing_id}", 302)]


def test_create_identity_creates_respondent_and_age_response(client):
    response = client.post(
        "/identities/new/",
        {"age": "42", "sex": "male", "side": "groom", "relation": "friend"},
    )

    assert response.status_code == 200
    respondent = Respondent.objects.get(id=response.json()["id"])
    assert respondent.age == 42

    age_response = Response.objects.get(respondent=respondent, question=Question.system_age_question())
    assert age_response.answer == {"value": 42}


def test_questionnaire_excludes_system_age_question_even_without_a_response(client):
    # Contrived on purpose: bypass create_identity() (which would normally auto-answer the
    # system question) so the respondent has zero Responses at all. The exclusion in
    # questionnaire() is filtered on is_system, not on "already answered", so the system
    # question must still be absent even here -- this is the belt-and-suspenders backstop the
    # task calls for, independent of the primary auto-answer mechanism.
    respondent = Respondent.objects.create(age=30, sex="female", side="bride", relation="family")

    response = client.get(f"/r/{respondent.id}/")

    assert response.status_code == 200
    assert Question.system_age_question() not in response.context["questions"]


def test_answer_question_rejects_posting_to_the_system_age_question(client):
    # Mirrors test_questionnaire_excludes_system_age_question_even_without_a_response above:
    # is_system=False must also gate answer_question() itself, not just the questionnaire
    # listing -- otherwise a guest who discovers the system question's real id (e.g. from the
    # host console's screen_control dropdown, which does show it) could POST straight to it and
    # overwrite the auto-recorded age Response that create_identity() wrote.
    respondent = Respondent.objects.create(age=30, sex="female", side="bride", relation="family")
    system_question = Question.system_age_question()

    response = client.post(f"/r/{respondent.id}/answer/{system_question.id}/", {"answer": "99"})

    assert response.status_code == 404


def test_age_stat_by_side_end_to_end(client):
    # Registers respondents through the real create_identity view (not the ORM directly), then
    # proves the resulting age Responses feed compute_breakdown() correctly grouped by side --
    # this is what actually makes "average/highest age by side" work as a big-screen stat.
    for age, side in [("20", "bride"), ("30", "bride"), ("50", "groom")]:
        resp = client.post("/identities/new/", {"age": age, "sex": "male", "side": side, "relation": "friend"})
        assert resp.status_code == 200

    question = Question.system_age_question()

    avg_by_side = compute_breakdown(question, BigScreenState.Breakdown.SIDE, aggregation=BigScreenState.Aggregation.AVG)
    assert avg_by_side["Brudens sida"]["value"] == pytest.approx(25.0)
    assert avg_by_side["Brudgummens sida"]["value"] == pytest.approx(50.0)

    overall_max = compute_breakdown(question, BigScreenState.Breakdown.OVERALL, aggregation=BigScreenState.Aggregation.MAX)
    assert overall_max["Alla"]["value"] == 50
