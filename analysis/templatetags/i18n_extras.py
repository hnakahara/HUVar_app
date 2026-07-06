"""テンプレート用 i18n 補助フィルタ。

CSpec の disease_label など、参照データ(TSV)由来で変数として渡る文字列を
描画時の現在言語で翻訳する。解析時ではなく描画時に翻訳することで、保存済み
結果を後から言語切替しても正しく表示できる。翻訳が無い値は原文をそのまま返す。
"""
from django import template
from django.utils.translation import gettext

register = template.Library()


@register.filter(name="tr")
def tr(value):
    """gettext カタログに登録済みなら翻訳、無ければ原文を返す。"""
    if not value:
        return value
    return gettext(str(value))
