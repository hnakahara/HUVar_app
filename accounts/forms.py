from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.utils.translation import gettext_lazy as _

from .models import TokenRequest, User


class SignUpForm(UserCreationForm):
    """自己登録フォーム。作成されるアカウントは無効で、メール確認と管理者の承認が必要。"""

    email = forms.EmailField(label=_("メールアドレス"))
    email_confirm = forms.EmailField(label=_("メールアドレス（確認）"))
    # ボットよけのハニーポット（人間には非表示。入力があれば拒否）
    website = forms.CharField(required=False, widget=forms.HiddenInput, label="")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "institution", "registration_purpose")
        labels = {
            "institution": _("所属"),
            "registration_purpose": _("利用目的"),
        }
        widgets = {
            "registration_purpose": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["institution"].required = True
        self.fields["registration_purpose"].required = True
        self.fields["username"].help_text = _(
            "150文字以内。半角英数字と @ . + - _ のみ使用できます。ログイン時に使用します。")
        # ブラウザの自動補完によるコピペ回避
        self.fields["email_confirm"].widget.attrs["autocomplete"] = "off"
        self.order_fields(["username", "email", "email_confirm", "institution",
                           "registration_purpose", "password1", "password2", "website"])

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                _("このメールアドレスは既に登録されています。"), code="duplicate_email")
        return email

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website"):
            raise forms.ValidationError(_("送信できませんでした。"))
        email = cleaned.get("email")
        email_confirm = (cleaned.get("email_confirm") or "").strip()
        if email and email_confirm and email.lower() != email_confirm.lower():
            self.add_error("email_confirm", _("メールアドレスが一致しません。"))
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.role = User.Role.GENERAL
        user.is_active = False
        user.is_staff = False
        user.approval_status = User.ApprovalStatus.EMAIL_PENDING
        if commit:
            user.save()
        return user


class ApprovalAwareAuthenticationForm(AuthenticationForm):
    """承認前の自己登録アカウントには「承認待ち」等の理由を表示するログインフォーム。

    無効ユーザーは認証バックエンドで拒否される（通常の「ユーザー名またはパスワードが
    違います」になる）。パスワードが正しい場合に限り、状態に応じた案内に置き換える
    （パスワードを知らない第三者には登録の有無も状態も分からない）。django-axes で
    ロックアウト中のリクエストでは判定しない。"""

    _STATUS_MESSAGES = {
        User.ApprovalStatus.EMAIL_PENDING: _(
            "メールアドレスの確認が完了していません。登録時に送信された確認メールの"
            "リンクを開いてください。"),
        User.ApprovalStatus.PENDING: _(
            "このアカウントは管理者の承認待ちです。承認されるとメールでお知らせします。"),
        User.ApprovalStatus.REJECTED: _("このアカウントは承認されませんでした。"),
    }

    def clean(self):
        try:
            return super().clean()
        except forms.ValidationError:
            msg = self._status_message()
            if msg is not None:
                raise forms.ValidationError(msg, code="not_approved") from None
            raise

    def _status_message(self):
        if self.request is not None and getattr(self.request, "axes_locked_out", False):
            return None
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if not username or not password:
            return None
        user_model = get_user_model()
        try:
            user = user_model._default_manager.get_by_natural_key(username)
        except user_model.DoesNotExist:
            return None
        if user.is_active or not user.check_password(password):
            return None
        return self._STATUS_MESSAGES.get(user.approval_status)


class TokenRequestForm(forms.ModelForm):
    """API トークン発行リクエストフォーム。メール確認入力つき。"""

    email_confirm = forms.EmailField(label="Email (confirm)")
    # ボットよけのハニーポット（人間には非表示。入力があれば拒否）
    website = forms.CharField(required=False, widget=forms.HiddenInput, label="")

    class Meta:
        model = TokenRequest
        fields = ["user_name", "email", "email_confirm", "institution", "intended_use"]
        labels = {
            "user_name": "User name",
            "intended_use": "Intended use",
        }
        widgets = {
            "intended_use": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email_confirm"].widget.attrs["autocomplete"] = "off"

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("website"):
            raise forms.ValidationError("送信できませんでした。")
        email = cleaned.get("email")
        email_confirm = cleaned.get("email_confirm")
        if email and email_confirm and email != email_confirm:
            self.add_error("email_confirm", "メールアドレスが一致しません。")
        return cleaned
