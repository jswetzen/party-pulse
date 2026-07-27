import uuid

from django.db import models


class Respondent(models.Model):
    """One guest identity. No accounts — the guest app maps an opaque
    localStorage token 1:1 to a Respondent; a device can hold several
    (shared phones), switched by a locally-stored alias never sent here."""

    class AgeBucket(models.TextChoices):
        TWENTIES = "20s", "20-talet"
        THIRTIES = "30s", "30-talet"
        FORTIES = "40s", "40-talet"
        FIFTY_PLUS = "50+", "50 eller äldre"

    class Sex(models.TextChoices):
        MALE = "male", "Man"
        FEMALE = "female", "Kvinna"

    class Side(models.TextChoices):
        BRIDE = "bride", "Brudens sida"
        GROOM = "groom", "Brudgummens sida"
        BOTH = "both", "Bådas sida"

    class Relation(models.TextChoices):
        FRIEND = "friend", "Vän"
        FAMILY = "family", "Familj"
        PLUS_ONE = "plus_one", "Plus one"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    age_bucket = models.CharField(max_length=8, choices=AgeBucket.choices)
    sex = models.CharField(max_length=8, choices=Sex.choices)
    side = models.CharField(max_length=8, choices=Side.choices)
    relation = models.CharField(max_length=8, choices=Relation.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_side_display()} · {self.get_relation_display()} ({str(self.id)[:8]})"


class Question(models.Model):
    class Type(models.TextChoices):
        BOOLEAN = "boolean", "Ja/Nej"
        MULTIPLE_CHOICE = "multiple_choice", "Flerval"
        NUMBER = "number", "Nummer"

    class Status(models.TextChoices):
        DRAFT = "draft", "Utkast"
        LIVE = "live", "Live"
        ARCHIVED = "archived", "Arkiverad"

    class Source(models.TextChoices):
        HOST = "host", "Värd"
        GUEST_SUGGESTED = "guest_suggested", "Gästförslag"

    class SuggestionReviewStatus(models.TextChoices):
        PENDING = "pending", "Väntar"
        PROMOTED = "promoted", "Godkänd"
        ARCHIVED = "archived", "Avvisad"

    text_sv = models.TextField(verbose_name="fråga")
    type = models.CharField(max_length=20, choices=Type.choices)
    options = models.JSONField(
        blank=True,
        default=list,
        help_text="Svarsalternativ, endast för flerval. Lista av strängar.",
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    # Determines guest-app and host-console ordering; host console exposes
    # drag-to-reorder against this field (see PLAN.md — missing from the
    # original idea's data model, added during refinement).
    order = models.IntegerField(default=0)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.HOST)
    suggested_by_respondent = models.ForeignKey(
        Respondent, null=True, blank=True, on_delete=models.SET_NULL, related_name="suggested_questions"
    )
    suggestion_review_status = models.CharField(
        max_length=10, choices=SuggestionReviewStatus.choices, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "created_at"]

    def __str__(self):
        return self.text_sv[:60]


class Response(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    respondent = models.ForeignKey(Respondent, on_delete=models.CASCADE, related_name="responses")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="responses")
    # Typed answer, shape depends on question.type:
    #   boolean -> {"value": true|false}
    #   multiple_choice -> {"value": "<one of question.options>"}
    #   number -> {"value": <number>}
    # Stored as JSON (not separate typed columns) because the generic
    # aggregation engine (pulse/aggregations.py) reads .answer["value"]
    # uniformly regardless of question type.
    answer = models.JSONField()
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["respondent", "question"], name="one_response_per_question"),
        ]

    def __str__(self):
        return f"{self.respondent_id} -> {self.question_id}"


class BigScreenState(models.Model):
    """Singleton, host-controlled, polled by the /screen display."""

    class Mode(models.TextChoices):
        STATISTICS = "statistics", "Statistik"
        GUESSING_GAME = "guessing_game", "Gissningslek"

    class Breakdown(models.TextChoices):
        OVERALL = "overall", "Alla"
        SEX = "sex", "Kön"
        AGE = "age", "Åldersgrupp"
        SIDE = "side", "Sida"
        RELATION = "relation", "Relation"

    class Aggregation(models.TextChoices):
        AVG = "avg", "Medel"
        MEDIAN = "median", "Median"
        MIN = "min", "Min"
        MAX = "max", "Max"
        COUNT_ABOVE_THRESHOLD = "count_above_threshold", "Antal över tröskel"

    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.STATISTICS)
    question = models.ForeignKey(Question, null=True, blank=True, on_delete=models.SET_NULL)
    breakdown = models.CharField(max_length=10, choices=Breakdown.choices, default=Breakdown.OVERALL)
    aggregation = models.CharField(max_length=25, choices=Aggregation.choices, null=True, blank=True)
    aggregation_threshold = models.FloatField(
        null=True, blank=True, help_text="Tröskelvärde för 'Antal över tröskel'."
    )
    revealed = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Big screen state"
        verbose_name_plural = "Big screen state"

    def save(self, *args, **kwargs):
        # Enforce singleton: id is always 1.
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return f"{self.mode} / {self.breakdown} (revealed={self.revealed})"
