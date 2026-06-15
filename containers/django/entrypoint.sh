#!/bin/sh
set -e

# HUHVar 解析エンジン（acmg-classifier）をボリュームマウント先からインストール。
# /huhvar に pyproject.toml があれば editable install（初回のみ実体反映）。
if [ -f /huhvar/pyproject.toml ]; then
    pip install -e /huhvar
fi

python manage.py makemigrations --noinput
python manage.py migrate --noinput
python manage.py collectstatic --noinput

# DEBUG=1（テスト）は runserver、DEBUG=0（本番）は gunicorn(uvicorn worker)
if [ "$DEBUG" = "1" ]; then
    python manage.py runserver 0.0.0.0:8000
else
    gunicorn --workers 8 -k uvicorn.workers.UvicornWorker config.asgi:application --bind 0.0.0.0:8000 -t 900
fi
