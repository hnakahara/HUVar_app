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
    "drf_spectacular",
    "drf_spectacular_sidecar",  # Swagger UI / Redoc の静的資産をローカル配信
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
    # 処理中のリクエストを保持（監査ログに接続元 IP / User-Agent を記録するため）
    "accounts.security.RequestContextMiddleware",
    # 管理画面(/admin/)を ADMIN_ALLOWED_NETWORKS の接続元に限定（未設定なら制限なし）
    "accounts.security.AdminNetworkRestrictionMiddleware",
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
        "NAME": os.environ.get("POSTGRES_NAME", "huvar"),
        "USER": os.environ.get("POSTGRES_USER", "huvar"),
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
# HUVAR_LOCALE_DIR: コンテナでは entrypoint.sh が locale/ を書き込み可能な場所へ写して
# .mo をコンパイルし、ここを差し替える（本番はコードを読み取り専用でマウントするため）。
LOCALE_PATHS = [Path(os.environ.get("HUVAR_LOCALE_DIR") or BASE_DIR / "locale")]

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
        # トークン認証（有効期限 API_TOKEN_MAX_AGE_DAYS・最終使用日時/IP の記録つき）
        "accounts.authentication.ExpiringTokenAuthentication",
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
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

# --- OpenAPI / Swagger UI（drf-spectacular） ---
SPECTACULAR_SETTINGS = {
    "TITLE": "HUVar ACMG Classifier API",
    "DESCRIPTION": (
        "[← アプリのトップへ戻る](/acmg/)　|　"
        "[使い方ガイド](/acmg/help/)\n\n"
        "外部クライアント向けの REST API（トークン認証）です。"
        "下の各エンドポイントは「Try it out」で試せます（認証が必要なものは "
        "右上の **Authorize** にトークンのキーを貼り付けてください。"
        "`Token ` の接頭辞は自動で付与されます）。\n\n"
        "### APIトークンの取得\n"
        "トークンは管理者が発行します。"
        "[➡ APIトークンの発行をリクエストする](/acmg/accounts/token-request/)\n"
    ),
    "VERSION": "1.0.0",
    # nginx が /acmg を除去してアプリへ渡すため、Swagger UI の「Try it out」が
    # 正しい公開 URL(/acmg/api/...) を叩くようサーバ接頭辞を明示する。
    # FORCE_SCRIPT_NAME 未設定（テスト等）の場合はルート("/")。
    "SERVERS": [{"url": FORCE_SCRIPT_NAME or "/"}],
    "SERVE_INCLUDE_SCHEMA": False,
    # sidecar 同梱の静的資産を使う（外部 CDN を読まず CSP self に準拠）
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
    # スキーマ/ドキュメントはデモ用に公開
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    # Authorize にトークンのキーのみ入力されたら送信時に自動で "Token " を付与する。
    # 文字列で渡すと drf-spectacular がそのまま JS 設定として挿入する（...swaggerSettings は
    # 組み込みの CSRF 用 requestInterceptor の後に spread されるため上書きになる→CSRF も補完）。
    "SWAGGER_UI_SETTINGS": r"""{
    "persistAuthorization": true,
    "requestInterceptor": (request) => {
        const a = request.headers && request.headers["Authorization"];
        if (a && !/^(Token|Bearer)\s/i.test(a)) {
            request.headers["Authorization"] = "Token " + a;
        }
        if (request.method && request.method.toUpperCase() !== "GET") {
            const m = document.cookie.match(/(?:^|;\s*)(?:huvar_)?csrftoken=([^;]+)/);
            if (m) { request.headers["X-CSRFToken"] = decodeURIComponent(m[1]); }
        }
        return request;
    }
}""",
}

# --- キャッシュ（Redis）: レート制限・DRF throttling 等に使用。Celery とは別 DB(1) ---
# 認証情報(パスワード)は REDIS_URL を踏襲し、末尾の DB 番号のみ 1 に差し替える。
_REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
CACHE_URL = os.environ.get("CACHE_URL") or (_REDIS_URL.rsplit("/", 1)[0] + "/1")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": CACHE_URL,
    }
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
# 接続元 IP は accounts.security.client_ip で判定する。前段の nginx を経由するため
# REMOTE_ADDR は常にプロキシの IP で、そのままでは全利用者が同じ IP 扱いになる
# （誰かが 5 回失敗すると全員がロックされる）。
AXES_CLIENT_IP_CALLABLE = "accounts.security.client_ip"

# --- 接続元 IP・管理画面の制限・セキュリティ通知 ---
# 前段の信頼できるリバースプロキシの段数。X-Forwarded-For の右端からこの位置の値を
# 接続元とする（vas の nginx 1 段なら 1。プロキシ無しで直接公開するなら 0）。
TRUSTED_PROXY_COUNT = int(os.environ.get("TRUSTED_PROXY_COUNT", "1"))
# 管理画面(/admin/)へのアクセスを許可するネットワーク（カンマ区切り CIDR。例:
# "133.41.0.0/16"）。未設定なら制限しない。許可外からは 404。
ADMIN_ALLOWED_NETWORKS = os.environ.get("ADMIN_ALLOWED_NETWORKS", "")
# API トークンの有効期限（発行からの日数。0 で無期限）。期限切れは管理者が再発行する。
API_TOKEN_MAX_AGE_DAYS = int(os.environ.get("API_TOKEN_MAX_AGE_DAYS", "365"))
# セキュリティ通知のしきい値（accounts.signals）。10 分間のログイン失敗が同一 IP から
# この回数、またはサイト全体でこの回数に達したら通知する。MFA コードの連続失敗も同様。
SECURITY_LOGIN_FAILURES_PER_IP = int(os.environ.get("SECURITY_LOGIN_FAILURES_PER_IP", "10"))
SECURITY_LOGIN_FAILURES_TOTAL = int(os.environ.get("SECURITY_LOGIN_FAILURES_TOTAL", "30"))
SECURITY_MFA_FAILURES = int(os.environ.get("SECURITY_MFA_FAILURES", "3"))

# --- TransVar サービス ---
TRANSVAR_SERVICE_URL = os.environ.get("TRANSVAR_SERVICE_URL", "http://transvar:5000")

# --- ジョブ成果物の保持期間（時間）。変異キャッシュ（FR-CACHE）は対象外。 ---
# バッチは解析終了（成功・失敗）からこの時間で、入力 VCF・結果 TSV ごと自動削除する。
JOB_ARTIFACT_RETENTION_HOURS = int(os.environ.get("JOB_ARTIFACT_RETENTION_HOURS", "1"))
# 待ち・解析中のまま残ったジョブ（worker の異常終了など）を削除するまでの日数
JOB_STALE_DAYS = int(os.environ.get("JOB_STALE_DAYS", "7"))

LOGIN_URL = "accounts:login"
# ログイン後は MFA フローへ（登録済みなら検証、未登録なら登録へ自動分岐）
LOGIN_REDIRECT_URL = "accounts:mfa_setup"

# --- MFA（TOTP）---
OTP_TOTP_ISSUER = "HUVar ACMG Classifier"

# --- メール（Gmail SMTP）/ 管理者通知 ---
# ADMIN_ADDRESS 宛に承認待ちの自己登録・ログイン・解析実行を通知する。自己登録の確認メール・
# 承認/却下の通知も同じ SMTP 設定で本人宛に送る
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

# --- エラー通知（サーバーエラー時に管理者へメール） ---
# 未処理の 500 エラー等を Django 標準の AdminEmailHandler が ADMINS 宛に通知する。
# 送信元は SERVER_EMAIL、宛先は ADMIN_ADDRESS。DEBUG=True の間は送信されない。
ADMINS = [("HUVar Admin", ADMIN_ADDRESS)] if ADMIN_ADDRESS else []
MANAGERS = ADMINS

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        # DEBUG=False のときのみ通知（Django 標準の挙動）
        "require_debug_false": {
            "()": "django.utils.log.RequireDebugFalse",
        },
    },
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        # 未処理例外・サーバーエラーを管理者へメール通知
        "mail_admins": {
            "level": "ERROR",
            "class": "django.utils.log.AdminEmailHandler",
            "filters": ["require_debug_false"],
            "include_html": True,
        },
    },
    # ルート: アプリ側の logger.exception(...) / logger.error(...)（api, analysis 等）を
    # 全て捕捉して管理者へ通知する。
    "root": {
        "handlers": ["console", "mail_admins"],
        "level": "INFO",
    },
    "loggers": {
        # リクエスト処理中の 5xx / 未処理例外（ルートへ伝播させず二重送信を防ぐ）
        "django.request": {
            "handlers": ["console", "mail_admins"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}
