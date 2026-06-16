from django import forms
from django.contrib.auth.validators import UnicodeUsernameValidator

from .models import AccountRequest


class AccountRequestForm(forms.ModelForm):
    email_confirm = forms.EmailField(label="Email (confirm)")

    class Meta:
        model = AccountRequest
        fields = ["full_name", "email", "email_confirm", "institution", "purpose"]
        labels = {
            "full_name": "User name",
        }
        widgets = {
            "purpose": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 承認時にそのままログイン用ユーザー名として使うため、username 制約に合わせる
        self.fields["full_name"].max_length = 150
        self.fields["full_name"].validators = [UnicodeUsernameValidator()]
        self.fields["full_name"].help_text = (
            "150文字以内。半角英数字と @ . + - _ のみ使用できます。"
        )
        # ブラウザの自動補完によるコピペ回避
        self.fields["email_confirm"].widget.attrs["autocomplete"] = "off"

    def clean(self):
        cleaned = super().clean()
        email = cleaned.get("email")
        email_confirm = cleaned.get("email_confirm")
        if email and email_confirm and email != email_confirm:
            self.add_error("email_confirm", "メールアドレスが一致しません。")
        return cleaned
