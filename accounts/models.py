from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """ロール付きカスタムユーザー。新規ユーザーは自己登録不可（administrator が作成）。"""

    class Role(models.TextChoices):
        ADMINISTRATOR = "administrator", "Administrator"
        GENERAL = "general", "General user"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.GENERAL)
    institution = models.CharField(max_length=255, blank=True)
    # MFA 免除（レビュー用）。administrator が admin で設定。True の間は MFA 強制を回避。
    mfa_exempt = models.BooleanField(
        default=False,
        help_text="レビュー等のため MFA を免除する（セキュリティ上、通常は無効）。",
    )
    # API バッチ(jobs)の月あたり実行上限（ユーザーごとに変更可）
    api_batch_monthly_limit = models.PositiveIntegerField(
        default=5,
        help_text="API バッチ(jobs)の月あたり実行回数の上限。",
    )
    # Web バッチ(VCF)の月あたり実行上限（ユーザーごとに変更可）
    web_batch_monthly_limit = models.PositiveIntegerField(
        default=50,
        help_text="Web バッチ(VCF)の月あたり実行回数の上限。",
    )

    @property
    def is_administrator(self) -> bool:
        return self.is_superuser or self.role == self.Role.ADMINISTRATOR


class AccountRequest(models.Model):
    """アカウント発行リクエスト。自己登録の代わりに送信され、administrator が処理する。"""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    full_name = models.CharField(max_length=255)
    email = models.EmailField()
    institution = models.CharField(max_length=255)
    purpose = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.full_name} <{self.email}> ({self.status})"


class TokenRequest(models.Model):
    """API トークン発行リクエスト。administrator が承認時にトークンを発行する。"""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    user_name = models.CharField(max_length=150)
    email = models.EmailField()
    institution = models.CharField(max_length=255, blank=True)
    intended_use = models.TextField(help_text="API の利用目的・想定用途")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.user_name} <{self.email}> ({self.status})"
