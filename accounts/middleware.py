"""MFA 強制ミドルウェア。

認証済みだが OTP 未検証のユーザーを、解析・管理画面など一切の保護リソースから
遮断し、MFA 登録（未登録の場合）または MFA 検証（登録済みの場合）へ誘導する。

django_otp.middleware.OTPMiddleware の後段に配置すること（request.user.is_verified が
利用可能になる）。トークン認証の API リクエストはセッション匿名のため対象外（DRF が
トークンで認証し、MFA はブラウザセッションにのみ適用される）。
"""
from __future__ import annotations

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

from django_otp.plugins.otp_totp.models import TOTPDevice


class MFAEnforcementMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not user.is_verified():
            path = request.path
            allowed = {
                reverse("accounts:login"),
                reverse("accounts:logout"),
                reverse("accounts:account_request"),
                reverse("accounts:mfa_setup"),
                reverse("accounts:mfa_verify"),
            }
            static_prefix = settings.STATIC_URL or "/static/"
            if path not in allowed and not path.startswith(static_prefix):
                has_device = TOTPDevice.objects.filter(
                    user=user, confirmed=True
                ).exists()
                return redirect(
                    "accounts:mfa_verify" if has_device else "accounts:mfa_setup"
                )
        return self.get_response(request)
