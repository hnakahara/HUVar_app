"""HUHVar 解析エンジン（acmg_classifier）連携。

単一変異は同期実行（FR-SINGLE-1）。エンジンは app/worker イメージに
`pip install -e /huhvar` で導入される前提（entrypoint）。未導入・参照データ未配置でも
Django が 500 にならないよう、import とデータ参照は関数内で行い EngineUnavailable を送出する。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional


# 手動編集 UI 用の strength 選択肢（Indeterminate は除外）
STRENGTH_CHOICES = ["VeryStrong", "Strong", "ThreePoint", "Moderate", "Supporting", "NotMet"]

# HUHVar 参照データ。entrypoint で /data にマウントする想定（ACMG_DATA_DIR で上書き可）。
DATA_DIR = os.environ.get("ACMG_DATA_DIR", "/data")


class EngineUnavailable(RuntimeError):
    """エンジン未導入・参照データ未配置・解析失敗を表す。"""


def _build_config(assembly: str):
    from acmg_classifier.config import Config
    from acmg_classifier.models.enums import Assembly

    try:
        asm = Assembly(assembly)
    except ValueError as exc:
        raise EngineUnavailable(f"未対応のアセンブリ: {assembly}") from exc
    return Config(data_dir=Path(DATA_DIR), assembly=asm)


def _build_supplement(entries: Optional[List[dict]], variant_id: str):
    """手動編集 -> SupplementEntry リスト（merge で再分類）。"""
    if not entries:
        return None
    from acmg_classifier.models.enums import ACMGCriterion, CriterionStrength
    from acmg_classifier.models.supplement import SupplementEntry

    out = []
    for e in entries:
        try:
            out.append(SupplementEntry(
                variant_id=variant_id,
                criterion=ACMGCriterion(e["criterion"]),
                strength=CriterionStrength(e["strength"]),
                evidence=e.get("evidence", ""),
            ))
        except (KeyError, ValueError):
            continue
    return out or None


def _to_display(result) -> dict:
    """ClassificationResult -> テンプレート/DB 用の素の dict。

    criteria は全クライテリア（not_met 含む）。points は @property のため個別取得。"""
    criteria = []
    for r in result.criteria_results:
        criteria.append({
            "criterion": r.criterion.value,
            "triggered": r.triggered,
            "strength": r.strength.value,
            "direction": r.direction.value,
            "evidence": r.evidence,
            "suppressed": r.suppressed,
            "points": r.points,
        })
    return {
        # 変異情報（run_pipeline 由来の結果には設定される。run_single では空）
        "variant_id": getattr(result, "variant_id", "") or "",
        "chrom": getattr(result, "chrom", "") or "",
        "pos": getattr(result, "pos", 0) or 0,
        "ref": getattr(result, "ref", "") or "",
        "alt": getattr(result, "alt", "") or "",
        "gene_symbol": getattr(result, "gene_symbol", "") or "",
        "transcript_id": getattr(result, "transcript_id", "") or "",
        "hgvs_c": getattr(result, "hgvs_c", "") or "",
        "hgvs_p": getattr(result, "hgvs_p", "") or "",
        "classification_2015": result.classification_2015.value,
        "rules": result.classification_2015_rules,
        "bayesian_score": result.bayesian_score,
        "classification_bayesian": result.classification_bayesian.value,
        "warnings": list(result.warnings or []),
        "criteria": criteria,
    }


def classify_single(
    assembly: str,
    chrom: str,
    pos: int,
    ref: str,
    alt: str,
    supplement_entries: Optional[List[dict]] = None,
) -> dict:
    """単一変異を ACMG 分類し、全クライテリア＋分類結果を dict で返す。

    supplement_entries: [{criterion, strength, evidence}] 手動編集（merge）。
    """
    try:
        from acmg_classifier.pipeline.pipeline import run_single
    except Exception as exc:  # noqa: BLE001  (ImportError 等)
        raise EngineUnavailable(
            f"解析エンジン(acmg_classifier)が利用できません: {exc}"
        ) from exc

    cfg = _build_config(assembly)
    variant_id = f"{chrom}:{pos}:{ref}:{alt}"
    supplement = _build_supplement(supplement_entries, variant_id)

    try:
        result = run_single(chrom, int(pos), ref, alt, cfg, supplement=supplement)
    except FileNotFoundError as exc:
        raise EngineUnavailable(
            f"参照データが見つかりません（{DATA_DIR} 配下を確認してください）: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise EngineUnavailable(f"解析に失敗しました: {exc}") from exc

    return _to_display(result)


def classify_batch(vcf_path: str, output_tsv_path: str, assembly: str) -> int:
    """VCF を一括 ACMG 分類し、全クライテリア列を含む TSV を output_tsv_path に書く。

    返り値は分類した変異数。バッチは Celery タスクから呼ばれる（FR-BATCH）。
    """
    try:
        from acmg_classifier.pipeline.pipeline import run_pipeline
    except Exception as exc:  # noqa: BLE001
        raise EngineUnavailable(
            f"解析エンジン(acmg_classifier)が利用できません: {exc}"
        ) from exc

    cfg = _build_config(assembly)
    try:
        results = run_pipeline(Path(vcf_path), cfg, output_path=Path(output_tsv_path))
    except FileNotFoundError as exc:
        raise EngineUnavailable(
            f"参照データ/入力が見つかりません（{DATA_DIR} 配下を確認）: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise EngineUnavailable(f"バッチ解析に失敗しました: {exc}") from exc
    return len(results)


def classify_vcf(vcf_path: str, assembly: str) -> dict:
    """VCF の全変異を解析し {variant_key: display} を返す（バッチキャッシュ用）。

    variant_key は VariantRecord.key（"chrom:pos:ref:alt"）と一致する。
    """
    try:
        from acmg_classifier.pipeline.pipeline import run_pipeline
    except Exception as exc:  # noqa: BLE001
        raise EngineUnavailable(
            f"解析エンジン(acmg_classifier)が利用できません: {exc}"
        ) from exc

    cfg = _build_config(assembly)
    try:
        results = run_pipeline(Path(vcf_path), cfg)  # output_path=None: TSV は当方で生成
    except FileNotFoundError as exc:
        raise EngineUnavailable(
            f"参照データ/入力が見つかりません（{DATA_DIR} 配下を確認）: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise EngineUnavailable(f"バッチ解析に失敗しました: {exc}") from exc

    out = {}
    for r in results:
        key = getattr(r, "variant_id", None) or f"{r.chrom}:{r.pos}:{r.ref}:{r.alt}"
        out[key] = _to_display(r)
    return out
