import json

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from accounts.notifications import notify_admin
from transvar_client.client import TransvarError
from transvar_client.client import convert as transvar_convert

from .cache import cached_classify_single
from .engine import STRENGTH_CHOICES, EngineUnavailable, classify_single
from .forms import BatchVariantForm, SingleVariantForm
from .models import AnalysisJob, AuditLog, CriterionEdit, VariantResult
from .tasks import cleanup_expired_jobs, run_batch_classification


@login_required
def index(request):
    """トップ画面（解析入力フォームへの入口）。"""
    return render(request, "analysis/index.html")


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
        return render(request, "analysis/single_result.html", {
            "variant": variant, "engine_error": str(exc),
            "strength_choices": STRENGTH_CHOICES,
        })

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
    notify_admin(
        "単一変異解析(explain)実行",
        f"ユーザー: {request.user.get_username()}\n"
        f"遺伝子: {vr.gene_symbol or '-'}\n"
        f"変異: {vr.hgvs_c or '-'} {vr.hgvs_p or ''}\n"
        f"座標: {vr.variant_id}\n"
        f"分類: {vr.classification}\n",
    )
    return redirect("analysis:single_result", pk=vr.pk)


@login_required
def single_result(request, pk: int):
    vr = get_object_or_404(VariantResult, pk=pk, job__owner=request.user)
    data = vr.result_json or {}
    return render(request, "analysis/single_result.html", {
        "result_id": vr.pk,
        "variant": data.get("variant", {}),
        "display": data.get("display", {}),
        "edits": data.get("edits", []),
        "strength_choices": STRENGTH_CHOICES,
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
        return render(request, "analysis/single_result.html", {
            "result_id": vr.pk, "variant": variant,
            "display": data.get("display", {}), "edits": entries,
            "engine_error": str(exc), "strength_choices": STRENGTH_CHOICES,
        })

    vr.classification = display["classification_bayesian"]
    vr.bayesian_score = display["bayesian_score"]
    vr.result_json = {"variant": variant, "display": display, "edits": entries}
    vr.save(update_fields=["classification", "bayesian_score", "result_json"])

    # 監査用に編集履歴を保存
    for e in entries:
        CriterionEdit.objects.create(
            variant_result=vr, criterion=e["criterion"], strength=e["strength"],
            triggered=(e["strength"] != "NotMet"), evidence=e.get("evidence", ""),
            editor=request.user,
        )
    AuditLog.objects.create(user=request.user, action="criterion_edit",
                            detail=f"result={vr.pk} edits={len(entries)}")
    return redirect("analysis:single_result", pk=vr.pk)


@login_required
def single_export(request, pk: int):
    """結果（全クライテリア＋根拠＋分類）を JSON でダウンロード（FR-SINGLE-6）。"""
    vr = get_object_or_404(VariantResult, pk=pk, job__owner=request.user)
    body = json.dumps(vr.result_json or {}, ensure_ascii=False, indent=2)
    resp = HttpResponse(body, content_type="application/json; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="acmg_result_{vr.pk}.json"'
    return resp


# ---------------------------------------------------------------------------
# バッチ（VCF）解析（FR-BATCH）— Celery で投入順に直列処理
# ---------------------------------------------------------------------------

@login_required
def batch_upload(request):
    """VCF アップロード → ジョブ作成 → Celery 投入 → ステータス画面へ。"""
    form = BatchVariantForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        job = AnalysisJob.objects.create(
            owner=request.user,
            kind=AnalysisJob.Kind.BATCH,
            assembly=form.cleaned_data["assembly"],
            input_file=form.cleaned_data["vcf"],
            input_text=form.cleaned_data["vcf"].name,
            status=AnalysisJob.Status.PENDING,
        )
        run_batch_classification.delay(job.id)
        AuditLog.objects.create(user=request.user, action="batch_submit",
                                detail=job.input_text)
        notify_admin(
            "バッチ解析(classify)投入",
            f"ユーザー: {request.user.get_username()}\n"
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
    resp["Content-Disposition"] = f'attachment; filename="acmg_batch_{job.id}.tsv"'
    return resp
