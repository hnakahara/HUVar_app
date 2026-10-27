"""TransVar 変換マイクロサービス（Python 3.9）。

app / worker（Python 3.11）から内部 HTTP で呼び出される。Python バージョン差異を
吸収し、genome / cDNA / protein の相互変換を行い、MANE（Select / Plus Clinical）の
transcript に限定して返す。MANE に該当が無い場合のみ全候補へフォールバックする。

TransVar 設定・参照は vas と同一位置（/tools/transvar/...）を使用。GRCh37(hg19) /
GRCh38(hg38) 両対応。ロジックは vas の transvar_function.py を移植・整理したもの。
"""
from __future__ import annotations

import csv
import gzip
import io
import os
import re
import subprocess
from functools import lru_cache
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="HUVar TransVar service")

# アセンブリ名 → TransVar refversion / 設定ファイル
_ASSEMBLY_TO_REFVERSION = {
    "GRCh38": "hg38", "hg38": "hg38",
    "GRCh37": "hg19", "hg19": "hg19",
}
_TRANSVAR_ROOT = os.environ.get("TRANSVAR_ROOT", "/tools/transvar")
# MANE summary（NCBI 配布の MANE.GRCh38.vX.summary.txt[.gz]）。環境変数 MANE_SUMMARY が
# 最優先。未指定なら HUVar 参照データの shared（HUVar の `setup_data.py --only
# mane-summary` が保存する。app/worker と同じ ACMG_DATA_DIR、既定 /data）を探す。
# 旧配置（/tools/transvar/mane/ の v1.3）は後方互換のフォールバックとして残す。
_DATA_DIR = os.environ.get("ACMG_DATA_DIR", "/data")
_MANE_SUMMARY_CANDIDATES = (
    f"{_DATA_DIR}/shared/MANE.GRCh38.v1.5.summary.txt.gz",
    f"{_TRANSVAR_ROOT}/mane/MANE.GRCh38.v1.3.summary.txt",
)


def _resolve_mane_summary() -> str:
    env = os.environ.get("MANE_SUMMARY")
    if env:
        return env
    for path in _MANE_SUMMARY_CANDIDATES:
        if os.path.exists(path):
            return path
    return _MANE_SUMMARY_CANDIDATES[0]


_MANE_SUMMARY = _resolve_mane_summary()

MANE_SELECT = "MANE Select"
MANE_PLUS_CLINICAL = "MANE Plus Clinical"
_MANE_STATUS_ORDER = {MANE_SELECT: 0, MANE_PLUS_CLINICAL: 1}

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


class PlusClinicalNote(BaseModel):
    """同一ゲノム座標の MANE Plus Clinical 上の表記（補足情報）。"""
    transcript: Optional[str] = None
    hgvs_c: Optional[str] = None
    hgvs_p: Optional[str] = None
    region: Optional[str] = None


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
    # "MANE Select" / "MANE Plus Clinical" / None（非 MANE のフォールバック候補）
    mane_status: Optional[str] = None
    # 同じゲノム座標を MANE Plus Clinical 上で表した表記（Select 候補に付与）
    plus_clinical: List[PlusClinicalNote] = []
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
    idx = _mane_index()
    out["mane_summary"] = {
        "path": _MANE_SUMMARY, "exists": os.path.exists(_MANE_SUMMARY),
        "mane_select_genes": sum(1 for v in idx["by_gene"].values()
                                 if any(s == MANE_SELECT for _, s in v)),
        "mane_plus_clinical_transcripts": sum(1 for _g, s in idx["by_acc"].values()
                                              if s == MANE_PLUS_CLINICAL),
    }
    return out


def _open_text(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", newline="", encoding="utf-8")
    return open(path, newline="", encoding="utf-8")


def _load_mane_index(path: str) -> dict:
    """MANE summary から transcript の MANE 区分を引く索引を構築する。

    Returns:
      {"by_gene": {symbol: [(refseq_base, status), ...]}   # Select を先頭に
       "by_acc":  {refseq_base: (symbol, status)}}
    status は "MANE Select" / "MANE Plus Clinical"。
    """
    by_gene: dict = {}
    by_acc: dict = {}
    try:
        with _open_text(path) as fh:
            # ヘッダは #NCBI_GeneID で始まるが、列名(symbol/RefSeq_nuc/MANE_status)は素のまま
            reader = csv.DictReader(fh, delimiter="\t")
            for row in reader:
                status = (row.get("MANE_status") or "").strip()
                if status not in _MANE_STATUS_ORDER:
                    continue
                sym = row.get("symbol")
                # 列名は配布版で異なる: 標準は RefSeq_nuc、vas 配置版は
                # RefSeq_nuc_major(=versioned NM_) / RefSeq_nuc_minor。
                nuc = (
                    row.get("RefSeq_nuc")
                    or row.get("RefSeq_nuc_major")
                    or row.get("RefSeq_nuc_minor")
                )
                if not (sym and nuc):
                    continue
                base = _base(nuc)
                by_gene.setdefault(sym, []).append((base, status))
                by_acc[base] = (sym, status)
    except FileNotFoundError:
        pass
    for v in by_gene.values():
        v.sort(key=lambda t: _MANE_STATUS_ORDER[t[1]])
    return {"by_gene": by_gene, "by_acc": by_acc}


@lru_cache(maxsize=1)
def _mane_index() -> dict:
    return _load_mane_index(_MANE_SUMMARY)


def _mane_map() -> dict:
    """後方互換: {gene_symbol: MANE Select RefSeq の基底アクセッション}。"""
    return {g: b for g, v in _mane_index()["by_gene"].items()
            for b, s in v if s == MANE_SELECT}


def _mane_status(row: dict) -> Optional[str]:
    """TransVar 行の transcript の MANE 区分（該当なしは None）。"""
    hit = _mane_index()["by_acc"].get(_base(row.get("transcript")))
    return hit[1] if hit else None


def _base(acc: Optional[str]) -> str:
    """アクセッションのバージョンを除いた基底（NM_000546.6 -> NM_000546）。"""
    return (acc or "").split(".")[0]


def _set_cfg(refversion: str) -> None:
    os.environ["TRANSVAR_CFG"] = f"{_TRANSVAR_ROOT}/{refversion}/transvar.cfg"


def _infer_kind(query: str) -> str:
    q = query.strip()
    # 明示プレフィックスが最優先
    if ":c." in q:
        return "cdna"
    if ":p." in q:
        return "protein"
    # 先頭が染色体名なら genome（chr17:7674221G>A / 17:7674221G>A）。
    head = q.split(":", 1)[0].strip()
    if re.fullmatch(r"(chr)?(\d{1,2}|X|Y|M|MT)", head, re.IGNORECASE):
        return "genome"
    # 以降は GENE:変異。塩基置換/indel 記法は cDNA、アミノ酸記法は protein。
    rest = q.split(":", 1)[1] if ":" in q else q
    if ">" in rest or "del" in rest or "dup" in rest or "ins" in rest:
        return "cdna"
    if re.search(r"[A-Za-z*]\d+", rest):
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
    # --seqmax 1000: 長い dup / indel は既定の配列長上限で no_valid_transcript_found に
    # なる（vas でも同様）。大きな正値で解決する（-1=無制限はなぜか失敗するため使わない）。
    res = subprocess.run(
        ["transvar", mode, "-i", query, "--refseq", "--gseq", "--seqmax", "1000"],
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


def _row_to_candidate(row: dict, status: Optional[str] = None) -> Optional[Candidate]:
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
        is_mane_select=(status == MANE_SELECT),
        mane_status=status,
        raw=coord,
    )


_ACCESSION_RE = re.compile(r"^(NM_|NR_|XM_|ENST)\d+(\.\d+)?$", re.IGNORECASE)


def _sort_mane(rows: List[dict]) -> List[dict]:
    """MANE Select → MANE Plus Clinical の順に安定ソート。"""
    return sorted(rows, key=lambda r: _MANE_STATUS_ORDER.get(_mane_status(r), 9))


def _filter_mane_for_gene(rows: List[dict], gene: str) -> List[dict]:
    """cDNA/protein 入力: 指定遺伝子の MANE（Select / Plus Clinical）transcript の行のみ
    残す（バージョン非依存一致）。'NM_033360.4:c.…' のように transcript を直接指定した
    場合は、その transcript の行のみ（MANE 区分は索引から付与）。"""
    if _ACCESSION_RE.match(gene):
        base = _base(gene)
        return [r for r in rows if _base(r.get("transcript")) == base]
    bases = {b for b, _s in _mane_index()["by_gene"].get(gene, [])}
    if not bases:
        return []
    return _sort_mane([r for r in rows if _base(r.get("transcript")) in bases])


def _filter_mane_genome(rows: List[dict]) -> List[dict]:
    """genome 入力: 各行の gene の MANE（Select / Plus Clinical）transcript の行のみ残す。"""
    idx = _mane_index()["by_acc"]
    out = []
    for r in rows:
        hit = idx.get(_base(r.get("transcript")))
        if hit and hit[0] == r.get("gene"):
            out.append(r)
    return _sort_mane(out)


def _build_candidates(cand_rows: List[dict], use_mane: bool) -> List[Candidate]:
    """TransVar 行 → 候補。同一ゲノム座標は1候補にまとめ、MANE Plus Clinical 上の
    表記は MANE Select 候補の plus_clinical に補足として付ける。座標が Select と
    異なる Plus Clinical の行（c. 番号の付け方が異なる場合）は独立した候補になる。"""
    by_coord: dict = {}
    candidates: List[Candidate] = []
    for r in cand_rows:
        status = _mane_status(r) if use_mane else None
        c = _row_to_candidate(r, status)
        if c is None:
            continue
        gkey = (c.chrom, c.pos, c.ref, c.alt)  # genome 座標で重複排除
        prev = by_coord.get(gkey)
        if prev is not None:
            if (c.mane_status == MANE_PLUS_CLINICAL and prev.mane_status == MANE_SELECT
                    and all(_base(n.transcript) != _base(c.transcript) for n in prev.plus_clinical)):
                prev.plus_clinical.append(PlusClinicalNote(
                    transcript=c.transcript, hgvs_c=c.hgvs_c, hgvs_p=c.hgvs_p,
                    region=c.region))
            continue
        by_coord[gkey] = c
        candidates.append(c)
    return candidates


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

        # MANE（Select → Plus Clinical の順）を優先。MANE で座標が得られなければ、
        # TransVar の全候補 transcript へフォールバックし、ユーザーに選択させる。
        if mane_rows:
            candidates = _build_candidates(mane_rows, use_mane=True)
        else:
            candidates = _build_candidates(rows, use_mane=False)

        if not candidates:
            avail = sorted({_base(r.get("transcript")) for r in rows if r.get("transcript")})
            return ConvertResponse(
                ok=False, kind=kind, refversion=refversion, candidates=[],
                error=("座標を取得できませんでした。"
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
