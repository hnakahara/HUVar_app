# nginx（vas と同一バージョン系）
FROM nginx:1.29.8-alpine

# 環境ごとに default.conf を切り替えてビルドする。
# デフォルトはテスト用（HTTP のみ）。本番は build arg で prod conf を指定。
ARG NGINX_CONF=containers/nginx/conf.d/default.conf
COPY ${NGINX_CONF} /etc/nginx/conf.d/default.conf
