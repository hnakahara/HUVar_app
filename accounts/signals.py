"""認証・権限まわりの監査ログ（NFR-SEC-8）とセキュリティ通知。

監査ログ: ログイン成功 / 失敗 / ログアウト、MFA の失敗・登録・削除、権限やパスワードの
変更、API トークンの発行を analysis.AuditLog に記録する（接続元 IP / User-Agent は
AuditLog が処理中のリクエストから自動で補完する）。

通知（ADMIN_ADDRESS 宛）:
  * すべてのログイン（従来どおり）。管理者権限ユーザー・初めての接続元からのログインは
    件名に【要確認】を付ける。
  * ログイン失敗の多発（同一 IP / サイト全体、10 分間のしきい値）とロックアウト。
  * MFA コードの連続失敗、MFA デバイスの削除・再登録。
  * 管理者・スタッフ権限の付与、パスワード・メールアドレスの変更、API トークンの発行。
  * 一般ユーザーによる管理画面へのアクセス（accounts.security のミドルウェア）。
AuditLog / 通知の import は受信時に遅延させ、アプリ起動順の問題を避ける。
"""
from django.conf import settings
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .security import client_ip, count_event, current_request, security_alert

_WINDOW = 600  # 失敗回数を数える時間幅（秒）


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


def _actor() -> str:
    req = current_request()
    user = getattr(req, "user", None) if req is not None else None
    if user is not None and getattr(user, "is_authenticated", False):
        return user.get_username()
    return "（不明: 管理コマンド・バッチ処理等）"


# --- ログイン / ログアウト -------------------------------------------------------

@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    from analysis.models import AuditLog
    from .notifications import notify_admin
    from .security import describe_request

    ip = client_ip(request)
    previous = AuditLog.objects.filter(user=user, action="login")
    first_login = not previous.exists()
    new_ip = bool(ip) and not first_login and not previous.filter(ip_address=ip).exists()
    _log(user, "login")

    is_admin = user.is_superuser or user.is_staff or getattr(user, "is_administrator", False)
    flags = []
    if is_admin:
        flags.append("管理者権限ユーザー")
    if new_ip:
        flags.append("初めての接続元")
    if first_login:
        flags.append("初回ログイン")
    subject = "ログイン通知"
    if is_admin or new_ip:
        subject = "【要確認】ログイン通知（" + "・".join(flags) + "）"
    notify_admin(
        subject,
        f"ユーザー: {user.get_username()}\nメール: {user.email}\nがログインしました。\n"
        + (f"区分: {'・'.join(flags)}\n" if flags else "")
        + "\n" + describe_request(request),
    )


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    _log(user, "logout")


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    username = (credentials or {}).get("username", "")
    _log(None, "login_failed", f"username={username}")
    ip = client_ip(request) if request is not None else ""
    per_ip = int(getattr(settings, "SECURITY_LOGIN_FAILURES_PER_IP", 10))
    total = int(getattr(settings, "SECURITY_LOGIN_FAILURES_TOTAL", 30))
    if ip and count_event(f"loginfail:ip:{ip}", _WINDOW) >= per_ip:
        security_alert(
            f"ログイン失敗の多発（接続元 {ip}）",
            f"同じ接続元から 10 分間に {per_ip} 回以上ログインに失敗しています。\n"
            f"最後に試されたユーザー名: {username}\n"
            "総当たり攻撃の可能性があります。axes のロックアウト状況と監査ログを確認して"
            "ください。",
            dedup_key=f"loginfail:ip:{ip}", request=request)
    if count_event("loginfail:total", _WINDOW) >= total:
        security_alert(
            "ログイン失敗の多発（サイト全体）",
            f"サイト全体で 10 分間に {total} 回以上ログインに失敗しています（複数の接続元から"
            "の攻撃の可能性）。\n"
            f"最後に試されたユーザー名: {username}\n",
            dedup_key="loginfail:total", request=request)


def _on_locked_out(sender, request=None, username=None, ip_address=None, **kwargs):
    _log(None, "lockout", f"username={username} ip={ip_address}")
    security_alert(
        f"ログインのロックアウト（{username or '-'} / {ip_address or '-'}）",
        f"ログイン失敗が上限（{getattr(settings, 'AXES_FAILURE_LIMIT', '-')} 回）に達したため"
        f"ロックしました。\nユーザー名: {username}\n接続元 IP: {ip_address}\n"
        f"ロック解除は {getattr(settings, 'AXES_COOLOFF_TIME', '-')} 時間後（手動解除: "
        "python manage.py axes_reset）。",
        dedup_key=f"lockout:{username}:{ip_address}", request=request)


try:  # django-axes のロックアウト通知
    from axes.signals import user_locked_out
    user_locked_out.connect(_on_locked_out, dispatch_uid="huvar_user_locked_out")
except Exception:  # noqa: BLE001
    pass


# --- MFA ------------------------------------------------------------------------

def mfa_failed(user) -> None:
    """MFA コードの誤り（accounts.views から呼ぶ）。連続失敗で通知する。"""
    _log(user, "mfa_failed")
    limit = int(getattr(settings, "SECURITY_MFA_FAILURES", 3))
    if count_event(f"mfafail:{user.pk}", 900) >= limit:
        security_alert(
            f"MFA コードの連続失敗（{user.get_username()}）",
            f"ユーザー {user.get_username()} が 15 分間に {limit} 回以上 MFA コードを"
            "誤りました。\nこのユーザーのパスワードが第三者に知られている可能性があります"
            "（パスワードは正しく入力された後の段階です）。",
            dedup_key=f"mfafail:{user.pk}")


def _connect_otp_signals():
    from django_otp.plugins.otp_totp.models import TOTPDevice

    @receiver(pre_save, sender=TOTPDevice, dispatch_uid="huvar_totp_pre_save",
              weak=False)
    def _totp_pre_save(sender, instance, **kwargs):
        old = sender.objects.filter(pk=instance.pk).values_list("confirmed", flat=True)\
            .first() if instance.pk else None
        instance._huvar_newly_confirmed = bool(instance.confirmed) and not old

    @receiver(post_save, sender=TOTPDevice, dispatch_uid="huvar_totp_post_save",
              weak=False)
    def _totp_post_save(sender, instance, **kwargs):
        if not getattr(instance, "_huvar_newly_confirmed", False):
            return
        from analysis.models import AuditLog
        user = instance.user
        again = AuditLog.objects.filter(user=user, action="mfa_registered").exists()
        _log(user, "mfa_registered", "re-registration" if again else "")
        if again:
            security_alert(
                f"MFA の再登録（{user.get_username()}）",
                f"ユーザー {user.get_username()} の MFA（認証アプリ）が新しく登録し直され"
                "ました。本人の操作か確認してください。",
                dedup_key=f"mfareg:{instance.pk}")

    @receiver(post_delete, sender=TOTPDevice, dispatch_uid="huvar_totp_post_delete",
              weak=False)
    def _totp_post_delete(sender, instance, **kwargs):
        if not instance.confirmed:
            return
        user = instance.user
        _log(user, "mfa_deleted", f"by={_actor()}")
        security_alert(
            f"MFA デバイスの削除（{user.get_username()}）",
            f"ユーザー {user.get_username()} の登録済み MFA デバイスが削除されました。\n"
            f"操作者: {_actor()}\n次回ログイン時に新しいデバイスを登録できる状態です。",
            dedup_key=f"mfadel:{instance.pk}")


_connect_otp_signals()


# --- 権限・パスワード・メールアドレスの変更 ------------------------------------------

def _connect_user_signals():
    from django.contrib.auth import get_user_model
    User = get_user_model()

    @receiver(pre_save, sender=User, dispatch_uid="huvar_user_pre_save",
              weak=False)
    def _user_pre_save(sender, instance, **kwargs):
        changes = []
        if instance.pk and not instance._state.adding:
            old = sender.objects.filter(pk=instance.pk).values(
                "is_superuser", "is_staff", "role", "email").first()
            if old:
                if instance.is_superuser and not old["is_superuser"]:
                    changes.append("スーパーユーザー権限の付与")
                if instance.is_staff and not old["is_staff"]:
                    changes.append("スタッフ権限（管理画面）の付与")
                if getattr(instance, "role", None) == "administrator" \
                        and old.get("role") != "administrator":
                    changes.append("administrator ロールの付与")
                if (old["email"] or "") != (instance.email or ""):
                    changes.append(f"メールアドレスの変更（{old['email']} → {instance.email}）")
            # set_password() による変更のみ（ログイン時のハッシュ更新は _password=None）
            if getattr(instance, "_password", None) is not None:
                changes.append("パスワードの変更")
        instance._huvar_security_changes = changes

    @receiver(post_save, sender=User, dispatch_uid="huvar_user_post_save",
              weak=False)
    def _user_post_save(sender, instance, created, **kwargs):
        changes = getattr(instance, "_huvar_security_changes", None) or []
        instance._huvar_security_changes = []
        if created:
            if instance.is_superuser or instance.is_staff:
                changes = ["管理者権限を持つユーザーの作成"]
            else:
                return
        if not changes:
            return
        _log(instance, "security_change", "; ".join(changes) + f" (by={_actor()})")
        priv = any("付与" in c or "作成" in c for c in changes)
        security_alert(
            ("【要確認】権限の変更" if priv else "アカウント情報の変更")
            + f"（{instance.get_username()}）",
            f"ユーザー: {instance.get_username()}\n変更: " + "、".join(changes)
            + f"\n操作者: {_actor()}\n心当たりがない場合は直ちに確認してください。")


_connect_user_signals()


# --- API トークン ------------------------------------------------------------------

def _connect_token_signals():
    from rest_framework.authtoken.models import Token

    @receiver(post_save, sender=Token, dispatch_uid="huvar_token_post_save",
              weak=False)
    def _token_post_save(sender, instance, created, **kwargs):
        if not created:
            return
        security_alert(
            f"API トークンの発行（{instance.user.get_username()}）",
            f"ユーザー {instance.user.get_username()} に API トークンが発行されました。\n"
            f"操作者: {_actor()}")


_connect_token_signals()
