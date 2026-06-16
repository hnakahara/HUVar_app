"""認証イベントの監査ログ（NFR-SEC-8）。

ログイン成功 / 失敗 / ログアウトを analysis.AuditLog に記録する。
AuditLog の import は受信時に遅延させ、アプリ起動順の問題を避ける。
"""
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.dispatch import receiver


def _log(user, action: str, detail: str = "") -> None:
    from analysis.models import AuditLog
    try:
        AuditLog.objects.create(
            user=user if getattr(user, "pk", None) else None,
            action=action,
            detail=detail,
        )
    except Exception:  # noqa: BLE001  監査記録の失敗で本処理を止めない
        pass


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    _log(user, "login")
    from .notifications import notify_admin
    notify_admin(
        "ログイン通知",
        f"ユーザー: {user.get_username()}\nメール: {user.email}\nがログインしました。",
    )


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    _log(user, "logout")


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    username = (credentials or {}).get("username", "")
    _log(None, "login_failed", f"username={username}")
