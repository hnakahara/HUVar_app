"""変異結果キャッシュ（FR-CACHE）。

一度解析した変異の自動判定結果を DB(VariantResultCache) に保存し、参照データが更新
されるまで 2 回目以降は DB から返す。参照データ版署名は、主要参照ファイルの
(サイズ + 更新時刻 mtime) ＋ エンジン版を連結した SHA-256。いずれかのファイルが更新
されると署名が変わり、キャッシュは自動的に無効化される（新署名で別エントリ扱い）。

手動編集（supplement）込みの再分類はユーザー依存のためキャッシュしない（毎回再計算）。
"""
from __future__ import annotations

import csv
import hashlib
import os
from pathlib import Path
from typing import Tuple

# 署名対象の Config パス属性（存在しないものはスキップ）。分類結果に影響する参照のみ。
_TRACKED_ATTRS = [
    "genome_fasta",
    "gnomad_duckdb",
    "gnomad_constraint_tsv",
    "clinvar_vcf",
    "clinvar_sqlite",
    "alphamissense_tsv",
    "esm1b_sqlite",
    "revel_tsv",
    "repeatmasker_bed",
]


def engine_version() -> str:
    try:
        from importlib.metadata import version
        return version("acmg-classifier")
    except Exception:  # noqa: BLE001
        try:
            import acmg_classifier
            return getattr(acmg_classifier, "__version__", "unknown")
        except Exception:  # noqa: BLE001
            return "unknown"


def _stat_token(path) -> Tuple[str, int, float]:
    """(name, size, mtime) を返す。存在しなければ size=-1。"""
    name = str(path)
    try:
        st = os.stat(path)
        return name, st.st_size, st.st_mtime
    except OSError:
        return name, -1, 0.0


def _tracked_paths(assembly: str):
    """Config から署名対象パスを解決して列挙（[(label, path)]）。"""
    from .engine import _build_config  # acmg_classifier 依存（解析時のみ）
    cfg = _build_config(assembly)
    out = []
    for attr in _TRACKED_ATTRS:
        try:
            out.append((attr, getattr(cfg, attr)))
        except Exception:  # noqa: BLE001  属性が無い版もある
            continue
    # ディレクトリ系（存在すれば mtime/サイズで検知）
    try:
        out.append(("data_shared", cfg.data_dir / "shared"))
    except Exception:  # noqa: BLE001
        pass
    try:
        out.append(("vep_cache", cfg.vep_cache_dir))
    except Exception:  # noqa: BLE001
        pass
    # 既定マニュアルエビデンス(eRepo)。更新時にキャッシュを無効化する。
    try:
        from .engine import manual_criteria_path
        mp = manual_criteria_path(cfg)
        if mp is not None:
            out.append(("erepo_manual", mp))
    except Exception:  # noqa: BLE001
        pass
    return out


def reference_signature(assembly: str) -> str:
    """参照データ版署名（SHA-256）。size+mtime+エンジン版から算出。"""
    parts = [f"engine={engine_version()}"]
    for label, path in _tracked_paths(assembly):
        name, size, mtime = _stat_token(path)
        parts.append(f"{label}:{name}:{size}:{int(mtime)}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _refresh_reference_versions(assembly: str) -> None:
    """ReferenceDataVersion を最新の参照データ状態に更新（admin 可視化用）。"""
    from django.utils import timezone

    from .models import ReferenceDataVersion
    for label, path in _tracked_paths(assembly):
        name, size, mtime = _stat_token(path)
        mt = timezone.datetime.fromtimestamp(mtime, tz=timezone.get_current_timezone()) \
            if mtime else None
        ReferenceDataVersion.objects.update_or_create(
            name=f"{assembly}:{label}",
            defaults={"sha256": "", "size_bytes": size, "mtime": mt},
        )


def cached_classify_single(assembly: str, chrom: str, pos: int, ref: str, alt: str):
    """キャッシュ参照付きの単一変異分類。戻り値 (display, hit)。

    参照データが未更新なら 2 回目以降は DB から返す（FR-CACHE-2）。
    手動編集（supplement）はキャッシュしないため、この関数は supplement を取らない。
    """
    from .engine import classify_single
    from .models import VariantResultCache

    sig = reference_signature(assembly)
    ev = engine_version()
    key = dict(assembly=assembly, chrom=chrom, pos=int(pos), ref=ref, alt=alt,
               engine_version=ev, refdata_signature=sig)

    row = VariantResultCache.objects.filter(**key).first()
    if row is not None:
        return row.result_json, True

    display = classify_single(assembly, chrom, int(pos), ref, alt)
    VariantResultCache.objects.update_or_create(**key, defaults={"result_json": display})
    _refresh_reference_versions(assembly)
    return display, False


# ---------------------------------------------------------------------------
# バッチ（VCF）キャッシュ（FR-CACHE）
# ---------------------------------------------------------------------------

_TSV_BASE_COLS = [
    "variant_id", "chrom", "pos", "ref", "alt", "gene_symbol", "transcript_id",
    "hgvs_c", "hgvs_p", "classification_2015", "rules", "bayesian_score",
    "classification_bayesian", "warnings",
]


def _read_variants(vcf_path: str, assembly: str):
    """VCF を read_vcf で列挙し [(key, chrom, pos, ref, alt)] を順序保持で返す。"""
    from acmg_classifier.io.vcf_reader import read_vcf
    from acmg_classifier.models.enums import Assembly

    out = []
    for v in read_vcf(Path(vcf_path), Assembly(assembly)):
        if v.alt and v.alt != ".":
            out.append((v.key, v.chrom, v.pos, v.ref, v.alt))
    return out


def _write_batch_tsv(out_path: str, displays: list) -> None:
    """display dict 群（VCF 順）から全クライテリア列付き TSV を書く。"""
    rows = [d for d in displays if d]
    crit_order = [c["criterion"] for c in rows[0]["criteria"]] if rows else []
    header = list(_TSV_BASE_COLS)
    for c in crit_order:
        header += [c, f"{c}_strength", f"{c}_evidence"]

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(header)
        for d in rows:
            crit_map = {c["criterion"]: c for c in d.get("criteria", [])}
            row = [
                d.get("variant_id", ""), d.get("chrom", ""), d.get("pos", ""),
                d.get("ref", ""), d.get("alt", ""), d.get("gene_symbol", ""),
                d.get("transcript_id", ""), d.get("hgvs_c", ""), d.get("hgvs_p", ""),
                d.get("classification_2015", ""), d.get("rules", ""),
                d.get("bayesian_score", ""), d.get("classification_bayesian", ""),
                " / ".join(d.get("warnings", [])),
            ]
            for c in crit_order:
                cc = crit_map.get(c, {})
                state = ("suppressed" if cc.get("suppressed")
                         else ("met" if cc.get("triggered") else "not_met"))
                row += [state, cc.get("strength", ""), cc.get("evidence", "")]
            w.writerow(row)


def cached_classify_batch(in_vcf_path: str, out_tsv_path: str, assembly: str) -> int:
    """VCF をキャッシュ活用で解析し TSV を出力。戻り値は出力した変異数。

    - 全変異がキャッシュ済み → エンジンを呼ばず TSV を即生成。
    - 未キャッシュが1つでもあれば全体を解析（VEP バッチ効率維持）し全件キャッシュ。
      （部分集合 VCF の分割は多アレル等で脆いため、堅実にフル解析する）
    """
    from .engine import EngineUnavailable, classify_vcf
    from .models import VariantResultCache

    try:
        variants = _read_variants(in_vcf_path, assembly)
    except Exception as exc:  # noqa: BLE001  cyvcf2 等の読み込み失敗
        raise EngineUnavailable(
            f"VCF を読み込めませんでした（VCF 形式を確認してください）: {exc}"
        ) from exc
    sig = reference_signature(assembly)
    ev = engine_version()

    cached = {}
    for key, chrom, pos, ref, alt in variants:
        row = VariantResultCache.objects.filter(
            assembly=assembly, chrom=chrom, pos=int(pos), ref=ref, alt=alt,
            engine_version=ev, refdata_signature=sig,
        ).first()
        if row is not None:
            cached[key] = row.result_json

    uncached = [v for v in variants if v[0] not in cached]

    fresh = {}
    if uncached:
        fresh = classify_vcf(in_vcf_path, assembly)
        for _key, disp in fresh.items():
            VariantResultCache.objects.update_or_create(
                assembly=assembly, chrom=disp.get("chrom", ""),
                pos=int(disp.get("pos") or 0), ref=disp.get("ref", ""),
                alt=disp.get("alt", ""), engine_version=ev, refdata_signature=sig,
                defaults={"result_json": disp},
            )
        _refresh_reference_versions(assembly)

    ordered = [fresh.get(key) or cached.get(key) for key, *_ in variants]
    _write_batch_tsv(out_tsv_path, ordered)
    return len([d for d in ordered if d])
