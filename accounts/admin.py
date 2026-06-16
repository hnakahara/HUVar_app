from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.db.models import Count, Q
from django.utils import timezone

from .models import AccountRequest, User

# AuditLog の action 分類（単一=explain 相当 / バッチ=classify 相当、API 含む）
_SINGLE_ACTIONS = ["single_analyze", "api_classify"]
_BATCH_ACTIONS = ["batch_submit", "api_batch_submit"]


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "email", "role", "institution", "mfa_exempt",
                    "n_login", "n_single", "n_batch", "is_active", "is_staff")
    list_filter = ("role", "mfa_exempt", "is_active", "is_staff")
    fieldsets = UserAdmin.fieldsets + (
        ("HUHVar", {"fields": ("role", "institution", "mfa_exempt")}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _n_login=Count("audit_logs",
                           filter=Q(audit_logs__action="login"), distinct=True),
            _n_single=Count("audit_logs",
                            filter=Q(audit_logs__action__in=_SINGLE_ACTIONS), distinct=True),
            _n_batch=Count("audit_logs",
                           filter=Q(audit_logs__action__in=_BATCH_ACTIONS), distinct=True),
        )

    @admin.display(description="ログイン回数", ordering="_n_login")
    def n_login(self, obj):
        return obj._n_login

    @admin.display(description="単一解析(explain)回数", ordering="_n_single")
    def n_single(self, obj):
        return obj._n_single

    @admin.display(description="バッチ解析(classify)回数", ordering="_n_batch")
    def n_batch(self, obj):
        return obj._n_batch


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
