"""変異結果キャッシュ（FR-CACHE）。

一度解析した変異の自動判定結果を DB(VariantResultCache) に保存し、参照データが更新
されるまで 2 回目以降は DB から返す。参照データ版署名は、主要参照ファイルの
(サイズ + 更新時刻 mtime) ＋ エンジン版を連結した SHA-256。いずれかのファイルが更新
されると署名が変わり、キャッシュは自動的に無効化される（新署名で別エントリ扱い）。

手動編集（supplement）込みの再分類はユーザー依存のためキャッシュしない（毎回再計算）。
"""
from __future__ import annotations

import hashlib
import os
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
