# HUHVar GUI アプリケーション 要件定義書

**版**: v0.3（ドラフト）
**作成日**: 2026-06-15
**最終更新**: 2026-06-15
**対象ディレクトリ**: `C:\Users\Nakahara\workspace\HUHVar_app`

---

## 実装進捗トラッキング

> 各要件の実装状況を本表で管理する。ステータスは PR/コミット時に更新すること。

**ステータス凡例**: ⬜ 未着手 / 🟡 実装中 / 🟢 完了 / ⏸ 保留 / ❌ 取り下げ

### 全体マイルストーン

| # | フェーズ | 状態 | 備考 |
|---|----------|------|------|
| M0 | 技術検証スパイク（TransVar 3.9 コンテナ / HUHVar 3.11 同居） | ⬜ | docker build / compose up は実サーバーで要検証 |
| M1 | docker compose 基盤（db/redis/app/worker/transvar/web・環境分離） | 🟢 | WSL で `docker compose up` 起動確認済（db/redis/app/web 健全・migrate・/acmg 配信・ログイン・admin 動作）。本番 HTTPS=443/HTTP=80 に確定 |
| M2 | 認証・ユーザー管理（MFA 必須・アカウントリクエスト） | 🟢 | 本番実機で確認完了: ログイン→MFA登録(QR)→検証→解析トップ、強制ミドルウェア動作、admin ユーザー追加/承認。QR は data-URI img で表示 |
| M3 | 入力 & TransVar 変換 & MANE 限定 | 🟢 | 本番実機で確認: MANE map size=19288、TP53:c.742C>T → MANE Select 1件(chr17:7674221G>A/p.R248W)。genome/cDNA/protein・c./p.省略・候補選択 UI 実装済 |
| M3 | 入力 & TransVar 変換 & MANE 限定 | ⬜ | |
| M4 | 単一変異解析・結果画面・手動編集 | 🟢 | 本番で実解析確認(VEP/エンジン導入・/ddrive/data 配置)。CLI explain・ブラウザ単一解析で全クライテリア＋分類を確認。手動編集・JSON エクスポートあり |
| M5 | バッチ（VCF）解析・TSV ダウンロード・Celery ジョブ | 🟢 | 本番で実解析確認: VCF アップロード→Celery→run_pipeline→TSV ダウンロード成功(ブラウザ/API 両方)。保持1時間 |
| M6 | 変異結果キャッシュ（DB 登録・参照データ更新で無効化） | 🟢 | 単一(web/API)＋バッチ実装。署名=参照ファイル size+mtime+エンジン版の SHA-256。バッチは VCF 列挙→全件キャッシュ済みならエンジン非実行で TSV 即生成、未済があればフル解析して全件キャッシュ。手動編集(supplement)は非キャッシュ |
| M7 | REST API（VAS 連携・トークン認証） | 🟢 | 本番で実解析確認: classify が分類＋全クライテリア JSON を返却、jobs→done→result.tsv ダウンロード成功。トークン認証・未認証 401 |
| M8 | セキュリティ強化・本番公開準備 | 🟡 | CSP/Permissions-Policy 等を Django ミドルウェアで全レスポンス付与、監査ログ(login/失敗/logout・解析/編集/バッチ・API)、API アップロード検証、CI(pip-audit/bandit/ruff)、SECRET_KEY の $ 回避注記。残: 本番ヘッダ実機確認・脆弱性対応の運用 |
| M9 | 多言語対応（i18n: 日本語/英語 切替） | 🟢 | i18n 基盤＋言語切替UI、全ユーザー向けテンプレ(base/index/login/account_request/single_input/single_resolve/single_result/batch_upload/batch_status/batch_list)を翻訳対象化、en .po 整備、gettext/compilemessages。本番で切替動作確認済。残(軽微): ビューのフラッシュメッセージ・admin は順次 |

### 機能要件ステータス

| 要件 ID | 概要 | 状態 |
|---------|------|------|
| FR-AUTH-1..6 | 認証・ユーザー管理・MFA 必須 | 🟡 | 自己登録禁止・admin 管理・login・MFA 必須(TOTP登録/検証/強制ミドルウェア)実装済。実機検証は未 |
| FR-IN-1..4 | 入力（genome / cDNA / protein / VCF） | 🟢 | 単一3形式＋VCF バッチを実機確認 |
| FR-CONV-1..5 | TransVar 変換・MANE 限定・候補選択 | 🟢 | 実機確認済(MANE Select 限定・c./p.省略可・GRCh37/38・複数候補は選択UI) |
| FR-SINGLE-1..6 | 単一変異解析・全クライテリア表示・手動編集 | 🟢 | 本番で実解析確認(同期・全28項目＋根拠・strength手動編集・JSON出力) |
| FR-BATCH-1..4 | VCF 解析・TSV ダウンロード・履歴 | 🟢 | 本番で実解析確認(アップロード/Celery直列/TSV/履歴/保持1時間) |
| FR-CACHE-1..4 | 変異結果キャッシュ・参照データ更新で無効化 | 🟢 | 単一・バッチとも実装(DB登録・署名=size+mtime+エンジン版で自動無効化・手動編集は非キャッシュ)。バッチは全件キャッシュ済みならエンジン非実行 |
| FR-API-1..4 | REST API（トークン認証） | 🟢 | 本番で実解析確認(classify JSON・jobs→result.tsv、トークン認証・throttle) |
| FR-I18N-1..6 | 多言語対応（日本語/英語 切替） | 🟢 | 切替UI・LocaleMiddleware・set_language・en .po・compilemessages。全ユーザー向け画面を翻訳。ビューのメッセージ/admin は軽微残 |
| NFR-SEC-* | セキュリティ | 🟡 | TLS/HSTS・nginx レート制限・セキュリティヘッダ(CSP等)・MFA必須・axes・監査ログ・アップロード検証・秘密の env 管理・CI 脆弱性スキャン 実装。実機ヘッダ確認と運用は継続 |
| NFR-ENV-* | 環境分離 | ⬜ |
| NFR-PORT-* | ポート設計 | ⬜ |
| NFR-OPS-* | 性能・運用・結果保持 | ⬜ |

---

## 1. 目的・背景

`HUHVar`（パッケージ名 `acmg-classifier` / ACMG 2015 + ClinGen SVI 準拠の完全ローカル型バリアント病的性分類ツール）を、Web ブラウザから操作できる GUI として公開する。将来的に `vas` から API 経由で解析結果を取得できるようにする。全世界の医療機関に向けて公開するため、強固なセキュリティを必須とする。

## 2. 対象ツールの現状整理（調査結果）

| 項目 | 内容 |
|------|------|
| パッケージ | `acmg-classifier` v0.1.0（`requires-python >=3.11`） |
| CLI | `acmg-classify classify <vcf>`（VCF 一括→TSV）／`explain <CHROM POS REF ALT>`（単一変異） |
| 入力 | 現状は **genome coordinate（VCF / CHROM POS REF ALT）のみ**。cDNA・protein 入力は未対応 |
| クライテリア | PVS1, PS1–4, PM1–6, PP1–5, BA1, BS1–4, BP1–7（全 28 項目を毎回 `CriteriaResult` として出力。not_met も保持） |
| 各クライテリア出力 | `triggered` / `strength` / `direction` / `evidence`（根拠テキスト） / `suppressed` / Bayesian `points` |
| 分類 | ルールベース（ACMG 2015）と Bayesian スコア（Tavtigian 2020 + Bergquist 2024）の両方 |
| 手動上書き | `--supplement` TSV（列: `variant_id, criterion, strength, evidence`）と `--evidence CRITERION:STRENGTH[:NOTE]`、`merge` / `manual-only` モード |
| 出力 | TSV / JSON / リッチコンソールレポート |

**重要**: HUHVar のエンジン層は「全クライテリアの根拠表示」「重み（strength）含む手動編集」「VCF→TSV」を **すでに内部的にサポート** している。GUI 層はこれらをラップし、不足機能（cDNA/protein 入力、TransVar 変換、MANE 限定、認証、API、キャッシュ）を追加する位置づけ。

## 3. システム構成

### 3.1 アーキテクチャ

```
[Browser] / [VAS server]
      │ HTTPS (本番) / HTTP (テスト)   ※ vas と同一ドメイン、サブパス /acmg
      ▼
┌─────────────┐   ┌──────────────────────┐   ┌──────────────┐
│   web       │──▶│        app           │──▶│     db       │
│  (nginx)    │   │ (Django 4.2          │   │ (PostgreSQL  │
│  TLS終端    │   │  + gunicorn/uvicorn) │   │  16)         │
│  /acmg配信  │   │  Python 3.11         │   │  変異キャッシュ│
│  レート制限 │   └───┬───────────┬──────┘   └──────────────┘
│  WAF/ヘッダ │       │ジョブ投入  │ 内部HTTP（変換要求）
└─────────────┘       ▼           ▼
            ┌──────────────┐  ┌──────────────────────┐
            │ redis(broker)│  │     transvar         │
            └──────┬───────┘  │  TransVar 2.5.10     │
                   │          │  Python 3.9          │
                   ▼          │  cDNA/protein↔genome │
            ┌──────────────┐  │  MANE Select 限定    │
            │ worker       │  │  設定: /tools/transvar│
            │ (Celery)     │  └──────────────────────┘
            │ HUHVar engine│
            │ Python 3.11  │  ← ジョブを順番に処理（concurrency=1）
            └──────────────┘
```

### 3.2 コンテナ構成（docker compose）

`vas` の構成を踏襲しつつ、**コンテナ名・ホストポート・ボリュームを vas と分離**する。
**TransVar は Python 依存（3.9 系で検証済み）のため独立コンテナとして切り出し**、app/worker（Python 3.11）から内部 HTTP で呼び出す。

| サービス | 役割 | ランタイム | container_name | ホスト公開 |
|----------|------|-----------|----------------|-----------|
| `db` | PostgreSQL 16（業務データ＋変異キャッシュ） | postgres:16 | `huhvar-postgres` | 公開しない |
| `redis` | Celery ブローカー / 結果バックエンド | redis | `huhvar-redis` | 公開しない |
| `app` | Django 4.2（Web/API） | **Python 3.11** | `huhvar-app` | `expose: 8000`（非公開） |
| `worker` | Celery ワーカー（HUHVar 解析実行） | **Python 3.11**（app と同一イメージ） | `huhvar-worker` | なし |
| `transvar` | cDNA/protein↔genome 変換サービス | **Python 3.9** + transvar 2.5.10 | `huhvar-transvar` | `expose: 5000`（内部のみ） |
| `web` | nginx（TLS 終端・静的配信・レート制限） | nginx | `huhvar-web` | **test: 28080 / prod: 28443・28080** |

- **ジョブ処理**: バッチ（VCF）は Redis をブローカーに Celery で **順番に処理**（ワーカーは concurrency=1 の直列実行）。**単一変異解析は同期実行（即応答）** とし、Celery を経由しない。
- **TransVar サービス**: Python 3.9 ベースの軽量 HTTP サービス（FastAPI/Flask 等）。エンドポイント例 `POST /convert`（入力: cDNA/protein/genome + assembly、出力: 解決済み genome coordinate・transcript・HGVS c./p.・MANE 候補リスト）、`GET /health`、`GET /refversion`（参照データの版情報）。
- ファイル分割: `docker-compose.yml`（テスト）／`docker-compose.prod.yml`（本番）。環境変数: `.env` / `.env.prod`（リポジトリ非追跡）。
- ボリューム: `~/huhvar_db_data`、HUHVar 解析データ、**TransVar 設定・参照（vas と同一の `~/tools` → `/tools/transvar/...` をマウント）**、ログ、tmp を用意。

### 3.3 バージョン方針（vas 整合）

| コンポーネント | バージョン | 配置 | 備考 |
|----------------|-----------|------|------|
| Django | 4.2（vas 同一） | app/worker | 3.11 で動作 |
| Python（app/worker） | **3.11** | app/worker | HUHVar の `>=3.11` 要件を満たす |
| Python（transvar） | **3.9** | transvar | TransVar 検証済みバージョン |
| PostgreSQL | 16（vas 同一） | db | |
| Redis | 安定版 | redis | Celery ブローカー |
| Celery | 5.x | app/worker | vas でも導入実績（コメントアウト） |
| nginx / gunicorn | vas 同一 | web / app | |
| TransVar | 2.5.10.20211024（vas 同一） | transvar | 独立コンテナで安定動作を確保 |

### 3.4 TransVar 参照データ（vas と共用）

- TransVar 設定ファイルは **vas と同じ位置**（`/tools/transvar/...`）に存在し、`~/tools` をマウントして利用する。**新規の参照 DB 構築は不要。**
- 設定例（hg38）:
  ```ini
  [DEFAULT]
  refversion = hg38

  [hg38]
  reference = /tools/transvar/hg38/hg38.fasta
  refseq    = /tools/transvar/hg38/hg38.refseq.gff.gz.transvardb
  ccds      = /tools/transvar/hg38/hg38.ccds.txt.transvardb
  ensembl   = /tools/transvar/hg38/hg38.ensembl.gtf.gz.transvardb
  gencode   = /tools/transvar/hg38/hg38.gencode.gtf.gz.transvardb
  ucsc      = /tools/transvar/hg38/hg38.ucsc.txt.gz.transvardb
  ```
- hg19/GRCh37 についても同様に `/tools/transvar/hg19/...` の設定を用いる（GRCh37/GRCh38 両対応）。

## 4. 機能要件

### 4.1 認証・ユーザー管理（FR-AUTH）

- **FR-AUTH-1**: ログイン必須。未認証ユーザーは解析・API を一切利用不可。
- **FR-AUTH-2**: **自己サインアップ禁止**。新規ユーザーは「アカウント発行リクエスト」フォーム（氏名・所属・メール・利用目的）からリクエスト送信のみ可能。
- **FR-AUTH-3**: リクエストは管理者へ通知（メール）。**administrator のみ** がユーザーを作成・有効化・無効化・削除できる。
- **FR-AUTH-4**: ロール: `administrator` / `general user`。ユーザー管理権限は administrator のみ。
- **FR-AUTH-5**: パスワードポリシー（最小長・複雑性）、ログイン失敗ロックアウト、セッションタイムアウト。
- **FR-AUTH-6**: **MFA（TOTP）を全ユーザーに必須化**。初回ログイン時に MFA 登録を強制し、未登録ユーザーは解析機能へアクセス不可。

### 4.2 入力（FR-IN）

3 形式に対応。`c.` / `p.` プレフィックスは **あってもなくても** 解析可能とする。

- **FR-IN-1**: genome coordinate — 例 `chr17:7674221G>A`
- **FR-IN-2**: cDNA — 例 `TP53:c.742C>T` / `TP53:742C>T`
- **FR-IN-3**: protein — 例 `TP53:p.R248W` / `TP53:R248W`
- **FR-IN-4**: VCF ファイルアップロード（複数変異一括）

### 4.3 相互変換・MANE 限定（FR-CONV）

- **FR-CONV-1**: cDNA / protein ↔ genome coordinate の相互変換が必要な場合は **TransVar**（独立コンテナ）を使用。
- **FR-CONV-2**: cDNA・protein を入力に用いる場合、対応トランスクリプトは **MANE Select を優先**する。ただし MANE Select に該当が無い変異（別アイソフォーム番号、例 SCN5A:p.E1784K 等）も解析できるよう、**MANE で座標が得られない場合は TransVar の候補トランスクリプトを提示してユーザーが選択**できる（genome 座標へ解決できれば解析可能）。genome 入力も同様（MANE 優先・無ければ候補提示）。候補は genome 座標で重複排除。
- **FR-CONV-3**: protein 入力は複数の塩基変化に対応し得るため、**複数候補が出る場合はユーザーに候補を提示し選択させる**（選択 UI）。
- **FR-CONV-4**: 変換結果（解決された CHROM/POS/REF/ALT・transcript・HGVS c./p.）を解析前にユーザーへ提示。
- **FR-CONV-5**: **GRCh37 / GRCh38 両対応**。ユーザーがアセンブリを指定可能とし、TransVar・HUHVar の両方に正しく伝播させる。

### 4.4 単一変異解析・結果画面（FR-SINGLE）

- **FR-SINGLE-1**: 1 変異を入力 → 内部で `run_single`（`explain` 相当）を **同期実行（即応答）** する（Celery ジョブ化しない）。キャッシュ命中時は DB から即返却。バッチ（VCF）のみ Celery ジョブとする。
- **FR-SINGLE-2**: 結果画面に **全 28 クライテリアを必ず表示**（triggered / not_met / suppressed を区別）。
- **FR-SINGLE-3**: 各クライテリアに対し **判定根拠（`evidence` テキスト）と strength・direction・Bayesian points** を提示。
- **FR-SINGLE-4**: ルールベース分類・Bayesian 分類・最終判定・警告を表示。
- **FR-SINGLE-5**: **手動編集機能** — 各クライテリアの triggered/strength（重み）・evidence をユーザーが画面上で変更でき、再分類結果（スコア・最終判定）が即時に反映される。内部的には supplement（merge / manual-only）機構を利用。
- **FR-SINGLE-6**: 編集後の結果を保存（DB）・エクスポート（JSON/TSV）可能。

### 4.5 バッチ（VCF）解析（FR-BATCH）

- **FR-BATCH-1**: VCF アップロード → `classify` 相当を **Celery ジョブとして投入し順番に処理**。進捗表示。
- **FR-BATCH-2**: 完了後、結果を **TSV でダウンロード**（HUHVar の `tsv_writer` 出力を踏襲。全クライテリアの triggered/strength/evidence 列を含む）。
- **FR-BATCH-3**: ジョブ履歴の一覧・再ダウンロード（ユーザー単位）。
- **FR-BATCH-4**: アップロードサイズ上限・行数上限・タイムアウトを設定。

### 4.6 変異結果キャッシュ（FR-CACHE）

- **FR-CACHE-1**: 一度解析した変異の分類結果（自動判定）を **DB に登録（永続キャッシュ）** する。キーは「正規化変異（assembly + CHROM + POS + REF + ALT）＋ 解析エンジン版 ＋ 参照データ版署名」。
- **FR-CACHE-2**: 2 回目以降の同一変異リクエストは、**参照データが未更新であれば DB のキャッシュから返す**（HUHVar を再実行しない）。単一変異・バッチ内の各変異の双方に適用。
- **FR-CACHE-3**: **使用している参照データ（HUHVar の参照配列・OpenSpliceAI モデル・phyloP bigWig・遺伝子リスト等、および TransVar 参照）のいずれかが更新された場合、キャッシュを無効化**し、次回アクセス時に再解析する。更新検知は **参照データ各ファイルの「ハッシュ ＋ サイズ ＋ 更新日時（mtime）」を組み合わせた版署名** で行う。いずれかが変化したら無効化する。
- **FR-CACHE-4**: ユーザーの **手動編集結果（CriterionEdit）はキャッシュとは別管理**とし、自動判定キャッシュを上書きしない（編集はユーザー/ジョブに紐づく）。

> 注: 本キャッシュ（自動判定結果）は参照データ更新まで永続保持する。NFR-OPS-3 の「保持期間 1 時間」は **ジョブ成果物（アップロード VCF・生成 TSV・ジョブ単位の結果ファイル）** に適用される別物である。

### 4.7 API（FR-API）

将来 `vas` から呼び出すための REST API。

- **FR-API-1**: **トークン認証方式**（DRF TokenAuthentication 等）。`/acmg/api/` 配下。トークンは administrator が発行・失効管理。
- **FR-API-2**: 単一変異解析エンドポイント（入力: genome/cDNA/protein + assembly、出力: 全クライテリア＋根拠＋分類の JSON。キャッシュ命中時は DB から即時返却）。
- **FR-API-3**: バッチ（VCF）解析エンドポイント（ジョブ投入 → ステータス取得 → 結果取得）。
- **FR-API-4**: レート制限（nginx `limit_req` ＋ DRF throttling）。

### 4.8 多言語対応（i18n）（FR-I18N）

全世界公開のため、UI を **日本語・英語**で切り替え可能とする。

- **FR-I18N-1**: 日本語・英語の 2 言語をサポートし、画面上の**言語切替**（ヘッダのリンク/メニュー）で即時に切り替えられる。
- **FR-I18N-2**: Django 標準の i18n を用いる（`USE_I18N=True`、`django.middleware.locale.LocaleMiddleware`、`gettext`／テンプレートの `{% translate %}`・`{% blocktranslate %}`、`locale/<lang>/LC_MESSAGES/*.po,*.mo`）。テンプレート・フォームのラベル・フラッシュメッセージ・バリデーション文言を翻訳対象にする。
- **FR-I18N-3**: 既定言語は**日本語**。ユーザーの選択は `django.views.i18n.set_language` でセッション/Cookie（`django_language`）に保持。`LANGUAGES = [("ja", "日本語"), ("en", "English")]`、`LOCALE_PATHS = [BASE_DIR/"locale"]`。
- **FR-I18N-4**: サブパス `/acmg` 配下でも言語切替が正しく動作すること（`set_language` の `next` を `/acmg` 配下に解決。`FORCE_SCRIPT_NAME` と整合）。URL プレフィックス方式（`i18n_patterns`：`/acmg/en/...`）を採る場合も `FORCE_SCRIPT_NAME` と矛盾しないよう設計する。
- **FR-I18N-5**: ACMG クライテリア名・strength 等の専門略語（PVS1, Strong 等）は原則そのまま表示し、**説明文・UI ラベル・操作文言**を翻訳対象とする。エビデンス本文（解析エンジン由来の英語）は原文表示を基本とする。
- **FR-I18N-6**: API（`/acmg/api/`）は言語非依存（機械可読の英語キー）を既定とし、必要に応じて `Accept-Language` で説明文言のみ切替可能とする。

## 5. 非機能要件

### 5.1 セキュリティ（NFR-SEC）— 全世界公開・IP 制限なし前提で強化

- **NFR-SEC-1**: 本番は全通信 HTTPS（TLS 1.2/1.3）、HSTS、HTTP→HTTPS リダイレクト。
- **NFR-SEC-2**: nginx でレート制限（`limit_req_zone`）、`client_max_body_size` 制限、悪性 IP の `deny` リスト運用。
- **NFR-SEC-3**: セキュリティヘッダ（CSP, X-Frame-Options/`DENY`, X-Content-Type-Options, Referrer-Policy, Permissions-Policy）。
- **NFR-SEC-4**: Django セキュリティ設定（`SECURE_*`、`SESSION_COOKIE_SECURE`、`CSRF_COOKIE_SECURE`、`SECURE_HSTS_*`、`ALLOWED_HOSTS` 厳格化、`DEBUG=False`、サブパス `/acmg` 対応の `FORCE_SCRIPT_NAME`/`USE_X_FORWARDED_*`）。
- **NFR-SEC-5**: 認証強化（ログイン失敗ロックアウト/`django-axes` 等、強パスワード、**MFA 必須**）。API トークンは安全に保管・失効可能とする。
- **NFR-SEC-6**: アップロードファイルの検証（拡張子・MIME・サイズ・内容スキャン）、解析はサンドボックス的に実行、パストラバーサル防止。
- **NFR-SEC-7**: 秘密情報は env / シークレット管理。リポジトリ非追跡。
- **NFR-SEC-8**: 監査ログ（ログイン、ユーザー作成、解析実行、結果編集、API トークン発行/失効）。
- **NFR-SEC-9**: 依存パッケージ脆弱性スキャン（CI）。
- **NFR-SEC-10**: 自己登録禁止・管理者のみユーザー作成（FR-AUTH と一体）。
- **NFR-SEC-11**: 内部サービス（transvar・db・redis）はホストに公開せず、compose ネットワーク内のみで到達可能とする。Redis にはパスワードを設定。
- 実装時、`security-reviewer` エージェントによるレビューを必須とする。

### 5.2 環境分離（NFR-ENV）

- **NFR-ENV-1**: テスト環境（`docker-compose.yml` + `.env`、`DEBUG=True` 可、**HTTP 28080 のみ（HTTPS 不使用）**）と本番環境（`docker-compose.prod.yml` + `.env.prod`、`DEBUG=False`、**HTTPS 28443**）を分離して起動可能。
- **NFR-ENV-2**: DB・Redis・ボリューム・コンテナ名を環境間で分離。

### 5.3 ポート設計（NFR-PORT）

サーバーで `vas`（`80/443/20080`）および他 compose が稼働中のため衝突を回避。**外部公開ポート 28080/28443 は使用可能であることを確認済み。**

| 用途 | テスト環境 | 本番環境 |
|------|-----------|----------|
| web（HTTP） | ホスト `28080` → コンテナ 80 | `80`（→ HTTPS リダイレクト） |
| web（HTTPS） | **不使用** | ホスト `443` → コンテナ 443 |
| app（Django） | `expose 8000`（非公開） | 同左 |
| transvar | `expose 5000`（内部のみ） | 同左 |
| redis | 非公開（ネットワーク内のみ） | 同左 |
| db（PostgreSQL） | **ホスト非公開**（ネットワーク内のみ） | 同左 |

- コンテナ名は `huhvar-` プレフィックスで vas と分離（内部衝突回避）。テスト用ホストポートは vas の 20080 と衝突しない 28080 を使用。
- **本番は A 方式（単一フロント nginx 統合）で確定**：vas の既存 nginx（80/443 を bind）が単一フロントとなり、`/acmg/` を `huhvar-app:8000` へプロキシする。**HUHVar 本番スタックはホストポートを公開しない**（自前 web nginx は廃止）。ポート衝突は発生しない。
  - vas nginx 設定（`vas/containers/nginx/conf.d/default.conf`）に `upstream acmg_app { server huhvar-app:8000; }` と `location /acmg/`・`location /acmg/api/` を追記済み。
  - `huhvar-app` は vas のネットワーク（external `vas_default`）へ参加。vas 上の `app` 別名と衝突しないよう HUHVar の本番サービス名は `acmgapp`（コンテナ名 `huhvar-app`）とする。
  - 静的ファイルは **WhiteNoise** がアプリ配信（vas 側の静的設定・ボリューム共有は不要）。`FORCE_SCRIPT_NAME=/acmg` は WhiteNoise が自動で静的プレフィックスから除去。
  - 反映には vas の web イメージ再ビルド＆再起動が必要（運用手順）。

### 5.4 性能・運用（NFR-OPS）

- **NFR-OPS-1**: 解析は **Celery + Redis** による非同期ジョブで **順番に処理**（直列）。大規模 VCF でもタイムアウトしないよう nginx/gunicorn のタイムアウトを調整（vas は 3600s）。
- **NFR-OPS-2**: HUHVar 参照データ（配列・OpenSpliceAI モデル・phyloP bigWig 等、数 GB）と TransVar 参照（`/tools/transvar`、vas と共用）はボリュームで提供。GRCh37/GRCh38 両アセンブリ分を用意。
- **NFR-OPS-3**: **ジョブ成果物（アップロード VCF・生成 TSV・ジョブ単位の結果ファイル）の保持期間は 1 時間**。経過後は自動削除（現状はジョブ一覧アクセス時に随時クリーンアップ。将来 Celery beat 化可）。※ 変異単位の自動判定キャッシュ（FR-CACHE）は対象外で、参照データ更新まで永続保持。
- **NFR-OPS-4**: ログ収集、ヘルスチェック（DB `pg_isready`、redis `PING`、transvar `/health`）。

## 6. データモデル（概要）

- `User`（Django 標準 + ロール + MFA デバイス）
- `ApiToken`（API 用トークン: 所有者・発行日時・失効フラグ）
- `AccountRequest`（アカウント発行リクエスト: 氏名・所属・メール・目的・状態）
- `AnalysisJob`（種別: single/batch、入力、assembly、ステータス、Celery タスク ID、結果ファイル、所有者、作成日時、**成果物有効期限 = 作成 +1 時間**）
- `VariantResultCache`（変異キャッシュ: 正規化変異キー〔assembly, chrom, pos, ref, alt〕＋ エンジン版 ＋ **参照データ版署名**、分類結果 JSON、作成/更新日時。FR-CACHE）
- `ReferenceDataVersion`（使用中参照データ〔HUHVar 各データ・TransVar〕の版署名 = ファイルごとの ハッシュ＋サイズ＋mtime。更新検知とキャッシュ無効化に使用）
- `VariantResult`（単一変異の最終結果・分類・スコア。ジョブ/ユーザー紐づけ）
- `CriterionEdit`（手動編集: criterion・strength・evidence・編集者・日時）— supplement 相当
- `AuditLog`

## 7. ディレクトリ構成案（HUHVar_app）

```
HUHVar_app/
├── docker-compose.yml          # テスト（HTTP 28080）
├── docker-compose.prod.yml     # 本番（HTTPS 28443）
├── .env / .env.prod
├── requirements.txt            # Django4.2, psycopg2, celery, redis, HUHVar(acmg-classifier) ほか（app/worker 用）
├── containers/
│   ├── django/   (Dockerfile, entrypoint.sh)       # Python 3.11（app/worker 共用イメージ）
│   ├── transvar/ (Dockerfile, app.py)              # Python 3.9 + transvar 2.5.10
│   ├── nginx/    (Dockerfile, conf.d/default.conf)  # /acmg サブパス
│   └── postgres/ (Dockerfile)                      # postgres:16
├── manage.py
├── config/        # Django settings (base/test/prod 分割), celery.py
├── accounts/      # 認証・ユーザー管理・MFA・アカウントリクエスト・API トークン
├── analysis/      # 解析（single/batch）・Celery タスク・結果編集・キャッシュ・保持期間管理
├── transvar_client/ # transvar サービスへの HTTP クライアント（MANE 限定・候補選択）
├── api/           # REST API (DRF, トークン認証)
├── templates/ , static/
└── docs/
    └── requirements.md         # 本書
```

> 注: HUHVar 解析データ（数 GB）と TransVar 参照（`/tools/transvar`、vas と共用）はホストパスからボリュームマウントし、リポジトリには含めない。

## 8. 未確定・要確認事項

| # | 項目 | 状態 |
|---|------|------|
| 1 | TransVar 独立コンテナ（3.9）の内部 API 仕様の詳細確定 | 要設計 |
| 2 | 公開ポート → 28080/28443 で確定 | ✅ 解決 |
| 3 | MFA 必須化 → 必須で確定 | ✅ 解決 |
| 4 | protein 複数候補 → 選択 UI で確定 | ✅ 解決 |
| 5 | ドメイン → vas と同一・`/acmg` サブパスで確定 | ✅ 解決 |
| 6 | アセンブリ → GRCh37 / GRCh38 両対応で確定 | ✅ 解決 |
| 7 | ジョブ成果物の保持期間 → 1 日で確定 | ✅ 解決 |
| 8 | API 認証 → **トークン方式**で確定 | ✅ 解決 |
| 9 | 非同期ジョブ基盤 → **Celery + Redis（直列処理）**で確定 | ✅ 解決 |
| 10 | TransVar 参照 → vas と共用（`/tools/transvar`）、DB 構築不要で確定 | ✅ 解決 |
| 11 | 参照データ版署名 → **ファイルごとの ハッシュ＋サイズ＋mtime** で確定 | ✅ 解決 |
| 12 | 単一変異解析 → **同期実行（即応答）**、バッチのみ Celery で確定 | ✅ 解決 |

---

## 変更履歴

| 版 | 日付 | 変更内容 |
|----|------|----------|
| v0.1 | 2026-06-15 | 初版ドラフト |
| v0.2 | 2026-06-15 | TransVar を Python 3.9 独立コンテナ化／テスト環境は HTTP のみ／MFA 必須化／protein 候補選択 UI／vas 同一ドメイン `/acmg` サブパス／GRCh37・38 両対応／ジョブ成果物保持 1 日／実装進捗トラッキング追加 |
| v0.3 | 2026-06-15 | API をトークン認証に確定／Celery + Redis で直列ジョブ処理（redis・worker コンテナ追加）／TransVar 参照は vas と共用（`/tools/transvar`、DB 構築不要）／変異結果キャッシュ（FR-CACHE: DB 永続登録・参照データ更新で無効化）追加／データモデルに ApiToken・VariantResultCache・ReferenceDataVersion 追加 |
| v0.4 | 2026-06-15 | 参照データ版署名を「ファイルごとの ハッシュ＋サイズ＋mtime」に確定／単一変異解析は同期実行（即応答）・バッチのみ Celery に確定。全項目確定 |
| v0.5 | 2026-06-15 | M1 基盤実装着手。docker compose(test/prod)・各 Dockerfile・entrypoint・nginx(/acmg)・transvar サービス雛形・Django プロジェクト(config 設定分割/celery)・accounts/analysis/api/transvar_client アプリ・データモデル・テンプレートを作成。全 Python ファイル構文検証 OK。進捗トラッカー更新（M1/M2/関連 FR を 🟡） |
| v0.6 | 2026-06-15 | M1 を WSL で起動検証（migrate・/acmg 配信・ログイン・admin・ユーザー追加/承認）。修正: libpq-dev 追加(psycopg2 ビルド)・axes バックエンド名(AxesStandaloneBackend)・テスト用 CSRF_TRUSTED_ORIGINS(localhost:28080)・compose version 行削除。本番 HTTPS=443/HTTP=80 に確定。M1 を 🟢 に更新 |
| v0.7 | 2026-06-15 | 本番を A 方式（vas nginx を単一フロントに流用）で確定。HUHVar 本番 compose を改修（自前 web 廃止・ホストポート非公開・サービス名 acmgapp・external network vas_default 参加）。WhiteNoise 導入でアプリ静的配信。vas nginx に upstream acmg_app と /acmg/・/acmg/api/ ロケーションを追記。HUHVar 本番 nginx conf(default.prod.conf)は A 方式では未使用 |
| v0.8 | 2026-06-15 | M2: MFA 必須化を実装。MFAEnforcementMiddleware(OTP未検証の認証ユーザーを遮断)・TOTP 登録(QR/SVG・Pillow不要)・検証フロー・LOGIN_REDIRECT_URL=mfa_setup・OTP_TOTP_ISSUER 追加。accounts に middleware/mfa views/urls/テンプレート追加。構文検証 OK。実機検証待ち |
| v0.9 | 2026-06-15 | GitHub リポジトリ hnakahara/HUHVar_app(Private) 作成・初期コミット push(.env系は除外)。本番 A 方式を実機疎通確認: vas nginx → huhvar-app 経由で https://<domain>/acmg/api/health/ が {"status":"ok"} を返却。ポート無衝突の単一フロント構成が稼働 |
| v0.10 | 2026-06-15 | 本番ログイン後に next が /acmg 抜き(/)になり vas ルートへ飛ぶ不具合を修正。原因は ASGI+FORCE_SCRIPT_NAME 不整合(reverse は /acmg 付き・ASGIRequest.path は root_path 未設定で裸)。本番起動を WSGI(gunicorn config.wsgi)へ変更。あわせて vas とドメイン共有のためクッキーを分離(SESSION/CSRF_COOKIE_NAME=huhvar_*、PATH=/acmg/)。別件: prod の ${REDIS_PASSWORD} 未解決と SECRET_KEY 内 $ の compose 展開警告は要対処(--env-file .env.prod 運用＋$を含まない秘密へ再生成) |
| v0.11 | 2026-06-15 | redis 認証修正: requirepass/healthcheck をコンテナ env(env_file)から取得($$ シェル展開)し compose の ${REDIS_PASSWORD} 依存を排除(worker の AUTH エラー解消)。500 の原因を特定: nginx が /acmg をストリップ転送しつつ SCRIPT_NAME ヘッダを送ると gunicorn(WSGI) が PATH_INFO 分割で IndexError。全 nginx 設定(vas/HUHVar test/prod)から proxy_set_header SCRIPT_NAME を削除し FORCE_SCRIPT_NAME に一本化 |
| v0.12 | 2026-06-15 | M2 を 🟢(本番実機で MFA 確認完了)。MFA QR を data-URI img 化。M3 着手: TransVar サービス /convert を本実装(vas 移植: canno/panno/ganno --refseq --gseq、MANE summary で MANE Select 限定、protein 3→1字、genome は g. 補完、複数候補返却)。Django 単一変異入力フロー(SingleVariantForm→transvar_client→候補選択 single_resolve→確認 single_analyze)とテンプレート追加。解析実行(run_single 連携)は M4 |
| v0.13 | 2026-06-16 | 多言語対応(i18n: 日本語/英語 切替)要件を追加(FR-I18N-1..6)。Django 標準 i18n(LocaleMiddleware/gettext/{% translate %}/set_language)、既定 ja、/acmg 整合、専門略語は原文・UI 文言を翻訳対象。マイルストーン M9 として計画(実装は後続) |
| v0.14 | 2026-06-16 | M3 完了(🟢)。MANE summary の列名が vas 配置版で RefSeq_nuc_major のため map が空だった不具合を修正(RefSeq_nuc→major/minor フォールバック)。本番実機で TP53:c.742C>T→MANE Select 1件(chr17:7674221G>A/p.R248W) を確認。FR-CONV 🟢、FR-IN 🟡(VCF は M5) |
| v0.15 | 2026-06-16 | M4 実装(🟡)。analysis/engine.py で acmg_classifier.run_single を同期実行(Python 直接 import、未導入/データ未配置は EngineUnavailable で graceful)。single_analyze→結果保存(AnalysisJob/VariantResult)→single_result で全クライテリア(28項目)＋根拠＋分類(2015/Bayesian)表示。single_edit で strength/evidence 手動編集→supplement(merge)再分類＋CriterionEdit 監査記録。single_export で JSON 出力。実機検証は参照データ(/data)配置後 |
| v0.16 | 2026-06-16 | M5 実装(🟡)。VCF アップロード→Celery(直列)投入→engine.classify_batch(run_pipeline)→全クライテリア列 TSV 生成。batch_upload/status(自動更新)/list/download、認証付き配信。成果物保持を 1日→**1時間**に変更(JOB_ARTIFACT_RETENTION_HOURS、default_expiry、cleanup_expired_jobs を一覧アクセス時に実行)。MEDIA_ROOT 追加。実解析検証はデータ配置後 |
| (fix) | 2026-06-16 | M3 補修2件: genome 入力の種別誤判定(_infer_kind を染色体名優先に)、長い dup/indel の no_valid_transcript_found(全 transvar 呼出に --seqmax 1000 付与)。本番で genome・ERBB2:c.2314_2325dup の MANE 解決を確認 |
| v0.17 | 2026-06-16 | M7 実装(🟡)。REST API: classify(単一・query/座標直指定・複数候補返却)、jobs(VCF→Celery)・job_status・job_result(TSV)。DRF TokenAuthentication(admin 発行・公開obtainなし)＋throttle、token 認証はセッション非依存のため MFA 強制対象外。実解析検証はデータ配置後 |
| v0.18 | 2026-06-16 | M8 実装(🟡)。SecurityHeadersMiddleware(CSP/Permissions-Policy/X-Content-Type-Options/Referrer-Policy/X-Frame-Options を全レスポンスに付与、フロント nginx 非依存)。監査ログ: 認証イベント(signals: login/login_failed/logout)＋解析/編集/バッチ/API 操作を AuditLog 記録。API アップロード検証(.vcf/.vcf.gz・50MB)。CI(.github security.yml: ruff/bandit/pip-audit)。SECRET_KEY は $ 非含有を注記。M8 本番でヘッダ/監査ログ確認済。REDIS_PASSWORD の $ 起因の compose 警告は $ 無し再生成で解消(.env 注記追加) |
| v0.19 | 2026-06-16 | M9 着手(🟡)。i18n: LocaleMiddleware・LANGUAGES(ja/en)・LOCALE_PATHS、config.urls に set_language、base ヘッダに言語切替セレクタ(/acmg・FORCE_SCRIPT_NAME 整合、MFA 未検証でも set_language 許可)。base/index/login/account_request を {% translate %} 化、locale/en の django.po 整備。Dockerfile に gettext 追加、entrypoint で compilemessages、.mo は gitignore |
| v0.20 | 2026-06-16 | M9 を 🟢。残りの全ユーザー向け解析画面(single_input/single_resolve/single_result/batch_upload/batch_status/batch_list)を {% translate %} 化し en .po を拡充。本番ビルドで日英切替を確認(/acmg/single 含む)。ビューのフラッシュメッセージ/admin は軽微残として継続 |
| v0.21 | 2026-06-16 | 実解析の本番投入。app/worker イメージに ensembl-vep=111＋samtools/bcftools/htslib を micromamba で内蔵(vas と独立)、vep は専用ラッパーで conda perl 実行(システム python:3.11 維持)。参照データを /ddrive/data→/data にマウント。エンジンは app/worker 双方で pip install -e /huhvar(worker は command 上書きで entrypoint を通らないため command に追加)。M4(単一)・M5(バッチ)・M7(API) を本番実データで検証完了し 🟢。FR-IN/SINGLE/BATCH/API も 🟢 |
| v0.22 | 2026-06-16 | M6(🟡) 変異結果キャッシュ。analysis/cache.py: cached_classify_single が VariantResultCache を参照(キー=assembly/chrom/pos/ref/alt/engine_version/refdata_signature)。署名は参照ファイルの size+mtime＋エンジン版の SHA-256 で、更新時に自動無効化。ReferenceDataVersion を admin 可視化用に更新。単一 web(single_analyze)/API(classify) に組込み。手動編集は非キャッシュ。バッチ per-variant は run_pipeline 一括のため未対応 |
| v0.23 | 2026-06-16 | M6 を 🟢。バッチキャッシュ実装: cached_classify_batch が VCF を read_vcf で列挙→VariantResultCache 照合。全件キャッシュ済みならエンジン非実行で TSV 即生成、未済が1件でもあればフル解析(VEPバッチ効率維持)し全件キャッシュ。engine.classify_vcf 追加、_to_display に変異情報追加。バッチ TSV は display dict から自前生成(全クライテリア列)。tasks は cached_classify_batch を使用 |
| v0.24 | 2026-06-16 | MANE 非該当変異の解析対応(FR-CONV-2 緩和)。TransVar /convert を「MANE Select 優先・無ければ全候補transcriptへフォールバック(is_mane_select=false)」に変更し genome 座標で重複排除。結果画面に MANE 列＋非MANE 選択注記を追加。単一(web/API)は候補選択→genome 座標で解析できるため MANE に無い変異も解析可能 |
| v0.25 | 2026-06-16 | 既定マニュアルエビデンスの適用。data/shared/erepo_manual_criteria_{hg38,hg19}.tsv を解析時に既定で merge(cfg.supplement_mode=MERGE)。単一(engine.classify_single)は変異キーで該当エントリを抽出しユーザー手動編集と併合、バッチ(classify_vcf→run_pipeline supplement_path)はファイルを渡して per-variant 適用。キャッシュ署名に erepo ファイルを追加し更新で自動無効化 |
