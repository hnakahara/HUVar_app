from django.contrib import admin

from .models import (
    AnalysisJob,
    AuditLog,
    CriterionEdit,
    ReferenceDataVersion,
    VariantResult,
    VariantResultCache,
)


@admin.register(AnalysisJob)
class AnalysisJobAdmin(admin.ModelAdmin):
    list_display = ("id", "owner", "kind", "assembly", "status", "created_at", "expires_at")
    list_filter = ("kind", "status", "assembly")


@admin.register(ReferenceDataVersion)
class ReferenceDataVersionAdmin(admin.ModelAdmin):
    list_display = ("name", "sha256", "size_bytes", "mtime", "updated_at")


@admin.register(VariantResultCache)
class VariantResultCacheAdmin(admin.ModelAdmin):
    list_display = ("assembly", "chrom", "pos", "ref", "alt", "engine_version", "updated_at")
    search_fields = ("chrom", "pos")


admin.site.register(VariantResult)
admin.site.register(CriterionEdit)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "action", "detail", "ip_address", "user_agent")
    list_filter = ("action",)
    search_fields = ("user__username", "action", "detail", "ip_address")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "user", "action", "detail", "ip_address", "user_agent")

    # 監査ログは管理画面から追加・変更させない（削除はスーパーユーザーのみ）
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
