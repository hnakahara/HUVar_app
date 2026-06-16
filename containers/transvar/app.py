"""TransVar 変換マイクロサービス（Python 3.9）。

app / worker（Python 3.11）から内部 HTTP で呼び出される。Python バージョン差異を
吸収し、genome / cDNA / protein の相互変換を行い、MANE Select に限定して返す。

TransVar 設定・参照は vas と同一位置（/tools/transvar/...）を使用。GRCh37(hg19) /
GRCh38(hg38) 両対応。ロジックは vas の transvar_function.py を移植・整理したもの。
"""
from __future__ import annotations

import csv
import io
import os
import re
import subprocess
from functools import lru_cache
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="HUHVar TransVar service")

# アセンブリ名 → TransVar refversion / 設定ファイル
_ASSEMBLY_TO_REFVERSION = {
    "GRCh38": "hg38", "hg38": "hg38",
    "GRCh37": "hg19", "hg19": "hg19",
}
_TRANSVAR_ROOT = os.environ.get("TRANSVAR_ROOT", "/tools/transvar")
_MANE_SUMMARY = os.environ.get(
    "MANE_SUMMARY", f"{_TRANSVAR_ROOT}/mane/MANE.GRCh38.v1.3.summary.txt"
)

# アミノ酸 3文字 → 1文字（protein 入力の正規化用）
_AA3_TO_1 = {
    "Gly": "G", "Ala": "A", "Ser": "S", "Thr": "T", "Asn": "N", "Gln": "Q",
    "Asp": "D", "Glu": "E", "Lys": "K", "Arg": "R", "His": "H", "Val": "V",
    "Leu": "L", "Ile": "I", "Tyr": "Y", "Phe": "F", "Trp": "W", "Pro": "P",
    "Met": "M", "Cys": "C", "Ter": "*",
}


class ConvertRequest(BaseModel):
    query: str
    assembly: str = "GRCh38"
    kind: Optional[str] = None  # "genome" | "cdna" | "protein"


class Candidate(BaseModel):
    transcript: Optional[str] = None
    gene: Optional[str] = None
    strand: Optional[str] = None
    chrom: Optional[str] = None
    pos: Optional[int] = None
    ref: Optional[str] = None
    alt: Optional[str] = None
    hgvs_g: Optional[str] = None
    hgvs_c: Optional[str] = None
    hgvs_p: Optional[str] = None
    region: Optional[str] = None
    is_mane_select: bool = True
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
def refversion_info():
    out = {}
    for rv in ("hg19", "hg38"):
        cfg = f"{_TRANSVAR_ROOT}/{rv}/transvar.cfg"
        out[rv] = {"cfg": cfg, "exists": os.path.exists(cfg)}
    out["mane_summary"] = {"path": _MANE_SUMMARY, "exists": os.path.exists(_MANE_SUMMARY)}
    return out


@lru_cache(maxsize=1)
def _mane_map() -> dict:
    """MANE summary から {gene_symbol: MANE Select RefSeq_nuc} を構築。"""
    mapping = {}
    try:
        with open(_MANE_SUMMARY, newline="", encoding="utf-8") as fh:
            # ヘッダは #NCBI_GeneID で始まるが、列名(symbol/RefSeq_nuc/MANE_status)は素のまま
            reader = csv.DictReader(fh, delimiter="\t")
            for row in reader:
                if row.get("MANE_status") == "MANE Select":
                    sym = row.get("symbol")
                    # 列名は配布版で異なる: 標準は RefSeq_nuc、vas 配置版は
                    # RefSeq_nuc_major(=versioned NM_) / RefSeq_nuc_minor。
                    nuc = (
                        row.get("RefSeq_nuc")
                        or row.get("RefSeq_nuc_major")
                        or row.get("RefSeq_nuc_minor")
                    )
                    if sym and nuc:
                        mapping[sym] = nuc
    except FileNotFoundError:
        pass
    return mapping


def _base(acc: Optional[str]) -> str:
    """アクセッションのバージョンを除いた基底（NM_000546.6 -> NM_000546）。"""
    return (acc or "").split(".")[0]


def _set_cfg(refversion: str) -> None:
    os.environ["TRANSVAR_CFG"] = f"{_TRANSVAR_ROOT}/{refversion}/transvar.cfg"


def _infer_kind(query: str) -> str:
    if ":c." in query or re.search(r":\d+[ACGTN]", query):
        return "cdna"
    if ":p." in query or re.search(r":[A-Za-z*]\d+", query):
        return "protein"
    return "genome"


def _normalize_protein(gene_rest: str) -> str:
    """'p.' 除去・3文字->1文字・Ter/X->*。入力は protein 部分のみ。"""
    s = gene_rest
    if s.startswith("p."):
        s = s[2:]
    for k, v in _AA3_TO_1.items():
        s = s.replace(k, v)
    s = s.replace("X", "*")
    return s


def _run_transvar(mode: str, query: str) -> List[dict]:
    """transvar <mode> -i <query> --refseq --gseq を実行し行(dict)を返す。"""
    res = subprocess.run(
        ["transvar", mode, "-i", query, "--refseq", "--gseq"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120,
    )
    if not res.stdout.strip():
        return []
    reader = csv.DictReader(io.StringIO(res.stdout), delimiter="\t")
    return list(reader)


_COORD_COL = "coordinates(gDNA/cDNA/protein)"


def _split_coords(row: dict):
    """coordinates(gDNA/cDNA/protein) を chr, g., c., p. に分解。"""
    coord = row.get(_COORD_COL, "") or ""
    parts = re.split(r"[:/]", coord)
    chrom = parts[0] if len(parts) > 0 else None
    g = parts[1] if len(parts) > 1 else None
    c = parts[2] if len(parts) > 2 else None
    p = parts[3] if len(parts) > 3 else None
    return chrom, g, c, p, coord


def _coord_failed(row: dict) -> bool:
    """座標取得失敗（gDNA 部分に '(' を含む）の判定。"""
    _, g, _, _, _ = _split_coords(row)
    return g is None or "(" in g


def _row_to_candidate(row: dict) -> Optional[Candidate]:
    if _coord_failed(row):
        return None
    chrom, g, c, p, coord = _split_coords(row)
    pos = row.get("POS")
    try:
        pos_int = int(pos) if pos not in (None, "", ".") else None
    except ValueError:
        pos_int = None
    return Candidate(
        transcript=row.get("transcript"),
        gene=row.get("gene"),
        strand=row.get("strand"),
        chrom=chrom,
        pos=pos_int,
        ref=row.get("REF") or None,
        alt=row.get("ALT") or None,
        hgvs_g=g,
        hgvs_c=c,
        hgvs_p=p,
        region=row.get("region"),
        is_mane_select=True,
        raw=coord,
    )


def _filter_mane_for_gene(rows: List[dict], gene: str) -> List[dict]:
    """指定遺伝子の MANE Select transcript の行のみ残す（バージョン非依存一致）。"""
    mane_tx = _mane_map().get(gene)
    if not mane_tx:
        return []
    base = _base(mane_tx)
    return [r for r in rows if _base(r.get("transcript")) == base]


def _filter_mane_genome(rows: List[dict]) -> List[dict]:
    """genome 入力: 各行の gene の MANE Select transcript に一致する行のみ残す。"""
    mane = _mane_map()
    out = []
    for r in rows:
        gene = r.get("gene")
        mane_tx = mane.get(gene)
        if mane_tx and _base(r.get("transcript")) == _base(mane_tx):
            out.append(r)
    return out


@app.post("/convert", response_model=ConvertResponse)
def convert(req: ConvertRequest) -> ConvertResponse:
    refversion = _ASSEMBLY_TO_REFVERSION.get(req.assembly)
    if refversion is None:
        return ConvertResponse(ok=False, kind=req.kind or "", refversion="",
                               error=f"unsupported assembly: {req.assembly}")
    cfg = f"{_TRANSVAR_ROOT}/{refversion}/transvar.cfg"
    if not os.path.exists(cfg):
        return ConvertResponse(ok=False, kind=req.kind or "", refversion=refversion,
                               error=f"transvar config not found: {cfg}")
    _set_cfg(refversion)

    kind = req.kind or _infer_kind(req.query)
    q = req.query.strip()

    try:
        if kind == "genome":
            # "chr17:7674221G>A" -> "chr17:g.7674221G>A"
            if ":" in q and "g." not in q:
                head, rest = q.split(":", 1)
                q = f"{head}:g.{rest}"
            rows = _run_transvar("ganno", q)
            mane_rows = _filter_mane_genome(rows)

        elif kind in ("cdna", "protein"):
            if ":" not in q:
                return ConvertResponse(ok=False, kind=kind, refversion=refversion,
                                       error="cDNA/protein 入力は 'GENE:変異' 形式が必要です")
            gene, rest = q.split(":", 1)
            gene = gene.strip()
            if kind == "cdna":
                rest = rest[2:] if rest.startswith("c.") else rest
                rows = _run_transvar("canno", f"{gene}:c.{rest}")
            else:
                rest = _normalize_protein(rest)
                rows = _run_transvar("panno", f"{gene}:p.{rest}")
            mane_rows = _filter_mane_for_gene(rows, gene)
        else:
            return ConvertResponse(ok=False, kind=kind, refversion=refversion,
                                   error=f"unknown kind: {kind}")

        candidates = [c for c in (_row_to_candidate(r) for r in mane_rows) if c]
        if not candidates:
            # MANE 一致なし or 座標取得失敗。アノテーション可能な transcript を参考提示。
            avail = sorted({_base(r.get("transcript")) for r in rows if r.get("transcript")})
            return ConvertResponse(
                ok=False, kind=kind, refversion=refversion, candidates=[],
                error=("MANE Select で座標を取得できませんでした。"
                       + (f" 候補transcript: {', '.join(avail)}" if avail else "")),
            )
        return ConvertResponse(ok=True, kind=kind, refversion=refversion,
                               candidates=candidates)
    except subprocess.TimeoutExpired:
        return ConvertResponse(ok=False, kind=kind, refversion=refversion,
                               error="transvar timeout")
    except Exception as exc:  # noqa: BLE001
        return ConvertResponse(ok=False, kind=kind, refversion=refversion,
                               error=f"transvar error: {exc}")
