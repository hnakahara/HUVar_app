from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.db.models import Count, Q
from django.utils import timezone

from .models import AccountRequest, User

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    # Web と API を別カウントで表示
    list_display = ("username", "email", "role", "institution", "mfa_exempt",
                    "n_login", "n_explain", "n_classify",
                    "n_api_classify", "n_api_jobs", "is_active", "is_staff")
    list_filter = ("role", "mfa_exempt", "is_active", "is_staff")
    fieldsets = UserAdmin.fieldsets + (
        ("HUHVar", {"fields": ("role", "institution", "mfa_exempt",
                               "api_batch_monthly_limit")}),
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

    @admin.action(description="選択したリクエストを承認済みにする")
    def mark_approved(self, request, queryset):
        queryset.update(status=AccountRequest.Status.APPROVED, processed_at=timezone.now())

    @admin.action(description="選択したリクエストを却下する")
    def mark_rejected(self, request, queryset):
        queryset.update(status=AccountRequest.Status.REJECTED, processed_at=timezone.now())
