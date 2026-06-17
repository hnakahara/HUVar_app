# HUHVar ACMG Classifier

遺伝子バリアントの病的性を **ACMG 2015 + ClinGen SVI** 基準で分類する Web アプリケーション／REST API です。
単一バリアント解析・VCF バッチ解析・結果キャッシュ・多要素認証・多言語(日本語/英語)・対話的 API ドキュメント(Swagger UI) を備え、Docker Compose で動作します。

> 既存サービス(vas)と同一ドメインの `/acmg` サブパス配下で提供する構成です。

---

## 主な機能

- **単一バリアント解析**: genome 座標 / cDNA / protein 表記に対応（`TP53:c.742C>T`、`TP53 R248W` のような空白区切りも可）。TransVar で **MANE Select** に限定して座標変換し、全 ACMG クライテリアと分類(ACMG 2015 / Bayesian)を表示。
- **手動編集と再分類**: クライテリアの strength・evidence を手動で上書きして再分類（結果は DB に保存せず画面表示のみ）。結果は **JSON / TSV** でダウンロード可能。
- **バッチ解析(VCF)**: VCF をアップロードして Celery で直列処理し、全クライテリア列付き TSV を出力。ジョブ履歴・保持期間(既定1時間)つき。
- **結果キャッシュ**: 自動判定結果を DB に保存し、参照データ更新まで再利用（バッチは全件キャッシュ済みならエンジン非実行で即時 TSV 生成）。
- **REST API**: トークン認証の API。`/api/docs`(Swagger UI)・`/api/redoc`・`/api/schema` を公開。API トークンの発行リクエストフォームつき。
- **認証・セキュリティ**: ログイン + **MFA(TOTP) 必須**、ログイン失敗ロックアウト(django-axes)、CSP(nonce)/各種セキュリティヘッダ、公開フォームの IP レート制限・ハニーポット、監査ログ、管理者へのメール通知。
- **多言語(i18n)**: 日本語 / 英語 切替。

---

## アーキテクチャ

Docker Compose による 6 サービス構成（コンテナ名は `huhvar-` プレフィックス）:

| サービス | 役割 |
|----------|------|
| `db` | PostgreSQL 16 |
| `redis` | Celery ブローカー＋キャッシュ（要パスワード） |
| `app` | Django（gunicorn/WSGI、本番） |
| `worker` | Celery ワーカー（VCF バッチを `concurrency=1` で直列処理） |
| `transvar` | TransVar 変換マイクロサービス（Python 3.9・FastAPI） |
| `web` | nginx（開発用フロント。本番は外部 nginx を流用） |

**技術スタック**: Django 4.2 / Django REST Framework / drf-spectacular / Celery / Redis / PostgreSQL / django-otp / django-axes / WhiteNoise / Docker。

### 外部依存（マウント前提）

解析エンジンと参照データはイメージに同梱せず、ボリュームマウントします（`docker-compose*.yml` 参照）:

- `~/HUHVar` → `/huhvar` … 解析エンジン `acmg_classifier`（`pip install -e` で導入）
- `/ddrive/data` → `/data` … 参照データ（FASTA / MANE / スコア等）
- `~/tools` → `/tools` … TransVar 設定・参照

---

## セットアップ

### 前提
- Docker / Docker Compose
- 上記「外部依存」の配置（解析を実行する場合）

### 1. 環境変数ファイルを用意

ルートに `.env`（開発）/ `.env.prod`（本番）を作成します。主な変数:

| 変数 | 説明 |
|------|------|
| `SECRET_KEY` | Django シークレットキー（**本番では必ず強い値を設定**） |
| `DJANGO_SETTINGS_MODULE` | `config.settings.test`（開発）/ `config.settings.prod`（本番） |
| `DEBUG` | `1`=開発 / `0`=本番 |
| `DJANGO_ALLOWED_HOSTS` | 許可ホスト（カンマ区切り） |
| `FORCE_SCRIPT_NAME` | サブパス提供時の接頭辞（例 `/acmg`） |
| `POSTGRES_NAME` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_HOST` / `POSTGRES_PORT` | DB 接続 |
| `REDIS_URL` | 例 `redis://:<password>@redis:6379/0`（**パスワード必須**） |
| `REDIS_PASSWORD` | Redis のパスワード |
| `CACHE_URL` | 任意。未指定時は `REDIS_URL` から DB を `1` に差し替えて使用 |
| `TRANSVAR_SERVICE_URL` | 既定 `http://transvar:5000` |
| `JOB_ARTIFACT_RETENTION_HOURS` | バッチ成果物の保持時間（既定 `1`） |
| `ADMIN_ADDRESS` | 通知メールの宛先 |
| `GMAIL_ADDRESS` / `GMAIL_PASS` | Gmail SMTP 送信（`GMAIL_PASS` は**アプリパスワード**） |

> `.env*` は秘密情報を含むため**コミットしないでください**（`.gitignore` 済み）。値の雛形は `.env.example` / `.env.prod.example` を参照。

### 2. 起動

**開発（HTTP, ポート 28080）**
```bash
docker compose up -d --build
# → http://localhost:28080/acmg/
```

**本番（HTTPS・外部 nginx 経由で /acmg 配下）**
```bash
docker compose -f docker-compose.prod.yml up -d --build
```

起動時に `entrypoint.sh` が `migrate` / `collectstatic` / `compilemessages` を自動実行します。

### 3. 管理者ユーザー作成
```bash
docker compose exec app python manage.py createsuperuser
```
新規ユーザーは自己登録できません。利用者は発行リクエストを送信し、管理者が Django admin で承認・作成します。

---

## 使い方

- **Web**: トップから単一/バッチ解析。初回ログイン時に MFA(認証アプリで QR 読取り) を設定。アプリ内「使い方」ページに各機能の手順あり。
- **API ドキュメント**: `/acmg/api/docs/`（Swagger UI）。右上の **Authorize** にトークンのキーを入力（`Token ` 接頭辞は自動付与）。
- **API トークン**: 管理者が発行。利用者は Swagger ページのリンクから発行リクエスト可能。
- **主なエンドポイント**: `GET /api/health/`, `GET /api/whoami/`, `POST /api/classify/`, `POST /api/jobs/`, `GET /api/jobs/<id>/`, `GET /api/jobs/<id>/result.tsv`。

---

## テスト
```bash
docker compose exec app python manage.py test
```

---

## ディレクトリ構成（抜粋）

```
config/        Django プロジェクト設定（settings: base/test/prod, celery, urls）
accounts/      認証・ユーザー・MFA・アカウント/トークン発行リクエスト・通知・CSP
analysis/      単一/バッチ解析・結果・キャッシュ・エンジン連携・Celery タスク
api/           REST API（ビュー・シリアライザ・OpenAPI）
transvar_client/  TransVar サービスへの HTTP クライアント
containers/    各サービスの Dockerfile / nginx 設定
templates/ static/ locale/  画面・静的資産・翻訳カタログ
docs/          要件定義 等
```

---

## ライセンス / 注意

- 本ツールは研究・検証用途です。臨床判断にそのまま用いないでください。
- 解析エンジン(`acmg_classifier`)・参照データ・TransVar 参照は別途用意・配置が必要です。
