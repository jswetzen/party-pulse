"""Seed the question bank with a curated set of fun wedding-reception questions.

The question bank itself lives in ``data/questions.json`` next to this file, so
it can be hand-edited without touching any Python. Each JSON object is shaped
``{"text_sv": ..., "type": ..., "options": [...]}`` where ``options`` is only
non-empty for ``multiple_choice`` entries. List order becomes ``Question.order``.

Idempotent: keyed on ``text_sv`` via ``get_or_create``, so re-running never
creates duplicates. Everything is seeded as ``draft`` — the host reviews and
flips individual questions to ``live`` from the Django admin.

Run from the repo root:

    uv run python manage.py seed_questions
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from pulse.models import Question

QUESTIONS_FILE = Path(__file__).resolve().parent / "data" / "questions.json"


class Command(BaseCommand):
    help = "Seed the question bank with curated wedding-reception questions (idempotent)."

    def handle(self, *args, **options):
        questions = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))

        created_count = 0
        existing_count = 0

        for order, entry in enumerate(questions):
            _, created = Question.objects.get_or_create(
                text_sv=entry["text_sv"],
                defaults={
                    "type": entry["type"],
                    "options": entry.get("options", []),
                    "order": order,
                    "status": Question.Status.DRAFT,
                    "source": Question.Source.HOST,
                },
            )
            if created:
                created_count += 1
            else:
                existing_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seedade frågebanken: {created_count} nya, "
                f"{existing_count} fanns redan (totalt {len(questions)} i banken)."
            )
        )
