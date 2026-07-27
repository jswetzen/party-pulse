from django.contrib import admin

from .models import BigScreenState, Question, Respondent, Response


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
    list_display = ("text_sv", "type", "status", "source", "suggestion_review_status", "order")
    list_editable = ("order",)
    list_filter = ("status", "type", "source", "suggestion_review_status")
    search_fields = ("text_sv",)
    ordering = ("order", "created_at")
    actions = [make_live, make_draft, archive_questions]


@admin.register(Respondent)
class RespondentAdmin(admin.ModelAdmin):
    # Counts-only overview per PLAN.md ("respondent/response overview
    # (counts only, sanity check)") — deliberately no per-guest answer
    # drill-down here, that would break the "party anonymous" model.
    list_display = ("id", "age_bucket", "sex", "side", "relation", "response_count", "created_at")
    list_filter = ("age_bucket", "sex", "side", "relation")
    readonly_fields = [f.name for f in Respondent._meta.fields]

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
