import secrets

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from .models import AccountRequest, TokenRequest, User
from .notifications import notify_user

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    # Web と API を別カウントで表示。上限・残り回数は一覧から直接編集できる。
    list_display = ("username", "email", "role", "institution", "mfa_exempt",
                    "n_login", "n_explain", "n_classify",
                    "n_api_classify", "n_api_jobs",
                    "api_single_monthly_limit", "api_single_remaining",
                    "api_batch_monthly_limit", "api_batch_remaining",
                    "approval_status", "is_active", "is_staff")
    # 一覧上で上限・残り回数を直接編集可能にする
    list_editable = ("api_single_monthly_limit", "api_single_remaining",
                     "api_batch_monthly_limit", "api_batch_remaining")
    list_filter = ("approval_status", "role", "mfa_exempt", "is_active", "is_staff")
    actions = ["approve_registrations", "reject_registrations"]
    readonly_fields = ("email_verified_at", "approval_processed_at",
                       "api_token_last_used_at", "api_token_last_ip")
    fieldsets = UserAdmin.fieldsets + (
        ("HUVar", {"fields": ("role", "institution", "mfa_exempt")}),
        ("自己登録・承認", {
            "fields": ("approval_status", "registration_purpose",
                       "email_verified_at", "approval_processed_at"),
            "description": "承認・却下はユーザー一覧で対象を選び、アクション"
                           "「選択した登録を承認する／却下する」から行います（本人にメールで通知）。",
        }),
        ("API 利用上限（月次・トークンごと）", {
            "fields": ("api_single_monthly_limit", "api_single_remaining",
                       "api_batch_monthly_limit", "api_batch_remaining",
                       "api_usage_period", "web_batch_monthly_limit",
                       "api_token_last_used_at", "api_token_last_ip"),
            "description": "上限・残り回数は自由に編集できます。残り回数は毎月初回の "
                           "API 呼び出し時に上限へ自動リセットされます。",
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)

        def _cnt(action):
            return Count("audit_logs", filter=Q(audit_logs__action=action), distinct=True)

        return qs.annotate(
            _n_login=_cnt("login"),
            _n_explain=_cnt("single_analyze"),       # Web 単一(explain)
            _n_classify=_cnt("batch_submit"),        # Web バッチ(classify)
            _n_api_classify=_cnt("api_classify"),    # API 単一(classify)
            _n_api_jobs=_cnt("api_batch_submit"),    # API バッチ(jobs)
        )

    @admin.action(description="選択した登録を承認する（アカウントを有効化し本人に通知）")
    def approve_registrations(self, request, queryset):
        from analysis.models import AuditLog
        login_url = request.build_absolute_uri(reverse("accounts:login"))
        for user in queryset:
            if user.approval_status == User.ApprovalStatus.APPROVED and user.is_active:
                self.message_user(request, f"{user.username}: 既に承認済みです。",
                                  level=messages.INFO)
                continue
            if user.approval_status == User.ApprovalStatus.EMAIL_PENDING:
                self.message_user(
                    request, f"{user.username}: メールアドレスの確認が済んでいないため"
                             "承認しませんでした。", level=messages.WARNING)
                continue
            user.is_active = True
            user.approval_status = User.ApprovalStatus.APPROVED
            user.approval_processed_at = timezone.now()
            user.save(update_fields=["is_active", "approval_status", "approval_processed_at"])
            AuditLog.objects.create(user=request.user, action="user_approved",
                                    detail=user.username)
            sent = notify_user(
                user, "アカウントが承認されました / Your account has been approved",
                f"{user.username} 様\n\n"
                "HUVar ACMG Classifier のアカウントが承認されました。次の URL から、登録した"
                "ユーザー名とパスワードでログインしてください。初回ログイン時に多要素認証"
                "（MFA）の設定が必要です。\n\n"
                f"{login_url}\n\n"
                "----\n"
                "Your HUVar ACMG Classifier account has been approved. Log in at the URL "
                "above with the user name and password you registered. You will be asked "
                "to set up multi-factor authentication (MFA) at the first login.\n",
            )
            self.message_user(
                request, f"{user.username}: 承認しました"
                         + ("（本人に通知メールを送信）。" if sent else
                            "。通知メールは送信できませんでした。"),
                level=messages.SUCCESS if sent else messages.WARNING)

    @admin.action(description="選択した登録を却下する（本人に通知）")
    def reject_registrations(self, request, queryset):
        from analysis.models import AuditLog
        for user in queryset:
            if not user.is_awaiting_approval:
                self.message_user(
                    request, f"{user.username}: 承認待ちではないため変更しませんでした"
                             "（既存アカウントの停止は「有効」のチェックを外してください）。",
                    level=messages.WARNING)
                continue
            # メール未確認のアドレスには送らない（他人のアドレスの可能性がある）
            verified = user.approval_status == User.ApprovalStatus.PENDING
            user.is_active = False
            user.approval_status = User.ApprovalStatus.REJECTED
            user.approval_processed_at = timezone.now()
            user.save(update_fields=["is_active", "approval_status", "approval_processed_at"])
            AuditLog.objects.create(user=request.user, action="user_rejected",
                                    detail=user.username)
            sent = verified and notify_user(
                user, "アカウント登録について / About your account registration",
                f"{user.username} 様\n\n"
                "HUVar ACMG Classifier へのアカウント登録は承認されませんでした。"
                "ご不明な点は管理者までお問い合わせください。\n\n"
                "----\n"
                "Your registration for HUVar ACMG Classifier was not approved. Please "
                "contact the administrator if you have any questions.\n",
            )
            self.message_user(
                request, f"{user.username}: 却下しました"
                         + ("（本人に通知メールを送信）。" if sent else "。"),
                level=messages.SUCCESS)

    @admin.display(description="ログイン回数", ordering="_n_login")
    def n_login(self, obj):
        return obj._n_login

    @admin.display(description="explain(Web)", ordering="_n_explain")
    def n_explain(self, obj):
        return obj._n_explain

    @admin.display(description="classify(Web)", ordering="_n_classify")
    def n_classify(self, obj):
        return obj._n_classify

    @admin.display(description="API classify(単一)", ordering="_n_api_classify")
    def n_api_classify(self, obj):
        return obj._n_api_classify

    @admin.display(description="API jobs(バッチ)", ordering="_n_api_jobs")
    def n_api_jobs(self, obj):
        return obj._n_api_jobs


@admin.register(AccountRequest)
class AccountRequestAdmin(admin.ModelAdmin):
    """（旧方式）アカウント発行リクエストの履歴。新規受付は自己登録（ユーザー一覧の
    「自己登録・承認」）に移行した。"""
    list_display = ("full_name", "email", "institution", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("full_name", "email", "institution")
    actions = ["mark_approved", "mark_rejected"]

    @admin.action(description="選択したリクエストを承認しユーザーを作成する")
    def mark_approved(self, request, queryset):
        from analysis.models import AuditLog
        for req in queryset:
            # 既存ユーザー（同一メール/ユーザー名）があれば作成せず承認のみ
            # ユーザー名はリクエストの User name をそのまま使用する
            username = req.full_name
            if User.objects.filter(email=req.email).exists() or \
                    User.objects.filter(username=username).exists():
                req.status = AccountRequest.Status.APPROVED
                req.processed_at = timezone.now()
                req.save(update_fields=["status", "processed_at"])
                self.message_user(
                    request, f"{req.email}: 既にユーザーが存在するため作成をスキップしました。",
                    level=messages.WARNING)
                continue

            temp_password = secrets.token_urlsafe(12)
            user = User.objects.create_user(
                username=username,
                email=req.email,
                password=temp_password,
                role=User.Role.GENERAL,
                institution=req.institution,
            )
            req.status = AccountRequest.Status.APPROVED
            req.processed_at = timezone.now()
            req.save(update_fields=["status", "processed_at"])
            AuditLog.objects.create(
                user=request.user, action="user_created", detail=user.username)
            self.message_user(
                request,
                f"ユーザー作成: {user.username} ／ 仮パスワード: {temp_password} "
                f"（本人に安全に伝達してください。初回ログイン時に MFA 設定が必要です）",
                level=messages.SUCCESS)

    @admin.action(description="選択したリクエストを却下する")
    def mark_rejected(self, request, queryset):
        queryset.update(status=AccountRequest.Status.REJECTED, processed_at=timezone.now())


@admin.register(TokenRequest)
class TokenRequestAdmin(admin.ModelAdmin):
    list_display = ("user_name", "email", "institution", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("user_name", "email", "institution")
    actions = ["approve_and_issue_token", "mark_rejected"]

    @admin.action(description="選択したリクエストを承認しAPIトークンを発行する")
    def approve_and_issue_token(self, request, queryset):
        from rest_framework.authtoken.models import Token
        from analysis.models import AuditLog
        for req in queryset:
            # メール一致する既存ユーザーにトークンを発行（無ければスキップ）
            user = User.objects.filter(email=req.email).first()
            if user is None:
                self.message_user(
                    request,
                    f"{req.email}: 一致するユーザーが見つかりません。先にユーザーを作成してください。",
                    level=messages.WARNING)
                continue
            from .authentication import token_expired
            token, created = Token.objects.get_or_create(user=user)
            if not created and token_expired(token):
                # 有効期限切れは新しいキーで再発行する（古いキーは無効になる）
                token.delete()
                token = Token.objects.create(user=user)
                created = True
            req.status = TokenRequest.Status.APPROVED
            req.processed_at = timezone.now()
            req.save(update_fields=["status", "processed_at"])
            AuditLog.objects.create(
                user=request.user, action="token_issued", detail=user.username)
            state = "発行" if created else "既存トークンを表示"
            self.message_user(
                request,
                f"{user.username} のトークン（{state}）: {token.key} "
                f"（本人に安全に伝達してください）",
                level=messages.SUCCESS)

    @admin.action(description="選択したリクエストを却下する")
    def mark_rejected(self, request, queryset):
        queryset.update(status=TokenRequest.Status.REJECTED, processed_at=timezone.now())
