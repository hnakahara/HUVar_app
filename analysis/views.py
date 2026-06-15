from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def index(request):
    """トップ画面（解析入力フォームへの入口）。M3-M5 で入力・結果画面を実装。"""
    return render(request, "analysis/index.html")
