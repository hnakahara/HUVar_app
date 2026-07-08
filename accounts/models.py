from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


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
    # --- API 利用上限（トークン=ユーザーごと。月初に残数を上限へ自動リセット） ---
    # API 単一解析(classify)の月あたり実行上限
    api_single_monthly_limit = models.PositiveIntegerField(
        default=100,
        help_text="API 単一解析(classify)の月あたり実行回数の上限。",
    )
    # API 単一解析(classify)の当月残り回数（admin で自由に編集可）
    api_single_remaining = models.PositiveIntegerField(
        default=100,
        help_text="API 単一解析(classify)の当月残り回数。月初に上限へ自動リセット。",
    )
    # API バッチ(jobs)の月あたり実行上限（ユーザーごとに変更可）
    api_batch_monthly_limit = models.PositiveIntegerField(
        default=5,
        help_text="API バッチ(jobs)の月あたり実行回数の上限。",
    )
    # API バッチ(jobs)の当月残り回数（admin で自由に編集可）
    api_batch_remaining = models.PositiveIntegerField(
        default=5,
        help_text="API バッチ(jobs)の当月残り回数。月初に上限へ自動リセット。",
    )
    # 残数を管理している対象月（"YYYY-MM"）。月が変われば残数をリセットする。
    api_usage_period = models.CharField(
        max_length=7,
        blank=True,
        default="",
        help_text="API 残数を管理している対象月（YYYY-MM）。内部管理用。",
    )
    # Web バッチ(VCF)の月あたり実行上限（ユーザーごとに変更可）
    web_batch_monthly_limit = models.PositiveIntegerField(
        default=50,
        help_text="Web バッチ(VCF)の月あたり実行回数の上限。",
    )

    @property
    def is_administrator(self) -> bool:
        return self.is_superuser or self.role == self.Role.ADMINISTRATOR

    def reset_api_usage_if_new_period(self) -> bool:
        """対象月が変わっていれば残数を上限へリセットする。

        呼び出し側で save() すること（消費処理と同一トランザクション内で使う）。
        リセットした場合は True を返す。
        """
        period = timezone.now().strftime("%Y-%m")
        if self.api_usage_period != period:
            self.api_usage_period = period
            self.api_single_remaining = self.api_single_monthly_limit
            self.api_batch_remaining = self.api_batch_monthly_limit
            return True
        return False


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
