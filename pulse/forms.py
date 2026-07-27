from django import forms

from .models import Respondent


class RespondentForm(forms.ModelForm):
    class Meta:
        model = Respondent
        fields = ["age_bucket", "sex", "side", "relation"]
