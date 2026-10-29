import csv
import io
import json
import logging
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.notifications import notify_admin
from transvar_client.client import TransvarError
from transvar_client.client import convert as transvar_convert

from .cache import cached_classify_single, cached_classify_somatic, somatic_judgment
from .engine import (
    STRENGTH_CHOICES,
    EngineUnavailable,
    UnknownTumorType,
    align_variant_to_evaluation,
    assembly_to_hg,
    classify_single,
    classify_somatic,
    normalize_tumor_type,
    parse_assembly_token,
    somatic_criteria,
)
from .cache import MANE_PC_COLS, mane_pc_cells
from .forms import BatchVariantForm, SingleVariantForm
from .models import AnalysisJob, AuditLog, VariantResult
from .tasks import cleanup_expired_jobs, run_batch_classification

logger = logging.getLogger(__name__)

# 参照データ等の内部パスを露出しないため、クライアントには汎用文を返す
_ENGINE_ERROR_MSG = "解析エンジンが一時的に利用できません。時間をおいて再度お試しください。"


@login_required
def index(request):
    """トップ画面（解析入力フォームへの入口）。"""
    return render(request, "analysis/index.html")


@login_required
def help_page(request):
    """各機能の使い方を説明するガイドページ。"""
    return render(request, "analysis/help.html")


@login_required
def single_input(request):
    """単一変異の入力 → TransVar 変換（MANE 限定）→ 候補提示。"""
    form = SingleVariantForm(request.POST or None)
    context = {"form": form}

    if request.method == "POST" and form.is_valid():
        try:
            result = transvar_convert(
                form.cleaned_data["query"],
                assembly=form.cleaned_data["assembly"],
                kind=form.cleaned_data.get("kind") or None,
            )
        except TransvarError as exc:
            context["error"] = f"TransVar サービスに接続できません: {exc}"
            return render(request, "analysis/single_input.html", context)

        if not result.get("ok"):
            context["error"] = result.get("error") or "変換に失敗しました。"
            return render(request, "analysis/single_input.html", context)

        return render(request, "analysis/single_resolve.html", {
            "query": form.cleaned_data["query"],
            "assembly": form.cleaned_data["assembly"],
            "refversion": result.get("refversion"),
            "kind": result.get("kind"),
            "candidates": result.get("candidates", []),
            # c./p. 番号が MANE Plus Clinical 上でのみ（または別座標で）解釈された候補
            "has_pc_candidate": any(c.get("mane_status") == "MANE Plus Clinical"
                                    for c in result.get("candidates", [])),
        })

    return render(request, "analysis/single_input.html", context)


def _variant_from_post(request) -> dict:
    return {
        "assembly": request.POST.get("assembly", ""),
        "chrom": request.POST.get("chrom", ""),
        "pos": request.POST.get("pos", ""),
        "ref": request.POST.get("ref", ""),
        "alt": request.POST.get("alt", ""),
        "gene": request.POST.get("gene", ""),
        "transcript": request.POST.get("transcript", ""),
        "hgvs_c": request.POST.get("hgvs_c", ""),
        "hgvs_p": request.POST.get("hgvs_p", ""),
    }


_ALLELE_RE = re.compile(r"^[ACGTN]+$", re.IGNORECASE)


def _parse_locus(locus: str):
    """パーマリンク locus 'chrom-pos-ref-alt-assembly' を解析する純粋関数。

    ref/alt は塩基列で '-' を含まないため末尾固定分割(rsplit)が安全。
    成功で (chrom, pos:int, ref, alt, assembly:内部値) を、不正なら None を返す。
    例: 'chr3-52576541-C-T-hg38' -> ('chr3', 52576541, 'C', 'T', 'GRCh38')
    """
    parts = (locus or "").rsplit("-", 4)
    if len(parts) != 5:
        return None
    chrom, pos, ref, alt, asm_token = parts
    assembly = parse_assembly_token(asm_token)
    # chrom に '-' が残る = 余分な区切りを含む不正入力（rsplit が先頭に残す）。
    if not assembly or not chrom or "-" in chrom:
        return None
    try:
        pos_i = int(pos)
    except (TypeError, ValueError):
        return None
    if pos_i <= 0:
        return None
    ref, alt = ref.upper(), alt.upper()
    if not (_ALLELE_RE.match(ref) and _ALLELE_RE.match(alt)):
        return None
    return chrom, pos_i, ref, alt, assembly


def _permalink_from_variant(request, variant: dict) -> str:
    """variant dict から共有用の座標パーマリンク（絶対URL）を組み立てる。
    必要項目が欠ける場合は空文字。"""
    if not variant:
        return ""
    if any(variant.get(k) in (None, "") for k in ("assembly", "chrom", "pos", "ref", "alt")):
        return ""
    hg = assembly_to_hg(str(variant["assembly"]))
    locus = f'{variant["chrom"]}-{variant["pos"]}-{variant["ref"]}-{variant["alt"]}-{hg}'
    return request.build_absolute_uri(reverse("analysis:single_variant", args=[locus]))


VIEW_AUTOMATED = "automated"


def _apply_view(request, display: dict):
    """?view=automated のとき「自動判定のみ」（eRepo / ClinVar エキスパートパネル由来を
    除外）の事前計算結果を返す。戻り値 (表示する display, 現在の view, 切替可否)。"""
    auto = (display or {}).get("automated_only")
    if request.GET.get("view") == VIEW_AUTOMATED and auto:
        return auto, VIEW_AUTOMATED, True
    return display, "", bool(auto)


@login_required
def single_variant(request, locus: str):
    """座標パーマリンク（chrom-pos-ref-alt-assembly）から直接解析して表示する。

    アドレスバーは座標URLのまま。結果は VariantResult に get_or_create し、
    編集/CSpec切替/export は従来どおり pk ベースで動作する。
    """
    parsed = _parse_locus(locus)
    if not parsed:
        raise Http404("不正なバリアント座標です。")
    chrom, pos, ref, alt, assembly = parsed

    try:
        display, _hit = cached_classify_single(assembly, chrom, pos, ref, alt)
    except (EngineUnavailable, ValueError) as exc:
        logger.warning("single_variant engine error: %s", exc)
        return render(request, "analysis/single_result.html", {
            "variant": {"assembly": assembly, "chrom": chrom, "pos": pos,
                        "ref": ref, "alt": alt},
            "engine_error": _ENGINE_ERROR_MSG,
            "strength_choices": STRENGTH_CHOICES,
        })

    variant = {
        "assembly": assembly, "chrom": chrom, "pos": pos, "ref": ref, "alt": alt,
        "gene": display.get("gene_symbol", ""),
        "transcript": display.get("transcript_id", ""),
        "hgvs_c": display.get("hgvs_c", ""),
        "hgvs_p": display.get("hgvs_p", ""),
    }
    variant_id = f"{chrom}:{pos}:{ref}:{alt}"
    result_json = {"variant": variant, "display": display, "edits": []}

    vr = (VariantResult.objects
          .filter(job__owner=request.user, variant_id=variant_id)
          .order_by("-id").first())
    if vr is None:
        job = AnalysisJob.objects.create(
            owner=request.user, kind=AnalysisJob.Kind.SINGLE, assembly=assembly,
            input_text=f"{chrom}:{pos}{ref}>{alt}", status=AnalysisJob.Status.DONE,
        )
        vr = VariantResult.objects.create(
            job=job, variant_id=variant_id, gene_symbol=variant["gene"],
            transcript_id=variant["transcript"], hgvs_c=variant["hgvs_c"],
            hgvs_p=variant["hgvs_p"], classification=display["classification_bayesian"],
            bayesian_score=display["bayesian_score"], result_json=result_json,
        )
        AuditLog.objects.create(user=request.user, action="single_variant",
                                detail=variant_id)
    else:
        # 既存結果を最新 display で更新（参照データ更新を反映、pk は安定）。
        vr.gene_symbol = variant["gene"]
        vr.transcript_id = variant["transcript"]
        vr.hgvs_c = variant["hgvs_c"]
        vr.hgvs_p = variant["hgvs_p"]
        vr.classification = display["classification_bayesian"]
        vr.bayesian_score = display["bayesian_score"]
        vr.result_json = result_json
        vr.save()

    shown, view, can_toggle = _apply_view(request, display)
    return render(request, "analysis/single_result.html", {
        "result_id": vr.pk,
        "variant": variant,
        "display": shown,
        "edits": [],
        "strength_choices": STRENGTH_CHOICES,
        "available_cspecs": display.get("available_cspecs", []) if not view else [],
        "active_cspec": "",
        "available_transcripts": display.get("available_transcripts", []) if not view else [],
        "active_transcript": "",
        "view": view,
        "can_toggle_erepo": can_toggle,
        "permalink": _permalink_from_variant(request, variant),
        "export_json": json.dumps({"variant": variant, "display": shown, "edits": [],
                                   "view": view or "default"}, ensure_ascii=False),
    })


@login_required
def single_analyze(request):
    """選択された変異（解決済み genome 座標）を ACMG 解析し、結果を保存して表示する。"""
    if request.method != "POST":
        return redirect("analysis:single_input")

    variant = _variant_from_post(request)
    try:
        display, _hit = cached_classify_single(
            variant["assembly"], variant["chrom"], int(variant["pos"]),
            variant["ref"], variant["alt"],
        )
    except (EngineUnavailable, ValueError) as exc:
        logger.warning("single_analyze engine error: %s", exc)
        return render(request, "analysis/single_result.html", {
            "variant": variant, "engine_error": _ENGINE_ERROR_MSG,
            "strength_choices": STRENGTH_CHOICES,
        })
    # 評価は MANE Select（エンジンの primary consequence）で行われる。Plus Clinical 候補
    # を選んだ場合も、保存・表示する transcript/HGVS は評価に使ったものにそろえ、
    # 入力時の表記は input_* として併記する。
    variant = align_variant_to_evaluation(variant, display)

    job = AnalysisJob.objects.create(
        owner=request.user,
        kind=AnalysisJob.Kind.SINGLE,
        assembly=variant["assembly"],
        input_text=f'{variant["chrom"]}:{variant["pos"]}{variant["ref"]}>{variant["alt"]}',
        status=AnalysisJob.Status.DONE,
    )
    vr = VariantResult.objects.create(
        job=job,
        variant_id=f'{variant["chrom"]}:{variant["pos"]}:{variant["ref"]}:{variant["alt"]}',
        gene_symbol=variant["gene"],
        transcript_id=variant["transcript"],
        hgvs_c=variant["hgvs_c"],
        hgvs_p=variant["hgvs_p"],
        classification=display["classification_bayesian"],
        bayesian_score=display["bayesian_score"],
        result_json={"variant": variant, "display": display, "edits": []},
    )
    AuditLog.objects.create(user=request.user, action="single_analyze",
                            detail=vr.variant_id)
    met_criteria = [
        f"{c['criterion']}({c['strength']})"
        for c in display.get("criteria", [])
        if c.get("triggered")
    ]
    met_summary = ", ".join(met_criteria) if met_criteria else "なし"
    notify_admin(
        "単一バリアント解析(explain)実行",
        f"ユーザー: {request.user.get_username()}\n"
        f"アセンブリ: {variant.get('assembly') or '-'}\n"
        f"遺伝子: {vr.gene_symbol or '-'}\n"
        f"バリアント: {vr.hgvs_c or '-'} {vr.hgvs_p or ''}\n"
        f"座標: {vr.variant_id}\n"
        f"分類(ACMG 2015): {display.get('classification_2015', '-')}\n"
        f"分類(Bayesian): {display.get('classification_bayesian', '-')}"
        f"（score {display.get('bayesian_score', '-')}）\n"
        f"Met クライテリア: {met_summary}\n",
    )
    # 変換結果で MANE Plus Clinical の候補（またはその表記）を選んだ場合は、
    # その transcript で評価した結果を開く。
    posted_tx = (request.POST.get("transcript", "") or "").split(".")[0]
    evals = display.get("transcript_evaluations") or {}
    hit = next((k for k in evals if posted_tx and k.split(".")[0] == posted_tx), None)
    if hit:
        return redirect("analysis:single_transcript", pk=vr.pk, transcript=hit)
    return redirect("analysis:single_result", pk=vr.pk)


@login_required
def single_result(request, pk: int):
    vr = get_object_or_404(VariantResult, pk=pk, job__owner=request.user)
    data = vr.result_json or {}
    display = data.get("display", {}) or {}
    variant = data.get("variant", {})
    shown, view, can_toggle = _apply_view(request, display)
    export = dict(data, display=shown, view=view or "default")
    return render(request, "analysis/single_result.html", {
        "result_id": vr.pk,
        "variant": variant,
        "display": shown,
        "edits": data.get("edits", []),
        "strength_choices": STRENGTH_CHOICES,
        # 多疾患遺伝子(RYR1/ACTA1/VWF)のみ非空。保守的(既定)が active。
        "available_cspecs": display.get("available_cspecs", []) if not view else [],
        "active_cspec": "",
        # MANE Plus Clinical を持つ遺伝子のみ非空。MANE Select（既定）が active。
        "available_transcripts": display.get("available_transcripts", []) if not view else [],
        "active_transcript": "",
        "view": view,
        "can_toggle_erepo": can_toggle,
        "permalink": _permalink_from_variant(request, variant),
        # 画面に表示中の内容をそのままエクスポートできるよう JSON を埋め込む
        "export_json": json.dumps(export, ensure_ascii=False),
    })


@login_required
def single_cspec(request, pk: int, cspec_id: str):
    """保存済み結果を特定 CSpec（疾患/病型）の事前計算結果に切り替えて表示する。

    再解析は行わず、single_analyze 時に同梱した cspec_evaluations から取り出すだけ。
    cspec_id が空 / "conservative" のときは保守的(既定)評価に戻す。
    """
    vr = get_object_or_404(VariantResult, pk=pk, job__owner=request.user)
    data = vr.result_json or {}
    variant = data.get("variant", {})
    base_display = data.get("display", {}) or {}
    available = base_display.get("available_cspecs", []) or []

    if cspec_id in ("", "conservative"):
        display = base_display
        active = ""
    else:
        evals = base_display.get("cspec_evaluations", {}) or {}
        display = evals.get(cspec_id)
        if display is None:
            raise Http404("指定された CSpec の結果がありません。")
        active = cspec_id

    export_data = {"variant": variant, "display": display, "edits": [],
                   "view": "cspec" if active else "default", "cspec": active}
    return render(request, "analysis/single_result.html", {
        "result_id": vr.pk,
        "variant": variant,
        "display": display,
        "edits": [],
        "strength_choices": STRENGTH_CHOICES,
        "available_cspecs": available,
        "active_cspec": active,
        "permalink": _permalink_from_variant(request, variant),
        "export_json": json.dumps(export_data, ensure_ascii=False),
    })


@login_required
def single_transcript(request, pk: int, transcript: str):
    """保存済み結果を MANE Plus Clinical の transcript で評価した事前計算結果に切り替える。

    再解析は行わず、解析時に同梱した transcript_evaluations から取り出すだけ。
    transcript が空 / "select" のときは MANE Select（既定）の評価に戻す。
    """
    vr = get_object_or_404(VariantResult, pk=pk, job__owner=request.user)
    data = vr.result_json or {}
    variant = data.get("variant", {})
    base_display = data.get("display", {}) or {}
    available = base_display.get("available_transcripts", []) or []
    if transcript in ("", "select"):
        return redirect("analysis:single_result", pk=pk)
    display = (base_display.get("transcript_evaluations", {}) or {}).get(transcript)
    if display is None:
        raise Http404("指定された transcript の結果がありません。")
    shown_variant = dict(variant, transcript=display.get("transcript_id", transcript),
                         hgvs_c=display.get("hgvs_c", ""), hgvs_p=display.get("hgvs_p", ""))
    export_data = {"variant": shown_variant, "display": display, "edits": [],
                   "view": "mane_plus_clinical"}
    return render(request, "analysis/single_result.html", {
        "result_id": vr.pk,
        "variant": shown_variant,
        "display": display,
        "edits": [],
        "strength_choices": STRENGTH_CHOICES,
        # CSpec 切替・eRepo 除外は MANE Select（既定）の評価でのみ提供する。
        "available_cspecs": [],
        "active_cspec": "",
        "available_transcripts": available,
        "active_transcript": transcript,
        "can_toggle_erepo": False,
        "permalink": _permalink_from_variant(request, variant),
        "export_json": json.dumps(export_data, ensure_ascii=False),
    })


@login_required
def single_edit(request, pk: int):
    """クライテリアの重み(strength)・evidence を手動編集して再分類（FR-SINGLE-5）。"""
    vr = get_object_or_404(VariantResult, pk=pk, job__owner=request.user)
    if request.method != "POST":
        return redirect("analysis:single_result", pk=pk)

    data = vr.result_json or {}
    variant = data.get("variant", {})
    prev_criteria = (data.get("display", {}) or {}).get("criteria", [])

    # 各クライテリアの編集を収集（strength を選んだものだけ supplement 化）
    entries = []
    for c in prev_criteria:
        crit = c["criterion"]
        strength = request.POST.get(f"strength_{crit}", "").strip()
        evidence = request.POST.get(f"evidence_{crit}", "").strip()
        if strength in STRENGTH_CHOICES:
            entries.append({"criterion": crit, "strength": strength, "evidence": evidence})

    try:
        display = classify_single(
            variant["assembly"], variant["chrom"], int(variant["pos"]),
            variant["ref"], variant["alt"], supplement_entries=entries,
        )
    except (EngineUnavailable, ValueError) as exc:
        logger.warning("single_edit engine error: %s", exc)
        return render(request, "analysis/single_result.html", {
            "result_id": vr.pk, "variant": variant,
            "display": data.get("display", {}), "edits": entries,
            "engine_error": _ENGINE_ERROR_MSG, "strength_choices": STRENGTH_CHOICES,
        })

    # Plus Clinical の評価を表示中に編集した場合は、同じ transcript の再評価を表示する
    # （手動編集は MANE Select・Plus Clinical の両評価に同じく適用される）。
    active_tx = request.POST.get("active_transcript", "").strip()
    shown_variant = variant
    if active_tx:
        tx_display = (display.get("transcript_evaluations", {}) or {}).get(active_tx)
        if tx_display is not None:
            shown_variant = dict(variant, transcript=tx_display.get("transcript_id", active_tx),
                                 hgvs_c=tx_display.get("hgvs_c", ""),
                                 hgvs_p=tx_display.get("hgvs_p", ""))
            display = tx_display
        else:
            active_tx = ""

    # 手動編集による再分類はデータベースに保存しない（その場で表示するのみ）。
    # 元の自動分類結果(VariantResult)は上書きせず保持する。
    edited = {"variant": shown_variant, "display": display, "edits": entries}
    return render(request, "analysis/single_result.html", {
        "result_id": vr.pk,
        "variant": shown_variant,
        "active_transcript": active_tx,
        "display": display,
        "edits": entries,
        "strength_choices": STRENGTH_CHOICES,
        "permalink": _permalink_from_variant(request, variant),
        # 未保存の編集結果をそのままエクスポートできるよう JSON を埋め込む
        "export_json": json.dumps(edited, ensure_ascii=False),
    })


def _result_to_tsv(data: dict) -> str:
    """単一変異の結果(result_json)を TSV 文字列に整形する。

    先頭にメタ情報（# 始まり）、続いてクライテリア表を出力する。
    """
    variant = data.get("variant", {}) or {}
    display = data.get("display", {}) or {}
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n")

    genome = (f'{variant.get("chrom", "")}:{variant.get("pos", "")}'
              f'{variant.get("ref", "")}>{variant.get("alt", "")}')
    warnings = display.get("warnings") or []
    if isinstance(warnings, (list, tuple)):
        warnings = " / ".join(str(x) for x in warnings)
    clinvar = display.get("clinvar") or {}
    vcep = display.get("vcep") or {}
    rule = display.get("rule_transcript") or {}
    sel = display.get("mane_select_evaluation") or {}
    meta = [
        ("assembly", variant.get("assembly", "")),
        ("genome", genome),
        ("gene", variant.get("gene", "")),
        ("transcript", variant.get("transcript", "")),
        ("hgvs_c", variant.get("hgvs_c", "")),
        ("hgvs_p", variant.get("hgvs_p", "")),
        ("evaluated_transcript_role", display.get("evaluated_transcript_role", "")),
        ("input_transcript", variant.get("input_transcript", "")),
        ("input_hgvs_c", variant.get("input_hgvs_c", "")),
        ("input_hgvs_p", variant.get("input_hgvs_p", "")),
        *[(f"mane_plus_clinical_{k}", v) for k, v in zip(MANE_PC_COLS, mane_pc_cells(display))],
        ("rule_numbering_transcript", rule.get("transcript_id", "")),
        ("rule_numbering_hgvs_c", rule.get("hgvs_c", "")),
        ("rule_numbering_hgvs_p", rule.get("hgvs_p", "")),
        ("rule_numbering_consequence", rule.get("consequence", "")),
        # Plus Clinical の評価をエクスポートしたときの比較対象（MANE Select での評価）
        ("mane_select_transcript", sel.get("transcript_id", "")),
        ("mane_select_hgvs_c", sel.get("hgvs_c", "")),
        ("mane_select_hgvs_p", sel.get("hgvs_p", "")),
        ("mane_select_classification_2015", sel.get("classification_2015", "")),
        ("mane_select_rules_2015", sel.get("rules", "")),
        ("mane_select_bayesian_score", "" if sel.get("bayesian_score") is None
         else sel.get("bayesian_score")),
        ("mane_select_classification_bayesian", sel.get("classification_bayesian", "")),
        ("criteria_changes_from_mane_select", sel.get("criteria_changes", "")),
        ("classification_2015", display.get("classification_2015", "")),
        ("rules", display.get("rules", "")),
        ("classification_bayesian", display.get("classification_bayesian", "")),
        ("bayesian_score", display.get("bayesian_score", "")),
        ("clinvar_significance", clinvar.get("significance", "")),
        ("clinvar_review_status", clinvar.get("review_status", "")),
        ("clinvar_stars", clinvar.get("star_rating", "")),
        ("clinvar_variation_id", clinvar.get("variation_id", "")),
        ("vcep_classification", vcep.get("assertion", "")),
        ("vcep_met_codes", vcep.get("met_codes", "")),
        ("vcep_multiple_classifications", "1" if vcep.get("conflict") else ("0" if vcep else "")),
        ("vcep_transcript", vcep.get("transcript", "")),
        ("vcep_hgvs_c", vcep.get("hgvs_c", "")),
        ("vcep_expert_panel", vcep.get("expert_panel", "")),
        ("vcep_disease", vcep.get("disease", "")),
        ("vcep_approval_date", vcep.get("approval_date", "")),
        ("erepo_url", vcep.get("erepo_url", "")),
        ("erepo_snapshot", vcep.get("snapshot", "")),
        ("view", data.get("view", "default")),
        ("cspec", data.get("cspec", "")),
        ("warnings", warnings),
    ]
    for k, v in meta:
        w.writerow([f"# {k}", v])
    # 使用データの版情報（ClinVar リリース日、VEP、gnomAD、ClinGen など）
    prov = display.get("provenance") or {}
    if prov.get("huvar_version"):
        w.writerow(["# huvar_version", prov.get("huvar_version", "")])
    for name, info in sorted((prov.get("resources") or {}).items()):
        if isinstance(info, dict):
            text = "; ".join(f"{k}={v}" for k, v in info.items() if v not in (None, ""))
        else:
            text = str(info)
        w.writerow([f"# data_{name}", text])

    # 体細胞（oncogenicity ＋ AMP tier）。生殖細胞系列と同じファイルに # somatic_* で併記する。
    somatic = data.get("somatic") or {}
    if somatic.get("error"):
        w.writerow(["# somatic_error", somatic["error"]])
    elif somatic:
        manual = ", ".join(f'{m.get("criterion")}={"met" if m.get("met") else "not_met"}'
                           for m in somatic.get("manual") or [])
        som_meta = [
            ("somatic_transcript", somatic.get("transcript_id", "")),
            ("somatic_hgvs_c", somatic.get("hgvs_c", "")),
            ("somatic_hgvs_p", somatic.get("hgvs_p", "")),
            ("somatic_consequence", somatic.get("consequence", "")),
            ("somatic_gene_role", somatic.get("gene_role", "")),
            ("somatic_gene_role_source", somatic.get("gene_role_source", "")),
            ("somatic_oncogenicity", somatic.get("classification", "")),
            ("somatic_score", somatic.get("score", "")),
            ("somatic_criteria", somatic.get("summary", "")),
            ("somatic_hotspot", somatic.get("hotspot", "")),
            ("somatic_tumor_type", somatic.get("tumor_code", "")),
            ("somatic_tumor_type_mapping", somatic.get("tumor_type", "")),
            ("amp_tier", somatic.get("amp_tier", "")),
            ("amp_level", somatic.get("amp_level", "")),
            ("amp_note", somatic.get("amp_note", "")),
            ("amp_evidence", " | ".join(somatic.get("amp_evidence") or [])),
            ("amp_candidates", " | ".join(somatic.get("amp_candidates") or [])),
            ("somatic_manual_evidence", manual),
            ("somatic_warnings", " / ".join(somatic.get("warnings") or [])),
        ]
        for k, v in som_meta:
            w.writerow([f"# {k}", v])

    # クライテリア表。framework 列で ACMG（生殖細胞系列）と somatic を区別する。
    w.writerow(["criterion", "judgment", "direction", "strength", "points", "evidence",
                "source", "framework"])
    for c in display.get("criteria", []) or []:
        if c.get("suppressed"):
            judgment = "suppressed"
        elif c.get("triggered"):
            judgment = "MET"
        else:
            judgment = "not met"
        w.writerow([
            c.get("criterion", ""), judgment, c.get("direction", ""),
            c.get("strength", ""), c.get("points", ""), c.get("evidence", ""),
            c.get("source", ""), "ACMG",
        ])
    if not somatic.get("error"):
        for c in somatic.get("criteria", []) or []:
            judgment = {"met": "MET", "not_met": "not met"}.get(somatic_judgment(c), "suppressed")
            w.writerow([
                c.get("criterion", ""), judgment, c.get("direction", ""),
                c.get("strength", ""), c.get("points", ""), c.get("evidence", ""),
                "user" if c.get("manual") else "automated", "somatic",
            ])
    return buf.getvalue()


def _germline_for_export(vr, tx: str) -> dict:
    """保存済みの生殖細胞系列の結果（tx 指定時は MANE Plus Clinical の評価）を
    エクスポート形 {variant, display, edits, view} で返す。"""
    data = vr.result_json or {}
    variant = data.get("variant", {}) or {}
    display = data.get("display", {}) or {}
    tx_display = (display.get("transcript_evaluations", {}) or {}).get(tx) if tx else None
    if tx_display is not None:
        shown = dict(variant, transcript=tx_display.get("transcript_id", tx),
                     hgvs_c=tx_display.get("hgvs_c", ""), hgvs_p=tx_display.get("hgvs_p", ""))
        return {"variant": shown, "display": tx_display, "edits": [],
                "view": "mane_plus_clinical"}
    return {"variant": variant, "display": display, "edits": data.get("edits", []),
            "view": "default"}


@login_required
def single_export(request, pk: int):
    """生殖細胞系列（ACMG）と体細胞（oncogenicity ＋ AMP tier）を統合した結果を
    JSON / TSV でダウンロード（FR-SINGLE-6）。

    体細胞の情報が不足するときに生殖細胞系列の情報を参照できるよう、どちらのタブから
    ダウンロードしても両方を含める。
      - result_json（POST）: 画面に表示中の生殖細胞系列の結果（未保存の手動編集を含む）。
        無ければ保存済みの結果（tx 指定時はその transcript の評価）。
      - somatic_json（POST）: 画面に表示中の体細胞評価（手動の根拠を含む）。無ければ
        この結果で最後に選んだ腫瘍型で評価する（キャッシュ）。
    fmt=tsv で TSV、それ以外（既定）は JSON を返す。
    """
    vr = get_object_or_404(VariantResult, pk=pk, job__owner=request.user)
    post = request.POST if request.method == "POST" else {}
    tx = (post.get("tx") or request.GET.get("tx") or "").strip()
    data = None
    if post.get("result_json"):
        try:
            data = json.loads(post["result_json"])
        except (ValueError, TypeError):
            data = None
    if not isinstance(data, dict):
        data = _germline_for_export(vr, tx)

    somatic = None
    if post.get("somatic_json"):
        try:
            somatic = json.loads(post["somatic_json"])
        except (ValueError, TypeError):
            somatic = None
    if not isinstance(somatic, dict):
        base = (vr.result_json or {}).get("variant", {}) or {}
        try:
            somatic, _err = _somatic_for(base, remembered_tumor(request, pk), tx)
        except Exception as exc:  # noqa: BLE001  体細胞評価の失敗は生殖細胞系列の出力を止めない
            logger.warning("single_export somatic error: %s", exc)
            somatic = {"error": _ENGINE_ERROR_MSG}
    data = dict(data, somatic=somatic)

    fmt = (post.get("fmt") or request.GET.get("fmt") or "json").lower()
    if fmt == "tsv":
        body = _result_to_tsv(data)
        resp = HttpResponse(body, content_type="text/tab-separated-values; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="acmg_result_{vr.pk}.tsv"'
        return resp

    body = json.dumps(data, ensure_ascii=False, indent=2)
    resp = HttpResponse(body, content_type="application/json; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="acmg_result_{vr.pk}.json"'
    return resp


# ---------------------------------------------------------------------------
# 体細胞変異としての評価（Horak 2022 oncogenicity ＋ AMP/ASCO/CAP 2017 tier）
# ---------------------------------------------------------------------------

ONC_EDIT_MET = "met"
ONC_EDIT_NOT_MET = "not_met"
_TUMOR_SESSION_KEY = "somatic_tumor"   # {str(result pk): OncoTree コード}
_TUMOR_SESSION_MAX = 50


def remembered_tumor(request, pk) -> str:
    """結果ごとに最後に選んだ腫瘍型（Germline/Somatic タブの切替で保持する）。"""
    try:
        return (request.session.get(_TUMOR_SESSION_KEY) or {}).get(str(pk), "")
    except Exception:  # noqa: BLE001
        return ""


def _remember_tumor(request, pk, code: str) -> None:
    store = dict(request.session.get(_TUMOR_SESSION_KEY) or {})
    store.pop(str(pk), None)
    store[str(pk)] = code
    while len(store) > _TUMOR_SESSION_MAX:
        store.pop(next(iter(store)))
    request.session[_TUMOR_SESSION_KEY] = store


def _plus_clinical_ids(display: dict) -> set:
    return {t.get("transcript_id") for t in (display or {}).get("available_transcripts") or []
            if t.get("role") == "MANE Plus Clinical" and t.get("transcript_id")}


def _somatic_manual_from_post(request) -> list:
    """編集フォーム（onc_<CRIT> = met / not_met、onc_evidence_<CRIT>）→ 手動根拠のリスト。"""
    entries = []
    for c in somatic_criteria():
        crit = c["criterion"]
        state = (request.POST.get(f"onc_{crit}") or "").strip()
        if state in (ONC_EDIT_MET, ONC_EDIT_NOT_MET):
            entries.append({
                "criterion": crit, "met": state == ONC_EDIT_MET,
                "evidence": (request.POST.get(f"onc_evidence_{crit}") or "").strip(),
            })
    return entries


def _amp_badge(tier: str) -> str:
    if tier in ("Tier I", "Tier II"):
        return "badge-path"
    if tier == "Tier IV":
        return "badge-benign"
    return "badge-vus"


_AMP_EVIDENCE_RE = re.compile(
    r"^(?P<tier>Tier [IV]+(?:, level [A-D])?) \((?P<category>[^,()]+), (?P<source>[^)]+)\): "
    r"(?P<text>.*)$")
_AMP_CANDIDATE_RE = re.compile(r"^\((?P<category>[^)]+)\) (?P<text>.*)$")
_CIVIC_REF_RE = re.compile(r"^CIViC (?P<ref>(?P<kind>[EA])ID(?P<num>\d+))\s*")
_CLINVAR_REF_RE = re.compile(r"\(ClinVar record (?P<num>\d+),\s*")
_PMID_RE = re.compile(r"\s*\(PMID (?P<pmid>[\d-]+), rating (?P<rating>[^)]*)\)")


def amp_evidence_rows(items, candidates: bool = False) -> list:
    """エンジンの AMP 根拠文字列（"Tier I, level A (therapeutic, CIViC): CIViC EID1411 ..."、
    候補は "(therapeutic) CIViC EID11238 ..."）を表の行 dict に分解する。
    形式が合わない文字列は内容列にそのまま入れる。"""
    rows = []
    for item in items or []:
        m = (_AMP_CANDIDATE_RE if candidates else _AMP_EVIDENCE_RE).match(item or "")
        row = {"tier": "", "badge": "badge-vus", "category": "", "source": "", "ref": "",
               "ref_url": "", "detail": item, "pmid": "", "pmid_url": ""}
        if m:
            text = m.group("text")
            row["category"] = m.group("category")
            if not candidates:
                row["tier"] = m.group("tier")
                row["badge"] = _amp_badge(m.group("tier").split(",")[0])
                row["source"] = m.group("source")
            ref = _CIVIC_REF_RE.match(text)
            if ref:
                row["source"] = row["source"] or "CIViC"
                row["ref"] = ref.group("ref")
                kind = "evidence" if ref.group("kind") == "E" else "assertions"
                row["ref_url"] = f"https://civicdb.org/{kind}/{ref.group('num')}"
                text = text[ref.end():]
                lv = re.match(r"level ([A-E?]) ", text)
                if lv:
                    text = f"CIViC evidence level {lv.group(1)}: {text[lv.end():]}"
            elif text.startswith("ClinVar"):
                row["source"] = row["source"] or "ClinVar"
                text = text.removeprefix("ClinVar somatic clinical impact ")
                cv = _CLINVAR_REF_RE.search(text)
                if cv:
                    row["ref"] = f"record {cv.group('num')}"
                    text = text[:cv.start()] + "(" + text[cv.end():]
            pm = _PMID_RE.search(text)
            if pm:
                pmid = pm.group("pmid")
                if pmid.isdigit():
                    row["pmid"] = pmid
                    row["pmid_url"] = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                text = (text[:pm.start()] + text[pm.end():]).strip()
                if pm.group("rating") not in ("", "-"):
                    text += f" (rating {pm.group('rating')})"
            row["detail"] = text
        rows.append(row)
    return rows


def _somatic_for(variant: dict, tumor: str, tx: str = "", manual=None):
    """体細胞評価（手動の根拠が無ければキャッシュ経由）。戻り値 (display, tumor_error)。
    不明な腫瘍型は腫瘍型なしで評価し、エラー文を返す。"""
    def _run(code):
        if manual:
            return classify_somatic(
                variant["assembly"], variant["chrom"], int(variant["pos"]), variant["ref"],
                variant["alt"], tumor_type=code, transcript=tx, manual_entries=manual)
        return cached_classify_somatic(
            variant["assembly"], variant["chrom"], int(variant["pos"]), variant["ref"],
            variant["alt"], tumor_type=code, transcript=tx)[0]
    try:
        return _run(tumor), ""
    except UnknownTumorType as exc:
        return _run(""), str(exc)


@login_required
def single_somatic(request, pk: int):
    """保存済みの単一バリアントを体細胞変異として評価して表示する（Somatic タブ）。

    GET ?tumor=<OncoTree>&tx=<Plus Clinical transcript> は自動判定（キャッシュあり）。
    tumor が無ければこの結果で最後に選んだ腫瘍型を使う（tumor= で明示的に解除）。
    POST は手動の根拠（OP2 など）を付けた再評価で、保存もキャッシュもしない。
    """
    vr = get_object_or_404(VariantResult, pk=pk, job__owner=request.user)
    data = vr.result_json or {}
    variant = data.get("variant", {}) or {}
    germline = data.get("display", {}) or {}
    src = request.POST if request.method == "POST" else request.GET
    if "tumor" in src:
        tumor = normalize_tumor_type(src.get("tumor", ""))
    else:
        tumor = remembered_tumor(request, pk)
    tx = (src.get("tx") or "").strip()
    if tx not in _plus_clinical_ids(germline):
        tx = ""
    manual = _somatic_manual_from_post(request) if request.method == "POST" else []

    context = {
        "result_id": vr.pk, "variant": variant, "germline": germline,
        "tumor": tumor, "active_transcript": tx,
        "plus_clinical": sorted(_plus_clinical_ids(germline)),
        "manual": manual,
        "permalink": _permalink_from_variant(request, variant),
    }
    try:
        somatic, tumor_error = _somatic_for(variant, tumor, tx, manual)
    except (EngineUnavailable, ValueError, KeyError) as exc:
        logger.warning("single_somatic engine error: %s", exc)
        context["engine_error"] = _ENGINE_ERROR_MSG
        return render(request, "analysis/single_somatic.html", context)
    if tumor_error:
        context["tumor_error"] = tumor_error
        tumor = context["tumor"] = ""
    _remember_tumor(request, pk, tumor)

    shown_variant = dict(variant, gene=somatic.get("gene_symbol") or variant.get("gene", ""),
                         transcript=somatic.get("transcript_id") or variant.get("transcript", ""),
                         hgvs_c=somatic.get("hgvs_c") or variant.get("hgvs_c", ""),
                         hgvs_p=somatic.get("hgvs_p") or variant.get("hgvs_p", ""))
    AuditLog.objects.create(user=request.user, action="single_somatic",
                            detail=f"{vr.variant_id} tumor={tumor or '-'}"
                                   f"{' manual' if manual else ''}")
    context.update({
        "variant": shown_variant,
        "somatic": somatic,
        "amp_badge": _amp_badge(somatic.get("amp_tier", "")),
        "amp_rows": (amp_evidence_rows(somatic.get("amp_evidence"))
                     or amp_evidence_rows(somatic.get("amp_candidates"), candidates=True)),
        "amp_rows_kind": "evidence" if somatic.get("amp_evidence") else "candidates",
        # 統合ダウンロード用。生殖細胞系列は保存済み（tx 指定時はその transcript の評価）を使う。
        "somatic_json": json.dumps(somatic, ensure_ascii=False),
    })
    return render(request, "analysis/single_somatic.html", context)


# ---------------------------------------------------------------------------
# バッチ（VCF）解析（FR-BATCH）— Celery で投入順に直列処理
# ---------------------------------------------------------------------------

@login_required
def batch_upload(request):
    """VCF アップロード → ジョブ作成 → Celery 投入 → ステータス画面へ。"""
    form = BatchVariantForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        # 月あたりの Web バッチ実行上限（ユーザーごと、既定 50）
        limit = getattr(request.user, "web_batch_monthly_limit", 50)
        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        used = AuditLog.objects.filter(
            user=request.user, action="batch_submit", created_at__gte=month_start,
        ).count()
        if used >= limit:
            messages.error(request, f"今月のバッチ実行上限（{limit} 回/月）に達しています。")
            return render(request, "analysis/batch_upload.html", {"form": form})
        job = AnalysisJob.objects.create(
            owner=request.user,
            kind=AnalysisJob.Kind.BATCH,
            assembly=form.cleaned_data["assembly"],
            input_file=form.cleaned_data["vcf"],
            input_text=form.cleaned_data["vcf"].name,
            mode=form.cleaned_data.get("mode") or AnalysisJob.Mode.GERMLINE,
            tumor_type=form.cleaned_data.get("tumor_type", ""),
            status=AnalysisJob.Status.PENDING,
        )
        run_batch_classification.delay(job.id)
        AuditLog.objects.create(user=request.user, action="batch_submit",
                                detail=job.input_text)
        notify_admin(
            "バッチ解析(classify)投入",
            f"ユーザー: {request.user.get_username()}\n"
            f"アセンブリ: {job.assembly}\n"
            f"モード: {job.get_mode_display()}"
            f"{'（腫瘍型 ' + job.tumor_type + '）' if job.tumor_type else ''}\n"
            f"バッチ解析が投入されました（内容は非開示）。\n",
        )
        return redirect("analysis:batch_status", pk=job.id)
    return render(request, "analysis/batch_upload.html", {"form": form})


@login_required
def batch_status(request, pk: int):
    """ジョブの進捗。完了で TSV ダウンロード可。pending/running は自動更新。"""
    job = get_object_or_404(AnalysisJob, pk=pk, owner=request.user,
                            kind=AnalysisJob.Kind.BATCH)
    refreshing = job.status in (AnalysisJob.Status.PENDING, AnalysisJob.Status.RUNNING)
    return render(request, "analysis/batch_status.html", {
        "job": job, "refreshing": refreshing,
    })


@login_required
def batch_list(request):
    """バッチジョブ履歴（保持期間切れは随時クリーンアップ）。"""
    cleanup_expired_jobs()
    jobs = AnalysisJob.objects.filter(
        owner=request.user, kind=AnalysisJob.Kind.BATCH
    ).order_by("-created_at")
    return render(request, "analysis/batch_list.html", {"jobs": jobs})


@login_required
def batch_download(request, pk: int):
    """生成 TSV をダウンロード（認証＋所有者チェック。/media は非公開）。"""
    job = get_object_or_404(AnalysisJob, pk=pk, owner=request.user,
                            kind=AnalysisJob.Kind.BATCH)
    if job.status != AnalysisJob.Status.DONE or not job.result_file:
        raise Http404("結果がまだありません。")
    resp = FileResponse(job.result_file.open("rb"), content_type="text/tab-separated-values")
    prefix = "acmg_somatic" if job.mode == AnalysisJob.Mode.SOMATIC else "acmg"
    resp["Content-Disposition"] = f'attachment; filename="{prefix}_batch_{job.id}.tsv"'
    return resp


@login_required
def batch_provenance(request, pk: int):
    """バッチ結果に使用したデータの版情報（JSON）をダウンロード。"""
    import os

    from .cache import provenance_path
    job = get_object_or_404(AnalysisJob, pk=pk, owner=request.user,
                            kind=AnalysisJob.Kind.BATCH)
    if job.status != AnalysisJob.Status.DONE or not job.result_file:
        raise Http404("結果がまだありません。")
    side = provenance_path(job.result_file.path)
    if not os.path.exists(side):
        raise Http404("版情報がありません。")
    resp = FileResponse(open(side, "rb"), content_type="application/json")
    prefix = "acmg_somatic" if job.mode == AnalysisJob.Mode.SOMATIC else "acmg"
    resp["Content-Disposition"] = f'attachment; filename="{prefix}_batch_{job.id}.provenance.json"'
    return resp
