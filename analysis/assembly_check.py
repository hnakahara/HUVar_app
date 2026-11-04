"""バッチ VCF の座標系（GRCh37 / GRCh38）の推定と、選択されたアセンブリとの照合。

利用者が選んだアセンブリと VCF の座標が明らかに食い違うときだけ投入を止める（取り違えた
まま解析すると、全変異が別の位置として評価されてしまうため）。判定は次の順で行う。

1. 中身（REF 塩基の照合）: 先頭から最大 ``MAX_RECORDS`` 件の REF を、サーバにある
   GRCh37 / GRCh38 の参照ゲノム FASTA と照合し、一致率を比べる。正しいアセンブリでは
   ほぼ 100% 一致し、取り違えると大きく下がる。ヘッダーが無い・誤っている VCF にも効く。
2. ヘッダー: 中身で判断できない（照合できる変異が少ない、FASTA が無い）ときだけ、
   ``##reference`` / ``##contig`` のアセンブリ名と染色体長で判定する。
"""
from __future__ import annotations

import gzip
import io
import re
from dataclasses import dataclass, field
from typing import Optional

ASSEMBLIES = ("GRCh38", "GRCh37")
MAX_RECORDS = 300      # 照合する変異数の上限（先頭から）
MIN_CHECKED = 20       # これ未満しか照合できなければ中身では判断しない
MATCH_OK = 0.95        # 正しいアセンブリとみなす一致率
MATCH_BAD = 0.90       # 選択アセンブリの一致率がこれ未満なら疑う
MATCH_VERY_BAD = 0.50  # もう一方の FASTA が無いとき、選択側がこれ未満なら止める

# 染色体長（##contig の length= で判定する）
_LENGTHS = {
    "GRCh38": {"1": 248956422, "2": 242193529, "13": 114364328, "17": 83257441,
               "X": 156040895},
    "GRCh37": {"1": 249250621, "2": 243199373, "13": 115169878, "17": 81195210,
               "X": 155270560},
}
_NAME_PATTERNS = {
    "GRCh38": re.compile(r"GRCh38|hg38|hs38|GCA_000001405\.(1[5-9]|2\d)", re.I),
    "GRCh37": re.compile(r"GRCh37|hg19|hs37|\bb37\b|human_g1k_v37", re.I),
}
_CONTIG_RE = re.compile(r"##contig=<.*?\bID=([^,>]+).*?\blength=(\d+)", re.I)
_BASES = re.compile(r"^[ACGTNacgtn]+$")


@dataclass
class AssemblyEvidence:
    """判定に使った材料。``detected`` は推定アセンブリ（不明なら None）。"""
    match: dict = field(default_factory=dict)    # assembly -> (一致数, 照合数)
    header: Optional[str] = None                  # ヘッダーから推定したアセンブリ
    header_reason: str = ""

    def rate(self, asm: str) -> Optional[float]:
        hit_n = self.match.get(asm)
        if not hit_n or hit_n[1] < MIN_CHECKED:
            return None
        return hit_n[0] / hit_n[1]

    def describe_match(self) -> str:
        parts = []
        for asm in ASSEMBLIES:
            if asm in self.match and self.match[asm][1]:
                hit, n = self.match[asm]
                parts.append(f"{asm} {hit / n:.0%}（{n} 件中 {hit} 件）")
        return " ／ ".join(parts)


def _open_text(fileobj):
    head = fileobj.read(2)
    fileobj.seek(0)
    raw = gzip.GzipFile(fileobj=fileobj) if head == b"\x1f\x8b" else fileobj
    return io.TextIOWrapper(raw, encoding="utf-8", errors="replace")


def read_vcf_sample(fileobj, max_records: int = MAX_RECORDS):
    """ヘッダー行と、先頭 ``max_records`` 件の (chrom, pos, ref) を返す。

    ``fileobj`` はバイナリで読めるファイル（Django の UploadedFile など）。読み終えたら
    先頭に戻す（その後の保存に影響しないように）。"""
    header, records = [], []
    fileobj.seek(0)
    text = _open_text(fileobj)
    try:
        for line in text:
            if line.startswith("##"):
                header.append(line.rstrip("\n"))
                continue
            if line.startswith("#") or not line.strip():
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 4:
                continue
            try:
                records.append((cols[0], int(cols[1]), cols[3]))
            except ValueError:
                continue
            if len(records) >= max_records:
                break
    finally:
        try:
            text.detach()
        except Exception:  # noqa: BLE001
            pass
        fileobj.seek(0)
    return header, records


def assembly_from_header(header: list[str]) -> tuple[Optional[str], str]:
    """ヘッダーから推定したアセンブリと、その根拠。"""
    for line in header:
        m = _CONTIG_RE.search(line)
        if not m:
            continue
        chrom = m.group(1).removeprefix("chr").removeprefix("CHR")
        length = int(m.group(2))
        for asm, lengths in _LENGTHS.items():
            if lengths.get(chrom) == length:
                return asm, f"##contig {m.group(1)} の長さ {length:,}"
    for line in header:
        if line.startswith(("##reference", "##contig", "##assembly")):
            hits = [asm for asm, pat in _NAME_PATTERNS.items() if pat.search(line)]
            if len(hits) == 1:
                return hits[0], line[:120]
    return None, ""


def _fasta_path(assembly: str):
    try:
        from .engine import _build_config
        path = _build_config(assembly).genome_fasta
        return path if path.exists() else None
    except Exception:  # noqa: BLE001  エンジン未導入・設定不備では照合しない
        return None


def _contig(fasta, chrom: str) -> Optional[str]:
    names = set(fasta.references)
    bare = chrom[3:] if chrom.lower().startswith("chr") else chrom
    for cand in (chrom, bare, "chr" + bare, "MT" if bare in ("M", "MT") else None,
                 "chrM" if bare in ("M", "MT") else None):
        if cand and cand in names:
            return cand
    return None


def ref_match(records, fasta_path) -> tuple[int, int]:
    """REF が参照ゲノムと一致した件数と、照合できた件数。"""
    import pysam
    hit = n = 0
    fasta = pysam.FastaFile(str(fasta_path))
    try:
        for chrom, pos, ref in records:
            if not _BASES.match(ref) or len(ref) > 50:
                continue
            name = _contig(fasta, chrom)
            if name is None:
                continue
            try:
                seq = fasta.fetch(name, pos - 1, pos - 1 + len(ref))
            except (ValueError, KeyError, IndexError):
                continue
            if len(seq) != len(ref):
                continue
            n += 1
            hit += seq.upper() == ref.upper()
    finally:
        fasta.close()
    return hit, n


def gather_evidence(fileobj, fasta_paths: Optional[dict] = None) -> AssemblyEvidence:
    header, records = read_vcf_sample(fileobj)
    ev = AssemblyEvidence()
    ev.header, ev.header_reason = assembly_from_header(header)
    for asm in ASSEMBLIES:
        path = (fasta_paths or {}).get(asm) if fasta_paths is not None else _fasta_path(asm)
        if path is None:
            continue
        try:
            ev.match[asm] = ref_match(records, path)
        except Exception:  # noqa: BLE001  照合できなければその材料は使わない
            continue
    return ev


def mismatch_reason(selected: str, ev: AssemblyEvidence) -> Optional[tuple[str, str]]:
    """選択したアセンブリと明らかに食い違うなら (推定アセンブリ, 根拠) を返す。"""
    if selected not in ASSEMBLIES:
        return None
    other = ASSEMBLIES[1] if selected == ASSEMBLIES[0] else ASSEMBLIES[0]
    r_sel, r_oth = ev.rate(selected), ev.rate(other)
    if r_sel is not None and r_oth is not None:
        if r_sel < MATCH_BAD and r_oth >= MATCH_OK:
            return other, "REF 一致率 " + ev.describe_match()
        return None                       # 中身で判断できた（一致 or 決め手なし）
    if r_sel is not None and r_oth is None:
        if r_sel < MATCH_VERY_BAD:
            return other, "REF 一致率 " + ev.describe_match()
        if r_sel >= MATCH_OK:
            return None
    if r_oth is not None and r_sel is None and r_oth >= MATCH_OK:
        return other, "REF 一致率 " + ev.describe_match()
    if ev.header and ev.header != selected:
        return ev.header, "VCF ヘッダー: " + ev.header_reason
    return None


def check_assembly(selected: str, fileobj, fasta_paths: Optional[dict] = None):
    """``mismatch_reason`` の結果（食い違いなし・判断不能なら None）。"""
    return mismatch_reason(selected, gather_evidence(fileobj, fasta_paths))


def mismatch_message(selected: str, fileobj) -> Optional[str]:
    """食い違うときの利用者向けメッセージ（フォーム・API 共通）。照合自体の失敗では
    投入を止めない（None）。"""
    from django.utils.translation import gettext as _
    try:
        found = check_assembly(selected, fileobj)
    except Exception:  # noqa: BLE001
        return None
    if found is None:
        return None
    detected, evidence = found
    return _("選択したアセンブリ（%(selected)s）と VCF の座標が一致しません。"
             "%(detected)s の座標である可能性が高いです（%(evidence)s）。"
             "アセンブリの選択を確認してください。") % {
        "selected": selected, "detected": detected, "evidence": evidence}
