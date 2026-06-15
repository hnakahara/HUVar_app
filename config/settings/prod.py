"""本番環境設定（HTTPS・全世界公開・強固なセキュリティ）。"""
import os

from .base import *  # noqa: F401,F403

DEBUG = False

# プロキシ（nginx）経由の HTTPS を認識
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# Cookie / 通信のセキュア化
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

# CSRF 信頼オリジン（HTTPS ドメイン）
CSRF_TRUSTED_ORIGINS = [
    f"https://{h}" for h in ALLOWED_HOSTS  # noqa: F405
]

# セッションタイムアウト（無操作で失効）
SESSION_COOKIE_AGE = 60 * 60  # 1 時間
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
