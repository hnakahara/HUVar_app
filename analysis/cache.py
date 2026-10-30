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
    "gnomad_noncancer_duckdb",
    "gnomad_constraint_tsv",
    "clinvar_vcf",
    "clinvar_sqlite",
    "alphamissense_tsv",
    "esm1b_sqlite",
    "revel_tsv",
    "repeatmasker_bed",
    "tp53_functional_tsv",  # TP53 PS3/BS3（NCI TP53 Database）
    # Genome Medicine 改訂で追加された参照（PVS1 / 遺伝子-疾患妥当性 / PP2）
    "clingen_dosage_tsv",
    "clingen_gene_validity_csv",
    "pp2_gene_stats_tsv",
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
    """(name, size, mtime) を返す。存在しない/None なら size=-1。"""
    if path is None:
        return "", -1, 0.0
    name = str(path)
    try:
        st = os.stat(path)
        return name, st.st_size, st.st_mtime
    except OSError:
        return name, -1, 0.0


def _tracked_paths(assembly: str, insilico_tool: str | None = None):
    """Config から署名対象パスを解決して列挙（[(label, path)]）。"""
    from .engine import _build_config  # acmg_classifier 依存（解析時のみ）
    cfg = _build_config(assembly, insilico_tool=insilico_tool)
    out = []
    # 選択した in silico ツールのスコアファイル（FuncVEP は後から配置されるため、
    # 配置・更新でキャッシュが無効化されるよう署名対象に含める）。
    try:
        from .engine import normalize_insilico_tool
        if normalize_insilico_tool(insilico_tool) == "funcvep":
            out.append(("funcvep_scores", cfg.funcvep_scores))
    except Exception:  # noqa: BLE001
        pass
    for attr in _TRACKED_ATTRS:
        try:
            p = getattr(cfg, attr)
        except Exception:  # noqa: BLE001  属性が無い版もある
            continue
        # GRCh37 では None を返す属性がある（例: gnomad_noncancer_duckdb は GRCh38 専用）。
        # None を os.stat に渡すと TypeError → 500 になるため署名対象から除外する。
        if p is not None:
            out.append((attr, p))
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
    # エンジン同梱の eRepo 分類スナップショット（VCEP 既分類の表示・PS1/PM5 比較変異）。
    # パッケージ版が同じでも同梱データ更新でキャッシュを無効化する。
    try:
        from acmg_classifier.local_db.erepo_db import _table
        out.append(("erepo_classifications", _table(cfg.assembly.value)))
    except Exception:  # noqa: BLE001
        pass
    try:
        from acmg_classifier.criteria.vcep_comparators import _RULES
        out.append(("vcep_comparator_rules", _RULES))
    except Exception:  # noqa: BLE001
        pass
    # CSpec 別閾値テーブル。更新時に cspec_evaluations を含むキャッシュを無効化する。
    try:
        from .engine import multispec_tsv_path
        out.append(("multispec", multispec_tsv_path(cfg)))
    except Exception:  # noqa: BLE001
        pass
    return out


# display dict の構造版。出力スキーマ（列/フィールド）を変えたら +1 する。
# 変更すると既存キャッシュが無効化され、次回アクセス時に新スキーマで再計算される。
# v2: ClinVar 分類サマリ（clinvar キー）を追加。
# v3: エンジンの GRCh37 MANE フォールバックで transcript 選択（HGVS/PVS1 等）が変わるため、
#     既存キャッシュ（非MANEで評価済み）を無効化して再計算させる。
# v4: Genome Medicine 改訂。criteria[].source、vcep（VCEP 既分類）、provenance（使用データ版）、
#     automated_only（eRepo / エキスパートパネル由来を除いた自動判定）を追加。
# v5: MANE Plus Clinical の補足（mane_plus_clinical / evaluated_transcript_role）を追加。
# v6: MANE Plus Clinical の transcript での評価（transcript_evaluations / available_transcripts）。
# v7: Plus Clinical 評価に比較用の mane_select_evaluation を追加。
# v8: PS3/BS3 を較正済み機能アッセイ（MaveDB / ENIGMA 表9）から判定、source に functional_assay。
_DISPLAY_SCHEMA_VERSION = 8


def reference_signature(assembly: str, insilico_tool: str | None = None) -> str:
    """参照データ版署名（SHA-256）。size+mtime+エンジン版+出力スキーマ版＋in silico
    ツールから算出。ツールを含めることで ESM1b と FuncVEP の結果が混ざらない。"""
    from .engine import normalize_insilico_tool
    tool = normalize_insilico_tool(insilico_tool)
    parts = [f"engine={engine_version()}", f"schema={_DISPLAY_SCHEMA_VERSION}",
             f"insilico={tool}"]
    for label, path in _tracked_paths(assembly, tool):
        name, size, mtime = _stat_token(path)
        parts.append(f"{label}:{name}:{size}:{int(mtime)}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _refresh_reference_versions(assembly: str, insilico_tool: str | None = None) -> None:
    """ReferenceDataVersion を最新の参照データ状態に更新（admin 可視化用）。"""
    from django.utils import timezone

    from .models import ReferenceDataVersion
    for label, path in _tracked_paths(assembly, insilico_tool):
        name, size, mtime = _stat_token(path)
        mt = timezone.datetime.fromtimestamp(mtime, tz=timezone.get_current_timezone()) \
            if mtime else None
        ReferenceDataVersion.objects.update_or_create(
            name=f"{assembly}:{label}",
            defaults={"sha256": "", "size_bytes": size, "mtime": mt},
        )


# VariantResultCache.ref/alt は varchar(512)。これを超える巨大アレル（大規模 indel/SV）は
# キャッシュ列・複合インデックスに収まらないため、キャッシュ保存をスキップする（解析自体は実行）。
_MAX_ALLELE_CACHE_LEN = 512


def _cacheable_allele(ref: str, alt: str) -> bool:
    return len(ref or "") <= _MAX_ALLELE_CACHE_LEN and len(alt or "") <= _MAX_ALLELE_CACHE_LEN


def cached_classify_single(assembly: str, chrom: str, pos: int, ref: str, alt: str,
                           insilico_tool: str | None = None):
    """キャッシュ参照付きの単一変異分類。戻り値 (display, hit)。

    参照データが未更新なら 2 回目以降は DB から返す（FR-CACHE-2）。
    手動編集（supplement）はキャッシュしないため、この関数は supplement を取らない。
    insilico_tool（esm1b / funcvep）は署名に含まれるので、ツールごとに別エントリになる。
    """
    from .engine import classify_single, normalize_insilico_tool
    from .models import VariantResultCache

    tool = normalize_insilico_tool(insilico_tool)
    sig = reference_signature(assembly, tool)
    ev = engine_version()
    key = dict(assembly=assembly, chrom=chrom, pos=int(pos), ref=ref, alt=alt,
               engine_version=ev, refdata_signature=sig)

    row = VariantResultCache.objects.filter(**key).first()
    # 単一経路とバッチ経路(cached_classify_batch)はこのキャッシュを共有するが、
    # バッチの result_json には CSpec 事前評価(available_cspecs/cspec_evaluations)が
    # 含まれない。その行をそのまま返すと多疾患遺伝子(RYR1/ACTA1/VWF)で解析後画面の
    # CSpec 切替が出ないため、単一解析として不完全な行はミス扱いにして再計算し、
    # 下の update_or_create で完全な内容へ上書きする（機能導入前の旧行も同様に更新）。
    if row is not None and "automated_only" in (row.result_json or {}):
        return row.result_json, True

    display = classify_single(assembly, chrom, int(pos), ref, alt, with_somatic=True,
                              insilico_tool=tool)
    # 同じアノテーションで作った体細胞評価（腫瘍型なし）は体細胞キャッシュへ。
    somatic = display.pop("_somatic", None)
    if _cacheable_allele(ref, alt):
        VariantResultCache.objects.update_or_create(**key, defaults={"result_json": display})
        _refresh_reference_versions(assembly, tool)
        if somatic:
            _store_somatic(assembly, chrom, pos, ref, alt, _somatic_slot("", ""), somatic,
                           somatic_signature(assembly, tool))
    return display, False


# ---------------------------------------------------------------------------
# バッチ（VCF）キャッシュ（FR-CACHE）
# ---------------------------------------------------------------------------

_TSV_BASE_COLS = [
    "variant_id", "chrom", "pos", "ref", "alt", "gene_symbol", "transcript_id",
    "hgvs_c", "hgvs_p",
    # MANE Plus Clinical の補足（評価は MANE Select）。複数（GNAS）は ";" 区切り。
    "mane_plus_clinical_transcript", "mane_plus_clinical_hgvs_c",
    "mane_plus_clinical_hgvs_p", "mane_plus_clinical_consequence",
    "mane_plus_clinical_consequence_differs",
    # Plus Clinical の transcript で評価した分類（規則の番号は MANE Select）
    "mane_plus_clinical_classification_2015", "mane_plus_clinical_rules_2015",
    "mane_plus_clinical_bayesian_score", "mane_plus_clinical_classification_bayesian",
    "mane_plus_clinical_criteria_changes",
    "classification_2015", "rules", "bayesian_score",
    "classification_bayesian", "clinvar_significance", "clinvar_review_status",
    "clinvar_stars", "clinvar_variation_id",
    "vcep_classification", "vcep_met_codes", "vcep_expert_panel", "vcep_approval_date",
    "erepo_url",
    "warnings",
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


def mane_pc_cells(display: dict) -> list:
    """display の mane_plus_clinical を TSV 5 列に整形（無ければ空欄）。"""
    pcs = (display or {}).get("mane_plus_clinical") or []
    if not pcs:
        return [""] * len(MANE_PC_COLS)

    def _j(key):
        vals = ["" if p.get(key) is None else str(p.get(key)) for p in pcs]
        return ";".join(vals) if any(vals) else ""

    changes = [p.get("criteria_changes") or "" for p in pcs]
    return [
        _j("transcript_id"), _j("hgvs_c"), _j("hgvs_p"), _j("consequence"),
        "1" if any(p.get("consequence_differs") for p in pcs) else "0",
        _j("classification_2015"), _j("rules_2015"), _j("bayesian_score"),
        _j("classification_bayesian"),
        " | ".join(changes) if any(changes) else "",
    ]


MANE_PC_COLS = (
    "transcript", "hgvs_c", "hgvs_p", "consequence", "consequence_differs",
    "classification_2015", "rules_2015", "bayesian_score", "classification_bayesian",
    "criteria_changes",
)


# 統合 TSV（生殖細胞系列＋体細胞）で生殖細胞系列の基本列の後に付ける体細胞の要約列
SOMATIC_SUMMARY_COLS = [
    "somatic_gene_role", "somatic_oncogenicity", "somatic_score", "somatic_criteria",
    "somatic_hotspot", "somatic_tumor_type", "amp_tier", "amp_level", "amp_evidence",
    "amp_note", "amp_candidates", "somatic_warnings",
]


def somatic_summary_cells(s: dict) -> list:
    if not s:
        return [""] * len(SOMATIC_SUMMARY_COLS)
    return [
        s.get("gene_role", ""), s.get("classification", ""), s.get("score", ""),
        s.get("summary", ""), s.get("hotspot", ""), s.get("tumor_code", ""),
        s.get("amp_tier", ""), s.get("amp_level", ""),
        " | ".join(s.get("amp_evidence") or []), s.get("amp_note", ""),
        " | ".join(s.get("amp_candidates") or []), " / ".join(s.get("warnings") or []),
    ]


def somatic_judgment(c: dict) -> str:
    if c.get("met") and c.get("suppressed"):
        return "suppressed"
    return "met" if c.get("met") else "not_met"


def _write_batch_tsv(out_path: str, displays: list, somatics: list = None) -> None:
    """display dict 群（VCF 順）から全クライテリア列付き TSV を書く。

    somatics（displays と同順、None 可）を渡すと、体細胞の要約列（基本列の直後）と
    体細胞クライテリア列（末尾）を加えた統合 TSV にする。"""
    pairs = [(d, (somatics[i] if somatics else None))
             for i, d in enumerate(displays) if d]
    rows = [d for d, _ in pairs]
    crit_order = [c["criterion"] for c in rows[0]["criteria"]] if rows else []
    som_order = []
    if somatics is not None:
        first = next((s for _, s in pairs if s), None)
        som_order = [c["criterion"] for c in first["criteria"]] if first else []
    header = list(_TSV_BASE_COLS)
    if somatics is not None:
        header += SOMATIC_SUMMARY_COLS
    for c in crit_order:
        header += [c, f"{c}_strength", f"{c}_evidence", f"{c}_source"]
    for c in som_order:
        header += [c, f"{c}_points", f"{c}_evidence", f"{c}_source"]

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(header)
        for d, som in pairs:
            crit_map = {c["criterion"]: c for c in d.get("criteria", [])}
            clinvar = d.get("clinvar") or {}
            row = [
                d.get("variant_id", ""), d.get("chrom", ""), d.get("pos", ""),
                d.get("ref", ""), d.get("alt", ""), d.get("gene_symbol", ""),
                d.get("transcript_id", ""), d.get("hgvs_c", ""), d.get("hgvs_p", ""),
                *mane_pc_cells(d),
                d.get("classification_2015", ""), d.get("rules", ""),
                d.get("bayesian_score", ""), d.get("classification_bayesian", ""),
                clinvar.get("significance", ""), clinvar.get("review_status", ""),
                clinvar.get("star_rating", ""), clinvar.get("variation_id", ""),
                (d.get("vcep") or {}).get("assertion", ""),
                (d.get("vcep") or {}).get("met_codes", ""),
                (d.get("vcep") or {}).get("expert_panel", ""),
                (d.get("vcep") or {}).get("approval_date", ""),
                (d.get("vcep") or {}).get("erepo_url", ""),
                " / ".join(d.get("warnings", [])),
            ]
            if somatics is not None:
                row += somatic_summary_cells(som)
            for c in crit_order:
                cc = crit_map.get(c, {})
                state = ("suppressed" if cc.get("suppressed")
                         else ("met" if cc.get("triggered") else "not_met"))
                row += [state, cc.get("strength", ""), cc.get("evidence", ""),
                        cc.get("source", "")]
            som_map = {c["criterion"]: c for c in (som or {}).get("criteria", [])}
            for c in som_order:
                sc = som_map.get(c)
                if sc is None:
                    row += ["", "", "", ""]
                    continue
                row += [somatic_judgment(sc), sc.get("points", ""), sc.get("evidence", ""),
                        "user" if sc.get("manual") else "automated"]
            w.writerow(row)


def cached_classify_batch(in_vcf_path: str, out_tsv_path: str, assembly: str,
                          somatic: bool = False, tumor_type: str = "",
                          insilico_tool: str | None = None) -> int:
    """VCF をキャッシュ活用で解析し TSV を出力。戻り値は出力した変異数。

    - 全変異がキャッシュ済み → エンジンを呼ばず TSV を即生成。
    - 未キャッシュが1つでもあれば全体を解析（VEP バッチ効率維持）し全件キャッシュ。
      （部分集合 VCF の分割は多アレル等で脆いため、堅実にフル解析する）
    - somatic=True なら体細胞評価（tumor_type の腫瘍型）も同じアノテーションで行い、
      生殖細胞系列と体細胞の両方の列を持つ統合 TSV を出力する。
    """
    from .engine import (EngineUnavailable, classify_vcf, normalize_insilico_tool,
                         normalize_tumor_type)
    from .models import VariantResultCache

    tool = normalize_insilico_tool(insilico_tool)

    try:
        variants = _read_variants(in_vcf_path, assembly)
    except Exception as exc:  # noqa: BLE001  cyvcf2 等の読み込み失敗
        raise EngineUnavailable(
            f"VCF を読み込めませんでした（VCF 形式を確認してください）: {exc}"
        ) from exc
    sig = reference_signature(assembly, tool)
    ev = engine_version()

    cached = {}
    for key, chrom, pos, ref, alt in variants:
        row = VariantResultCache.objects.filter(
            assembly=assembly, chrom=chrom, pos=int(pos), ref=ref, alt=alt,
            engine_version=ev, refdata_signature=sig,
        ).first()
        if row is not None:
            cached[key] = row.result_json

    code = normalize_tumor_type(tumor_type)
    slot = _somatic_slot(code, "")
    som_cached = {}
    if somatic:
        som_sig = somatic_signature(assembly, tool)
        for key, chrom, pos, ref, alt in variants:
            d = _load_somatic(assembly, chrom, pos, ref, alt, slot, som_sig)
            if d is not None:
                som_cached[key] = d

    uncached = [v for v in variants
                if v[0] not in cached or (somatic and v[0] not in som_cached)]

    fresh, som_fresh = {}, {}
    if uncached:
        fresh = classify_vcf(in_vcf_path, assembly, somatic=somatic, tumor_type=code,
                             insilico_tool=tool)
        for _key, disp in fresh.items():
            som = disp.pop("_somatic", None)
            if som:
                som_fresh[_key] = som
            # 512 文字超の巨大アレルはキャッシュ列に収まらないため保存をスキップ
            if not _cacheable_allele(disp.get("ref", ""), disp.get("alt", "")):
                continue
            VariantResultCache.objects.update_or_create(
                assembly=assembly, chrom=disp.get("chrom", ""),
                pos=int(disp.get("pos") or 0), ref=disp.get("ref", ""),
                alt=disp.get("alt", ""), engine_version=ev, refdata_signature=sig,
                defaults={"result_json": disp},
            )
            if som:
                _store_somatic(assembly, disp.get("chrom", ""), int(disp.get("pos") or 0),
                               disp.get("ref", ""), disp.get("alt", ""), slot, som, som_sig)
        _refresh_reference_versions(assembly, tool)

    ordered = [fresh.get(key) or cached.get(key) for key, *_ in variants]
    somatics = ([som_fresh.get(key) or som_cached.get(key) for key, *_ in variants]
                if somatic else None)
    _write_batch_tsv(out_tsv_path, ordered, somatics)
    _write_batch_provenance(out_tsv_path, assembly, tool)
    return len([d for d in ordered if d])


def provenance_path(out_tsv_path: str) -> str:
    """バッチ結果 TSV に添付する使用データ版情報（JSON）のパス。"""
    return f"{out_tsv_path}.provenance.json"


def _write_batch_provenance(out_tsv_path: str, assembly: str,
                            insilico_tool: str | None = None) -> None:
    """使用データの版情報を TSV と同じ場所に JSON で保存する（失敗しても解析は止めない）。"""
    import json

    from .engine import batch_provenance
    try:
        prov = batch_provenance(assembly, insilico_tool)
        with open(provenance_path(out_tsv_path), "w", encoding="utf-8") as fh:
            json.dump(prov, fh, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 体細胞評価（oncogenicity ＋ AMP tier）のキャッシュ
# ---------------------------------------------------------------------------

# 体細胞評価だけが使う参照（Config 属性, 同梱ファイル名）。同梱版があるものは
# data/shared に無ければ同梱版を署名対象にする（エンジンの resolve と同じ規則）。
_SOMATIC_TRACKED = [
    ("somatic_gene_roles_tsv", "somatic_gene_roles.tsv"),
    ("cancer_hotspots_tsv", "cancer_hotspots_v2.tsv"),
    ("somatic_functional_curated_tsv", "somatic_functional_curated.tsv"),
    ("oncotree_doid_tsv", "oncotree_doid.tsv"),
    ("doid_hierarchy_tsv", "doid_hierarchy.tsv"),
    ("civic_somatic_tsv", None),
    ("civic_clinical_tsv", None),
]

# 体細胞 display の構造版（engine._somatic_display を変えたら +1）。
_SOMATIC_SCHEMA_VERSION = 1


def somatic_signature(assembly: str, insilico_tool: str | None = None) -> str:
    """体細胞評価のキャッシュ署名。生殖細胞系列の署名（ClinVar・gnomAD・VEP など共通の
    参照を含む）に体細胞専用の参照（CIViC・cancerhotspots・NCG・OncoTree 等）を加える。
    VariantResultCache の生殖細胞系列の行とは署名が異なるので混ざらない。"""
    from .engine import _build_config, normalize_insilico_tool
    tool = normalize_insilico_tool(insilico_tool)
    parts = [f"somatic_schema={_SOMATIC_SCHEMA_VERSION}",
             reference_signature(assembly, tool)]
    cfg = _build_config(assembly, insilico_tool=tool)
    try:
        from acmg_classifier.somatic.resources import resolve
    except Exception:  # noqa: BLE001
        resolve = None
    for attr, packaged in _SOMATIC_TRACKED:
        try:
            p = getattr(cfg, attr)
        except Exception:  # noqa: BLE001
            continue
        if packaged and resolve is not None:
            p = resolve(p, packaged)
        name, size, mtime = _stat_token(p)
        parts.append(f"{attr}:{name}:{size}:{int(mtime)}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _somatic_slot(tumor_code: str, transcript: str) -> str:
    return f"{tumor_code or ''}|{transcript or ''}"


def _somatic_key(assembly, chrom, pos, ref, alt, sig=None) -> dict:
    return dict(assembly=assembly, chrom=chrom, pos=int(pos), ref=ref, alt=alt,
                engine_version=engine_version(),
                refdata_signature=sig or somatic_signature(assembly))


def _load_somatic(assembly, chrom, pos, ref, alt, slot, sig=None):
    from .models import VariantResultCache
    row = VariantResultCache.objects.filter(**_somatic_key(assembly, chrom, pos, ref, alt, sig)).first()
    if row is None:
        return None
    return ((row.result_json or {}).get("somatic") or {}).get(slot)


def _store_somatic(assembly, chrom, pos, ref, alt, slot, display, sig=None) -> None:
    """体細胞キャッシュ（1 変異 1 行、{slot: display}）へ保存。失敗しても解析は止めない。"""
    from .models import VariantResultCache
    if not _cacheable_allele(ref, alt):
        return
    try:
        key = _somatic_key(assembly, chrom, pos, ref, alt, sig)
        row = VariantResultCache.objects.filter(**key).first()
        store = dict((row.result_json or {}).get("somatic") or {}) if row is not None else {}
        store[slot] = display
        VariantResultCache.objects.update_or_create(**key, defaults={"result_json": {"somatic": store}})
    except Exception:  # noqa: BLE001
        pass


def cached_classify_somatic(assembly: str, chrom: str, pos: int, ref: str, alt: str,
                            tumor_type: str = "", transcript: str = "",
                            insilico_tool: str | None = None):
    """キャッシュ参照付きの体細胞評価。戻り値 (display, hit)。

    1 変異 1 行（署名 = somatic_signature）に {腫瘍型|transcript: display} を蓄積する。
    手動の根拠（OP2 など）を付けた再評価はキャッシュしない（engine.classify_somatic を直接呼ぶ）。
    """
    from .engine import classify_somatic, normalize_insilico_tool, normalize_tumor_type

    tool = normalize_insilico_tool(insilico_tool)
    sig = somatic_signature(assembly, tool)
    slot = _somatic_slot(normalize_tumor_type(tumor_type), transcript)
    hit = _load_somatic(assembly, chrom, pos, ref, alt, slot, sig)
    if hit is not None:
        return hit, True
    display = classify_somatic(assembly, chrom, int(pos), ref, alt, tumor_type=tumor_type,
                               transcript=transcript, insilico_tool=tool)
    _store_somatic(assembly, chrom, pos, ref, alt, slot, display, sig)
    return display, False
