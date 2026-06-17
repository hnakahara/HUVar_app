"""テスト環境設定（HTTP・DEBUG 可）。"""
from .base import *  # noqa: F401,F403

DEBUG = True

# テストではローカル許可
if not ALLOWED_HOSTS:  # noqa: F405
    ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]

# テストは HTTP のため secure cookie は無効
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# 非標準ポート経由のため CSRF 信頼オリジンを明示（Django 4.x の Origin 検証）
CSRF_TRUSTED_ORIGINS = [
    "http://localhost:28080",
    "http://127.0.0.1:28080",
]

# テスト/開発では SMTP に接続せずコンソールに出力（EMAIL_BACKEND 未指定時）
import os  # noqa: E402

if "EMAIL_BACKEND" not in os.environ:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# テストは Redis 非依存にする（レート制限はローカルメモリで動作）
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
}
