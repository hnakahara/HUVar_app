#!/bin/sh
# コンテナの起動処理。root で準備だけを行い、サーバー本体は一般ユーザー（huvar）で動かす。
#
#   entrypoint.sh          Web（DEBUG=1: runserver / それ以外: gunicorn）
#   entrypoint.sh worker   Celery worker
#
# root で行う準備:
#   1. huvar の UID/GID をホストの /code（HUVar_app）の所有者に合わせる
#      （バインドマウント先に書いたファイルがホスト側でも同じ所有者になる）
#   2. HUVar 解析エンジンの editable install（Python のシステム領域への書き込み）
#   3. アプリが書き込む場所（media・各 migrations・locale・/static）の所有者を huvar に
#      （以前 root で動いていたときに作られたファイルも含めて直す）
set -e

APP_USER=huvar
ROLE="${1:-web}"

# huvar で実行するための前置き（環境変数＝.env の設定は引き継ぎ、HOME だけ差し替える）。
# exec でも使えるよう関数ではなくコマンド列にしている。
AS_APP="env HOME=/home/$APP_USER setpriv --reuid=$APP_USER --regid=$APP_USER --init-groups"

if [ "$(id -u)" = "0" ]; then
    # 1. UID/GID をホストの /code の所有者に合わせる（root 所有なら仮の 1000 のまま）
    host_uid=$(stat -c %u /code)
    host_gid=$(stat -c %g /code)
    if [ "$host_uid" != "0" ] && [ "$(id -u "$APP_USER")" != "$host_uid" ]; then
        groupmod -o -g "$host_gid" "$APP_USER"
        usermod -o -u "$host_uid" -g "$host_gid" "$APP_USER"
        chown -R "$APP_USER:$APP_USER" "/home/$APP_USER" /static
    fi

    # 2. HUVar 解析エンジン（acmg-classifier）をボリュームマウント先から editable install。
    # 依存パッケージとビルドバックエンド(hatchling)はイメージ build 時に導入済み
    # （containers/django/requirements-huvar.txt）なので、ここではリンクを張るだけで
    # ネットワークは使わない（--no-build-isolation）。HUVar に依存が追加された場合のみ
    # その分がダウンロードされる。
    if [ -f /huvar/pyproject.toml ]; then
        pip install --no-build-isolation --no-input --root-user-action=ignore -e /huvar
    fi

    # 3. 書き込み先の所有者
    mkdir -p /code/media
    for d in /code/media /code/locale /static \
             /code/accounts/migrations /code/analysis/migrations /code/api/migrations; do
        if [ -e "$d" ]; then
            chown -R "$APP_USER:$APP_USER" "$d"
        fi
    done

    # 読み取り専用の参照データが huvar から読めるか（読めないと解析が失敗する）
    for d in /huvar /data /tools; do
        if [ -e "$d" ] && ! $AS_APP test -r "$d" -a -x "$d"; then
            echo "WARNING: $d is not readable by $APP_USER (uid $(id -u "$APP_USER"));" \
                 "check the permissions on the host" >&2
        fi
    done
    RUN="$AS_APP"
else
    # 既に一般ユーザーで起動された場合（docker run --user 等）は準備を省いてそのまま動く
    RUN=""
fi

if [ "$ROLE" = "worker" ]; then
    exec $RUN celery -A config worker --loglevel=info --concurrency=1
fi

$RUN python manage.py makemigrations --noinput
$RUN python manage.py migrate --noinput
$RUN python manage.py collectstatic --noinput
# 翻訳カタログ(.po)を .mo にコンパイル（i18n: 日本語/英語）
$RUN python manage.py compilemessages

# DEBUG=1（テスト）は runserver、DEBUG=0（本番）は gunicorn(WSGI)
# WSGI を使う理由: FORCE_SCRIPT_NAME(/acmg) が request.path にも反映され、
# ログイン後の next リダイレクトが /acmg 配下に正しく解決される（ASGI+root_path 未設定だと
# reverse は /acmg 付きだが request.path は裸になり、next が / になってしまう）。
if [ "$DEBUG" = "1" ]; then
    exec $RUN python manage.py runserver 0.0.0.0:8000
else
    exec $RUN gunicorn --workers 8 --threads 2 config.wsgi:application --bind 0.0.0.0:8000 -t 900
fi
