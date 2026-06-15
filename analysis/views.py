from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from transvar_client.client import TransvarError
from transvar_client.client import convert as transvar_convert

from .forms import SingleVariantForm


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


@login_required
def single_analyze(request):
    """選択された変異（解決済み genome 座標）で解析を実行する。

    M4 で HUHVar(run_single) 連携・全クライテリア表示・手動編集を実装する。
    現状は解決済み変異の確認画面までを表示する。
    """
    if request.method != "POST":
        return render(request, "analysis/single_input.html",
                      {"form": SingleVariantForm()})
    variant = {
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
    return render(request, "analysis/single_analyze.html", {"variant": variant})
