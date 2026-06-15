# Celery アプリを Django 起動時に読み込む
from .celery import app as celery_app

__all__ = ("celery_app",)
