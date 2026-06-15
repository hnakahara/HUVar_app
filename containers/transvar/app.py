"""TransVar 変換マイクロサービス（Python 3.9）。

app / worker（Python 3.11）から内部 HTTP で呼び出される。Python バージョン差異を
吸収し、cDNA / protein / genome の相互変換を行う。MANE Select に限定する。

TransVar 設定ファイルは vas と同一位置（/tools/transvar/<refversion>/...）を使用。
GRCh37(hg19) / GRCh38(hg38) 両対応。
"""
from __future__ import annotations

import re
import subprocess
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="HUHVar TransVar service")

# アセンブリ名 → TransVar refversion
_ASSEMBLY_TO_REFVERSION = {
    "GRCh38": "hg38",
    "hg38": "hg38",
    "GRCh37": "hg19",
    "hg19": "hg19",
}


class ConvertRequest(BaseModel):
    # 入力（いずれか1形式）。例:
    #   genome  : "chr17:7674221G>A"
    #   cdna    : "TP53:c.742C>T" / "TP53:742C>T"
    #   protein : "TP53:p.R248W" / "TP53:R248W"
    query: str
    assembly: str = "GRCh38"
    # 入力種別。未指定なら query から推定。
    kind: Optional[str] = None  # "genome" | "cdna" | "protein"


class Candidate(BaseModel):
    transcript: Optional[str] = None
    gene: Optional[str] = None
    chrom: Optional[str] = None
    pos: Optional[int] = None
    ref: Optional[str] = None
    alt: Optional[str] = None
    hgvs_c: Optional[str] = None
    hgvs_p: Optional[str] = None
    is_mane_select: bool = False
    raw: str = ""


class ConvertResponse(BaseModel):
    ok: bool
    kind: str
    refversion: str
    candidates: List[Candidate] = []
    error: Optional[str] = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/refversion")
def refversion():
    """使用中の TransVar 設定（参照データ版の確認用）。"""
    try:
        out = subprocess.run(
            ["transvar", "config", "--show"],
            capture_output=True, text=True, timeout=30,
        )
        return {"ok": True, "config": out.stdout}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _infer_kind(query: str) -> str:
    if ":c." in query or re.search(r":\d+[ACGT]>", query):
        return "cdna"
    if ":p." in query or re.search(r":[A-Z*]\d+", query):
        return "protein"
    return "genome"


def _normalize_prefix(query: str, kind: str) -> str:
    """c. / p. プレフィックスが無い場合に補う。"""
    if kind == "cdna" and ":c." not in query and ":" in query:
        gene, rest = query.split(":", 1)
        return f"{gene}:c.{rest}" if not rest.startswith("c.") else query
    if kind == "protein" and ":p." not in query and ":" in query:
        gene, rest = query.split(":", 1)
        return f"{gene}:p.{rest}" if not rest.startswith("p.") else query
    return query


# NOTE: 実際の TransVar 出力パースと MANE Select 判定は M3 で実装する。
# ここではサービスの I/F とプレフィックス正規化・種別推定の骨組みを提供する。
@app.post("/convert", response_model=ConvertResponse)
def convert(req: ConvertRequest) -> ConvertResponse:
    refversion = _ASSEMBLY_TO_REFVERSION.get(req.assembly)
    if refversion is None:
        return ConvertResponse(ok=False, kind=req.kind or "", refversion="",
                               error=f"unsupported assembly: {req.assembly}")

    kind = req.kind or _infer_kind(req.query)
    query = _normalize_prefix(req.query, kind)

    # TODO(M3): transvar panno/canno/ganno をサブプロセス実行し、出力をパースして
    # candidates を構築。MANE Select トランスクリプトのみ is_mane_select=True とし、
    # cDNA/protein 入力時は MANE Select に限定する。複数候補は呼び出し側で選択させる。
    return ConvertResponse(
        ok=True,
        kind=kind,
        refversion=refversion,
        candidates=[],
        error="not_implemented: conversion logic is scheduled for M3",
    )
