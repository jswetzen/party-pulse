from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .aggregations import compute_breakdown
from .forms import RespondentForm
from .models import BigScreenState, Question, Respondent, Response

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
    respondent = form.save()
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
    questions = Question.objects.filter(status=Question.Status.LIVE).exclude(id__in=answered_ids)
    return render(
        request,
        "pulse/questionnaire.html",
        {"respondent": respondent, "questions": questions},
    )


@require_POST
def answer_question(request, respondent_id, question_id):
    respondent = get_object_or_404(Respondent, id=respondent_id)
    question = get_object_or_404(Question, id=question_id, status=Question.Status.LIVE)

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
        state.aggregation = request.POST.get("aggregation") or None
        threshold = request.POST.get("aggregation_threshold")
        state.aggregation_threshold = float(threshold) if threshold else None
        state.revealed = request.POST.get("action") == "reveal"
        state.save()
        return redirect("host-screen-control")

    live_questions = Question.objects.filter(status=Question.Status.LIVE)
    return render(
        request,
        "pulse/screen_control.html",
        {"state": state, "live_questions": live_questions},
    )


# ---------------------------------------------------------------------------
# Big screen display ("/screen") — unauthenticated (read-only aggregates
# only, never individual answers — see PLAN.md "Reveal semantics"); relies
# on the venue network being effectively private. Polls every 2s via htmx,
# no websockets/SSE. See PLAN.md "start simple with polling".
# ---------------------------------------------------------------------------


def screen_display(request):
    return render(request, "pulse/screen_display.html")


def screen_state(request):
    state = BigScreenState.load()
    breakdown = None
    if state.revealed and state.question:
        breakdown = compute_breakdown(state.question, state.breakdown, state.aggregation, state.aggregation_threshold)
    return render(request, "pulse/_screen_state.html", {"state": state, "breakdown": breakdown})
