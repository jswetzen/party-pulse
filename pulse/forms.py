from django import forms

from .models import EventSettings, Respondent


class RespondentForm(forms.ModelForm):
    class Meta:
        model = Respondent
        fields = ["age", "sex", "side", "relation"]

    def __init__(self, *args, event_settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        settings = event_settings or EventSettings.load()
        # side/relation are blank=True on the model (so a disabled category can be left
        # empty at all) -- Django's ModelForm derives `required` from that, so it defaults
        # every field to optional regardless of EventSettings. Re-require it explicitly
        # when the category is enabled instead, and drop it entirely when it's not.
        if settings.side_enabled:
            self.fields["side"].required = True
        else:
            del self.fields["side"]
        if settings.relation_enabled:
            self.fields["relation"].required = True
        else:
            del self.fields["relation"]
