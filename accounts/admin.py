import secrets

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.db.models import Count, Q
from django.utils import timezone

from .models import AccountRequest, TokenRequest, User

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    # Web と API を別カウントで表示
    list_display = ("username", "email", "role", "institution", "mfa_exempt",
                    "n_login", "n_explain", "n_classify",
                    "n_api_classify", "n_api_jobs", "is_active", "is_staff")
    list_filter = ("role", "mfa_exempt", "is_active", "is_staff")
    fieldsets = UserAdmin.fieldsets + (
        ("HUVar", {"fields": ("role", "institution", "mfa_exempt",
                               "api_batch_monthly_limit", "web_batch_monthly_limit")}),
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
            token, created = Token.objects.get_or_create(user=user)
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
