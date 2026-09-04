"""Podium/Bouquet big-screen styles -- see BigScreenState.Style and
views.style_is_compatible/_rank_podium/_bouquet_geometry/screen_state. Both styles came out
of the 10-concept design exploration (PLAN.md "Big-screen design exploration") and are each
built around one specific data shape rather than being general-purpose; these tests cover
the compatibility gate, the two styles' data-shaping helpers, and screen_state()'s fallback
to GENERIC when a style doesn't fit what's actually revealed."""

import pytest
from django.contrib.auth.models import User

from pulse.models import BigScreenState, Question, Respondent, Response
from pulse.views import _bouquet_geometry, _rank_podium, style_is_compatible

pytestmark = pytest.mark.django_db


def make_respondent(**kwargs):
    defaults = dict(age=35, sex="female", side="bride", relation="friend")
    defaults.update(kwargs)
    return Respondent.objects.create(**defaults)


def make_mc_question(options):
    return Question.objects.create(
        text_sv="Vilken tårta?", type=Question.Type.MULTIPLE_CHOICE, status="live", options=options
    )


def make_boolean_question():
    return Question.objects.create(text_sv="Har ni dansat?", type=Question.Type.BOOLEAN, status="live")


# ---------------------------------------------------------------------------
# style_is_compatible() -- the actual gate screen_state() renders against; the control
# form's JS (screen_control.html) mirrors this but doesn't decide anything on its own.
# ---------------------------------------------------------------------------


def test_podium_fits_multiple_choice_overall_only():
    mc, boolean, number = make_mc_question(["A", "B"]), make_boolean_question(), Question.objects.create(
        text_sv="Hur många?", type=Question.Type.NUMBER, status="live"
    )

    assert style_is_compatible(BigScreenState.Style.PODIUM, mc, BigScreenState.Breakdown.OVERALL)
    assert not style_is_compatible(BigScreenState.Style.PODIUM, mc, BigScreenState.Breakdown.SEX)
    assert not style_is_compatible(BigScreenState.Style.PODIUM, boolean, BigScreenState.Breakdown.OVERALL)
    assert not style_is_compatible(BigScreenState.Style.PODIUM, number, BigScreenState.Breakdown.OVERALL)


def test_bouquet_fits_boolean_overall_only():
    mc, boolean = make_mc_question(["A", "B"]), make_boolean_question()

    assert style_is_compatible(BigScreenState.Style.BOUQUET, boolean, BigScreenState.Breakdown.OVERALL)
    assert not style_is_compatible(BigScreenState.Style.BOUQUET, boolean, BigScreenState.Breakdown.AGE)
    assert not style_is_compatible(BigScreenState.Style.BOUQUET, mc, BigScreenState.Breakdown.OVERALL)


def test_generic_fits_everything():
    mc, boolean = make_mc_question(["A", "B"]), make_boolean_question()

    assert style_is_compatible(BigScreenState.Style.GENERIC, mc, BigScreenState.Breakdown.SEX)
    assert style_is_compatible(BigScreenState.Style.GENERIC, boolean, BigScreenState.Breakdown.AGE)


# ---------------------------------------------------------------------------
# _rank_podium() -- ranks a single-group (breakdown=OVERALL) options_pct dict.
# ---------------------------------------------------------------------------


def test_rank_podium_orders_highest_first_with_1_based_ranks():
    breakdown = {"Alla": {"count": 10, "options_pct": {"A": 20.0, "B": 50.0, "C": 30.0}}}

    ranked = _rank_podium(breakdown)

    assert ranked == [
        {"rank": 1, "label": "B", "pct": 50.0},
        {"rank": 2, "label": "C", "pct": 30.0},
        {"rank": 3, "label": "A", "pct": 20.0},
    ]


def test_rank_podium_empty_when_nobody_has_answered():
    breakdown = {"Alla": {"count": 0, "options_pct": {}}}

    assert _rank_podium(breakdown) == []


# ---------------------------------------------------------------------------
# _bouquet_geometry() -- pixel placement + the near-tie flourish flag.
# ---------------------------------------------------------------------------


def test_bouquet_geometry_even_split_puts_marker_and_centers_at_track_midpoints():
    geo = _bouquet_geometry(yes_pct=50.0, count=8)

    assert geo["nej_pct"] == 50.0
    assert geo["marker_left"] == 960  # dead center of the 360..1560 track, same as the fixed guideline
    assert geo["is_near_tie"] is True
    assert geo["nej_seg_width"] < 600 < geo["ja_seg_left"]  # seam straddles the midpoint, not past it either side


def test_bouquet_geometry_lopsided_split_is_not_a_near_tie():
    geo = _bouquet_geometry(yes_pct=75.2, count=141)

    assert geo["nej_pct"] == 24.8
    assert geo["is_near_tie"] is False
    # nej is the minority here -- its segment should be the shorter one.
    assert geo["nej_seg_width"] < geo["ja_seg_width"]


def test_bouquet_geometry_segments_never_overlap_or_go_negative_at_the_extremes():
    # A 100/0 (or 0/100) split is the edge case the seam-half clamp in _bouquet_geometry
    # exists for -- without max(0, ...)/min(TRACK_WIDTH, ...) one segment would go negative.
    geo = _bouquet_geometry(yes_pct=100.0, count=5)

    assert geo["nej_seg_width"] == 0
    assert geo["ja_seg_left"] >= geo["seam_left"]
    assert geo["ja_seg_width"] >= 0


# ---------------------------------------------------------------------------
# screen_state() -- the actual render, including the fallback to GENERIC.
# ---------------------------------------------------------------------------


def test_screen_state_renders_podium_for_a_compatible_multiple_choice_reveal(client):
    question = make_mc_question(["Tårta", "Bakelse", "Glass"])
    for option in ["Tårta", "Tårta", "Bakelse", "Glass"]:
        Response.objects.create(respondent=make_respondent(), question=question, answer={"value": option})
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "overall", "podium", True
    state.save()

    response = client.get("/screen/state/")

    assert response.status_code == 200
    assert response.context["effective_style"] == "podium"
    assert response.context["podium_rank1"] == {"rank": 1, "label": "Tårta", "pct": 50.0}
    assert response.context["podium_count"] == 4
    assert b"style-podium" in response.content


def test_screen_state_renders_bouquet_for_a_compatible_boolean_reveal(client):
    question = make_boolean_question()
    Response.objects.create(respondent=make_respondent(), question=question, answer={"value": True})
    Response.objects.create(respondent=make_respondent(), question=question, answer={"value": False})
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "overall", "bouquet", True
    state.save()

    response = client.get("/screen/state/")

    assert response.status_code == 200
    assert response.context["effective_style"] == "bouquet"
    assert response.context["bouquet"]["yes_pct"] == 50.0
    assert b"style-bouquet" in response.content


def test_screen_state_falls_back_to_generic_when_style_does_not_fit_question_type(client):
    # Podium picked, but the revealed question is boolean -- style_is_compatible() rejects
    # this combination, so the reveal must render exactly like GENERIC, not a broken Podium.
    question = make_boolean_question()
    Response.objects.create(respondent=make_respondent(), question=question, answer={"value": True})
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "overall", "podium", True
    state.save()

    response = client.get("/screen/state/")

    assert response.context["effective_style"] == "generic"
    assert b"style-podium" not in response.content
    assert b"screen-question" in response.content


def test_screen_state_falls_back_to_generic_when_breakdown_is_not_overall(client):
    question = make_mc_question(["A", "B"])
    Response.objects.create(respondent=make_respondent(), question=question, answer={"value": "A"})
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "sex", "podium", True
    state.save()

    response = client.get("/screen/state/")

    assert response.context["effective_style"] == "generic"


def test_screen_state_falls_back_to_generic_when_nobody_has_answered_yet(client):
    # Type/breakdown are compatible, but there's no data to rank/split yet -- Podium and
    # Bouquet would both render an empty/broken visual, so this falls back same as above.
    # (breakdown=OVERALL always has exactly one "Alla" group -- count=0, not zero groups --
    # so the GENERIC template's own {% empty %} "Inga svar än." never fires here; it just
    # shows "0 svar" with no bars, which is the actual thing being asserted below.)
    question = make_mc_question(["A", "B"])
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "overall", "podium", True
    state.save()

    response = client.get("/screen/state/")

    assert response.context["effective_style"] == "generic"
    assert response.context["podium_rank1"] is None
    assert b"0 svar" in response.content
    assert b"style-podium" not in response.content


# ---------------------------------------------------------------------------
# screen_control() persists the chosen style, same as mode/breakdown/aggregation.
# ---------------------------------------------------------------------------


def test_screen_control_post_persists_style(client):
    User.objects.create_user("host", password="pw")
    client.login(username="host", password="pw")
    question = make_mc_question(["A", "B"])

    client.post(
        "/host/screen/",
        {"mode": "statistics", "question": question.id, "breakdown": "overall", "style": "podium", "action": "reveal"},
    )

    state = BigScreenState.load()
    assert state.style == "podium"
