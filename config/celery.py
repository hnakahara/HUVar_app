"""Celery アプリ定義。Redis をブローカーに、投入順で直列処理する（worker は concurrency=1）。"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")

app = Celery("huvar")
# settings の CELERY_ 接頭辞を取り込む
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
