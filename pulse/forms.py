from django import forms

from .models import Respondent


class RespondentForm(forms.ModelForm):
    class Meta:
        model = Respondent
        fields = ["age", "sex", "side", "relation"]
