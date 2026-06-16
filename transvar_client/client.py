"""transvar サービス（Python 3.9 コンテナ）への HTTP クライアント。

app / worker（Python 3.11）から変換要求を送る。MANE Select 限定・複数候補選択は
サービス側の出力（candidates / is_mane_select）に基づき呼び出し側で処理する。
"""
from __future__ import annotations

import re
from typing import Optional

import requests
from django.conf import settings


class TransvarError(RuntimeError):
    pass


_GENE_SPACE_RE = re.compile(r"^(\S+)\s+(.+)$")


def normalize_query(query: str) -> str:
    """空白区切り入力を 'GENE:変異' 形式へ正規化する。

    例: 'TP53 742C>T' -> 'TP53:742C>T', 'TP53 R248W' -> 'TP53:R248W'。
    既にコロンを含む入力（chr17:7674221G>A / TP53:c.742C>T 等）はそのまま返す。
    """
    q = (query or "").strip()
    if not q or ":" in q:
        return q
    m = _GENE_SPACE_RE.match(q)
    if m:
        return f"{m.group(1)}:{m.group(2).strip()}"
    return q


def convert(query: str, assembly: str = "GRCh38", kind: Optional[str] = None,
            timeout: int = 60) -> dict:
    """変異表記（genome / cDNA / protein）を変換する。

    Returns: transvar サービスの ConvertResponse（dict）。
    candidates のうち is_mane_select=True のものに限定する判断は呼び出し側で行う。
    """
    url = settings.TRANSVAR_SERVICE_URL.rstrip("/") + "/convert"
    payload = {"query": normalize_query(query), "assembly": assembly}
    if kind:
        payload["kind"] = kind
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise TransvarError(str(exc)) from exc
    return resp.json()


def health(timeout: int = 5) -> bool:
    url = settings.TRANSVAR_SERVICE_URL.rstrip("/") + "/health"
    try:
        resp = requests.get(url, timeout=timeout)
        return resp.ok
    except requests.RequestException:
        return False
