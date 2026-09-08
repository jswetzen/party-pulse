"""Podium/Bouquet big-screen styles -- see BigScreenState.Style and
views.style_is_compatible/_rank_podium/_bouquet_geometry_boolean/_multiple_choice/_number/
screen_state. Podium is built around one specific data shape (multiple-choice options, only
at breakdown=OVERALL). Bouquet has one diagram type per Question.Type instead (see
_bouquet_geometry_*'s docstrings) plus, since 2026-09-06, a "Ribbon Rows" grouped sibling of
each for every other breakdown (see _bouquet_geometry_*_grouped's docstrings) -- so Bouquet
fits any question type at any breakdown, same as GENERIC. These tests cover the
compatibility gate, each style's data-shaping helper(s), and screen_state()'s fallback to
GENERIC when a style doesn't fit what's actually revealed (or nobody's answered yet)."""

import re
from pathlib import Path

import pytest
from django.contrib.auth.models import User

from pulse.models import BigScreenState, Question, Respondent, Response
from pulse.views import (
    _bouquet_geometry_boolean,
    _bouquet_geometry_boolean_grouped,
    _bouquet_geometry_multiple_choice,
    _bouquet_geometry_multiple_choice_grouped,
    _bouquet_geometry_number,
    _bouquet_geometry_number_grouped,
    _rank_podium,
    style_is_compatible,
)

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


def make_number_question():
    return Question.objects.create(text_sv="Hur många kanelbullar?", type=Question.Type.NUMBER, status="live")


# ---------------------------------------------------------------------------
# style_is_compatible() -- the actual gate screen_state() renders against; the control
# form's JS (screen_control.html) mirrors this but doesn't decide anything on its own.
# ---------------------------------------------------------------------------


def test_podium_fits_multiple_choice_overall_only():
    mc, boolean, number = make_mc_question(["A", "B"]), make_boolean_question(), make_number_question()

    assert style_is_compatible(BigScreenState.Style.PODIUM, mc, BigScreenState.Breakdown.OVERALL)
    assert not style_is_compatible(BigScreenState.Style.PODIUM, mc, BigScreenState.Breakdown.SEX)
    assert not style_is_compatible(BigScreenState.Style.PODIUM, boolean, BigScreenState.Breakdown.OVERALL)
    assert not style_is_compatible(BigScreenState.Style.PODIUM, number, BigScreenState.Breakdown.OVERALL)


def test_bouquet_fits_any_question_type_at_any_breakdown():
    # Widened 2026-09-06 alongside the *_grouped geometry functions -- Bouquet used to be
    # OVERALL-only (see git history/docs/screen-styles.md), same restriction Podium still
    # has below. Now it's as permissive as GENERIC.
    mc, boolean, number = make_mc_question(["A", "B"]), make_boolean_question(), make_number_question()

    for breakdown in BigScreenState.Breakdown.values:
        assert style_is_compatible(BigScreenState.Style.BOUQUET, boolean, breakdown)
        assert style_is_compatible(BigScreenState.Style.BOUQUET, mc, breakdown)
        assert style_is_compatible(BigScreenState.Style.BOUQUET, number, breakdown)


def test_podium_still_only_fits_multiple_choice_overall_after_bouquet_widened():
    # Regression guard: widening BOUQUET's branch in style_is_compatible() must not have
    # accidentally loosened PODIUM's own, separate check just above it.
    mc, boolean, number = make_mc_question(["A", "B"]), make_boolean_question(), make_number_question()

    assert style_is_compatible(BigScreenState.Style.PODIUM, mc, BigScreenState.Breakdown.OVERALL)
    for breakdown in [
        BigScreenState.Breakdown.SEX,
        BigScreenState.Breakdown.AGE,
        BigScreenState.Breakdown.SIDE,
        BigScreenState.Breakdown.RELATION,
    ]:
        assert not style_is_compatible(BigScreenState.Style.PODIUM, mc, breakdown)
    assert not style_is_compatible(BigScreenState.Style.PODIUM, boolean, BigScreenState.Breakdown.OVERALL)
    assert not style_is_compatible(BigScreenState.Style.PODIUM, number, BigScreenState.Breakdown.OVERALL)


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
# _bouquet_geometry_boolean() -- pixel placement + the near-tie flourish flag.
# ---------------------------------------------------------------------------


def test_bouquet_geometry_even_split_puts_marker_and_centers_at_track_midpoints():
    geo = _bouquet_geometry_boolean(yes_pct=50.0, count=8)

    assert geo["nej_pct"] == 50.0
    assert geo["marker_left"] == 960  # dead center of the 360..1560 track, same as the fixed guideline
    assert geo["is_near_tie"] is True
    assert geo["nej_seg_width"] < 600 < geo["ja_seg_left"]  # seam straddles the midpoint, not past it either side


def test_bouquet_geometry_lopsided_split_is_not_a_near_tie():
    geo = _bouquet_geometry_boolean(yes_pct=75.2, count=141)

    assert geo["nej_pct"] == 24.8
    assert geo["is_near_tie"] is False
    # nej is the minority here -- its segment should be the shorter one.
    assert geo["nej_seg_width"] < geo["ja_seg_width"]


def test_bouquet_geometry_segments_never_overlap_or_go_negative_at_the_extremes():
    # A 100/0 (or 0/100) split is the edge case the seam-half clamp in _bouquet_geometry
    # exists for -- without max(0, ...)/min(TRACK_WIDTH, ...) one segment would go negative.
    geo = _bouquet_geometry_boolean(yes_pct=100.0, count=5)

    assert geo["nej_seg_width"] == 0
    assert geo["ja_seg_left"] >= geo["seam_left"]
    assert geo["ja_seg_width"] >= 0


# ---------------------------------------------------------------------------
# _bouquet_geometry_multiple_choice() -- one stem per option, centre-out by rank.
# ---------------------------------------------------------------------------


def test_bouquet_geometry_multiple_choice_puts_highest_pct_in_the_centre_slot():
    geo = _bouquet_geometry_multiple_choice({"A": 20.0, "B": 50.0, "C": 30.0}, count=10)

    assert geo["kind"] == "multiple_choice"
    assert len(geo["stems"]) == 3
    # Stems are returned left-to-right by slot; the middle slot (index 1 of 3) is the
    # highest-pct option, "B" -- see _bouquet_mc_slot_order's centre-out rule.
    assert geo["stems"][1]["label"] == "B"
    assert geo["stems"][1]["rank"] == 1
    # A real bouquet's biggest bloom is also its tallest stem and biggest blossom.
    assert geo["stems"][1]["stem_height"] > geo["stems"][0]["stem_height"]
    assert geo["stems"][1]["stem_height"] > geo["stems"][2]["stem_height"]
    assert geo["stems"][1]["bloom_size"] > geo["stems"][0]["bloom_size"]


def test_bouquet_geometry_multiple_choice_stem_height_is_linear_in_raw_pct():
    # Same 0..100 semantics as GENERIC's own `width:{{ pct }}%` bar-fill -- not renormalized
    # to the group's own max, so a 40/30/30 split doesn't get stretched to look dramatic.
    geo = _bouquet_geometry_multiple_choice({"A": 0.0, "B": 100.0}, count=4)
    by_label = {stem["label"]: stem for stem in geo["stems"]}

    assert by_label["A"]["stem_height"] == 70  # _BOUQUET_MC_MIN_STEM
    assert by_label["B"]["stem_height"] == 430  # _BOUQUET_MC_MAX_STEM


def test_bouquet_geometry_multiple_choice_stems_stay_within_the_track_and_dont_collide():
    geo = _bouquet_geometry_multiple_choice({"A": 10.0, "B": 20.0, "C": 15.0, "D": 55.0}, count=20)
    xs = [stem["x"] for stem in geo["stems"]]

    assert xs == sorted(xs)  # left-to-right order matches slot order
    assert all(210 <= x <= 1710 for x in xs)  # inside _BOUQUET_MC_TRACK_LEFT/_WIDTH
    gaps = [b - a for a, b in zip(xs, xs[1:])]
    assert all(gap > 300 for gap in gaps)  # plenty of clearance for even the biggest blossom


# ---------------------------------------------------------------------------
# _bouquet_geometry_number() -- the single aggregated value, not spatially scaled.
# ---------------------------------------------------------------------------


def test_bouquet_geometry_number_carries_the_value_and_swedish_aggregation_label():
    geo = _bouquet_geometry_number(value=7.4, aggregation="median", count=30)

    assert geo == {"kind": "number", "count": 30, "value": 7.4, "aggregation_label": "Median"}


def test_bouquet_geometry_number_defaults_to_avg_label_when_aggregation_is_none():
    # compute_breakdown()/BigScreenState both allow aggregation=None (falls back to AVG) --
    # see aggregations._aggregate_group.
    geo = _bouquet_geometry_number(value=3.0, aggregation=None, count=5)

    assert geo["aggregation_label"] == "Medel"


# ---------------------------------------------------------------------------
# _bouquet_geometry_boolean_grouped() -- one miniature ja/nej track per group ("Ribbon
# Rows"), see docs/screen-styles.md's Generalization mockup.
# ---------------------------------------------------------------------------


def test_bouquet_geometry_boolean_grouped_places_each_row_and_seam_from_its_own_yes_pct():
    # A sex breakdown (2 groups) -- also the "small-group" case, exercising the roomier end
    # of the row-height scaling (see _bouquet_grouped_label_font_sizes).
    breakdown = {
        "Man": {"count": 40, "yes_pct": 60.0},
        "Kvinna": {"count": 60, "yes_pct": 25.0},
    }

    geo = _bouquet_geometry_boolean_grouped(breakdown)

    assert geo["kind"] == "boolean_grouped"
    assert geo["count"] == 100  # sum of every row's count -- must match a breakdown=OVERALL reveal of the same data
    assert len(geo["rows"]) == 2

    man, kvinna = geo["rows"]
    assert man["label"] == "Man" and man["yes_pct"] == 60.0 and man["nej_pct"] == 40.0
    assert man["row_top"] == 414 and man["row_center"] == 524
    assert man["seam_x"] == 1316  # track_left(620) + track_width(1160) * 60%
    assert man["ja_seg_width"] == 696 and man["nej_seg_width"] == 464
    assert man["winner"] == "ja"  # yes_pct > 50

    # row_center == 743, not the naive 742 midpoint -- 633 + 219/2 == 742.5 lands exactly on
    # a rounding tie, and _round_half_up (not round()) always breaks that the same direction
    # so consecutive rows' row_center values stay a uniform row_height apart -- see that
    # helper's own docstring, and this file's row-to-row spacing regression test below.
    assert kvinna["row_top"] == 633 and kvinna["row_center"] == 743
    assert kvinna["seam_x"] == 910  # 620 + 1160 * 25%
    assert kvinna["winner"] == "nej"  # yes_pct < 50


def test_bouquet_geometry_boolean_grouped_exact_tie_is_deterministically_ja():
    breakdown = {"Alla vänner": {"count": 12, "yes_pct": 50.0}}

    geo = _bouquet_geometry_boolean_grouped(breakdown)

    assert geo["rows"][0]["winner"] == "ja"  # >= 50, not > 50 -- see the function's own comment


def test_bouquet_geometry_boolean_grouped_six_groups_get_shorter_rows_than_two():
    # AGE is the 6-group breakdown -- the "hardest" case row-height-wise. Rows must still be
    # ordered top-to-bottom, non-overlapping, and packed into the same fixed vertical band a
    # 2-group breakdown uses (see the two-group test above).
    breakdown = {
        "Under 20 år": {"count": 5, "yes_pct": 40.0},
        "20-29 år": {"count": 10, "yes_pct": 55.0},
        "30-39 år": {"count": 15, "yes_pct": 48.0},
        "40-49 år": {"count": 8, "yes_pct": 70.0},
        "50-59 år": {"count": 6, "yes_pct": 33.0},
        "60+ år": {"count": 6, "yes_pct": 20.0},
    }

    geo = _bouquet_geometry_boolean_grouped(breakdown)

    assert geo["count"] == 50
    assert len(geo["rows"]) == 6
    tops = [row["row_top"] for row in geo["rows"]]
    assert tops == sorted(tops)  # rows read top-to-bottom in the same order compute_breakdown returned
    # Fixed vertical band (438px) split 6 ways is ~73px/row (was ~87.6px at 5 groups before
    # the 2026-09-07 bucket split added a 6th AGE group) -- floor lowered from 80 to 70 to
    # match, still comfortably below the real per-row height so rows can never overlap.
    assert all(b - a >= 70 for a, b in zip(tops, tops[1:]))

    # Fewer groups -> roomier rows -> bigger type, same spirit as
    # _bouquet_geometry_multiple_choice's own real-data-driven scaling.
    two_group_geo = _bouquet_geometry_boolean_grouped({"A": {"count": 1, "yes_pct": 50.0}, "B": {"count": 1, "yes_pct": 50.0}})
    assert two_group_geo["name_font_size"] > geo["name_font_size"]
    assert two_group_geo["flower_size"] > geo["flower_size"]


@pytest.mark.parametrize("n", [2, 3, 4, 6])
def test_bouquet_geometry_boolean_grouped_pct_labels_never_overlap_the_track_or_a_neighbor(n):
    # Regression test for a real bug (found 2026-09-08 by screenshotting the actual running
    # app, not visible from the geometry numbers alone -- see
    # _bouquet_geometry_boolean_grouped's own docstring): the ja/nej pct labels used to sit
    # vertically centered on the track's own centerline, exactly like the track itself, so
    # the 7px track line was drawn straight through the middle of the digits. `pct_top` must
    # keep the label's whole line clear above the track (a fixed half-height + clearance) --
    # and, since font size and row height both vary with group count, that clearance must
    # hold at every group count this app actually uses (sex=2 up through age=6), not just
    # the specific one a screenshot happened to catch.
    breakdown = {f"G{i}": {"count": 1, "yes_pct": 50.0 + i} for i in range(n)}

    geo = _bouquet_geometry_boolean_grouped(breakdown)

    track_half_height = 3.5  # .bg-track's CSS height (7px) / 2
    line_height = 1.2  # same approximation the geometry function itself budgets against
    pct_font = geo["pct_font_size"]
    label_height = pct_font * line_height

    for row in geo["rows"]:
        # The label's bottom edge (pct_top, per the template's `translateY(-100%)`) must
        # clear the track's own top edge -- i.e. sit strictly above the line, not on it.
        assert row["pct_top"] <= row["row_center"] - track_half_height

        # The label's own top edge (pct_top - label_height) must not creep above this row's
        # own top boundary, which would collide with the row above at tight (6-group) row
        # heights.
        assert row["pct_top"] - label_height >= row["row_top"]


@pytest.mark.parametrize(
    "geometry_fn, breakdown_of",
    [
        (_bouquet_geometry_boolean_grouped, lambda i: {"count": 1, "yes_pct": 50.0}),
        (_bouquet_geometry_multiple_choice_grouped, lambda i: {"count": 1, "options_pct": {"A": 50.0, "B": 50.0}}),
        (_bouquet_geometry_number_grouped, lambda i: {"count": 1, "value": 1.0, "aggregation": "avg"}),
    ],
)
def test_bouquet_geometry_grouped_row_center_spacing_is_uniform_at_six_groups(geometry_fn, breakdown_of):
    # Regression test for a real bug (found 2026-09-08 on the live 6-group Åldersgrupp
    # screenshot Johan actually reported "element alignment" against, distinct from the
    # number/track-overlap and left-right text bugs a506c73 already fixed that same day --
    # see _round_half_up's own docstring for the full mechanism): at exactly 6 groups, the
    # fixed 438px row band splits into a *whole-number* row_height (73.0), which puts every
    # row_center's `+row_height/2` term exactly on a .5 rounding tie -- and since 73 is odd,
    # plain round()'s banker's-rounding (ties go to the nearest *even* integer) alternates
    # which way it rounds from one row to the next, so consecutive row_center values came out
    # 74px, 72px, 74px, 72px, 74px apart instead of a uniform 73px. Every row's own
    # label/track/seam still agreed with EACH OTHER (all three always shared that row's one
    # row_center), which is why this was invisible from any single row's geometry -- only
    # diffing consecutive rows' row_center catches it, which is exactly what this test does,
    # for all three grouped diagram types since they share the identical row_top/row_center
    # formula (see _bouquet_grouped_label_font_sizes' own module comment on why they're
    # defined once for all three).
    breakdown = {f"G{i}": breakdown_of(i) for i in range(6)}

    geo = geometry_fn(breakdown)

    centers = [row["row_center"] for row in geo["rows"]]
    gaps = [b - a for a, b in zip(centers, centers[1:])]
    assert len(set(gaps)) == 1  # every row-to-row gap is the exact same width, not alternating
    assert gaps[0] == 73  # (852-414)/6, this app's real Åldersgrupp row height


# ---------------------------------------------------------------------------
# _bouquet_geometry_multiple_choice_grouped() -- one strung-bloom stem per group, ranked
# left-to-right (not centre-out like the single-group fan), bloom size/opacity normalized
# against the single highest pct anywhere in the whole breakdown.
# ---------------------------------------------------------------------------


def test_bouquet_geometry_multiple_choice_grouped_scales_blooms_off_the_global_max_pct():
    # "Only" (100%) is the global max across both groups -- it gets the biggest, most
    # saturated bloom on the whole stage. "Q" (25%) is a clean quarter of that max, so its
    # diameter/opacity land on round numbers worth pinning exactly (see
    # _bouquet_geometry_multiple_choice_grouped's docstring for the formula).
    breakdown = {
        "Familj": {"count": 5, "options_pct": {"Only": 100.0}},
        "Vän": {"count": 5, "options_pct": {"P": 75.0, "Q": 25.0}},
    }

    geo = _bouquet_geometry_multiple_choice_grouped(breakdown)

    assert geo["kind"] == "multiple_choice_grouped"
    assert geo["count"] == 10
    assert len(geo["rows"]) == 2

    familj_only = geo["rows"][0]["options"][0]
    assert familj_only["label"] == "Only" and familj_only["is_winner"] is True
    assert familj_only["bloom_size"] == 100  # bloom_max, since 100% *is* the global max
    assert familj_only["bloom_opacity"] == 0.9  # opacity_max

    van_p, van_q = geo["rows"][1]["options"]  # left-to-right by rank: 75% then 25%
    assert van_p["label"] == "P" and van_p["is_winner"] is True
    assert van_q["label"] == "Q" and van_q["is_winner"] is False
    assert van_q["bloom_size"] == 68  # bloom_min(35) + (bloom_max-bloom_min)(65) * sqrt(25/100)
    assert van_q["bloom_opacity"] == 0.45  # 0.30 + 0.60 * (25/100)
    # P scored 3x Q but isn't 3x the diameter -- sqrt-scaling means area, not diameter,
    # tracks pct roughly linearly.
    assert van_q["bloom_size"] < van_p["bloom_size"] < familj_only["bloom_size"]


def test_bouquet_geometry_multiple_choice_grouped_genuine_tie_crowns_both_winners():
    # Small groups tie for first place often -- e.g. 2 of 4 guests in a group picked each of
    # two options. Both must get the expensive "winner" name+pct treatment, not just
    # whichever happened to sort first.
    breakdown = {
        "Plus en": {"count": 4, "options_pct": {"Tårta": 50.0, "Glass": 50.0, "Bakelse": 0.0}},
    }
    # (Bakelse: 0.0 is unrealistic for a real compute_breakdown() result -- see that
    # function's own guarantee that every listed option got at least one vote -- but the
    # geometry function itself doesn't assume anything about the actual pct values, so this
    # still exercises the tie-detection logic (pct == row's own max) correctly.)

    geo = _bouquet_geometry_multiple_choice_grouped(breakdown)
    options = geo["rows"][0]["options"]
    by_label = {opt["label"]: opt for opt in options}

    assert by_label["Tårta"]["is_winner"] is True
    assert by_label["Glass"]["is_winner"] is True
    assert by_label["Bakelse"]["is_winner"] is False
    assert by_label["Tårta"]["bloom_size"] == by_label["Glass"]["bloom_size"]


def test_bouquet_geometry_multiple_choice_grouped_options_are_left_to_right_by_rank():
    breakdown = {"Grupp": {"count": 10, "options_pct": {"C": 10.0, "A": 55.0, "B": 35.0}}}

    geo = _bouquet_geometry_multiple_choice_grouped(breakdown)
    options = geo["rows"][0]["options"]

    assert [opt["label"] for opt in options] == ["A", "B", "C"]  # highest pct first, left-to-right
    xs = [opt["x"] for opt in options]
    assert xs == sorted(xs)


# ---------------------------------------------------------------------------
# _bouquet_geometry_number_grouped() -- one flower + one honest value per group, no
# invented shared scale (mirrors _bouquet_geometry_number's own reasoning, per-row).
# ---------------------------------------------------------------------------


def test_bouquet_geometry_number_grouped_carries_each_rows_own_value_and_a_shared_aggregation_label():
    breakdown = {
        "Familj": {"count": 20, "value": 42.5, "aggregation": "avg"},
        "Vän": {"count": 30, "value": 41.4, "aggregation": "avg"},
        "Plus en": {"count": 10, "value": 31.7, "aggregation": "avg"},
    }

    geo = _bouquet_geometry_number_grouped(breakdown)

    assert geo["kind"] == "number_grouped"
    assert geo["count"] == 60  # sum of every row's count
    assert geo["aggregation_label"] == "Medel"
    assert len(geo["rows"]) == 3

    labels_and_values = [(row["label"], row["value"]) for row in geo["rows"]]
    assert labels_and_values == [("Familj", 42.5), ("Vän", 41.4), ("Plus en", 31.7)]  # order preserved, not re-sorted
    tops = [row["row_top"] for row in geo["rows"]]
    assert tops == [414, 560, 706]  # (852-414)/3 = 146px rows, back-to-back from the shared rows band


def test_bouquet_geometry_number_grouped_uses_the_real_aggregation_not_a_hardcoded_avg():
    breakdown = {
        "Familj": {"count": 5, "value": 68.0, "aggregation": "max"},
        "Vän": {"count": 5, "value": 12.0, "aggregation": "max"},
    }

    geo = _bouquet_geometry_number_grouped(breakdown)

    assert geo["aggregation_label"] == "Max"


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


def test_screen_state_renders_bouquet_for_a_compatible_multiple_choice_reveal(client):
    question = make_mc_question(["Tårta", "Bakelse", "Glass"])
    for option in ["Tårta", "Tårta", "Bakelse", "Glass"]:
        Response.objects.create(respondent=make_respondent(), question=question, answer={"value": option})
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "overall", "bouquet", True
    state.save()

    response = client.get("/screen/state/")

    assert response.status_code == 200
    assert response.context["effective_style"] == "bouquet"
    assert response.context["bouquet"]["kind"] == "multiple_choice"
    assert len(response.context["bouquet"]["stems"]) == 3
    assert b"style-bouquet" in response.content


def test_screen_state_renders_bouquet_for_a_compatible_number_reveal(client):
    question = make_number_question()
    for value in [3, 5, 7]:
        Response.objects.create(respondent=make_respondent(), question=question, answer={"value": value})
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "overall", "bouquet", True
    state.aggregation = "avg"
    state.save()

    response = client.get("/screen/state/")

    assert response.status_code == 200
    assert response.context["effective_style"] == "bouquet"
    assert response.context["bouquet"]["kind"] == "number"
    assert response.context["bouquet"]["value"] == 5.0
    assert response.context["bouquet"]["aggregation_label"] == "Medel"
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


def test_screen_state_falls_back_to_generic_when_bouquet_number_has_no_answers_yet(client):
    question = make_number_question()
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "overall", "bouquet", True
    state.save()

    response = client.get("/screen/state/")

    assert response.context["effective_style"] == "generic"
    assert response.context["bouquet"] is None


def test_screen_state_renders_bouquet_number_when_the_real_average_is_zero(client):
    # value=0 is a legitimate real average (e.g. "coffee cups before noon") -- the guard in
    # screen_state() must be `is not None`, not a truthiness check, or a real 0.0 average
    # would wrongly also fall back to GENERIC like the "nobody's answered" case above.
    question = make_number_question()
    Response.objects.create(respondent=make_respondent(), question=question, answer={"value": 0})
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "overall", "bouquet", True
    state.save()

    response = client.get("/screen/state/")

    assert response.context["effective_style"] == "bouquet"
    assert response.context["bouquet"]["value"] == 0


# ---------------------------------------------------------------------------
# screen_state() -- the grouped ("Ribbon Rows") path, breakdown != OVERALL. Real DB-backed
# reveals (not hand-built dicts) so compute_breakdown()'s own group-label ordering/shape is
# exercised too, not just the geometry functions in isolation above.
# ---------------------------------------------------------------------------


def test_screen_state_renders_bouquet_boolean_grouped_for_a_sex_breakdown(client):
    question = make_boolean_question()
    for sex, value in [("male", True), ("male", False), ("female", True), ("female", True)]:
        Response.objects.create(respondent=make_respondent(sex=sex), question=question, answer={"value": value})
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "sex", "bouquet", True
    state.save()

    response = client.get("/screen/state/")

    assert response.status_code == 200
    assert response.context["effective_style"] == "bouquet"
    assert response.context["bouquet"]["kind"] == "boolean_grouped"
    assert len(response.context["bouquet"]["rows"]) == 2  # Kön has exactly 2 choices, see Respondent.Sex
    assert response.context["bouquet"]["count"] == 4
    assert b"style-bouquet" in response.content
    assert b"uppdelat efter K\xc3\xb6n" in response.content  # the new subline (get_breakdown_display)


def test_screen_state_renders_bouquet_multiple_choice_grouped_for_an_age_breakdown(client):
    # The "hardest case" this whole effort is about: 6 age groups x a multi-option question.
    question = make_mc_question(["Tårta", "Bakelse", "Glass"])
    ages_and_options = [
        (15, "Tårta"), (15, "Glass"),
        (25, "Tårta"), (25, "Tårta"), (25, "Bakelse"),
        (35, "Glass"),
        (45, "Bakelse"), (45, "Bakelse"),
        (55, "Tårta"), (55, "Glass"),
        (65, "Glass"),
    ]  # fmt: skip
    for age, option in ages_and_options:
        Response.objects.create(respondent=make_respondent(age=age), question=question, answer={"value": option})
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "age", "bouquet", True
    state.save()

    response = client.get("/screen/state/")

    assert response.status_code == 200
    bouquet = response.context["bouquet"]
    assert bouquet["kind"] == "multiple_choice_grouped"
    assert len(bouquet["rows"]) == 6  # Under 20, 20-29, 30-39, 40-49, 50-59, 60+ år
    assert bouquet["count"] == len(ages_and_options)
    # Rows must stay chronological (Under 20 first), not re-sorted alphabetically -- see
    # aggregations.compute_breakdown's _AGE_LABEL_ORDER.
    assert [row["label"] for row in bouquet["rows"]] == [
        "Under 20 år", "20-29 år", "30-39 år", "40-49 år", "50-59 år", "60+ år",
    ]  # fmt: skip
    assert b"style-bouquet" in response.content


def test_screen_state_renders_bouquet_number_grouped_for_a_relation_breakdown_with_avg(client):
    question = Question.system_age_question()
    for relation, age in [("family", 60), ("family", 50), ("friend", 30), ("plus_one", 22)]:
        Response.objects.create(respondent=make_respondent(relation=relation, age=age), question=question, answer={"value": age})
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "relation", "bouquet", True
    state.aggregation = "avg"
    state.save()

    response = client.get("/screen/state/")

    assert response.status_code == 200
    bouquet = response.context["bouquet"]
    assert bouquet["kind"] == "number_grouped"
    assert bouquet["aggregation_label"] == "Medel"
    assert len(bouquet["rows"]) == 3  # Familj, Vän, Plus en
    by_label = {row["label"]: row["value"] for row in bouquet["rows"]}
    assert by_label["Familj"] == 55.0  # mean of 60 and 50
    assert by_label["Vän"] == 30.0
    assert by_label["Plus en"] == 22.0
    assert bouquet["count"] == 4
    assert b"style-bouquet" in response.content


def test_screen_state_falls_back_to_generic_when_grouped_bouquet_has_no_answers_yet(client):
    # Same "nobody's answered" fallback as the OVERALL path
    # (test_screen_state_falls_back_to_generic_when_bouquet_number_has_no_answers_yet above),
    # but exercised through the new `elif breakdown:` branch in screen_state() -- an empty
    # compute_breakdown() result (literally zero groups, not zero-count groups) must still
    # fall back to GENERIC instead of _bouquet_geometry_boolean_grouped({}) blowing up on
    # max()/division-by-zero over an empty dict.
    question = make_boolean_question()
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "sex", "bouquet", True
    state.save()

    response = client.get("/screen/state/")

    assert response.context["effective_style"] == "generic"
    assert response.context["bouquet"] is None


# ---------------------------------------------------------------------------
# screen_control() persists the chosen style, same as mode/breakdown/aggregation.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# screen_state() -- the *asking* state (state.revealed == False), added alongside
# _podium_asking.html/_bouquet_asking.html so the question itself shows in Podium/Bouquet's
# own voice while guests are still answering, not just as plain text (see PLAN.md "Big-
# screen design exploration", "show the question itself styled ... before reveal"). Same
# style_is_compatible() gate as the revealed branches above -- these tests mirror the
# existing revealed compatibility tests, just with revealed=False.
# ---------------------------------------------------------------------------


def test_screen_state_renders_podium_asking_for_a_compatible_multiple_choice_question(client):
    question = make_mc_question(["Tårta", "Bakelse", "Glass"])
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "overall", "podium", False
    state.save()

    response = client.get("/screen/state/")

    assert response.status_code == 200
    assert response.context["effective_style"] == "podium"
    assert b"style-podium" in response.content
    assert "FRÅGAN".encode() in response.content
    assert question.text_sv.upper().encode() in response.content
    # No rank-data-driven markup should leak into the asking state.
    assert b"medal" not in response.content
    assert b"rank-num" not in response.content


def test_screen_state_podium_asking_falls_back_to_generic_when_incompatible(client):
    # Podium picked, but the live question is boolean -- same incompatibility
    # style_is_compatible() already rejects for the revealed state (see
    # test_screen_state_falls_back_to_generic_when_style_does_not_fit_question_type above).
    question = make_boolean_question()
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "overall", "podium", False
    state.save()

    response = client.get("/screen/state/")

    assert response.context["effective_style"] == "generic"
    assert b"style-podium" not in response.content
    assert b"screen-question" in response.content
    assert question.text_sv.encode() in response.content


def test_screen_state_renders_bouquet_asking_with_live_answer_count(client):
    question = make_boolean_question()
    for value in [True, False, True]:
        Response.objects.create(respondent=make_respondent(), question=question, answer={"value": value})
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "overall", "bouquet", False
    state.save()

    response = client.get("/screen/state/")

    assert response.status_code == 200
    assert response.context["effective_style"] == "bouquet"
    assert response.context["asking_count"] == 3
    assert b"style-bouquet" in response.content
    assert b"3</b> svar hittills" in response.content
    # No boolean-specific reveal markup (the vine/track/marker) should leak in either.
    assert b"track-wrap" not in response.content


def test_screen_state_bouquet_asking_count_is_zero_before_anyone_answers(client):
    question = make_mc_question(["A", "B"])
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "overall", "bouquet", False
    state.save()

    response = client.get("/screen/state/")

    assert response.context["effective_style"] == "bouquet"
    assert response.context["asking_count"] == 0
    assert b"0</b> svar hittills" in response.content


def test_screen_state_bouquet_asking_is_permissive_at_any_breakdown():
    # Bouquet is as breakdown-permissive pre-reveal as it is post-reveal (see
    # test_bouquet_fits_any_question_type_at_any_breakdown) -- the asking state doesn't
    # render anything breakdown-specific, but the compatibility gate itself shouldn't reject
    # a non-overall breakdown just because reveal hasn't happened yet.
    question = make_mc_question(["A", "B"])
    for breakdown in BigScreenState.Breakdown.values:
        assert style_is_compatible(BigScreenState.Style.BOUQUET, question, breakdown)


def test_screen_state_asking_count_is_none_when_revealed(client):
    # asking_count is only meaningful pre-reveal -- confirms it doesn't leak into the
    # revealed context (where podium_count/breakdown group counts are the real numbers).
    question = make_mc_question(["A", "B"])
    Response.objects.create(respondent=make_respondent(), question=question, answer={"value": "A"})
    state = BigScreenState.load()
    state.question, state.breakdown, state.style, state.revealed = question, "overall", "podium", True
    state.save()

    response = client.get("/screen/state/")

    assert response.context["asking_count"] is None


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


# ---------------------------------------------------------------------------
# Regression guard for a real bug (found 2026-09-08 by screenshotting the actual running
# app): the Ribbon Rows grouped diagrams' left-hand ".??-label" column (.bg-label/.mcg-label/
# .ng-label) has an explicit `width` but no `text-align` of its own, so it silently inherited
# `text-align: center` from the ambient `.screen` page wrapper (style.css) -- every other bit
# of Bouquet text either has no explicit width (so text-align can't visibly shift it) or sets
# its own text-align, so this one slipped through. The visible effect was the group name/count
# text centering inside its 420px column instead of hugging track_left's counterpart margin
# on the row's other side -- read as "extra empty space on the left, too little on the right"
# even though the underlying pixel geometry (label_left vs. canvas_width - track_right) was
# already symmetric. A geometry-only test can't see this (the bug is pure CSS inheritance),
# so this checks the stylesheet text directly rather than leaving it uncovered.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label_class", ["bg-label", "mcg-label", "ng-label"])
def test_screen_styles_css_grouped_label_column_is_left_aligned_not_inherited_center(label_class):
    css = Path(__file__).resolve().parent.parent.joinpath("static", "pulse", "screen_styles.css").read_text()
    match = re.search(r"\.style-bouquet \." + re.escape(label_class) + r"\s*\{([^}]*)\}", css)
    assert match, f".style-bouquet .{label_class} rule not found in screen_styles.css"
    assert "text-align: left" in match.group(1), (
        f".{label_class} must set text-align:left explicitly -- without it, .screen's "
        "ambient text-align:center (style.css) silently re-centers this column's text"
    )
