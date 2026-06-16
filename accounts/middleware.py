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
                reverse("set_language"),  # 言語切替は未検証でも許可
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


class SecurityHeadersMiddleware:
    """全レスポンスにセキュリティヘッダを付与する（フロント nginx 構成に依存しない）。

    CSP は本アプリのインライン style / 最小の inline script（自動更新）を許容する
    実用的な基準値。将来 nonce 化で 'unsafe-inline' を外して強化可能。
    """

    CSP = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        resp = self.get_response(request)
        resp.setdefault("Content-Security-Policy", self.CSP)
        resp.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        resp.setdefault("X-Content-Type-Options", "nosniff")
        resp.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.setdefault("X-Frame-Options", "DENY")
        return resp
