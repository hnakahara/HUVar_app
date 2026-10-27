"""HUVar 解析エンジン（acmg_classifier）連携。

単一変異は同期実行（FR-SINGLE-1）。エンジンは app/worker イメージに
`pip install -e /huvar` で導入される前提（entrypoint）。未導入・参照データ未配置でも
Django が 500 にならないよう、import とデータ参照は関数内で行い EngineUnavailable を送出する。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional


# 手動編集 UI 用の strength 選択肢（Indeterminate は除外）
STRENGTH_CHOICES = ["VeryStrong", "Strong", "ThreePoint", "Moderate", "Supporting", "NotMet"]

# HUVar 参照データ。entrypoint で /data にマウントする想定（ACMG_DATA_DIR で上書き可）。
DATA_DIR = os.environ.get("ACMG_DATA_DIR", "/data")


class EngineUnavailable(RuntimeError):
    """エンジン未導入・参照データ未配置・解析失敗を表す。"""


def _build_config(assembly: str, openspliceai_flanking_size: Optional[int] = None):
    from acmg_classifier.config import Config
    from acmg_classifier.models.enums import Assembly

    try:
        asm = Assembly(assembly)
    except ValueError as exc:
        raise EngineUnavailable(f"未対応のアセンブリ: {assembly}") from exc
    kwargs = {}
    # TP53 の PS3/BS3（VCEP 機能解析フローチャート、NCI TP53 Database 由来）。
    # データの利用条件は収益化につながる利用を禁止しているためエンジン既定は OFF だが、
    # 本アプリは非営利の学術公開なので ON にする（HUVAR_TP53_FUNCTIONAL=0 で無効化）。
    kwargs["use_tp53_functional"] = os.environ.get("HUVAR_TP53_FUNCTIONAL", "1") != "0"
    # 単一バリアント解析のみ高感度な flanking を指定（batch は既定 80nt のまま）。
    if openspliceai_flanking_size is not None:
        kwargs["openspliceai_flanking_size"] = openspliceai_flanking_size
    return Config(data_dir=Path(DATA_DIR), assembly=asm, **kwargs)


# アセンブリ → eRepo マニュアルクライテリアのファイル接尾辞 / パーマリンク表記
_HGVER = {"GRCh38": "hg38", "GRCh37": "hg19"}
# パーマリンク URL のトークン → 内部 Assembly 値。hg / GRCh どちらの表記も受理。
_ASSEMBLY_TOKENS = {
    "hg38": "GRCh38", "grch38": "GRCh38",
    "hg19": "GRCh37", "grch37": "GRCh37",
}


def assembly_to_hg(assembly: str) -> str:
    """内部 Assembly 値（GRCh38/GRCh37）→ パーマリンク表記（hg38/hg19）。
    未知の値はそのまま返す。"""
    return _HGVER.get(assembly, assembly)


def parse_assembly_token(token: str) -> Optional[str]:
    """パーマリンクの assembly トークン（hg38/hg19/GRCh38/GRCh37、大小無視）を
    内部 Assembly 値へ。未対応は None。"""
    return _ASSEMBLY_TOKENS.get((token or "").strip().lower())


def manual_criteria_path(cfg):
    """既定マニュアルエビデンス（eRepo）の TSV パス。data/shared/erepo_manual_criteria_<hg>.tsv。"""
    hg = _HGVER.get(cfg.assembly.value)
    if not hg:
        return None
    return cfg.data_dir / "shared" / f"erepo_manual_criteria_{hg}.tsv"


def _load_manual_for_variant(cfg, variant_key: str):
    """eRepo マニュアルクライテリアから当該変異の SupplementEntry リストを返す。"""
    path = manual_criteria_path(cfg)
    if not path or not path.exists():
        return []
    try:
        from acmg_classifier.io.supplement_reader import read_supplement
        return read_supplement(path).get(variant_key, [])
    except Exception:  # noqa: BLE001  マニュアル読込失敗は解析を止めない
        return []


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


def _gene_from_ann(ann) -> str:
    """アノテーションの primary consequence から遺伝子シンボルを取り出す。
    CSpec(多疾患)判定のキー。取れなければ空文字。"""
    pc = getattr(ann, "primary_consequence", None)
    return (getattr(pc, "gene_symbol", "") if pc else "") or ""


def multispec_tsv_path(cfg):
    """CSpec 別閾値テーブル(disease_prevalence_multispec.tsv)のパス。"""
    return cfg.data_dir / "shared" / "disease_prevalence_multispec.tsv"


def _clinvar_display(ann) -> dict:
    """AnnotationData から ClinVar 分類サマリを抽出する（JSON/TSV 出力用）。

    exact-match（同一 chrom:pos:ref:alt）の ClinVar VCF レコードを対象にする。
    複数提出者が食い違う場合は star_rating 最大のレコードを代表値とする
    （3-star のエキスパートパネル判定が 1-star の食い違い提出より優先）。
    ClinVar に該当が無ければ空 dict を返す。"""
    records = list(getattr(ann, "clinvar_vcf", None) or []) if ann is not None else []
    if not records:
        return {}
    best = max(records, key=lambda r: getattr(r, "star_rating", 0))
    return {
        "significance": best.clinical_significance,
        "review_status": best.review_status,
        "star_rating": best.star_rating,
        "variation_id": best.variation_id or "",
        "records": [
            {
                "significance": r.clinical_significance,
                "review_status": r.review_status,
                "star_rating": r.star_rating,
                "variation_id": r.variation_id or "",
            }
            for r in records
        ],
    }


# クライテリアの由来ラベル（結果画面のバッジ・エクスポート用）。
SOURCE_USER = "user"                # 画面上でユーザーが編集
SOURCE_EREPO = "erepo"              # 同梱 eRepo マニュアルクライテリア（VCEP 判定）
SOURCE_EXPERT_PANEL = "expert_panel"  # ClinVar エキスパートパネル（★3以上）の判定
SOURCE_TEXT_MINING = "text_mining"  # ClinVar 提出テキストの自動抽出（Supporting 上限）
SOURCE_AUTOMATED = "automated"      # ローカルデータからの自動判定


def _annotate_sources(display: dict, erepo_criteria=(), user_criteria=()) -> dict:
    """display["criteria"] の各要素に source を付与する（in place）。

    Met でないクライテリアは空文字。erepo_criteria / user_criteria は当該変異に
    供給された supplement のクライテリア名集合。"""
    erepo = set(erepo_criteria)
    user = set(user_criteria)
    for c in display.get("criteria", []) or []:
        if not c.get("triggered") or c.get("strength") == "NotMet":
            c["source"] = ""
            continue
        crit, ev = c.get("criterion", ""), c.get("evidence", "") or ""
        if crit in user:
            c["source"] = SOURCE_USER
        elif crit in erepo:
            c["source"] = SOURCE_EREPO
        elif "[ClinVar expert panel]" in ev:
            c["source"] = SOURCE_EXPERT_PANEL
        elif "[ClinVar text mining]" in ev:
            c["source"] = SOURCE_TEXT_MINING
        else:
            c["source"] = SOURCE_AUTOMATED
    return display


def _vcep_record(cfg, chrom, pos, ref, alt) -> dict:
    """VCEP が分類済みならその情報（eRepo スナップショット）、無ければ {}。"""
    try:
        from acmg_classifier.local_db.erepo_db import lookup
        return lookup(cfg.assembly.value, chrom, int(pos), ref, alt) or {}
    except Exception:  # noqa: BLE001  旧エンジンでは存在しない
        return {}


def _provenance(cfg) -> dict:
    """使用データの版情報（acmg_classifier.provenance.collect）。旧エンジンでは {}。"""
    try:
        from acmg_classifier.provenance import collect
        return collect(cfg)
    except Exception:  # noqa: BLE001
        return {}


_MANE_PC_KEYS = ("transcript_id", "hgvs_c", "hgvs_p", "consequence",
                 "classification_2015", "rules_2015", "classification_bayesian",
                 "criteria_changes")


def _mane_plus_clinical_display(result, ann) -> list:
    """MANE Plus Clinical 上の表記（補足情報）。評価は MANE Select のまま。

    run_pipeline 由来の result は mane_plus_clinical を持つ。classify_annotated 由来は
    持たないため AnnotationData から求める（旧エンジンでは属性が無く空リスト）。"""
    items = getattr(result, "mane_plus_clinical", None)
    if not items and ann is not None and hasattr(ann, "mane_plus_clinical_summary"):
        items = ann.mane_plus_clinical_summary()
    out = []
    for m in items or []:
        d = m.model_dump() if hasattr(m, "model_dump") else dict(m)
        row = {k: d.get(k) or "" for k in _MANE_PC_KEYS}
        row["consequence_differs"] = bool(d.get("consequence_differs"))
        row["bayesian_score"] = d.get("bayesian_score")
        out.append(row)
    return out


def _evaluated_transcript_role(ann) -> str:
    """評価に使った transcript（primary consequence）の MANE 区分。"""
    pc = getattr(ann, "primary_consequence", None) if ann is not None else None
    if pc is not None and getattr(pc, "is_mane_select", False):
        return "MANE Select"
    if pc is not None and getattr(pc, "is_mane_plus_clinical", False):
        return "MANE Plus Clinical"
    return ""


def align_variant_to_evaluation(variant: dict, display: dict) -> dict:
    """variant の transcript/HGVS を「実際に評価した transcript」にそろえる。

    TransVar 候補（MANE Plus Clinical 上の c. 表記など）から解析した場合でも、エンジンは
    その座標を MANE Select で評価する。入力時の表記は input_* に残して画面に併記する。"""
    v = dict(variant)
    ev_tx = display.get("transcript_id") or ""
    in_tx = v.get("transcript") or ""
    # 同じ transcript なら入力時の表記（TransVar 形式）をそのまま使う（従来どおり）。
    if not ev_tx or (in_tx and in_tx.split(".")[0] == ev_tx.split(".")[0]):
        return v
    if in_tx:
        v["input_transcript"] = in_tx
        v["input_hgvs_c"] = v.get("hgvs_c", "")
        v["input_hgvs_p"] = v.get("hgvs_p", "")
    v["transcript"] = ev_tx
    v["hgvs_c"] = display.get("hgvs_c") or ""
    v["hgvs_p"] = display.get("hgvs_p") or ""
    v["gene"] = display.get("gene_symbol") or v.get("gene", "")
    return v


def _to_display(result, ann=None) -> dict:
    """ClassificationResult -> テンプレート/DB 用の素の dict。

    criteria は全クライテリア（not_met 含む）。points は @property のため個別取得。
    ann は ClinVar サマリ抽出用の AnnotationData（省略時は result.annotation を使う。
    classify_annotated 由来の result は annotation を持たないため呼び出し側で渡す）。"""
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
    clinvar_ann = ann if ann is not None else getattr(result, "annotation", None)
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
        "clinvar": _clinvar_display(clinvar_ann),
        "evaluated_transcript_role": _evaluated_transcript_role(clinvar_ann),
        "mane_plus_clinical": _mane_plus_clinical_display(result, clinvar_ann),
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

    RYR1/ACTA1/VWF など複数 CSpec を持つ遺伝子では、保守的（既定）評価に加えて
    各 CSpec での評価も事前計算して同梱する（アノテーションは1回のみ実行し、判定
    層だけ CSpec 別の閾値テーブルで再評価する）。返り値 dict に以下を追加:
      - available_cspecs: [{cspec_id, label, source_gn}]（無ければ []）
      - cspec_evaluations: {cspec_id: display}（保守的版と同じ形）
      - active_cspec: 既定は ""（保守的）。表示切替は保存済み結果から行う。
    """
    try:
        from acmg_classifier.pipeline.pipeline import annotate_one, classify_annotated
        from acmg_classifier.criteria.cspec_overlay import available_cspecs, overlaid_config
    except Exception as exc:  # noqa: BLE001  (ImportError 等)
        raise EngineUnavailable(
            f"解析エンジン(acmg_classifier)が利用できません: {exc}"
        ) from exc

    # 単一バリアントは OpenSpliceAI を高感度設定（2000nt）で実行する。batch は既定 80nt。
    cfg = _build_config(assembly, openspliceai_flanking_size=2000)
    # 既定でマニュアルエビデンス(eRepo)を適用するため、ClinVar エキスパートパネル
    # （★3以上）の判定も取り込む（acmg_classifier は curated_evidence=True のときのみ参照）。
    # 「自動判定のみ」版は下で exclude_self_expert_panel=True により除外する。
    cfg = cfg.model_copy(update={"curated_evidence": True})
    # 変異キー（chrom 正規化込み）を算出してマニュアル照合・supplement に使う
    try:
        from acmg_classifier.models.enums import Assembly
        from acmg_classifier.models.variant import VariantRecord
        variant_id = VariantRecord(
            chrom=chrom, pos=int(pos), ref=ref, alt=alt, assembly=Assembly(assembly)
        ).key
    except Exception:  # noqa: BLE001
        variant_id = f"{chrom}:{pos}:{ref}:{alt}"

    # 既定マニュアルエビデンス(eRepo) ＋ ユーザー手動編集 を merge（cfg.supplement_mode=MERGE）
    # ユーザーが編集したクライテリアは eRepo より優先する（編集した項目のみ eRepo を除外）。
    manual = _load_manual_for_variant(cfg, variant_id)
    user = _build_supplement(supplement_entries, variant_id) or []
    if user:
        edited = {u.criterion for u in user}
        manual = [m for m in manual if getattr(m, "criterion", None) not in edited]
    supplement = (list(manual) + list(user)) or None

    # アノテーション（重い処理）は1回だけ。判定は保守的版＋各 CSpec 版で使い回す。
    try:
        variant, ann = annotate_one(chrom, int(pos), ref, alt, cfg)
        conservative = classify_annotated(variant, ann, cfg, supplement=supplement)
    except FileNotFoundError as exc:
        raise EngineUnavailable(
            f"参照データが見つかりません（{DATA_DIR} 配下を確認してください）: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise EngineUnavailable(f"解析に失敗しました: {exc}") from exc

    display = _to_display(conservative, ann)
    erepo_crits = {m.criterion.value for m in manual}
    user_crits = {u.criterion.value for u in user}
    _annotate_sources(display, erepo_crits, user_crits)
    # VCEP 既分類の表示（Reviewer 3 コメント20）と使用データの版情報
    display["vcep"] = _vcep_record(cfg, chrom, pos, ref, alt)
    display["provenance"] = _provenance(cfg)

    # eRepo 由来クライテリアと ClinVar エキスパートパネル由来クライテリアを除いた
    # 「自動判定のみ」の結果。同一アノテーションを再利用する（ユーザー編集は保持）。
    if erepo_crits or any(c.get("source") == SOURCE_EXPERT_PANEL for c in display["criteria"]):
        try:
            cfg_auto = cfg.model_copy(update={"exclude_self_expert_panel": True})
            auto = classify_annotated(variant, ann, cfg_auto, supplement=list(user) or None)
            auto_display = _annotate_sources(_to_display(auto, ann), (), user_crits)
            for key in ("gene_symbol", "transcript_id", "hgvs_c", "hgvs_p",
                        "evaluated_transcript_role", "mane_plus_clinical"):
                auto_display[key] = display.get(key, "")
            auto_display["vcep"] = display["vcep"]
            auto_display["provenance"] = display["provenance"]
            display["automated_only"] = auto_display
        except Exception:  # noqa: BLE001  自動判定のみ版の失敗は本結果を壊さない
            display["automated_only"] = None
    else:
        display["automated_only"] = None
    # 座標URL経由など variant メタ(gene/transcript/HGVS)が未設定のとき、
    # アノテーションの primary consequence から補完する（run_single 由来は空のため）。
    pc = getattr(ann, "primary_consequence", None)
    if pc is not None:
        for key in ("gene_symbol", "transcript_id", "hgvs_c", "hgvs_p"):
            if not display.get(key):
                display[key] = getattr(pc, key, "") or ""
    gene = display.get("gene_symbol", "")

    # 多疾患遺伝子のみ CSpec 別に事前評価（同一 annotation を再利用、TSV 差し替えのみ）。
    multispec = multispec_tsv_path(cfg)
    cspecs = available_cspecs(gene, multispec)
    cspec_evaluations: dict = {}
    for cs in cspecs:
        try:
            with overlaid_config(cfg, multispec, gene, cs["cspec_id"]) as c2:
                r = classify_annotated(variant, ann, c2, supplement=supplement)
            d2 = _annotate_sources(_to_display(r, ann), erepo_crits, user_crits)
            d2["vcep"] = display["vcep"]
            d2["provenance"] = display["provenance"]
            cspec_evaluations[cs["cspec_id"]] = d2
        except Exception:  # noqa: BLE001  CSpec 評価の失敗は保守的結果を壊さない
            continue
    # 実際に評価できた CSpec のみ提示する。
    display["available_cspecs"] = [c for c in cspecs if c["cspec_id"] in cspec_evaluations]
    display["cspec_evaluations"] = cspec_evaluations
    display["active_cspec"] = ""

    # MANE Plus Clinical の transcript で評価した結果（同一アノテーションを再利用し、
    # 判定層だけ再評価）。VCEP の位置規則・ClinVar の同一コドン照合は MANE Select の
    # 番号で行われる（エンジンの rule_consequence）。保守的 CSpec・eRepo/手動編集込み。
    transcript_evaluations, available_transcripts = _transcript_evaluations(
        variant, ann, cfg, supplement, erepo_crits, user_crits, display)
    display["transcript_evaluations"] = transcript_evaluations
    display["available_transcripts"] = available_transcripts
    display["active_transcript"] = ""
    return display


def _transcript_summary(display: dict, role: str) -> dict:
    return {
        "transcript_id": display.get("transcript_id", ""),
        "role": role,
        "hgvs_c": display.get("hgvs_c", ""),
        "hgvs_p": display.get("hgvs_p", ""),
        "classification_2015": display.get("classification_2015", ""),
        "classification_bayesian": display.get("classification_bayesian", ""),
        "bayesian_score": display.get("bayesian_score"),
    }


def _transcript_evaluations(variant, ann, cfg, supplement, erepo_crits, user_crits,
                            base_display: dict):
    """{transcript_id: display} for each MANE Plus Clinical transcript, and the
    switch list [{transcript_id, role, ...}] (MANE Select first). Empty when the
    gene has no Plus Clinical transcript or the engine predates phase 2."""
    if ann is None or not hasattr(ann, "for_transcript"):
        return {}, []
    from acmg_classifier.pipeline.pipeline import classify_annotated

    evaluations: dict = {}
    for m in ann.mane_plus_clinical_summary():
        try:
            ann_tx = ann.for_transcript(m.transcript_id)
            r = classify_annotated(variant, ann_tx, cfg, supplement=supplement)
            d = _annotate_sources(_to_display(r, ann_tx), erepo_crits, user_crits)
        except Exception:  # noqa: BLE001  Plus Clinical 評価の失敗は既定結果を壊さない
            continue
        tpc, rc = ann_tx.primary_consequence, ann_tx.rule_consequence
        d["gene_symbol"] = getattr(tpc, "gene_symbol", "") or ""
        d["transcript_id"] = getattr(tpc, "transcript_id", "") or ""
        d["hgvs_c"] = getattr(tpc, "hgvs_c", "") or ""
        d["hgvs_p"] = getattr(tpc, "hgvs_p", "") or ""
        # 評価中の transcript 自身は補足行に出さず、規則の番号に使った MANE Select を示す。
        d["mane_plus_clinical"] = []
        d["rule_transcript"] = ({
            "transcript_id": rc.transcript_id, "hgvs_c": rc.hgvs_c or "",
            "hgvs_p": rc.hgvs_p or "", "consequence": rc.consequence.value,
        } if rc is not None and rc is not tpc else None)
        d["vcep"] = base_display.get("vcep")
        d["provenance"] = base_display.get("provenance")
        d["automated_only"] = None
        evaluations[d["transcript_id"]] = d
    if not evaluations:
        return {}, []
    switch = [_transcript_summary(base_display, base_display.get("evaluated_transcript_role", ""))]
    switch += [_transcript_summary(d, "MANE Plus Clinical") for d in evaluations.values()]
    return evaluations, switch


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
    mpath = manual_criteria_path(cfg)
    sup_path = mpath if (mpath and mpath.exists()) else None  # 既定マニュアル(merge)
    try:
        results = run_pipeline(Path(vcf_path), cfg, supplement_path=sup_path)  # TSV は当方で生成
    except FileNotFoundError as exc:
        raise EngineUnavailable(
            f"参照データ/入力が見つかりません（{DATA_DIR} 配下を確認）: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise EngineUnavailable(f"バッチ解析に失敗しました: {exc}") from exc

    erepo_by_var = {}
    if sup_path is not None:
        try:
            from acmg_classifier.io.supplement_reader import read_supplement
            erepo_by_var = read_supplement(sup_path)
        except Exception:  # noqa: BLE001
            erepo_by_var = {}
    prov = _provenance(cfg)
    out = {}
    for r in results:
        key = getattr(r, "variant_id", None) or f"{r.chrom}:{r.pos}:{r.ref}:{r.alt}"
        d = _to_display(r)
        _annotate_sources(d, {e.criterion.value for e in erepo_by_var.get(key, [])})
        d["vcep"] = _vcep_record(cfg, r.chrom, r.pos, r.ref, r.alt)
        d["provenance"] = prov
        out[key] = d
    return out


def batch_provenance(assembly: str) -> dict:
    """バッチ結果に添付する使用データの版情報。"""
    return _provenance(_build_config(assembly))
