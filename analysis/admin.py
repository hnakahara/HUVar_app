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
admin.site.register(AuditLog)
