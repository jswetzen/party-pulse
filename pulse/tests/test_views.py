import uuid

import pytest
from django.contrib.auth.models import User

from pulse.aggregations import compute_breakdown
from pulse.models import BigScreenState, Question, Respondent, Response
from pulse.qr import render_qr_svg

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


# ---------------------------------------------------------------------------
# screen_control() suggestion-card prefill (see pulse/suggestions.py) -- clicking a
# suggestion must only pre-select the form's fields, never touch the persisted
# BigScreenState/reveal itself. Only a POST (the host's own explicit click) may do that.
# ---------------------------------------------------------------------------


def _login_host(client):
    User.objects.create_user("host", password="pw")
    client.login(username="host", password="pw")


def test_screen_control_get_prefills_from_query_params_without_persisting(client):
    _login_host(client)
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    state = BigScreenState.load()
    state.breakdown = BigScreenState.Breakdown.OVERALL
    state.save()

    response = client.get(f"/host/screen/?question={question.id}&breakdown=side")

    assert response.status_code == 200
    assert response.context["prefill_question_id"] == question.id
    assert response.context["prefill_breakdown"] == "side"
    # The GET must not have written anything back to the singleton state.
    state.refresh_from_db()
    assert state.question_id is None
    assert state.breakdown == BigScreenState.Breakdown.OVERALL


def test_screen_control_get_ignores_invalid_question_param(client):
    _login_host(client)
    # No live question at all with this id -- an old/stale suggestion link, or a question
    # that has since been archived.
    response = client.get("/host/screen/?question=999999&breakdown=side")

    assert response.status_code == 200
    assert response.context["prefill_question_id"] is None


def test_screen_control_get_without_query_params_falls_back_to_persisted_state(client):
    _login_host(client)
    question = Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")
    state = BigScreenState.load()
    state.question = question
    state.breakdown = BigScreenState.Breakdown.SEX
    state.save()

    response = client.get("/host/screen/")

    assert response.context["prefill_question_id"] == question.id
    assert response.context["prefill_breakdown"] == BigScreenState.Breakdown.SEX


# ---------------------------------------------------------------------------
# QR sign ("/qr/") -- unauthenticated signage page, see qr_sign() in views.py.
# ---------------------------------------------------------------------------


def test_qr_sign_is_reachable_without_login(client):
    response = client.get("/qr/")

    assert response.status_code == 200
    assert b"<svg" in response.content


def test_qr_sign_encodes_the_guest_entry_url(client, settings):
    settings.ALLOWED_HOSTS = ["party.example.com"]

    response = client.get("/qr/", SERVER_NAME="party.example.com")

    guest_url = response.context["guest_url"]
    assert guest_url == "http://party.example.com/"
    assert response.context["qr_svg"] == render_qr_svg(guest_url)
