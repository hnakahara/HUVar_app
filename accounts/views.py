import base64
import io
import logging
from datetime import timedelta

import qrcode
import qrcode.image.svg
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.mail import send_mail
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.utils.translation import gettext as _

from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from .forms import SignUpForm, TokenRequestForm
from .models import User
from .notifications import notify_admin
from .tokens import email_verification_token

log = logging.getLogger(__name__)


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


def _purge_stale_unverified() -> None:
    """確認リンクの有効期限を過ぎてもメール確認されなかった自己登録を削除する。

    ユーザー名・メールアドレスを解放し、同じ情報で登録し直せるようにする。"""
    cutoff = timezone.now() - timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT)
    User.objects.filter(
        approval_status=User.ApprovalStatus.EMAIL_PENDING,
        is_active=False,
        date_joined__lt=cutoff,
    ).delete()


def _send_verification_email(request, user) -> None:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = email_verification_token.make_token(user)
    url = request.build_absolute_uri(
        reverse("accounts:verify_email", kwargs={"uidb64": uid, "token": token}))
    days = max(1, settings.PASSWORD_RESET_TIMEOUT // 86400)
    send_mail(
        subject="[HUVar] メールアドレスの確認 / Confirm your email address",
        message=(
            f"{user.get_username()} 様\n\n"
            "HUVar ACMG Classifier へのご登録ありがとうございます。\n"
            f"次のリンクを開いてメールアドレスを確認してください（有効期限 {days} 日）。\n"
            "確認後、管理者の承認を経てご利用いただけるようになります。\n\n"
            f"{url}\n\n"
            "このメールに心当たりがない場合は破棄してください。\n\n"
            "----\n"
            "Thank you for registering with HUVar ACMG Classifier.\n"
            f"Open the link above within {days} day(s) to confirm your email address. "
            "Your account can be used after an administrator approves it.\n"
            "If you did not register, please ignore this email.\n"
        ),
        from_email=None,
        recipient_list=[user.email],
        fail_silently=False,
    )


def signup(request):
    """自己登録。アカウントは無効で作成し、本人に確認メールを送る。

    確認リンクを開くと承認待ちになり、管理者へ通知される（verify_email）。"""
    if request.method == "POST":
        if _rate_limited(request, "signup"):
            messages.error(request, _("リクエストが多すぎます。しばらく時間をおいて再度お試しください。"))
            return render(request, "accounts/signup.html", {"form": SignUpForm()})
        _purge_stale_unverified()
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            try:
                _send_verification_email(request, user)
            except Exception:  # noqa: BLE001  確認メールが届かない登録は残さない
                log.exception("signup verification email failed for %s", user.email)
                user.delete()
                messages.error(request, _(
                    "確認メールを送信できませんでした。時間をおいて再度お試しください。"))
                return render(request, "accounts/signup.html", {"form": form})
            from analysis.models import AuditLog
            AuditLog.objects.create(user=user, action="signup", detail=user.email)
            return redirect("accounts:signup_done")
    else:
        form = SignUpForm()
    return render(request, "accounts/signup.html", {"form": form})


def signup_done(request):
    return render(request, "accounts/signup_done.html")


def verify_email(request, uidb64, token):
    """確認リンク。正しければ承認待ち（pending）にして管理者へ通知する。"""
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and user.approval_status == User.ApprovalStatus.EMAIL_PENDING \
            and email_verification_token.check_token(user, token):
        user.approval_status = User.ApprovalStatus.PENDING
        user.email_verified_at = timezone.now()
        user.save(update_fields=["approval_status", "email_verified_at"])
        from analysis.models import AuditLog
        AuditLog.objects.create(user=user, action="signup_email_verified", detail=user.email)
        admin_url = request.build_absolute_uri(
            reverse("admin:accounts_user_changelist")
            + f"?approval_status__exact={User.ApprovalStatus.PENDING}")
        notify_admin(
            "新規アカウントの承認待ち",
            f"ユーザー名: {user.get_username()}\n"
            f"メール: {user.email}（確認済み）\n"
            f"所属: {user.institution}\n"
            f"利用目的: {user.registration_purpose}\n\n"
            f"承認・却下はユーザー一覧のアクションから行ってください:\n{admin_url}\n",
        )
        state = "verified"
    elif user is not None and user.approval_status != User.ApprovalStatus.EMAIL_PENDING:
        state = "already"
    else:
        state = "invalid"
    return render(request, "accounts/verify_email.html", {"state": state})


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
