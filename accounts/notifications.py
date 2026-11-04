"""管理者(ADMIN_ADDRESS)宛の通知メール送信ヘルパー。

ログインや各解析の実行を ADMIN に通知する。メール未設定・送信失敗時は
本処理を止めないよう黙って無視する（fail_silently）。
"""
import os

from django.conf import settings
from django.core.mail import send_mail


def _admin_address() -> str:
    return getattr(settings, "ADMIN_ADDRESS", "") or os.environ.get("ADMIN_ADDRESS", "")


def notify_user(user, subject: str, message: str) -> bool:
    """ユーザー本人宛にメールを送る。送信できたら True（失敗しても例外は出さない）。"""
    if not getattr(user, "email", ""):
        return False
    try:
        return bool(send_mail(
            subject=f"[HUVar] {subject}",
            message=message,
            from_email=None,
            recipient_list=[user.email],
            fail_silently=True,
        ))
    except Exception:  # noqa: BLE001  通知失敗で本処理を止めない
        return False


def notify_admin(subject: str, message: str) -> None:
    """ADMIN_ADDRESS 宛に通知メールを送る。未設定なら何もしない。"""
    address = _admin_address()
    if not address:
        return
    try:
        send_mail(
            subject=f"[HUVar] {subject}",
            message=message,
            from_email=None,
            recipient_list=[address],
            fail_silently=True,
        )
    except Exception:  # noqa: BLE001  通知失敗で本処理を止めない
        pass
