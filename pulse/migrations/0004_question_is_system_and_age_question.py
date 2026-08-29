# Adds Question.is_system (marks a question as host/system-managed -- guests never see or
# answer it through the normal questionnaire flow, see Question.is_system's own docstring in
# pulse/models.py) and creates exactly one such question: a NUMBER question representing exact
# guest age. Making age a normal LIVE NUMBER Question -- rather than a parallel demographic-stats
# system -- means it flows through the existing compute_breakdown()/screen_control/
# _screen_state.html machinery completely unmodified: the host can already pick "average age by
# side" or "highest age" from the existing dropdowns with zero new template branches. See
# PLAN.md "Generic stats engine".
#
# This app already ran once for a real event (see 0002_replace_age_bucket_with_age's own
# commentary) -- so, like that migration, this one can't assume an empty respondent table. Every
# *existing* Respondent gets a backfilled Response to the new system question, built from their
# already-exact `age` (no lossy midpoint guessing needed this time, unlike 0002 -- age is already
# the real number). Guests who register after this migration get the same Response created
# automatically by create_identity() (pulse/views.py) at signup time instead; the backfill loop
# below explicitly skips anyone who already has one, so this stays idempotent if it's ever run
# again against a partially-migrated database.
#
# Reverse: deletes the Response rows against the system question first (they FK to it), then the
# question itself, then drops the column. This only ever touches the one row this migration
# itself get_or_create's (matched by is_system+type, not text) -- any other NUMBER question a
# host has separately authored is untouched in either direction.
from django.db import migrations, models

SYSTEM_AGE_QUESTION_TEXT = "Hur gammal är du?"


def create_system_age_question_and_backfill(apps, schema_editor):
    Question = apps.get_model("pulse", "Question")
    Response = apps.get_model("pulse", "Response")
    Respondent = apps.get_model("pulse", "Respondent")

    question, _ = Question.objects.get_or_create(
        is_system=True,
        type="number",
        defaults={
            "text_sv": SYSTEM_AGE_QUESTION_TEXT,
            "status": "live",
            "source": "host",
        },
    )

    already_answered = Response.objects.filter(question=question).values_list("respondent_id", flat=True)
    for respondent in Respondent.objects.exclude(id__in=already_answered):
        Response.objects.create(respondent=respondent, question=question, answer={"value": respondent.age})


def remove_system_age_question(apps, schema_editor):
    Question = apps.get_model("pulse", "Question")
    Response = apps.get_model("pulse", "Response")
    for question in Question.objects.filter(is_system=True, type="number"):
        Response.objects.filter(question=question).delete()
        question.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("pulse", "0003_alter_respondent_relation"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="is_system",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(create_system_age_question_and_backfill, remove_system_age_question),
    ]
