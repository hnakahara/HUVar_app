#!/bin/sh
set -e

# HUVar 解析エンジン（acmg-classifier）をボリュームマウント先から editable install。
# 依存パッケージとビルドバックエンド(hatchling)はイメージ build 時に導入済み
# （containers/django/requirements-huvar.txt）なので、ここではリンクを張るだけで
# ネットワークは使わない（--no-build-isolation）。HUVar に依存が追加された場合のみ
# その分がダウンロードされる。
if [ -f /huvar/pyproject.toml ]; then
    pip install --no-build-isolation --no-input -e /huvar
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
