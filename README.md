# HUVar ACMG Classifier Graphical User Interface

A web application and REST API that **visualizes** the
[HUVar](https://github.com/hnakahara/HUVar) `acmg_classifier` engine, classifying the pathogenicity
of genetic variants using the **ACMG 2015 + ClinGen SVI** criteria. It provides single-variant
analysis, VCF batch analysis, result caching, multi-factor authentication, internationalization
(Japanese/English), and an interactive API reference (Swagger UI). It runs via Docker Compose.

> This app is the web/visualization front end; the classification logic itself lives in the
> separate [HUVar](https://github.com/hnakahara/HUVar) repository (mounted as the `acmg_classifier`
> analysis engine).

> The app is served under the `/acmg` sub-path of the same domain shared with an existing service (vas).

## Live instance

- App: <https://1603-027.a.hiroshima-u.ac.jp/acmg/>
- API reference (Swagger UI): <https://1603-027.a.hiroshima-u.ac.jp/acmg/api/docs/>

Sign-in (and an API token, issued by an administrator) is required for most features.

---

## Input / Output

### Input

| Mode | Accepted input |
|------|----------------|
| **Single-variant** | One variant as **genome coordinate** (e.g. `chr17:7674221G>A`), **cDNA** (e.g. `TP53:c.742C>T` / `TP53 742C>T`), or **protein** (e.g. `TP53 R248W`). The `c.` / `p.` prefix is optional; input type can be auto-detected or specified explicitly. |
| **Assembly** | `GRCh38` (default) or `GRCh37`, selectable for every analysis. |
| **Batch (VCF)** | A **`.vcf` or `.vcf.gz`** file. **Maximum upload size: 50 MB** (aligned with the nginx `client_max_body_size` limit; larger files are rejected). |
| **CSpec** | For supported genes, the displayed disease/CSpec can be switched on the result page (see below). |

### Output

| Mode | Output |
|------|--------|
| **Single-variant** | All ACMG criteria with their met/unmet state and strength, the **ACMG 2015** classification and the **Bayesian (point-based)** classification, the resolved coordinate/assembly, and the affected gene/transcript. Downloadable as **JSON** or **TSV**. |
| **Batch (VCF)** | A **TSV** file with one row per variant containing all criteria columns and both classifications. Retrieved from job history; artifacts are retained for a configurable period (default **1 hour**). |
| **API** | `POST /api/classify/` returns the classification as JSON; `GET /api/jobs/<id>/result.tsv` returns the batch TSV. |

### CSpec (disease-specific) switching for supported genes

Genes with multiple ClinGen VCEP specifications — for example **`RYR1`**, **`ACTA1`**, and **`VWF`** —
are evaluated with the conservative (default) thresholds **and** each disease-specific CSpec in a single
annotation pass. On the single-variant result page you can **switch the displayed disease/CSpec** without
re-running the engine. Genes without a CSpec are simply shown with the default evaluation.

---

## Features

- **Single-variant analysis**: Accepts genome coordinates / cDNA / protein notation
  (e.g. `TP53:c.742C>T`, or space-separated `TP53 R248W`). Coordinates are converted with TransVar
  **limited to MANE Select**, then all ACMG criteria and the classification (ACMG 2015 / Bayesian) are shown.
  Single-variant analysis runs OpenSpliceAI in a **high-sensitivity setting (2000 nt flanking)**
  (batch analysis keeps the default 80 nt).
- **CSpec (disease-specific) switching**: For genes with multiple ClinGen VCEP specifications
  (e.g. `RYR1` / `ACTA1` / `VWF`), the conservative (default) evaluation plus each CSpec evaluation
  are pre-computed in a single annotation pass. The result page lets you switch the displayed
  disease/CSpec without re-running the engine.
- **Coordinate permalink (Franklin-style)**: Each result has a shareable URL of the form
  `single/v/<chrom>-<pos>-<ref>-<alt>-<assembly>/` that re-runs (or reuses the cached) analysis
  directly from the coordinates.
- **Manual editing & re-classification**: Override each criterion's strength/evidence and re-classify
  (results are shown on screen only and are **not** persisted). Results can be downloaded as **JSON / TSV**.
- **Batch analysis (VCF)**: Upload a VCF (**`.vcf` / `.vcf.gz`, max 50 MB**), processed serially via
  Celery, producing a TSV with all criteria columns. Includes job history and an artifact retention
  period (default 1 hour).
- **Result cache**: Automated results are stored in the DB and reused until reference data changes
  (a batch whose variants are all cached is served instantly without invoking the engine).
- **REST API**: Token-authenticated API. `/api/docs` (Swagger UI), `/api/redoc`, and `/api/schema`
  are exposed, with a form to request an API token.
- **Authentication & security**: Login + **mandatory MFA (TOTP)**, login lockout (django-axes),
  CSP (nonce) and security headers, IP rate limiting and honeypot on public forms, audit logging,
  and admin email notifications (login / analysis events, including the assembly, both
  ACMG 2015 / Bayesian classifications, and the list of **Met criteria**).
- **i18n**: Japanese / English switching.
- **Header link to source**: The page header links to the upstream
  [HUVar](https://github.com/hnakahara/HUVar) repository.

---

## Architecture

A six-service Docker Compose stack (container names are prefixed with `huvar-`):

| Service | Role |
|---------|------|
| `db` | PostgreSQL 16 |
| `redis` | Celery broker + cache (password required) |
| `app` | Django (gunicorn/WSGI in production) |
| `worker` | Celery worker (processes VCF batches serially with `concurrency=1`) |
| `transvar` | TransVar conversion microservice (Python 3.9 / FastAPI) |
| `web` | nginx (front proxy for development; production reuses an external nginx) |

**Tech stack**: Django 4.2 / Django REST Framework / drf-spectacular / Celery / Redis / PostgreSQL /
django-otp / django-axes / WhiteNoise / Docker.

### External dependencies (mounted)

The analysis engine and reference data are not bundled into the image; they are mounted as volumes
(see `docker-compose*.yml`):

- `~/HUVar` → `/huvar` … the `acmg_classifier` analysis engine (installed via `pip install -e`)
- `/ddrive/data` → `/data` … reference data (FASTA / MANE / scores, etc.)
- `~/tools` → `/tools` … TransVar configuration and references

---

## Setup

### Prerequisites
- Docker / Docker Compose
- The external dependencies above (when running analyses)

### 1. Create environment files

Create `.env` (development) and `.env.prod` (production) in the project root. Key variables:

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Django secret key (**must be a strong value in production**) |
| `DJANGO_SETTINGS_MODULE` | `config.settings.test` (dev) / `config.settings.prod` (prod) |
| `DEBUG` | `1` for development, `0` for production |
| `DJANGO_ALLOWED_HOSTS` | Allowed hosts (comma-separated) |
| `FORCE_SCRIPT_NAME` | Sub-path prefix when served under a path (e.g. `/acmg`) |
| `POSTGRES_NAME` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_HOST` / `POSTGRES_PORT` | Database connection |
| `REDIS_URL` | e.g. `redis://:<password>@redis:6379/0` (**password required**) |
| `REDIS_PASSWORD` | Redis password |
| `CACHE_URL` | Optional. If unset, derived from `REDIS_URL` with the DB switched to `1` |
| `TRANSVAR_SERVICE_URL` | Default `http://transvar:5000` |
| `JOB_ARTIFACT_RETENTION_HOURS` | Retention time for batch artifacts (default `1`) |
| `ADMIN_ADDRESS` | Recipient for notification emails |
| `GMAIL_ADDRESS` / `GMAIL_PASS` | Gmail SMTP sending (`GMAIL_PASS` is an **app password**) |

> `.env*` files contain secrets and **must not be committed** (already in `.gitignore`).
> See `.env.example` / `.env.prod.example` for templates.

### 2. Start

**Development (HTTP, port 28080)**
```bash
docker compose up -d --build
# → http://localhost:28080/acmg/
```

**Production (HTTPS, served under /acmg via an external nginx)**
```bash
docker compose -f docker-compose.prod.yml up -d --build
```

On startup, `entrypoint.sh` automatically runs `migrate` / `collectstatic` / `compilemessages`.

### 3. Create an administrator
```bash
docker compose exec app python manage.py createsuperuser
```
New users cannot self-register: they submit an account request, and an administrator approves/creates
the account from the Django admin.

---

## Usage

- **Web**: Run single/batch analysis from the home page. On first login, set up MFA (scan the QR code
  with an authenticator app). The in-app "Help" page documents each feature.
- **API reference**: `/acmg/api/docs/` (Swagger UI). Click **Authorize** at the top right and enter your
  token key (the `Token ` prefix is added automatically).
- **API token**: Issued by an administrator. Users can request one from the link on the Swagger page.
- **Main endpoints**: `GET /api/health/`, `GET /api/whoami/`, `POST /api/classify/`, `POST /api/jobs/`,
  `GET /api/jobs/<id>/`, `GET /api/jobs/<id>/result.tsv`.

---

## Tests
```bash
docker compose exec app python manage.py test
```

---

## License / Notes

- This tool is for research and validation purposes. Do not use its output directly for clinical decisions.
- The analysis engine (`acmg_classifier`), reference data, and TransVar references must be provided and
  mounted separately.
