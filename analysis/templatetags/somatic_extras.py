"""体細胞評価画面用のテンプレートタグ（OncoTree の選択肢）。"""
from django import template

register = template.Library()


@register.inclusion_tag("analysis/_oncotree_datalist.html")
def oncotree_datalist(list_id="oncotree-list"):
    """腫瘍型（OncoTree コード）の <datalist>。エンジン未導入なら空。"""
    from analysis.engine import oncotree_choices
    return {"list_id": list_id, "choices": oncotree_choices()}


@register.inclusion_tag("analysis/_result_tabs.html", takes_context=True)
def result_tabs(context, result_id, active="germline", transcript=""):
    """結果画面の Germline / Somatic タブ。Somatic へはこの結果で最後に選んだ腫瘍型と、
    表示中の MANE Plus Clinical transcript を引き継ぐ。"""
    from analysis.views import remembered_tumor
    request = context.get("request")
    tumor = remembered_tumor(request, result_id) if request is not None else ""
    return {"result_id": result_id, "active": active, "transcript": transcript or "",
            "tumor": tumor}
