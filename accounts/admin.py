from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils import timezone

from .models import AccountRequest, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ("username", "email", "role", "institution", "mfa_exempt",
                    "is_active", "is_staff")
    list_filter = ("role", "mfa_exempt", "is_active", "is_staff")
    fieldsets = UserAdmin.fieldsets + (
        ("HUHVar", {"fields": ("role", "institution", "mfa_exempt")}),
    )


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
