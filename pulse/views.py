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
    see BigScreenState.Style for why PODIUM/BOUQUET each need one specific shape and
    GENERIC (today's bar-chart markup) fits everything. Used by screen_state() below to
    fall back to GENERIC rather than render a style against data it wasn't built for; the
    control form mirrors this client-side (see screen_control.html's script) purely so the
    host doesn't pick a combination that's about to be silently downgraded, but this is the
    check that actually decides what gets rendered."""
    if style == BigScreenState.Style.PODIUM:
        return question.type == Question.Type.MULTIPLE_CHOICE and breakdown == BigScreenState.Breakdown.OVERALL
    if style == BigScreenState.Style.BOUQUET:
        return question.type == Question.Type.BOOLEAN and breakdown == BigScreenState.Breakdown.OVERALL
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


# Bouquet's ja/nej "vine" track spans this fixed box inside the 1920x1080 stage (see
# screen_styles/_bouquet.html); _bouquet_geometry() below places every element in it from
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


def _bouquet_geometry(yes_pct: float, count: int) -> dict:
    nej_pct = round(100 - yes_pct, 1)
    boundary = round(_BOUQUET_TRACK_WIDTH * nej_pct / 100)  # 0..1200, relative to track-wrap
    seam_left = max(0, boundary - _BOUQUET_SEAM_HALF)
    seam_right = min(_BOUQUET_TRACK_WIDTH, boundary + _BOUQUET_SEAM_HALF)
    return {
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


def screen_state(request):
    state = BigScreenState.load()
    breakdown = None
    effective_style = BigScreenState.Style.GENERIC
    podium_rank1 = podium_rank2 = podium_rank3 = None
    podium_rest = None
    podium_count = podium_question_font_size = None
    bouquet = None

    if state.revealed and state.question:
        breakdown = compute_breakdown(state.question, state.breakdown, state.aggregation, state.aggregation_threshold)

        if style_is_compatible(state.style, state.question, state.breakdown):
            if state.style == BigScreenState.Style.PODIUM:
                ranked = _rank_podium(breakdown)
                if ranked:  # nobody's answered yet -> fall back to GENERIC's "Inga svar än."
                    effective_style = state.style
                    # Physical podium has exactly 3 slots (see screen_styles/_podium.html);
                    # rank 4+ goes in a chip row below that lays out with plain flexbox, so
                    # it takes any remaining count gracefully.
                    top3, podium_rest = ranked[:3], ranked[3:]
                    podium_rank1 = top3[0] if len(top3) > 0 else None
                    podium_rank2 = top3[1] if len(top3) > 1 else None
                    podium_rank3 = top3[2] if len(top3) > 2 else None
                    podium_count = next(iter(breakdown.values()))["count"]
                    podium_question_font_size = _podium_question_font_size(state.question.text_sv)
            elif state.style == BigScreenState.Style.BOUQUET:
                group = next(iter(breakdown.values()))
                if group["yes_pct"] is not None:  # same "nobody's answered yet" guard
                    bouquet = _bouquet_geometry(group["yes_pct"], group["count"])
                    effective_style = state.style
            else:
                effective_style = state.style

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
