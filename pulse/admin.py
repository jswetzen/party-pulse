from django.contrib import admin

from .models import BigScreenState, Question, Respondent, Response

# (bucket_value, display_label) pairs matching the boundaries in Respondent.age_bucket_label()
# (pulse/models.py) -- kept as an explicit range table here rather than importing that function,
# since a SimpleListFilter needs both the lookup value and a queryset-filterable (min, max) range
# per bucket, not just a label.
_AGE_BUCKET_RANGES = [
    ("under_20", "Under 20 år", (None, 20)),
    ("20s", "20-29 år", (20, 30)),
    ("30s", "30-39 år", (30, 40)),
    ("40s", "40-49 år", (40, 50)),
    ("50s", "50-59 år", (50, 60)),
    ("60_plus", "60+ år", (60, None)),
]


class AgeBucketFilter(admin.SimpleListFilter):
    # Filters on the real `age` field by the same decade boundaries age_bucket_label() uses
    # for display -- restores the admin filtering the stale "same as before" comment promised,
    # now that age_bucket is a computed method rather than a real field admin.list_filter can
    # point at directly (a real field is required there; a bare method raises Django's
    # admin.E116 check).
    title = "Åldersgrupp"
    parameter_name = "age_bucket"

    def lookups(self, request, model_admin):
        return [(value, label) for value, label, _ in _AGE_BUCKET_RANGES]

    def queryset(self, request, queryset):
        for value, _, (lo, hi) in _AGE_BUCKET_RANGES:
            if self.value() == value:
                if lo is not None:
                    queryset = queryset.filter(age__gte=lo)
                if hi is not None:
                    queryset = queryset.filter(age__lt=hi)
                return queryset
        return queryset


@admin.action(description="Gör live (synlig för gäster + på storbild)")
def make_live(modeladmin, request, queryset):
    # Making a guest-suggested question live IS approving it — set both in one click instead
    # of requiring a separate "promote" action first.
    queryset.filter(suggestion_review_status=Question.SuggestionReviewStatus.PENDING).update(
        suggestion_review_status=Question.SuggestionReviewStatus.PROMOTED
    )
    updated = queryset.update(status=Question.Status.LIVE)
    modeladmin.message_user(request, f"{updated} fråg{'a' if updated == 1 else 'or'} gjorda live.")


@admin.action(description="Gör till utkast")
def make_draft(modeladmin, request, queryset):
    updated = queryset.update(status=Question.Status.DRAFT)
    modeladmin.message_user(request, f"{updated} fråg{'a' if updated == 1 else 'or'} gjorda till utkast.")


@admin.action(description="Arkivera (dölj för gäster)")
def archive_questions(modeladmin, request, queryset):
    # Archiving a pending suggestion IS rejecting it.
    queryset.filter(suggestion_review_status=Question.SuggestionReviewStatus.PENDING).update(
        suggestion_review_status=Question.SuggestionReviewStatus.ARCHIVED
    )
    updated = queryset.update(status=Question.Status.ARCHIVED)
    modeladmin.message_user(request, f"{updated} fråg{'a' if updated == 1 else 'or'} arkiverade.")


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("text_sv", "type", "status", "source", "suggestion_review_status", "order", "is_system")
    list_editable = ("order",)
    list_filter = ("status", "type", "source", "suggestion_review_status", "is_system")
    search_fields = ("text_sv",)
    ordering = ("order", "created_at")
    actions = [make_live, make_draft, archive_questions]
    # is_system is provisioned once by the 0004 migration (see Question.system_age_question())
    # and looked up by that flag, not by text -- read-only here so nobody can accidentally flip
    # a second question to "system" (which create_identity()'s lookup would then find
    # ambiguous) or un-flag the real one via the admin form. Proportionate to this being a
    # private single-household tool, not hardening against an adversarial host.
    #
    # `type` is additionally locked down, but only on the system row itself: system_age_question()
    # looks the row up by (is_system=True, type=NUMBER), so changing `type` away from NUMBER on
    # that one row via the admin change form would silently break create_identity() for every
    # future signup. Ordinary questions keep `type` freely editable.
    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.is_system:
            return ("is_system", "type")
        return ("is_system",)

    def has_delete_permission(self, request, obj=None):
        # The system age question backs every guest registration (create_identity) --
        # deleting it from admin would silently break signups from that point on. Ordinary
        # host/guest-suggested questions are unaffected; this only blocks is_system rows, and
        # applies to both the single-object delete button and the bulk "Delete selected"
        # action (Django's admin checks has_delete_permission per object for both).
        if obj is not None and obj.is_system:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Respondent)
class RespondentAdmin(admin.ModelAdmin):
    # Counts-only overview per PLAN.md ("respondent/response overview
    # (counts only, sanity check)") — deliberately no per-guest answer
    # drill-down here, that would break the "party anonymous" model.
    list_display = ("id", "age", "age_bucket", "sex", "side", "relation", "response_count", "created_at")
    # Filtering on the raw "age" would give one filter option per distinct age (useless
    # with a small guest list); filter on the decade bucket instead, same as before, via the
    # custom AgeBucketFilter above (age_bucket itself is a computed @admin.display method, not
    # a real field, so it can't go directly in list_filter).
    list_filter = ("sex", "side", "relation", AgeBucketFilter)
    readonly_fields = [f.name for f in Respondent._meta.fields]

    @admin.display(description="Åldersgrupp")
    def age_bucket(self, obj):
        return obj.age_bucket_label

    @admin.display(description="Svar")
    def response_count(self, obj):
        return obj.responses.count()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Response)
class ResponseAdmin(admin.ModelAdmin):
    list_display = ("question", "respondent", "answer", "answered_at")
    list_filter = ("question",)
    readonly_fields = [f.name for f in Response._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(BigScreenState)
class BigScreenStateAdmin(admin.ModelAdmin):
    list_display = ("mode", "question", "breakdown", "aggregation", "revealed")

    def has_add_permission(self, request):
        # Singleton — only ever edit the one row created by BigScreenState.load().
        return not BigScreenState.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
