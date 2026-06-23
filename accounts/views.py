import base64
import io
import os

import qrcode
import qrcode.image.svg
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.mail import send_mail
from django.shortcuts import redirect, render

from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from .forms import AccountRequestForm, TokenRequestForm
from .notifications import notify_admin


def _client_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _rate_limited(request, action: str, limit: int = 5, window: int = 600) -> bool:
    """同一 IP の action 送信を window 秒で limit 回までに制限（超過で True）。

    キャッシュ障害時は制限せず通す（可用性優先）。公開フォームのスパム抑止用。
    """
    key = f"rl:{action}:{_client_ip(request)}"
    try:
        cache.add(key, 0, window)
        return cache.incr(key) > limit
    except Exception:  # noqa: BLE001
        return False


def account_request(request):
    """新規ユーザーは自己登録できないため、発行リクエストのみ送信する。"""
    if request.method == "POST":
        if _rate_limited(request, "account_request"):
            messages.error(request, "リクエストが多すぎます。しばらく時間をおいて再度お試しください。")
            return render(request, "accounts/account_request.html", {"form": AccountRequestForm()})
        form = AccountRequestForm(request.POST)
        if form.is_valid():
            obj = form.save()
            admin_address = getattr(settings, "ADMIN_ADDRESS", "") or os.environ.get(
                "ADMIN_ADDRESS", ""
            )
            if admin_address:
                send_mail(
                    subject="[HUVar] 新規アカウント発行リクエスト",
                    message=(
                        f"ユーザー名: {obj.full_name}\n"
                        f"メール: {obj.email}\n"
                        f"所属: {obj.institution}\n"
                        f"目的: {obj.purpose}\n"
                    ),
                    from_email=None,
                    recipient_list=[admin_address],
                    fail_silently=True,
                )
            messages.success(request, "リクエストを送信しました。管理者の承認をお待ちください。")
            return redirect("accounts:login")
    else:
        form = AccountRequestForm()
    return render(request, "accounts/account_request.html", {"form": form})


def token_request(request):
    """API トークン発行リクエストを送信する（administrator が承認時に発行）。"""
    if request.method == "POST":
        if _rate_limited(request, "token_request"):
            messages.error(request, "リクエストが多すぎます。しばらく時間をおいて再度お試しください。")
            return render(request, "accounts/token_request.html", {"form": TokenRequestForm()})
        form = TokenRequestForm(request.POST)
        if form.is_valid():
            obj = form.save()
            notify_admin(
                "API トークン発行リクエスト",
                f"ユーザー名: {obj.user_name}\n"
                f"メール: {obj.email}\n"
                f"所属: {obj.institution}\n"
                f"利用目的: {obj.intended_use}\n",
            )
            messages.success(request, "トークン発行リクエストを送信しました。管理者の承認をお待ちください。")
            return redirect("api:swagger-ui")
    else:
        form = TokenRequestForm()
    return render(request, "accounts/token_request.html", {"form": form})


def _qr_data_uri(data: str) -> str:
    """otpauth URI を QR(SVG) の data URI にする（Pillow 不要・<img> で確実に表示）。"""
    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(data, image_factory=factory, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return "data:image/svg+xml;base64," + b64


@login_required
def mfa_setup(request):
    """MFA（TOTP）登録。確認済みデバイスがあれば検証/トップへ振り分ける。"""
    # MFA 免除ユーザー（レビュー用）は設定不要でトップへ
    if getattr(request.user, "mfa_exempt", False):
        return redirect("analysis:index")
    confirmed = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()
    if confirmed:
        if request.user.is_verified():
            return redirect("analysis:index")
        return redirect("accounts:mfa_verify")

    device = TOTPDevice.objects.filter(user=request.user, confirmed=False).first()
    if device is None:
        device = TOTPDevice.objects.create(user=request.user, name="default", confirmed=False)

    if request.method == "POST":
        token = request.POST.get("token", "").strip()
        if device.verify_token(token):
            device.confirmed = True
            device.save()
            otp_login(request, device)
            messages.success(request, "MFA を設定しました。")
            return redirect("analysis:index")
        messages.error(request, "認証コードが正しくありません。")

    return render(request, "accounts/mfa_setup.html", {
        "qr_data_uri": _qr_data_uri(device.config_url),
        "secret": device.bin_key.hex(),
    })


@login_required
def mfa_verify(request):
    """ログイン後の TOTP 検証（2要素目）。"""
    device = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()
    if device is None:
        return redirect("accounts:mfa_setup")
    if request.user.is_verified():
        return redirect("analysis:index")

    if request.method == "POST":
        token = request.POST.get("token", "").strip()
        if device.verify_token(token):
            otp_login(request, device)
            return redirect("analysis:index")
        messages.error(request, "認証コードが正しくありません。")

    return render(request, "accounts/mfa_verify.html")
