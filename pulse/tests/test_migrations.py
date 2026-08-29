import importlib

import pytest
from django.apps import apps as django_apps

from pulse.models import Question, Respondent, Response

pytestmark = pytest.mark.django_db

# Migration filenames start with a digit, so they can't be imported with a normal `import`
# statement (not a valid Python identifier) -- importlib.import_module works fine since it just
# takes a dotted string, no different from how Django's own migration loader gets at these.
_migration = importlib.import_module("pulse.migrations.0004_question_is_system_and_age_question")


def test_backfill_creates_exactly_one_system_age_question():
    # The normal test-DB setup already ran this migration once (against an empty table) to
    # build the schema -- delete that row first so this test exercises the "no system question
    # yet" starting state the migration is actually written for, not the idempotent no-op path.
    Question.objects.filter(is_system=True).delete()
    Respondent.objects.create(age=25, sex="female", side="bride", relation="friend")
    Respondent.objects.create(age=61, sex="male", side="groom", relation="family")

    _migration.create_system_age_question_and_backfill(django_apps, None)

    questions = Question.objects.filter(is_system=True)
    assert questions.count() == 1
    question = questions.get()
    assert question.type == Question.Type.NUMBER
    assert question.status == Question.Status.LIVE
    assert question.source == Question.Source.HOST


def test_backfill_creates_a_response_per_pre_existing_respondent():
    Question.objects.filter(is_system=True).delete()
    r1 = Respondent.objects.create(age=25, sex="female", side="bride", relation="friend")
    r2 = Respondent.objects.create(age=61, sex="male", side="groom", relation="family")

    _migration.create_system_age_question_and_backfill(django_apps, None)

    question = Question.objects.get(is_system=True)
    responses = Response.objects.filter(question=question)
    assert responses.count() == 2
    assert responses.get(respondent=r1).answer == {"value": 25}
    assert responses.get(respondent=r2).answer == {"value": 61}


def test_backfill_is_idempotent_and_skips_already_answered_respondents():
    respondent = Respondent.objects.create(age=40, sex="male", side="both", relation="plus_one")

    # Run twice -- once more than the test-DB setup already did -- to prove re-running against a
    # partially-migrated database doesn't create a second system question or a duplicate Response
    # (which would violate the one_response_per_question constraint).
    _migration.create_system_age_question_and_backfill(django_apps, None)
    _migration.create_system_age_question_and_backfill(django_apps, None)

    assert Question.objects.filter(is_system=True).count() == 1
    question = Question.objects.get(is_system=True)
    assert Response.objects.filter(question=question, respondent=respondent).count() == 1
