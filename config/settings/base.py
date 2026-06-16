"""共通設定（test / prod で継承）。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "insecure-dev-key")
DEBUG = os.environ.get("DEBUG", "0") == "1"
ALLOWED_HOSTS = [h for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h]

# vas と同一ドメインの /acmg サブパスで提供
FORCE_SCRIPT_NAME = os.environ.get("FORCE_SCRIPT_NAME", "") or None

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # 3rd party
    "rest_framework",
    "rest_framework.authtoken",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "axes",
    # local
    "accounts",
    "analysis",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # 静的ファイル配信（本番でフロント nginx に vas を流用するためアプリ側で配信）
    # FORCE_SCRIPT_NAME(/acmg) は WhiteNoise が自動で静的プレフィックスから除去する。
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # セキュリティヘッダ（CSP/Permissions-Policy 等）を全レスポンスに付与
    "accounts.middleware.SecurityHeadersMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # 多言語（日本語/英語 切替）。SessionMiddleware の後・CommonMiddleware の前。
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # MFA（必須化）— 認証後に OTP 検証状態を request に付与
    "django_otp.middleware.OTPMiddleware",
    # OTP 未検証の認証済みユーザーを MFA 登録/検証へ強制誘導
    "accounts.middleware.MFAEnforcementMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # ログイン失敗ロックアウト（最後段に近い位置）
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_NAME", "huhvar"),
        "USER": os.environ.get("POSTGRES_USER", "huhvar"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

# カスタムユーザー（ロール・MFA 対応）
AUTH_USER_MODEL = "accounts.User"

# 認証バックエンド: axes を先頭に
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ja"
TIME_ZONE = "Asia/Tokyo"
USE_I18N = True
USE_TZ = True

# 対応言語（既定 ja）。UI 上で切替可能（FR-I18N）。
LANGUAGES = [
    ("ja", "日本語"),
    ("en", "English"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]

STATIC_URL = "/acmg/static/"
STATIC_ROOT = "/static"
# プロジェクト同梱の静的ファイル（自前 CSS 等）を collectstatic 対象にする
STATICFILES_DIRS = [BASE_DIR / "static"]

# アップロード VCF / 生成 TSV の保存先（認証付きビューで配信。/media は公開しない）
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/acmg/media/"

# WhiteNoise（圧縮配信）。Manifest は使わず test/prod 双方で安全に動かす。
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
# フロント nginx(vas) が /acmg を剥がして /static/ でアプリに渡すため、WhiteNoise の
# 配信 prefix は /static/ に固定する（STATIC_URL=/acmg/static/ から自動導出だと
# /acmg/static/ になり、剥がし後の /static/ と一致せず admin の CSS 等が 404 になる）。
WHITENOISE_STATIC_PREFIX = "/static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- REST Framework（トークン認証） ---
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": "60/min",
    },
}

# --- Celery（Redis ブローカー・直列処理） ---
CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("REDIS_URL", "redis://redis:6379/0")
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # 直列処理を担保
CELERY_TASK_TIME_LIMIT = 60 * 60
# Redis 接続のレジリエンス（一時切断時の自動再接続・keepalive）
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_TRANSPORT_OPTIONS = {"socket_keepalive": True, "health_check_interval": 30}
CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS = {"socket_keepalive": True, "health_check_interval": 30}
# 接続断時は実行中タスクを安全に再配信（acks_late と併用。再実行はキャッシュで高速）
CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS = True

# --- django-axes（ログイン失敗ロックアウト） ---
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1  # hours
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]

# --- TransVar サービス ---
TRANSVAR_SERVICE_URL = os.environ.get("TRANSVAR_SERVICE_URL", "http://transvar:5000")

# --- ジョブ成果物の保持期間（時間）。変異キャッシュ（FR-CACHE）は対象外。 ---
JOB_ARTIFACT_RETENTION_HOURS = int(os.environ.get("JOB_ARTIFACT_RETENTION_HOURS", "1"))

LOGIN_URL = "accounts:login"
# ログイン後は MFA フローへ（登録済みなら検証、未登録なら登録へ自動分岐）
LOGIN_REDIRECT_URL = "accounts:mfa_setup"

# --- MFA（TOTP）---
OTP_TOTP_ISSUER = "HUHVar ACMG Classifier"

# --- メール（Gmail SMTP）/ 管理者通知 ---
# ADMIN_ADDRESS 宛にアカウント発行リクエスト・ログイン・解析実行を通知する
# （accounts.notifications.notify_admin）。未設定なら通知はスキップされる。
# 認証は GMAIL_ADDRESS / GMAIL_PASS（Gmail アプリパスワード）を使用する。
ADMIN_ADDRESS = os.environ.get("ADMIN_ADDRESS", "")

EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("GMAIL_ADDRESS", "")
EMAIL_HOST_PASSWORD = os.environ.get("GMAIL_PASS", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "0") == "1"
# リクエストスレッドが SMTP 応答待ちでブロックし続けないようタイムアウトを設ける
EMAIL_TIMEOUT = int(os.environ.get("EMAIL_TIMEOUT", "10"))

# 送信元は Gmail アカウント（Gmail は From のなりすましを許可しないため）
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "") or EMAIL_HOST_USER
SERVER_EMAIL = os.environ.get("SERVER_EMAIL", "") or DEFAULT_FROM_EMAIL
