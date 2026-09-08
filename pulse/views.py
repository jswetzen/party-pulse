import math

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .aggregations import compute_breakdown
from .forms import RespondentForm
from .models import BigScreenState, Question, Respondent, Response
from .qr import render_qr_svg
from .suggestions import compute_suggestions

# ---------------------------------------------------------------------------
# Guest app ("/") — identity is a UUID in the URL, not a server session; the
# guest app's JS keeps the list of {id, alias} in localStorage and never
# sends the alias to the server. See PLAN.md "Identity model".
# ---------------------------------------------------------------------------


def identity_picker(request):
    return render(request, "pulse/identity.html")


@require_POST
def create_identity(request):
    form = RespondentForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors}, status=400)
    # Wrapped in a transaction so a failure creating the age Response (e.g. the system
    # question is somehow missing) can't leave behind a Respondent with no matching age
    # answer -- see Question.system_age_question() and PLAN.md "Demographics".
    with transaction.atomic():
        respondent = form.save()
        Response.objects.create(
            respondent=respondent,
            question=Question.system_age_question(),
            answer={"value": respondent.age},
        )
    return JsonResponse({"id": str(respondent.id)})


def questionnaire(request, respondent_id):
    respondent = Respondent.objects.filter(id=respondent_id).first()
    if respondent is None:
        # The browser's localStorage still points at a respondent id that no longer exists
        # server-side (e.g. the DB was reset between visits). A raw 404 is a dead end for a
        # guest who did nothing wrong -- send them back to create a new identity, and tell the
        # identity picker's JS which stale id to drop from localStorage so "Fortsätt som ..."
        # doesn't just lead back here in a loop.
        return redirect(f"/?stale={respondent_id}")
    answered_ids = Response.objects.filter(respondent=respondent).values_list("question_id", flat=True)
    # is_system=False excludes the system age question as a backstop in case its auto-answer
    # step in create_identity() ever failed to run for some respondent -- the primary
    # mechanism is that auto-answer, this exclusion is belt-and-suspenders, not the main path.
    questions = Question.objects.filter(status=Question.Status.LIVE, is_system=False).exclude(id__in=answered_ids)
    return render(
        request,
        "pulse/questionnaire.html",
        {"respondent": respondent, "questions": questions},
    )


@require_POST
def answer_question(request, respondent_id, question_id):
    respondent = get_object_or_404(Respondent, id=respondent_id)
    # is_system=False here for the same reason as questionnaire()'s filter above: without it a
    # guest could POST straight to the system age question's id and overwrite its
    # auto-recorded Response (normally only ever written by create_identity()).
    question = get_object_or_404(Question, id=question_id, status=Question.Status.LIVE, is_system=False)

    value = _parse_answer(question, request.POST)
    if value is None:
        return render(
            request,
            "pulse/_question_card.html",
            {"question": question, "respondent": respondent, "error": "Ogiltigt svar."},
        )

    Response.objects.update_or_create(
        respondent=respondent, question=question, defaults={"answer": {"value": value}}
    )
    return render(request, "pulse/_question_answered.html", {"question": question})


def _parse_answer(question, post):
    raw = post.get("answer")
    if raw is None or raw == "":
        return None
    if question.type == Question.Type.BOOLEAN:
        return raw == "true"
    if question.type == Question.Type.MULTIPLE_CHOICE:
        return raw if raw in question.options else None
    if question.type == Question.Type.NUMBER:
        try:
            return float(raw)
        except ValueError:
            return None
    return None


@require_POST
def suggest_question(request, respondent_id):
    respondent = get_object_or_404(Respondent, id=respondent_id)
    text = request.POST.get("text_sv", "").strip()
    if text:
        Question.objects.create(
            text_sv=text,
            type=Question.Type.BOOLEAN,
            status=Question.Status.DRAFT,
            source=Question.Source.GUEST_SUGGESTED,
            suggested_by_respondent=respondent,
            suggestion_review_status=Question.SuggestionReviewStatus.PENDING,
        )
    return render(request, "pulse/_suggestion_form.html", {"submitted": bool(text), "respondent": respondent})


def qr_sign(request):
    # Unauthenticated like screen_display() below -- this is a poster/signage view meant
    # to be pulled up on a lobby TV or printed and taped to a wall, not something only the
    # host should reach. build_absolute_uri points at "/" (identity_picker), the actual
    # guest entry point, and picks up the right scheme via SECURE_PROXY_SSL_HEADER
    # (settings.py) so the QR still encodes https:// behind the reverse proxy.
    guest_url = request.build_absolute_uri("/")
    return render(request, "pulse/qr_sign.html", {"guest_url": guest_url, "qr_svg": render_qr_svg(guest_url)})


# ---------------------------------------------------------------------------
# Host console ("/host") — gated by Django's regular auth (one shared
# operator account, created with `manage.py createsuperuser`). Not
# security-critical (private party), just enough to keep randoms off it.
# See PLAN.md "Host console auth".
# ---------------------------------------------------------------------------


@login_required
def host_home(request):
    live_questions = Question.objects.filter(status=Question.Status.LIVE)
    pending_suggestions = Question.objects.filter(
        suggestion_review_status=Question.SuggestionReviewStatus.PENDING
    )
    return render(
        request,
        "pulse/host_home.html",
        {"live_questions": live_questions, "pending_suggestions": pending_suggestions},
    )


@login_required
def screen_control(request):
    state = BigScreenState.load()

    if request.method == "POST":
        state.mode = request.POST.get("mode", state.mode)
        question_id = request.POST.get("question")
        state.question = Question.objects.filter(id=question_id).first() if question_id else None
        state.breakdown = request.POST.get("breakdown", state.breakdown)
        state.style = request.POST.get("style", state.style)
        state.aggregation = request.POST.get("aggregation") or None
        threshold = request.POST.get("aggregation_threshold")
        state.aggregation_threshold = float(threshold) if threshold else None
        state.revealed = request.POST.get("action") == "reveal"
        state.save()
        return redirect("host-screen-control")

    live_questions = Question.objects.filter(status=Question.Status.LIVE)

    # "Interesting stats" suggestion cards (see pulse/suggestions.py) link back to this
    # same page with ?question=&breakdown= to pre-fill the form below -- never a new
    # reveal path, and never touching `state`/the singleton itself, since only a POST
    # (the host's own explicit "show"/"reveal" click) persists anything. Falls back to
    # the persisted state's own question/breakdown when no suggestion was clicked, or
    # when the query params are missing/invalid (e.g. a stale link to an archived
    # question no longer in live_questions).
    prefill_question_id = state.question_id
    question_param = request.GET.get("question")
    if question_param:
        try:
            candidate_id = int(question_param)
        except ValueError:
            candidate_id = None
        if candidate_id is not None and live_questions.filter(id=candidate_id).exists():
            prefill_question_id = candidate_id
    prefill_breakdown = request.GET.get("breakdown") or state.breakdown

    return render(
        request,
        "pulse/screen_control.html",
        {
            "state": state,
            "live_questions": live_questions,
            "prefill_question_id": prefill_question_id,
            "prefill_breakdown": prefill_breakdown,
            "suggestions": compute_suggestions(),
        },
    )


# ---------------------------------------------------------------------------
# Big screen display ("/screen") — unauthenticated (read-only aggregates
# only, never individual answers — see PLAN.md "Reveal semantics"); relies
# on the venue network being effectively private. Polls every 2s via htmx,
# no websockets/SSE. See PLAN.md "start simple with polling".
# ---------------------------------------------------------------------------


def screen_display(request):
    return render(request, "pulse/screen_display.html")


def style_is_compatible(style: str, question: Question, breakdown: str) -> bool:
    """Whether `style` fits `question`'s type + `breakdown` well enough to reveal with --
    see BigScreenState.Style for why PODIUM needs one specific shape (and GENERIC, today's
    bar-chart markup, fits everything). Used by screen_state() below to fall back to
    GENERIC rather than render a style against data it wasn't built for; the control form
    mirrors this client-side (see screen_control.html's script) purely so the host doesn't
    pick a combination that's about to be silently downgraded, but this is the check that
    actually decides what gets rendered.

    BOUQUET fits any question type at any breakdown -- it has one diagram type per
    Question.Type at breakdown=OVERALL (_bouquet_geometry_boolean/_multiple_choice/_number)
    and a "Ribbon Rows" per-group sibling of each for every other breakdown
    (_bouquet_geometry_boolean_grouped/_multiple_choice_grouped/_number_grouped, added
    2026-09-06 -- see docs/screen-styles.md's session summary of that date), so it's as
    permissive as GENERIC. PODIUM stays the narrow one: it ranks a single set of labelled
    scores, which only exists for a multiple-choice question's options at breakdown=OVERALL
    -- a per-group breakdown would be several separate option distributions, not one ranked
    list (see docs/screen-styles.md "Possible next steps" for that still-deferred extension)."""
    if style == BigScreenState.Style.PODIUM:
        return question.type == Question.Type.MULTIPLE_CHOICE and breakdown == BigScreenState.Breakdown.OVERALL
    return True


def _rank_podium(breakdown: dict) -> list[dict]:
    """[{"rank": 1, "label": option, "pct": ...}, ...] sorted highest-first, from a
    multiple_choice breakdown computed with breakdown=OVERALL (so `breakdown` has exactly
    one group -- see aggregations._group). Empty if nobody has answered yet."""
    group = next(iter(breakdown.values()))
    ranked = sorted(group["options_pct"].items(), key=lambda item: -item[1])
    return [{"rank": i, "label": label, "pct": pct} for i, (label, pct) in enumerate(ranked, start=1)]


def _podium_question_font_size(text: str) -> int:
    """Podium's marquee plaque (see screen_styles/_podium.html) is a fixed 1600px-wide box
    in a fixed 1920x1080 canvas -- not a responsive page, so this is a stepped heuristic on
    character count rather than a fluid vw-based size like the GENERIC template uses."""
    length = len(text)
    if length <= 46:
        return 62
    if length <= 64:
        return 46
    return 36


# Every Bouquet coordinate below is a pixel inside the fixed 1920x1080 stage
# (screen_display.html's #style-canvas, scaled to whatever's actually plugged in) -- named
# here so the layouts that want to be symmetric about the stage can say so in terms of the
# stage's own width instead of repeating 1920 as a bare number.
_BOUQUET_CANVAS_WIDTH = 1920

# Bouquet's ja/nej "vine" track spans this fixed box inside the 1920x1080 stage (see
# screen_styles/_bouquet.html); _bouquet_geometry_boolean() below places every element in it from
# the real yes_pct, where the original mockup had these hand-placed for one specific
# 51.1/48.9 split.
_BOUQUET_TRACK_LEFT = 360
_BOUQUET_TRACK_WIDTH = 1200
_BOUQUET_SEAM_HALF = 12
# How close to a 50/50 split counts as a "near tie" worth calling out with the
# tendril+note flourish (kept at their original fixed position -- see
# screen_styles/_bouquet.html -- since it only ever renders when the real split lands near
# the guideline anyway).
_BOUQUET_NEAR_TIE_POINTS = 5


def _bouquet_geometry_boolean(yes_pct: float, count: int) -> dict:
    nej_pct = round(100 - yes_pct, 1)
    boundary = round(_BOUQUET_TRACK_WIDTH * nej_pct / 100)  # 0..1200, relative to track-wrap
    seam_left = max(0, boundary - _BOUQUET_SEAM_HALF)
    seam_right = min(_BOUQUET_TRACK_WIDTH, boundary + _BOUQUET_SEAM_HALF)
    return {
        "kind": "boolean",
        "yes_pct": yes_pct,
        "nej_pct": nej_pct,
        "count": count,
        "nej_seg_width": seam_left,
        "ja_seg_left": seam_right,
        "ja_seg_width": _BOUQUET_TRACK_WIDTH - seam_right,
        "seam_left": seam_left,
        "seam_width": seam_right - seam_left,
        "nej_center": round(_BOUQUET_TRACK_LEFT + boundary / 2),
        "ja_center": round(_BOUQUET_TRACK_LEFT + boundary + (_BOUQUET_TRACK_WIDTH - boundary) / 2),
        "marker_left": _BOUQUET_TRACK_LEFT + boundary,
        "is_near_tie": abs(yes_pct - 50) <= _BOUQUET_NEAR_TIE_POINTS,
    }


# Bouquet's multiple_choice "fanned arrangement" diagram type (see
# screen_styles/_bouquet_visual_multiple_choice.html) -- one stem per option inside this
# fixed box, added 2026-09-05 alongside the number diagram type when Bouquet grew past its
# original boolean-only shape (see docs/screen-styles.md "Possible next steps" #1).
_BOUQUET_MC_TRACK_LEFT = 210
_BOUQUET_MC_TRACK_WIDTH = 1500
_BOUQUET_MC_BASELINE_Y = 760
_BOUQUET_MC_LABEL_TOP = _BOUQUET_MC_BASELINE_Y + 14
_BOUQUET_MC_MIN_STEM = 70
_BOUQUET_MC_MAX_STEM = 430
# Gap between a stem's blossom and its percentage number, and the percentage number's own
# rough text height -- both baked into num_top below so the template only ever places a
# ready-made pixel, never does arithmetic (Django templates have no multiply/subtract
# filters worth the noise -- see _bouquet_geometry_boolean's identical convention above).
_BOUQUET_MC_NUM_GAP = 40


def _bouquet_mc_slot_order(n: int) -> list[int]:
    """Slot index (0-based, left-to-right) that each rank position (0-based, 0 = highest
    pct) should occupy: centre-out, so the best-scoring option lands in the middle slot and
    the rest flank it in descending order -- an arranged bouquet reads with its biggest
    bloom at centre, not wherever the option happened to sort alphabetically. Ties broken
    by slot index for determinism."""
    mid = (n - 1) / 2
    return sorted(range(n), key=lambda slot: (abs(slot - mid), slot))


def _bouquet_mc_label_font_size(label: str) -> int:
    """Stepped heuristic on character count, same idea as _podium_question_font_size --
    labels sit in a fixed-width slot (track width / option count), not a responsive page."""
    length = len(label)
    if length <= 16:
        return 24
    if length <= 26:
        return 20
    if length <= 40:
        return 17
    return 15


def _bouquet_mc_stalk_path(stem_height: int, curve_dir: int) -> str:
    """SVG path `d` for one stem's stalk, a gentle bezier from the baseline (local y =
    stem_height) up to the blossom (local y = 0) in a 48-wide local viewBox -- curve_dir
    (+1/-1) alternates which way neighboring stems bow so the arrangement doesn't read as a
    row of identical vertical rulers."""
    ctrl1_y = round(stem_height * 0.62)
    ctrl2_y = round(stem_height * 0.3)
    return f"M24,{stem_height} C {24 + curve_dir * 14},{ctrl1_y} {24 + curve_dir * 10},{ctrl2_y} 24,0"


def _bouquet_geometry_multiple_choice(options_pct: dict[str, float], count: int) -> dict:
    """Stem height is linear in pct directly (0..100), matching GENERIC's own
    `width:{{ pct }}%` bar-fill semantics rather than rescaling to the group's own max -- a
    close race still reads as short, similar stems instead of an artificially dramatic
    spread. Caller guards the "nobody's answered yet" case (empty options_pct)."""
    ranked = sorted(options_pct.items(), key=lambda item: -item[1])
    n = len(ranked)
    slot_order = _bouquet_mc_slot_order(n)
    slot_width = _BOUQUET_MC_TRACK_WIDTH / n
    stems = [None] * n
    for rank_index, (label, pct) in enumerate(ranked):
        slot = slot_order[rank_index]
        stem_height = round(_BOUQUET_MC_MIN_STEM + (pct / 100) * (_BOUQUET_MC_MAX_STEM - _BOUQUET_MC_MIN_STEM))
        stem_top = _BOUQUET_MC_BASELINE_Y - stem_height
        bloom_size = 108 if rank_index == 0 else (86 if rank_index < 3 else 68)
        curve_dir = 1 if slot % 2 == 0 else -1
        stems[slot] = {
            "label": label,
            "pct": pct,
            "rank": rank_index + 1,
            "x": round(_BOUQUET_MC_TRACK_LEFT + slot_width * (slot + 0.5)),
            "stem_height": stem_height,
            "stem_top": stem_top,
            "stalk_path": _bouquet_mc_stalk_path(stem_height, curve_dir),
            "bloom_size": bloom_size,
            "num_top": stem_top - bloom_size // 2 - _BOUQUET_MC_NUM_GAP,
            "num_font_size": 56 if rank_index == 0 else (42 if rank_index < 3 else 32),
            "label_width": round(slot_width) - 24,
            "label_font_size": _bouquet_mc_label_font_size(label),
            "leaf_ref": "leaf" if slot % 2 == 0 else "leaf-r",
        }
    return {
        "kind": "multiple_choice",
        "count": count,
        "baseline_y": _BOUQUET_MC_BASELINE_Y,
        "label_top": _BOUQUET_MC_LABEL_TOP,
        "stems": stems,  # left-to-right (list built by slot index)
    }


def _bouquet_geometry_number(value: float, aggregation: str | None, count: int) -> dict:
    """The single aggregated value as one centerpiece stem -- deliberately not
    spatially-scaled (no stem-height-by-magnitude like the multiple_choice diagram type
    above), since NUMBER questions have no declared min/max in the schema (age, cinnamon
    buns, coffee cups/day, ... are all the same `type=number` with wildly different ranges)
    -- inventing a domain to plot against would visually imply a scale that isn't real.
    Same "just show the number" semantics as GENERIC's own `.result-number`, dressed in
    Bouquet's botanical voice instead of plotted as a fake gauge."""
    aggregation = aggregation or BigScreenState.Aggregation.AVG
    return {
        "kind": "number",
        "count": count,
        "value": value,
        "aggregation_label": dict(BigScreenState.Aggregation.choices)[aggregation],
    }


# ---------------------------------------------------------------------------
# Bouquet -- grouped ("Ribbon Rows") diagram types, added 2026-09-06 alongside widening
# style_is_compatible() to accept any breakdown. The three single-group diagrams above
# (_bouquet_geometry_boolean/_multiple_choice/_number) only ever plot breakdown=OVERALL's
# one "Alla" group; these three plot every other breakdown's 2-5 groups (see
# BigScreenState.Breakdown -- sex=2, side=3, relation=3, age=5), one horizontal row per
# group, generalizing each single-group diagram's own visual idea into a row rather than
# inventing a fourth unrelated layout. Picked after a real design-exploration pass (six
# concept mockups, a design canvas, user feedback on legibility) landed on this "Ribbon
# Rows" direction -- see docs/screen-styles.md's session summary of that date for the full
# story, including the exact bloom-size/opacity formula the multiple_choice version below
# reverse-engineers from that mockup's own annotated SVG comments.
#
# All three share the same vertical row band and left-hand group-label column -- defined
# once here rather than per-kind -- so switching which question type is revealed at, say,
# breakdown=age always plants its rows in the same place on the stage. The horizontal *data*
# region is shared by the two kinds that actually fill it edge to edge (boolean's track,
# multiple_choice's stem); number_grouped's row is a small fixed-width cluster instead and
# keeps its own left edge -- see _BOUQUET_NG_FLOWER_LEFT.
# ---------------------------------------------------------------------------

# The header above (eyebrow/headline/divider, see _bouquet.html) and the footer below
# (rule/count, see _bouquet_footer.html) are unchanged for the grouped path, but a grouped
# reveal also always shows the "uppdelat efter ..." subline (breakdown != OVERALL is exactly
# when these functions run) right under the header's divider -- see .bq-subline in
# screen_styles.css -- so the rows band starts a bit lower than a hypothetical
# subline-less layout would allow, and stays well clear of the footer rule (top:872px).
_BOUQUET_GROUPED_ROWS_TOP = 414
_BOUQUET_GROUPED_ROWS_BOTTOM = 852
# Left-hand "group name / N svar" column, shared by all three kinds so a host flipping
# between question types at the same breakdown sees the group labels stay put.
#
# LABEL_WIDTH is what the column *reserves*, and it has to be justified by what the column
# can ever actually hold, because the group name is left-aligned inside it (see .bg-label's
# `text-align: left` in screen_styles.css) -- every pixel of reserved-but-unused width turns
# into a void between the group name and its own data, not into padding around the text.
# The set of group names is closed and known (Respondent.Sex/Side/Relation's own display
# labels plus models.age_bucket_label()'s six buckets), and the biggest font this column
# ever uses is _bouquet_grouped_label_font_sizes()'s own 32px cap, so the worst case is
# exactly measurable rather than a guess: "Brudgummens sida" at 32px renders 217.5px wide in
# this style's real face (measured in headless Chromium against the running app, 2026-09-08;
# every other name at every other size this app can produce is narrower -- the widest at the
# 20px floor, i.e. the 6-group Åldersgrupp case, is 136px). 260 clears that worst case by
# ~42px, so the name can never wrap to a second line (which would collide with the next row
# vertically) and never crowds the gap below.
_BOUQUET_GROUPED_LABEL_LEFT = 140
_BOUQUET_GROUPED_LABEL_WIDTH = 260
# Gutter between the label column and the data band. Explicit rather than implied by the
# difference between two absolute constants, so "how far is the group name from its own
# row" is a number someone can read and change.
_BOUQUET_GROUPED_LABEL_GAP = 40
# Horizontal region the data itself lives in (the ja/nej track for boolean and the
# strung-bloom stem for multiple_choice; number_grouped has its own cluster, see
# _BOUQUET_NG_FLOWER_LEFT below). Derived from the label column rather than hardcoded, and
# the width derived so the band's right margin is the label column's own left margin by
# construction (140 either side) -- the previous hardcoded pair (620/1160) happened to
# satisfy that too, but only by coincidence, and nothing said so.
#
# Both numbers changed 2026-09-08 (label column 420 -> 260, band 620/1160 -> 440/1340) to
# fix a real horizontal-spacing bug found by measuring the running app: the label zone
# (column + gutter) reserved 480px for text that renders 46-156px wide in every real
# breakdown, so every row had a ~394px void between the group name and the start of its own
# track, and the data band's centre sat at x=1200 -- 240px right of x=960, the axis the
# headline, the "uppdelat efter ..." subline, the divider and the footer count are all
# centred on (headline ink measured 372.6..1547.4, centre 960.0). The whole diagram read as
# pushed right with a hole under the headline. 260+40 restores the label zone to the 300px
# the RibbonRows/Generalization mockups this layout came from actually specified
# (`.bool-label { width: 300px }` there, with the track starting immediately after it) --
# the implementation had drifted to 480px without that being a decision anyone made. See
# docs/screen-styles.md's session summary of this date for the full measured before/after.
_BOUQUET_GROUPED_TRACK_LEFT = _BOUQUET_GROUPED_LABEL_LEFT + _BOUQUET_GROUPED_LABEL_WIDTH + _BOUQUET_GROUPED_LABEL_GAP
_BOUQUET_GROUPED_TRACK_WIDTH = _BOUQUET_CANVAS_WIDTH - _BOUQUET_GROUPED_LABEL_LEFT - _BOUQUET_GROUPED_TRACK_LEFT

# boolean_grouped's pct labels sit above the ja/nej track rather than sharing its own
# vertical center (see _bouquet_geometry_boolean_grouped's docstring) -- half of .bg-track's
# own fixed CSS height (7px), plus a fixed clearance above that, plus one line of text at
# whatever pct_font comes out to, must all fit inside half a row or the label collides with
# the track (small group counts) or the row above it (large group counts, e.g. Åldersgrupp's
# 6). Line-height matches the multiple_choice_grouped constant below -- same "a CSS line's
# rendered height in px per 1px of font-size" reasoning, this diagram type just didn't need
# the number until now.
_BOUQUET_BG_TRACK_HALF_HEIGHT = 3.5
_BOUQUET_BG_PCT_CLEARANCE = 6
_BOUQUET_BG_LINE_HEIGHT = 1.2
# row_top/row_center/pct_top are each independently rounded to whole pixels (this file's own
# "precomputed pixel, not template arithmetic" convention), which can each lose up to ~0.5px
# in the direction that shrinks the gap this budget is trying to guarantee -- this slack
# absorbs that compounding rounding error so the *exact* (pre-rounding) margin computed below
# survives being rounded, rather than the budget being exactly zero and rounding tipping it
# negative (found via the parametrized regression test at n=6, this app's tightest real case).
_BOUQUET_BG_ROUNDING_SLACK = 2


def _round_half_up(x: float) -> int:
    """Python's builtin round() breaks an exact .5 tie by rounding to the nearest *even*
    integer (banker's rounding) -- invisible for a one-off value, but every row_center below
    is one term in a sequence of N evenly-spaced coordinates
    (_BOUQUET_GROUPED_ROWS_TOP + (i+0.5)*row_height), and whenever row_height itself happens
    to be a whole number -- true today only at n=6, this app's real Åldersgrupp breakdown:
    (852-414)/6 = 73.0 exactly -- the `+row_height/2` term lands EVERY row exactly on a .5
    boundary, and since 73 is odd, each successive row's target value's integer part flips
    parity (450.5, 523.5, 596.5, ...), so round()'s x.5->nearest-even rule alternates which
    way it rounds from one row to the next. The result: consecutive row_center values come
    out 74px, 72px, 74px, 72px, 74px apart instead of a uniform 73px, even though every row's
    own label/track/seam still agree with EACH OTHER (all three still share that row's one
    row_center) -- a "row-to-row" drift, not a within-row one, so it doesn't show up by
    inspecting any single row's geometry, only by diffing consecutive rows' row_center
    against each other (found exactly that way, 2026-09-08, on the real 6-group Åldersgrupp
    screenshot -- see docs/screen-styles.md's session summary of this date). floor(x + 0.5)
    always breaks a tie the same direction, so the row-to-row gap stays exactly row_height
    wide regardless of row_height's own parity."""
    return math.floor(x + 0.5)


def _bouquet_grouped_label_font_sizes(row_height: float) -> tuple[int, int]:
    """(group-name font size, "N svar" font size) for the shared left-hand label column --
    scaled continuously with the row's own height (itself (rows band height) / (group
    count), see the three geometry functions below) rather than fixed, so a 2-row breakdown
    (sex) reads noticeably bigger than a 5-row one (age) instead of both using the same size
    that only really fits the cramped case. Clamped at the low end so even 5 rows never dips
    below this project's own legibility floor for supporting text."""
    name_font = round(min(32, max(20, row_height * 0.16)))
    count_font = round(min(16, max(13, row_height * 0.08)))
    return name_font, count_font


def _bouquet_geometry_boolean_grouped(breakdown: dict) -> dict:
    """Ribbon Rows' boolean generalization: each group becomes one miniature two-color
    ja/nej track (a shrunken version of _bouquet_geometry_boolean's single vine) instead of
    one shared split -- see docs/screen-styles.md's Generalization mockup. Every row is the
    same fixed track width, split at that row's own real yes_pct, with a single seam flower
    colored to whichever side actually won that group (see .bg-seam-nej's hue-rotate filter
    in screen_styles.css -- reusing #bq-blossom's shape, not redefining it, per
    _bouquet_decor.html's own rule). Caller (screen_state()) guards the "nobody's answered
    yet" case; every group in `breakdown` is guaranteed count > 0 (see aggregations._group).

    The two pct labels are lifted clear of the track line itself (`pct_top`, anchored by
    its own bottom edge via the template's `translateY(-100%)`) rather than sharing the
    track's own vertical center -- an earlier version put both on the exact same centerline
    as the 7px track, which put the track visibly through the middle of the digits (found by
    screenshotting the real 6-group Åldersgrupp breakdown, not visible from the geometry
    numbers alone -- see docs/screen-styles.md's session summary for the date this was
    fixed). `pct_font` is therefore also clamped by how much vertical room is actually free
    above the track in half a row, the same "legibility floor first, shrink to fit if the
    floor doesn't clear" pattern multiple_choice_grouped's `bloom_max` already uses below --
    at 6 rows the floor barely clears with the fixed clearance chosen here; a hard-coded
    offset that ignored this would silently start re-overlapping at a 7th group count this
    app doesn't have yet but shouldn't need re-deriving by hand if it ever does.

    `ja_label_x`/`nej_label_x` are `seam_x +/- label_gap`, clamped to the track's own
    [track_left, track_left+track_width] bounds -- unclamped, a group at an extreme yes_pct
    (near 0% or 100%, both legitimate real values: aggregations._aggregate_group's
    `round(100*yes/count, 1)` is exactly 0.0/100.0 whenever every respondent in that group
    answered the same way) would push the *outer* label -- the one on the small-percentage
    side, further from the seam toward that end of the track -- past the track's own end:
    into the stationery frame past the far edge, or back into the label gutter past the near
    edge. No real seeded data reaches that range today (measured span across every real
    boolean x breakdown x group combination: 9.1%-89.3%), so this has no visible trigger yet
    -- clamped anyway since the geometry should be correct at every mathematically possible
    yes_pct, not just the ones this app's current question bank happens to produce. The clamp
    lands the label at the track's own edge nearest the tiny segment (rather than sailing past
    it), which is also exactly right at the true 0%/100% edge cases where that segment is
    literally empty -- e.g. at yes_pct=0, seam_x sits at track_left, and clamping ja_label_x
    to track_left plants the (0%) ja label right at the track's own start rather than off its
    left end."""
    items = list(breakdown.items())
    n = len(items)
    row_height = (_BOUQUET_GROUPED_ROWS_BOTTOM - _BOUQUET_GROUPED_ROWS_TOP) / n
    name_font, count_font = _bouquet_grouped_label_font_sizes(row_height)
    half_row = row_height / 2
    # Legibility floor/ceiling first (same starting point as before), then clamped down if
    # half a row doesn't actually have room for the track's own half-height, the fixed
    # clearance above it, and one line of text at that size -- see docstring above. floor()
    # (not round()) on the space budget so the clamp always rounds towards "definitely still
    # fits", never towards "fits on paper, doesn't after pixel-rounding".
    pct_font = round(min(34, max(22, row_height * 0.20)))
    max_pct_font_by_space = (
        half_row - _BOUQUET_BG_TRACK_HALF_HEIGHT - _BOUQUET_BG_PCT_CLEARANCE - _BOUQUET_BG_ROUNDING_SLACK
    ) / _BOUQUET_BG_LINE_HEIGHT
    pct_font = min(pct_font, max(16, math.floor(max_pct_font_by_space)))
    flower_size = round(min(56, max(32, row_height * 0.36)))
    # Half the clearance kept between the seam flower and each pct label -- flower_size
    # varies with row_height (see above), so a fixed CSS padding around the seam would
    # either leave a gap that's too wide for a small flower or (as first tried) too narrow
    # for a big one, with the flower painting right over the tail of the "ja"/"nej" text.
    # Computed once here and applied per row below instead.
    label_gap = round(flower_size / 2 + 10)

    rows = []
    total_count = 0
    for i, (label, group) in enumerate(items):
        yes_pct = group["yes_pct"]
        nej_pct = round(100 - yes_pct, 1)
        count = group["count"]
        total_count += count
        # _round_half_up, not round() -- see that helper's own docstring for why a plain
        # round() here alternately rounds row_center up/down row-to-row (banker's rounding
        # tie-breaking on an exact .5, hit whenever row_height itself is a whole number).
        row_top = _round_half_up(_BOUQUET_GROUPED_ROWS_TOP + i * row_height)
        row_center = _round_half_up(row_top + row_height / 2)
        seam_x = round(_BOUQUET_GROUPED_TRACK_LEFT + _BOUQUET_GROUPED_TRACK_WIDTH * yes_pct / 100)
        rows.append(
            {
                "label": label,
                "count": count,
                "yes_pct": yes_pct,
                "nej_pct": nej_pct,
                "row_top": row_top,
                "row_center": row_center,
                # floor(), not round() -- see _BOUQUET_BG_ROUNDING_SLACK's comment above --
                # so this only ever rounds towards "further from the track", never closer.
                "pct_top": math.floor(row_center - _BOUQUET_BG_TRACK_HALF_HEIGHT - _BOUQUET_BG_PCT_CLEARANCE),
                "seam_x": seam_x,
                # Clamped to the track's own bounds -- see this function's docstring. At a
                # moderate split neither clamp ever engages (label_gap is small relative to
                # track_width, so seam_x +/- label_gap stays well inside [track_left,
                # track_left+track_width] whenever both segments have real width), but at an
                # extreme split (one segment near-empty) the *unclamped* outer label would sail
                # past the track's own end -- into the stationery frame on the small segment's
                # side, or back into the label gutter on the near-100% side.
                "ja_label_x": max(_BOUQUET_GROUPED_TRACK_LEFT, seam_x - label_gap),
                "nej_label_x": min(_BOUQUET_GROUPED_TRACK_LEFT + _BOUQUET_GROUPED_TRACK_WIDTH, seam_x + label_gap),
                "ja_seg_width": seam_x - _BOUQUET_GROUPED_TRACK_LEFT,
                "nej_seg_left": seam_x,
                "nej_seg_width": _BOUQUET_GROUPED_TRACK_LEFT + _BOUQUET_GROUPED_TRACK_WIDTH - seam_x,
                # >=50 rather than >50 so an exact tie renders as a (barely) ja-colored seam
                # instead of picking arbitrarily -- ties are rare enough that which way this
                # falls doesn't matter, it just needs to be deterministic.
                "winner": "ja" if yes_pct >= 50 else "nej",
            }
        )
    return {
        "kind": "boolean_grouped",
        "count": total_count,
        "rows": rows,
        "track_left": _BOUQUET_GROUPED_TRACK_LEFT,
        "track_width": _BOUQUET_GROUPED_TRACK_WIDTH,
        "label_left": _BOUQUET_GROUPED_LABEL_LEFT,
        "label_width": _BOUQUET_GROUPED_LABEL_WIDTH,
        "name_font_size": name_font,
        "count_font_size": count_font,
        "pct_font_size": pct_font,
        "flower_size": flower_size,
    }


# number_grouped's row content is a fixed-width cluster (flower, the one value, the
# aggregation tag), not a band-spanning track/stem like the other two kinds -- so it is
# deliberately NOT tied to _BOUQUET_GROUPED_TRACK_LEFT, and keeps the exact absolute
# position it was tuned and visually verified at on 2026-09-06. Narrowing the label zone
# (see that constant's own comment) moves the *band* left, which is right for a track that
# fills it; dragging this cluster along with it would only trade the void it currently
# leaves on its right for a bigger one, since the cluster's own width doesn't grow to
# match. number_grouped's own horizontal balance (at 6 groups the cluster measures
# x=600..1131 and then nothing until the frame at 1854) is a separate, unreported design
# question -- see docs/screen-styles.md's session summary of 2026-09-08 -- deliberately not
# changed here rather than redesigned as a side effect of someone else's bug fix.
_BOUQUET_NG_FLOWER_LEFT = 620

# Vertical clearance (px) kept between a bloom's own edge and the label text next to it.
_BOUQUET_MCG_GAP = 5
# A CSS line's rendered height in px per 1px of font-size -- used below to work out how much
# vertical room a label of a given font-size actually needs, since Django templates can't do
# that arithmetic themselves (same "pre-computed pixel, not template arithmetic" convention
# as everywhere else in this file).
_BOUQUET_MCG_LINE_HEIGHT = 1.2
# Horizontal clearance (px, split evenly either side) subtracted from a raw slot's width
# before it's handed to the template as the label's own CSS `width` -- see `slot_width`
# below. Without it, two adjacent labels that each truncate right up to their own slot's
# exact edge would visually touch with zero gap between the ellipses; this keeps a hairline
# of breathing room between them instead, same spirit as _BOUQUET_BG_PCT_CLEARANCE elsewhere
# in this module.
_BOUQUET_MCG_SLOT_GUTTER = 8


def _bouquet_geometry_multiple_choice_grouped(breakdown: dict) -> dict:
    """Ribbon Rows' multiple_choice generalization -- see docs/screen-styles.md's RibbonRows
    mockup. Unlike the single-group fan (_bouquet_geometry_multiple_choice), ordering here
    is left-to-right by rank (highest pct first), not centre-out -- simpler, and reads
    naturally alongside the ja/nej and number rows which are also left-to-right. Every
    option in every group is strung, blossom-to-blossom, along one shared horizontal stem
    per row; only the top-scoring bloom in each row gets a "Label pct%" line above the stem,
    the rest get the same "Label pct%" in smaller type below it -- both always one line, not
    two (an earlier version stacked a name line over a separate pct line for the winner,
    which is exactly the RibbonRows mockup's own layout, but that mockup had roughly double
    this app's real per-row vertical budget -- see the module comment on
    _BOUQUET_GROUPED_ROWS_TOP -- so at 5 rows (AGE) two stacked lines collided with the row
    above/below; one line fits everywhere the real header/footer chrome actually leaves).

    Both bloom diameter and fill-opacity scale with the real pct, sqrt-scaled for diameter
    (so *area*, the thing an eye actually compares between two blooms, tracks pct roughly
    linearly, not diameter) -- the shape of this reverse-engineered from the RibbonRows
    mockup's own annotated `d=.. op=..` SVG comments: every option's pct is normalized
    against `global_max_pct`, the single highest pct anywhere in the whole breakdown (not
    each row's own max), so the one truly best-scoring option across all groups reads as the
    biggest, most saturated bloom on the whole stage and every other bloom is honestly
    smaller/fainter relative to it: opacity = 0.30 + 0.60*(pct/global_max) (fit exactly to
    the mockup's own numbers). The diameter formula itself is *not* copied verbatim, though
    -- seeded 34..100px there against an ~180px-tall row, it would be oversized against this
    app's real ~90px rows at 5 groups; see `bloom_max` below, sized backwards from how much
    room is actually left over once this row's own label text has taken what it needs, so
    text (the thing legibility rules actually care about, see docs/screen-styles.md) always
    wins the space fight over bloom size, not the other way around.

    A genuine tie for the top spot (two options at the same max pct within a row) is common
    with small groups -- both get the winner's "above the stem" treatment, decided by `pct
    == row's own max` rather than `rank == 0`, so a tie doesn't arbitrarily crown only one of
    them.

    Each option's label (`.mcg-winner-label`/`.mcg-other-label`) is `white-space: nowrap` and
    centred on its own bloom, with nothing constraining its rendered width -- a real,
    pre-existing bug (found 2026-09-06, still present after the 2026-09-08 band-widening
    reduced but didn't eliminate it -- see docs/screen-styles.md's "Possible next steps" #2a
    and that date's follow-up-2 session summary): a row's own per-option slot is only
    `track_width / m` wide (`m` = that row's own option count), but real seeded option text
    can be wider than that at low group counts regardless of slot width -- "Något bubbligt och
    alkoholfritt 27,0%" measures ~265px against a ~268px slot even at the roomiest (2-group)
    case, and shrinking the font further would drop below this project's own legibility floor
    (`other_font`/`winner_font`'s own `max(...)` clamps above). Fixed by handing the template
    `slot_width` (this row's own real per-option slot, minus a small shared gutter -- see
    `_BOUQUET_MCG_SLOT_GUTTER`) so the label can be given a CSS `width` and truncated with an
    ellipsis (`text-overflow: ellipsis`) when it doesn't fit, rather than left to overflow into
    a neighboring slot's text -- the standard mechanism for "this text is really this wide, but
    only this much room exists," and the only one of the three considered upfront (ellipsis,
    hide non-winner labels, shrink font further) that doesn't either drop information guests
    can currently read or breach the legibility floor everything else in this diagram already
    respects."""
    items = list(breakdown.items())
    n = len(items)
    row_height = (_BOUQUET_GROUPED_ROWS_BOTTOM - _BOUQUET_GROUPED_ROWS_TOP) / n
    name_font, count_font = _bouquet_grouped_label_font_sizes(row_height)
    global_max_pct = max(pct for _, group in items for pct in group["options_pct"].values())

    # Font sizes are decided first (legibility floors win), then bloom_max is whatever
    # vertical room is left in half a row after the taller (winner) label's own text height
    # and the fixed gap are subtracted -- not the other way around -- so a label can never
    # collide with the row above/below regardless of how many groups there are. See the
    # docstring above for why this is backwards from a "pick a nice bloom size" design.
    half_row = row_height / 2
    winner_font = round(min(30, max(20, row_height * 0.15)))
    other_font = round(min(20, max(15, row_height * 0.11)))
    bloom_max = max(18, 2 * (half_row - _BOUQUET_MCG_GAP - winner_font * _BOUQUET_MCG_LINE_HEIGHT))
    bloom_max = min(bloom_max, 100)  # never comically large just because a breakdown has 2 groups
    bloom_min = max(14, 0.35 * bloom_max)

    rows = []
    total_count = 0
    for i, (label, group) in enumerate(items):
        count = group["count"]
        total_count += count
        # _round_half_up, not round() -- see that helper's own docstring for why a plain
        # round() here alternately rounds row_center up/down row-to-row (banker's rounding
        # tie-breaking on an exact .5, hit whenever row_height itself is a whole number).
        row_top = _round_half_up(_BOUQUET_GROUPED_ROWS_TOP + i * row_height)
        row_center = _round_half_up(row_top + row_height / 2)
        # Stable sort keeps ties in their original (dict-insertion) order, same convention
        # as _rank_podium/_bouquet_geometry_multiple_choice's identical sort call above.
        ranked = sorted(group["options_pct"].items(), key=lambda item: -item[1])
        row_max_pct = ranked[0][1]
        m = len(ranked)
        # This row's own real per-option slot, minus a small shared gutter -- see
        # _BOUQUET_MCG_SLOT_GUTTER's own comment and this function's docstring. Handed to the
        # template as the label's CSS `width` so overlong option text truncates with an
        # ellipsis instead of overflowing into the neighboring slot's label.
        slot_width = max(40, round(_BOUQUET_GROUPED_TRACK_WIDTH / m - _BOUQUET_MCG_SLOT_GUTTER))
        options = []
        for rank_index, (opt_label, pct) in enumerate(ranked):
            x = round(_BOUQUET_GROUPED_TRACK_LEFT + _BOUQUET_GROUPED_TRACK_WIDTH * (rank_index + 0.5) / m)
            frac = pct / global_max_pct if global_max_pct else 0.0
            bloom_size = round(bloom_min + (bloom_max - bloom_min) * math.sqrt(frac))
            bloom_opacity = round(0.30 + 0.60 * frac, 2)
            bloom_top = row_center - bloom_size / 2
            bloom_bottom = row_center + bloom_size / 2
            options.append(
                {
                    "label": opt_label,
                    "pct": pct,
                    "rank": rank_index + 1,
                    "is_winner": pct == row_max_pct,
                    "x": x,
                    "bloom_size": bloom_size,
                    "bloom_opacity": bloom_opacity,
                    # Bottom-anchored (text grows upward) for the winner's label above the
                    # bloom; top-anchored (text grows downward) for everyone else's below it
                    # -- same convention as the single-group diagrams' own .mc-pct/.mc-label.
                    "winner_label_top": round(bloom_top - _BOUQUET_MCG_GAP),
                    "other_label_top": round(bloom_bottom + _BOUQUET_MCG_GAP),
                }
            )
        rows.append(
            {
                "label": label,
                "count": count,
                "row_top": row_top,
                "row_center": row_center,
                "slot_width": slot_width,
                "options": options,
            }
        )
    return {
        "kind": "multiple_choice_grouped",
        "count": total_count,
        "rows": rows,
        "track_left": _BOUQUET_GROUPED_TRACK_LEFT,
        "track_width": _BOUQUET_GROUPED_TRACK_WIDTH,
        "label_left": _BOUQUET_GROUPED_LABEL_LEFT,
        "label_width": _BOUQUET_GROUPED_LABEL_WIDTH,
        "name_font_size": name_font,
        "count_font_size": count_font,
        "winner_font_size": winner_font,
        "other_font_size": other_font,
    }


def _bouquet_geometry_number_grouped(breakdown: dict) -> dict:
    """Ribbon Rows' number generalization -- see docs/screen-styles.md's Generalization
    mockup. Each row repeats the single-group diagram's own "one flower, one honest number"
    idea (_bouquet_geometry_number) rather than inventing any shared scale/axis across
    groups -- NUMBER questions still have no declared min/max (see that function's
    docstring), and that's just as true per-group as it is overall, so there's still nothing
    real to plot a bar or gauge against. The `aggregation` label is the same for every row
    (one BigScreenState.aggregation setting applies to the whole reveal), so it's computed
    once and returned at the top level rather than repeated per row."""
    items = list(breakdown.items())
    n = len(items)
    row_height = (_BOUQUET_GROUPED_ROWS_BOTTOM - _BOUQUET_GROUPED_ROWS_TOP) / n
    name_font, count_font = _bouquet_grouped_label_font_sizes(row_height)
    flower_size = round(min(92, max(40, row_height * 0.5)))
    value_font_size = round(min(92, max(38, row_height * 0.46)))
    tag_font_size = round(min(20, max(13, row_height * 0.09)))

    rows = []
    total_count = 0
    aggregation = None
    for i, (label, group) in enumerate(items):
        count = group["count"]
        total_count += count
        aggregation = group["aggregation"]
        # _round_half_up, not round() -- see that helper's own docstring for why a plain
        # round() here alternately rounds row_center up/down row-to-row (banker's rounding
        # tie-breaking on an exact .5, hit whenever row_height itself is a whole number).
        row_top = _round_half_up(_BOUQUET_GROUPED_ROWS_TOP + i * row_height)
        row_center = _round_half_up(row_top + row_height / 2)
        rows.append(
            {
                "label": label,
                "count": count,
                "value": group["value"],
                "row_top": row_top,
                "row_center": row_center,
            }
        )
    aggregation = aggregation or BigScreenState.Aggregation.AVG
    return {
        "kind": "number_grouped",
        "count": total_count,
        "rows": rows,
        "label_left": _BOUQUET_GROUPED_LABEL_LEFT,
        "label_width": _BOUQUET_GROUPED_LABEL_WIDTH,
        "flower_left": _BOUQUET_NG_FLOWER_LEFT,
        # Fixed offsets past the flower/value, generous enough for this style's biggest
        # possible flower (see flower_size's own clamp above) and a 3-digit-plus-decimal
        # value at this style's biggest possible value_font_size, so they never collide
        # regardless of which group count/aggregation actually rendered.
        "value_left": _BOUQUET_NG_FLOWER_LEFT + 130,
        "tag_left": _BOUQUET_NG_FLOWER_LEFT + 130 + 320,
        "name_font_size": name_font,
        "count_font_size": count_font,
        "flower_size": flower_size,
        "value_font_size": value_font_size,
        "tag_font_size": tag_font_size,
        "aggregation_label": dict(BigScreenState.Aggregation.choices)[aggregation],
    }


def screen_state(request):
    state = BigScreenState.load()
    breakdown = None
    effective_style = BigScreenState.Style.GENERIC
    podium_rank1 = podium_rank2 = podium_rank3 = None
    podium_rest = None
    podium_count = podium_question_font_size = None
    bouquet = None
    asking_count = None

    # style_is_compatible() only looks at state.question's type + state.breakdown, neither
    # of which differs between "still asking" and "revealed" -- so this one check, hoisted
    # above the revealed/not-revealed split below, covers both (added when the asking state
    # grew its own Podium/Bouquet chrome -- see PLAN.md "Big-screen design exploration",
    # "show the question itself styled ... before reveal"). It's necessary but not
    # sufficient for the revealed branches below: PODIUM/BOUQUET can still be compatible in
    # *shape* (right question type + breakdown) but have no actual data yet (nobody's
    # answered), which those branches detect and downgrade to GENERIC themselves, same as
    # before this change.
    if state.question and style_is_compatible(state.style, state.question, state.breakdown):
        effective_style = state.style

    if effective_style == BigScreenState.Style.PODIUM:
        # Same stepped, character-count heuristic Podium's reveal marquee has always used
        # (fixed 1600px-wide plaque, not a responsive page -- see the function's own
        # docstring) -- computed here, ahead of the revealed/not-revealed split, since the
        # asking-state marquee (_podium_asking.html) needs the identical sizing and the
        # question text doesn't change between asking and revealed.
        podium_question_font_size = _podium_question_font_size(state.question.text_sv)

    if state.question and not state.revealed:
        # Bouquet's asking placeholder shows a live "N svar hittills" count while guests are
        # still answering (see screen_styles/_bouquet_asking.html) -- Podium's asking state
        # doesn't need one, but it's cheap enough to just always compute it here rather than
        # branch on style first. Same underlying number as the revealed aggregations'
        # agg["count"]/podium_count above, just flat (no breakdown grouping exists -- or
        # matters -- before reveal).
        asking_count = Response.objects.filter(question=state.question).count()

    elif state.revealed and state.question:
        breakdown = compute_breakdown(state.question, state.breakdown, state.aggregation, state.aggregation_threshold)

        if effective_style == BigScreenState.Style.PODIUM:
            ranked = _rank_podium(breakdown)
            if ranked:
                # Physical podium has exactly 3 slots (see screen_styles/_podium.html);
                # rank 4+ goes in a chip row below that lays out with plain flexbox, so
                # it takes any remaining count gracefully.
                top3, podium_rest = ranked[:3], ranked[3:]
                podium_rank1 = top3[0] if len(top3) > 0 else None
                podium_rank2 = top3[1] if len(top3) > 1 else None
                podium_rank3 = top3[2] if len(top3) > 2 else None
                podium_count = next(iter(breakdown.values()))["count"]
            else:  # nobody's answered yet -> fall back to GENERIC's "Inga svar än."
                effective_style = BigScreenState.Style.GENERIC
        elif effective_style == BigScreenState.Style.BOUQUET:
            if state.breakdown == BigScreenState.Breakdown.OVERALL:
                group = next(iter(breakdown.values()))
                # Three diagram types, one per Question.Type -- see
                # _bouquet_geometry_boolean/_multiple_choice/_number's docstrings for why
                # each looks the way it does. Each branch's own "nobody's answered yet"
                # guard falls back to GENERIC's "Inga svar än." instead of rendering a
                # broken/empty botanical diagram.
                if state.question.type == Question.Type.BOOLEAN and group["yes_pct"] is not None:
                    bouquet = _bouquet_geometry_boolean(group["yes_pct"], group["count"])
                elif state.question.type == Question.Type.MULTIPLE_CHOICE and group["options_pct"]:
                    bouquet = _bouquet_geometry_multiple_choice(group["options_pct"], group["count"])
                elif state.question.type == Question.Type.NUMBER and group["value"] is not None:
                    bouquet = _bouquet_geometry_number(group["value"], group["aggregation"], group["count"])
                else:
                    effective_style = BigScreenState.Style.GENERIC
            elif breakdown:
                # Their grouped ("Ribbon Rows") siblings -- one row per group instead of
                # one shared diagram -- see _bouquet_geometry_*_grouped's docstrings.
                # compute_breakdown() only ever includes a group that has at least one
                # response (aggregations._group), so every group here has count > 0; the
                # `elif breakdown:` guard above is only about the whole dict being empty
                # (literally nobody has answered this question yet).
                if state.question.type == Question.Type.BOOLEAN:
                    bouquet = _bouquet_geometry_boolean_grouped(breakdown)
                elif state.question.type == Question.Type.MULTIPLE_CHOICE:
                    bouquet = _bouquet_geometry_multiple_choice_grouped(breakdown)
                elif state.question.type == Question.Type.NUMBER:
                    bouquet = _bouquet_geometry_number_grouped(breakdown)
            else:  # nobody's answered yet -> fall back to GENERIC, same spirit as above
                effective_style = BigScreenState.Style.GENERIC

    return render(
        request,
        "pulse/_screen_state.html",
        {
            "state": state,
            "breakdown": breakdown,
            "effective_style": effective_style,
            "podium_rank1": podium_rank1,
            "podium_rank2": podium_rank2,
            "podium_rank3": podium_rank3,
            "podium_rest": podium_rest,
            "podium_count": podium_count,
            "podium_question_font_size": podium_question_font_size,
            "bouquet": bouquet,
            "asking_count": asking_count,
        },
    )


# ---------------------------------------------------------------------------
# Big-screen redesign concepts ("/screen/concepts/…") — 10 frozen mockups from
# the design exploration (see PLAN.md), each a fixed 1920x1080 layout with one
# real reception-data snapshot baked in at author time. Deliberately NOT wired
# to BigScreenState/compute_breakdown/htmx polling like screen_display() above
# -- they're for comparing the 10 visual directions on a real device (a design
# canvas artboard is awkward to view on a phone), not a live host tool. Only
# whichever direction gets picked is worth the work of making it data-driven.
# ---------------------------------------------------------------------------

SCREEN_CONCEPT_TITLES = {
    1: "Broadsheet",
    2: "Highscore",
    3: "Bouquet",
    4: "Fika Party",
    5: "Breaking Pulse",
    6: "Gauge Cluster",
    7: "Podium",
    8: "Chalkboard Café",
    9: "Dansgolvet",
    10: "Root Terminal",
}


def screen_concepts_index(request):
    concepts = [(n, f"{n} · {title}") for n, title in SCREEN_CONCEPT_TITLES.items()]
    return render(request, "pulse/screen_concepts_index.html", {"concepts": concepts})


def screen_concept(request, n):
    title = SCREEN_CONCEPT_TITLES.get(n)
    if title is None:
        raise Http404(f"no screen concept #{n}")
    return render(request, f"pulse/screen_concepts/screen{n}.html", {"concept_title": f"{n} · {title}"})
