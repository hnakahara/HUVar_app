from django import forms

from .models import AccountRequest


class AccountRequestForm(forms.ModelForm):
    class Meta:
        model = AccountRequest
        fields = ["full_name", "email", "institution", "purpose"]
        widgets = {
            "purpose": forms.Textarea(attrs={"rows": 4}),
        }
