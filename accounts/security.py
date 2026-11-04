"""接続元 IP の判定・現在のリクエスト情報・セキュリティ通知。

接続元 IP
    本番では vas の nginx がリバースプロキシとして前段にあり、``X-Forwarded-For`` に
    ``$proxy_add_x_forwarded_for``（＝クライアントが送ってきた値 + nginx が見た接続元）を
    設定して渡す。先頭の値はクライアントが自由に書けるため信用できず、``REMOTE_ADDR`` は
    常に nginx コンテナの IP になる。信頼できるプロキシ（``TRUSTED_PROXY_COUNT`` 段）が
    付けた **右端から数えた値** だけを接続元として使う。django-axes（ログイン失敗の
    ロックアウト）・フォームの送信回数制限・監査ログ・通知はすべてこの関数を使う。

現在のリクエスト
    ``RequestContextMiddleware`` が処理中のリクエストを ContextVar に置き、監査ログ
    （analysis.AuditLog）が保存時に IP / User-Agent を自動で記録する。

セキュリティ通知
    ``security_alert`` は ADMIN_ADDRESS 宛に「[HUVar][セキュリティ] …」のメールを送る。
    同じ事象の連続通知は dedup_key ごとに一定時間（既定 1 時間）抑制する。
"""
from __future__ import annotations

import contextvars
import ipaddress
import logging

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

log = logging.getLogger("huvar.security")

_current_request: contextvars.ContextVar = contextvars.ContextVar(
    "huvar_current_request", default=None)


def _valid_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request) -> str:
    """リクエストの接続元 IP（信頼できるプロキシが付けた X-Forwarded-For の右端）。

    ``REMOTE_ADDR`` がプライベート/ループバックのとき（＝プロキシ経由）だけ
    X-Forwarded-For を見る。直接アクセスやヘッダ無しでは ``REMOTE_ADDR`` を返す。"""
    if request is None:
        return ""
    meta = getattr(request, "META", {}) or {}
    remote = meta.get("REMOTE_ADDR", "") or ""
    hops = int(getattr(settings, "TRUSTED_PROXY_COUNT", 1) or 0)
    if hops <= 0:
        return remote
    try:
        via_proxy = ipaddress.ip_address(remote).is_private or \
            ipaddress.ip_address(remote).is_loopback
    except ValueError:
        via_proxy = False
    if not via_proxy:
        return remote
    parts = [p.strip() for p in meta.get("HTTP_X_FORWARDED_FOR", "").split(",") if p.strip()]
    if len(parts) < hops:
        return remote
    return _valid_ip(parts[-hops]) or remote


def user_agent(request) -> str:
    if request is None:
        return ""
    return (getattr(request, "META", {}) or {}).get("HTTP_USER_AGENT", "")[:255]


def current_request():
    return _current_request.get()


class RequestContextMiddleware:
    """処理中のリクエストを ContextVar に保持する（監査ログの IP / UA 記録用）。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current_request.set(request)
        try:
            return self.get_response(request)
        finally:
            _current_request.reset(token)


class AdminNetworkRestrictionMiddleware:
    """``ADMIN_ALLOWED_NETWORKS``（カンマ区切りの CIDR）以外からの管理画面アクセスを
    404 にする（存在自体を見せない）。未設定なら制限しない。

    ログイン済みの一般ユーザー（スタッフ権限なし）が管理画面にアクセスした場合は
    セキュリティ通知を送る（ユーザーごとに 1 時間 1 回）。"""

    def __init__(self, get_response):
        self.get_response = get_response
        raw = getattr(settings, "ADMIN_ALLOWED_NETWORKS", "") or ""
        nets = []
        for item in raw.split(","):
            item = item.strip()
            if item:
                nets.append(ipaddress.ip_network(item, strict=False))
        self.networks = nets

    def __call__(self, request):
        if self.networks and request.path_info.startswith("/admin/"):
            ip = client_ip(request)
            try:
                addr = ipaddress.ip_address(ip)
                allowed = any(addr in n for n in self.networks)
            except ValueError:
                allowed = False
            if not allowed:
                log.warning("admin access blocked from %s (%s)", ip, request.path_info)
                from django.http import Http404
                raise Http404()
        response = self.get_response(request)
        if request.path_info.startswith("/admin/"):
            user = getattr(request, "user", None)
            if user is not None and user.is_authenticated and not user.is_staff:
                security_alert(
                    f"一般ユーザーによる管理画面へのアクセス（{user.get_username()}）",
                    f"スタッフ権限のないユーザー {user.get_username()} が管理画面 "
                    f"({request.path_info}) にアクセスしました（アクセス自体は拒否されます）。",
                    dedup_key=f"adminprobe:{user.pk}", request=request)
        return response


def describe_request(request=None) -> str:
    """通知本文に付ける接続元情報。"""
    request = request if request is not None else current_request()
    if request is None:
        return f"日時: {timezone.localtime():%Y-%m-%d %H:%M:%S %Z}\n"
    return (f"日時: {timezone.localtime():%Y-%m-%d %H:%M:%S %Z}\n"
            f"接続元 IP: {client_ip(request) or '-'}\n"
            f"User-Agent: {user_agent(request) or '-'}\n"
            f"URL: {request.get_full_path()}\n")


def security_alert(subject: str, message: str, dedup_key: str | None = None,
                   window: int = 3600, request=None) -> bool:
    """ADMIN_ADDRESS 宛にセキュリティ通知を送る（送った場合 True）。

    ``dedup_key`` を指定すると、同じキーの通知を ``window`` 秒間は送らない（攻撃が
    続いてもメールが大量に届かないように）。通知の失敗で本処理は止めない。"""
    if dedup_key:
        try:
            if not cache.add(f"secalert:{dedup_key}", 1, window):
                return False
        except Exception:  # noqa: BLE001  キャッシュ障害時は抑制せず送る
            pass
    log.warning("security alert: %s", subject)
    from .notifications import notify_admin
    notify_admin(f"[セキュリティ] {subject}", message.rstrip() + "\n\n" + describe_request(request))
    return True


def count_event(key: str, window: int) -> int:
    """``window`` 秒間の事象の回数を数える（キャッシュ障害時は 0）。"""
    try:
        cache.add(f"seccount:{key}", 0, window)
        return cache.incr(f"seccount:{key}")
    except Exception:  # noqa: BLE001
        return 0
