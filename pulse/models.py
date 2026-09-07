import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

# Age-bucket labels for age -> bucket display, reused by both Respondent.age_bucket_label
# and the aggregation engine (pulse/aggregations.py). Originally these were the fixed
# choices of a guest-facing "which decade are you in" dropdown ("20-talet", "50 eller
# äldre") -- we now capture an exact age instead (see AGE_MIN/AGE_MAX below and the
# age_bucket migration) and derive a label from it at read time. Reworded 2026-09-07:
# the old "<N>-talet" labels read as Swedish calendar decades ("the 1920s"), not age
# brackets, and "50 eller äldre" doesn't read as natural Swedish for a person's own age
# group either -- both replaced with "<lo>-<hi> år" / "Under 20 år" / "60+ år" phrasing.
# Same change also splits what used to be one open-ended "50 eller äldre" bucket into
# "50-59 år" and "60+ år": lumping a 50-year-old parent-of-the-couple in with an
# 80-year-old grandparent under one label was too coarse a breakdown group for the big
# screen. Keyed by the decade's *start* (20, 30, 40, 50) so the lookup is one dict index
# rather than four hardcoded if/elif branches -- under-20 and 60+ are handled separately
# below since they're open-ended, not a single decade.
_DECADE_LABELS = {
    20: "20-29 år",
    30: "30-39 år",
    40: "40-49 år",
    50: "50-59 år",
}
_UNDER_TWENTY_LABEL = "Under 20 år"
_SIXTY_PLUS_LABEL = "60+ år"

# Bounds for the guest-facing exact-age input. Lower bound of 1 (not 0) because "0 years
# old" is never a meaningful self-report at a wedding; upper bound of 119 is generous
# headroom past any plausible guest age without being unbounded (catches fat-finger typos
# like an extra trailing digit).
AGE_MIN = 1
AGE_MAX = 119


def age_bucket_label(age: int) -> str:
    """Map an exact age to its Swedish age-group label, for aggregation/display (see
    PLAN.md "Demographics"). Six buckets: "Under 20 år", "20-29 år", "30-39 år",
    "40-49 år", "50-59 år", "60+ år".

    Buckets below 20 have no equivalent in the old decade-dropdown scheme this replaced
    (the dropdown started at "20-talet") because self-reporting "which decade" only made
    sense once you're solidly in one; an exact age has no such gap, so we need a label
    for it anyway.
    """
    if age < 20:
        return _UNDER_TWENTY_LABEL
    if age >= 60:
        return _SIXTY_PLUS_LABEL
    decade_start = (age // 10) * 10
    return _DECADE_LABELS[decade_start]


class Respondent(models.Model):
    """One guest identity. No accounts — the guest app maps an opaque
    localStorage token 1:1 to a Respondent; a device can hold several
    (shared phones), switched by a locally-stored alias never sent here."""

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
        PLUS_ONE = "plus_one", "Plus en"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Exact age, not a self-reported decade bucket -- see age_bucket_label() above for
    # why, and the 0002 migration for how this replaced the old age_bucket column.
    age = models.PositiveSmallIntegerField(
        validators=[
            MinValueValidator(AGE_MIN, message="Åldern måste vara minst 1 år."),
            MaxValueValidator(AGE_MAX, message="Åldern verkar inte stämma, kolla att du skrev rätt."),
        ]
    )
    sex = models.CharField(max_length=8, choices=Sex.choices)
    side = models.CharField(max_length=8, choices=Side.choices)
    relation = models.CharField(max_length=8, choices=Relation.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def age_bucket_label(self) -> str:
        """Swedish age-group label for this respondent's age, e.g. "30-39 år".
        See module-level age_bucket_label() for the bucketing rule."""
        return age_bucket_label(self.age)

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
    # System-managed questions are never shown in the guest questionnaire and are never
    # answered through the normal answer_question flow -- currently exactly one exists: the
    # NUMBER question every guest answers implicitly at registration time with their own
    # Respondent.age (see create_identity() in pulse/views.py and Question.system_age_question()
    # below). Modelled as a plain BooleanField on an ordinary Question row -- not a separate
    # table/parallel demographic-stats system -- specifically so it's a completely normal LIVE
    # NUMBER question as far as the host console and the generic compute_breakdown() engine are
    # concerned; "average age by side" then needs zero new code in either place. See PLAN.md
    # "Generic stats engine".
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "created_at"]

    @classmethod
    def system_age_question(cls):
        """The one system-managed NUMBER question representing exact guest age. Looked up by
        is_system+type rather than matching on text_sv, so a host editing the question's wording
        in admin (it's guest-invisible, but the host still sees it in the screen_control
        dropdown) can never silently break create_identity()'s auto-answer step. Created by the
        0004 migration; raises Question.DoesNotExist if that migration hasn't run, same as any
        other lookup of required, migration-provisioned data."""
        return cls.objects.get(is_system=True, type=cls.Type.NUMBER)

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

    class Style(models.TextChoices):
        # GENERIC is today's bar-chart _screen_state.html markup, unchanged -- it fits any
        # question/breakdown, which is why it's also the fallback whenever PODIUM/BOUQUET
        # is picked for a combination they don't actually fit (see
        # views.style_is_compatible). PODIUM and BOUQUET came out of the 10-concept design
        # exploration (see PLAN.md "Big-screen design exploration") and are each built
        # around one specific data shape rather than being general-purpose:
        #   PODIUM  -- ranks a single set of labelled scores. Only a real fit for a
        #              multiple-choice question's options (breakdown=OVERALL: a per-group
        #              breakdown would be several separate option distributions, not one
        #              ranked list).
        #   BOUQUET -- one botanical diagram per Question.Type: a boolean split as a
        #              two-ended vine, multiple-choice options as a fanned arrangement (one
        #              stem per option, height = real pct), a number question's aggregated
        #              value as a single centerpiece stem (see views._bouquet_geometry_*).
        #              That trio is breakdown=OVERALL only; every other breakdown gets a
        #              "Ribbon Rows" sibling of each -- one horizontal row per group, added
        #              2026-09-06 (see docs/screen-styles.md's session summary of that date)
        #              -- so BOUQUET is now as breakdown-permissive as GENERIC
        #              (views._bouquet_geometry_*_grouped).
        GENERIC = "generic", "Standard"
        PODIUM = "podium", "Podium"
        BOUQUET = "bouquet", "Bukett"

    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.STATISTICS)
    question = models.ForeignKey(Question, null=True, blank=True, on_delete=models.SET_NULL)
    breakdown = models.CharField(max_length=10, choices=Breakdown.choices, default=Breakdown.OVERALL)
    style = models.CharField(max_length=10, choices=Style.choices, default=Style.GENERIC)
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
