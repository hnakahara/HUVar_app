"""API トークン認証（有効期限・最終使用の記録つき）。

DRF 標準の TokenAuthentication に次を加える。

* 発行（created）から ``API_TOKEN_MAX_AGE_DAYS`` 日を過ぎたトークンは拒否する
  （0 なら無期限）。管理者がトークン発行アクションで再発行すると新しいキーになる。
* 最終使用日時と接続元 IP を User に記録する（admin で確認でき、漏洩の調査に使う）。
  書き込みは 10 分に 1 回、または接続元 IP が変わったときだけ行う。
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import exceptions
from rest_framework.authentication import TokenAuthentication

from .security import client_ip

_TOUCH_INTERVAL = timedelta(minutes=10)


def token_expired(token) -> bool:
    days = int(getattr(settings, "API_TOKEN_MAX_AGE_DAYS", 0) or 0)
    return days > 0 and token.created < timezone.now() - timedelta(days=days)


class ExpiringTokenAuthentication(TokenAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        if token_expired(token):
            raise exceptions.AuthenticationFailed(_(
                "API トークンの有効期限が切れています。管理者に再発行を依頼してください。"))
        self._touch(user, client_ip(request))
        return user, token

    @staticmethod
    def _touch(user, ip: str) -> None:
        now = timezone.now()
        last = getattr(user, "api_token_last_used_at", None)
        if last is not None and now - last < _TOUCH_INTERVAL \
                and (getattr(user, "api_token_last_ip", "") or "") == ip:
            return
        type(user).objects.filter(pk=user.pk).update(
            api_token_last_used_at=now, api_token_last_ip=ip or None)
        user.api_token_last_used_at, user.api_token_last_ip = now, ip or None
