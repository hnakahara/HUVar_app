# app / worker 共用イメージ（Python 3.11 — HUVar の requires-python >=3.11 を満たす）
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

# --- Ensembl VEP 111 + htslib ツール（HUVar 用。vas とは独立にイメージ内へ） ---
# micromamba で専用 conda 環境を作る。conda env には python3.13 が含まれるため、
# env を PATH 先頭に置くと Django 用のシステム python:3.11 を上書きしてしまう。
# そこで PATH は末尾追加（システム python 優先）にし、vep だけは専用ラッパー経由で
# conda の perl(DBI/Bio::EnsEMBL 等)＋ツールで実行する。find_vep_cmd() は which("vep")
# で /usr/local/bin/vep(ラッパー) を検出する（/usr/local/bin は PATH 前方のため優先）。
RUN curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest \
    | tar -xj -C /usr/local/bin --strip-components=1 bin/micromamba && \
    micromamba create -y -p /opt/conda/envs/vep \
        -c conda-forge -c bioconda \
        ensembl-vep=111 samtools bcftools htslib && \
    micromamba clean -a -y && \
    rm -rf /root/.cache
RUN printf '#!/bin/sh\nexport PATH="/opt/conda/envs/vep/bin:$PATH"\nexec /opt/conda/envs/vep/bin/perl /opt/conda/envs/vep/bin/vep "$@"\n' \
    > /usr/local/bin/vep && chmod +x /usr/local/bin/vep
ENV PATH="${PATH}:/opt/conda/envs/vep/bin"

COPY requirements.txt /code/
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . /code/
COPY ./containers/django/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
