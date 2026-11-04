from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """ロール付きカスタムユーザー。

    新規ユーザーは登録フォーム（accounts:signup）から自己登録できるが、作成された
    アカウントは無効（is_active=False）で、メールアドレス確認 → administrator の承認を
    経て初めて有効になる。administrator が admin で直接作成したユーザーは承認済み扱い。
    """

    class Role(models.TextChoices):
        ADMINISTRATOR = "administrator", "Administrator"
        GENERAL = "general", "General user"

    class ApprovalStatus(models.TextChoices):
        EMAIL_PENDING = "email_pending", "Email not verified"
        PENDING = "pending", "Awaiting approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.GENERAL)
    institution = models.CharField(max_length=255, blank=True)
    # --- 自己登録と承認 ---
    # 既定は承認済み（既存ユーザー・admin で作成したユーザー）。自己登録は
    # email_pending で作成され、確認リンク → pending → administrator が承認/却下する。
    approval_status = models.CharField(
        max_length=20,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.APPROVED,
        help_text="自己登録アカウントの状態。承認・却下はユーザー一覧のアクションから行う"
                  "（本人にメールで通知される）。",
    )
    registration_purpose = models.TextField(
        blank=True, help_text="自己登録時に入力された利用目的。")
    email_verified_at = models.DateTimeField(
        null=True, blank=True, help_text="登録時のメールアドレス確認日時。")
    approval_processed_at = models.DateTimeField(
        null=True, blank=True, help_text="承認または却下した日時。")
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

    @property
    def is_awaiting_approval(self) -> bool:
        return self.approval_status in (self.ApprovalStatus.EMAIL_PENDING,
                                        self.ApprovalStatus.PENDING)

    def save(self, *args, **kwargs):
        # admin の編集画面で承認待ちユーザーを直接「有効」にした場合も状態を揃える
        # （承認アクションを使わない有効化。本人へのメール通知は行われない）。
        if self.is_active and self.is_awaiting_approval:
            self.approval_status = self.ApprovalStatus.APPROVED
            self.approval_processed_at = self.approval_processed_at or timezone.now()
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                    "approval_status", "approval_processed_at"}
        super().save(*args, **kwargs)

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
    """（旧方式）アカウント発行リクエスト。

    自己登録（accounts:signup）導入前の方式。新規の受付は行わず、過去のリクエスト履歴の
    参照用に admin に残している。"""

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
