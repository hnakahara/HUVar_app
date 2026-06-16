# app / worker 共用イメージ（Python 3.11 — HUHVar の requires-python >=3.11 を満たす）
FROM python:3.11

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /code

# psycopg2（libpq-dev）と cyvcf2 / pysam / pyBigWig 等のビルドに必要なシステムライブラリ
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    zlib1g-dev \
    libbz2-dev \
    liblzma-dev \
    libcurl4-openssl-dev \
    libssl-dev \
    gettext \
    curl \
    bzip2 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- Ensembl VEP 111 + htslib ツール（HUHVar 用。vas とは独立にイメージ内へ） ---
# micromamba で専用 conda 環境を作る。VEP は env 同梱の perl(DBI/Bio::EnsEMBL 等)で
# 動く必要があるため、env の bin を PATH 先頭に置く（vep のシェバン env perl が conda
# perl を拾うように）。この env には python/gunicorn/celery を含めないため、Django 側の
# システム python:3.11 は引き続き使われる。find_vep_cmd() は which("vep") で検出する。
RUN curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest \
    | tar -xj -C /usr/local/bin --strip-components=1 bin/micromamba && \
    micromamba create -y -p /opt/conda/envs/vep \
        -c conda-forge -c bioconda \
        ensembl-vep=111 samtools bcftools htslib && \
    micromamba clean -a -y && \
    rm -rf /root/.cache
ENV PATH="/opt/conda/envs/vep/bin:${PATH}"

COPY requirements.txt /code/
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . /code/
COPY ./containers/django/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
