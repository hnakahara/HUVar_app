from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        # 認証イベントの監査ログ用シグナルを登録
        from . import signals  # noqa: F401
