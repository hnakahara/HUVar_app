#!/bin/sh
set -e

# HUVar 解析エンジン（acmg-classifier）をボリュームマウント先からインストール。
# /huvar に pyproject.toml があれば editable install（初回のみ実体反映）。
if [ -f /huvar/pyproject.toml ]; then
    pip install -e /huvar
fi

python manage.py makemigrations --noinput
python manage.py migrate --noinput
python manage.py collectstatic --noinput
# 翻訳カタログ(.po)を .mo にコンパイル（i18n: 日本語/英語）
python manage.py compilemessages

# DEBUG=1（テスト）は runserver、DEBUG=0（本番）は gunicorn(WSGI)
# WSGI を使う理由: FORCE_SCRIPT_NAME(/acmg) が request.path にも反映され、
# ログイン後の next リダイレクトが /acmg 配下に正しく解決される（ASGI+root_path 未設定だと
# reverse は /acmg 付きだが request.path は裸になり、next が / になってしまう）。
if [ "$DEBUG" = "1" ]; then
    python manage.py runserver 0.0.0.0:8000
else
    gunicorn --workers 8 --threads 2 config.wsgi:application --bind 0.0.0.0:8000 -t 900
fi
