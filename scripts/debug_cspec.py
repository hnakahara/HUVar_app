"""CSpec 事前評価がなぜ落ちるかを切り分ける診断スクリプト。

`analysis.engine.classify_single` は各 CSpec の再評価を

    try:
        ... classify_annotated(variant, ann, overlaid_cfg) ...
    except Exception:
        continue        # ← 例外もトレースバックも残さない

で握りつぶすため、CSpec が「選択できない」ときに原因の criterion が分からない。
このスクリプトは同じ処理を握りつぶさずに実行し、落ちた CSpec の完全なトレースバックを表示する。

実行（解析エンジンと参照データが入ったコンテナ内で）:

    # dev
    docker compose exec -T acmgapp python /code/scripts/debug_cspec.py
    # prod
    docker compose -f docker-compose.prod.yml exec -T acmgapp python /code/scripts/debug_cspec.py

acmgapp が無い構成なら `app` / `worker` でも可（同じイメージ・同じマウント）。
"""
from __future__ import annotations

import os
import traceback
from pathlib import Path

DATA_DIR = os.environ.get("ACMG_DATA_DIR", "/data")
ASSEMBLY = "GRCh38"

# (ラベル, chrom, pos, ref, alt)
CASES = [
    ("R3772Q  CSpec が出る側 (対照)", "chr19", 38534775, "G", "A"),
    ("P863L   CSpec が出ない側",      "chr19", 38463433, "C", "T"),
]


def build_config():
    from acmg_classifier.config import Config
    from acmg_classifier.models.enums import Assembly

    # 単一バリアント解析と同じ高感度設定
    return Config(
        data_dir=Path(DATA_DIR),
        assembly=Assembly(ASSEMBLY),
        openspliceai_flanking_size=2000,
    )


def multispec_path(cfg) -> Path:
    return cfg.data_dir / "shared" / "disease_prevalence_multispec.tsv"


def run_case(label: str, chrom: str, pos: int, ref: str, alt: str) -> None:
    from acmg_classifier.pipeline.pipeline import annotate_one, classify_annotated
    from acmg_classifier.criteria.cspec_overlay import available_cspecs, overlaid_config

    print("=" * 80)
    print(f"{label}   {chrom}:{pos}{ref}>{alt}")
    print("=" * 80)

    cfg = build_config()

    # --- アノテーション（CSpec 非依存・重い処理。1 回だけ） ---
    try:
        variant, ann = annotate_one(chrom, int(pos), ref, alt, cfg)
    except Exception:
        print("[annotate_one] EXCEPTION:")
        traceback.print_exc()
        return

    pc = getattr(ann, "primary_consequence", None)
    gene = (getattr(pc, "gene_symbol", "") if pc else "") or ""
    print(f"  primary_consequence.gene_symbol = {gene!r}")
    print(f"  transcript = {getattr(pc, 'transcript_id', '')!r}"
          f"  hgvs_c = {getattr(pc, 'hgvs_c', '')!r}"
          f"  hgvs_p = {getattr(pc, 'hgvs_p', '')!r}")

    # --- 保守版（既定）評価 ---
    try:
        classify_annotated(variant, ann, cfg)
        print("  [conservative]            OK")
    except Exception:
        print("  [conservative]            EXCEPTION:")
        traceback.print_exc()

    # --- CSpec 候補 ---
    ms = multispec_path(cfg)
    print(f"  multispec table exists = {ms.exists()}  ({ms})")
    cspecs = available_cspecs(gene, ms)
    print(f"  available_cspecs({gene!r}) -> {[c['cspec_id'] for c in cspecs]}")
    if not cspecs:
        print("  → 候補ゼロ。gene_symbol が想定と違うか multispec テーブル未配置。"
              "（これが原因なら例外ではなく gene 解決の問題）")
        return

    # --- 各 CSpec を握りつぶさずに評価 ---
    ok, ng = [], []
    for cs in cspecs:
        cid = cs["cspec_id"]
        try:
            with overlaid_config(cfg, ms, gene, cid) as c2:
                classify_annotated(variant, ann, c2)
            ok.append(cid)
            print(f"  [{cid}] OK")
        except Exception:
            ng.append(cid)
            print(f"  [{cid}] EXCEPTION:")
            traceback.print_exc()

    print(f"\n  --> 成功 {ok or '(なし)'} / 失敗 {ng or '(なし)'}")
    print("  （engine.classify_single では成功したものだけが画面の CSpec 切替に出る）")


if __name__ == "__main__":
    for case in CASES:
        run_case(*case)
        print()
