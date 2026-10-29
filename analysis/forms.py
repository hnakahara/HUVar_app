from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Assembly


class SingleVariantForm(forms.Form):
    """単一変異入力。genome / cDNA / protein に対応（c./p. はあってもなくても可）。"""

    query = forms.CharField(
        label=_("バリアント"),
        max_length=255,
        widget=forms.TextInput(attrs={
            "placeholder": "chr17:7674221G>A  /  TP53 742C>T  /  TP53 R248W",
            "size": 48,
        }),
    )
    assembly = forms.ChoiceField(
        label=_("アセンブリ"),
        choices=Assembly.choices,
        initial=Assembly.GRCH38,
    )
    kind = forms.ChoiceField(
        label=_("入力種別"),
        required=False,
        choices=[
            ("", _("自動判定")),
            ("genome", "genome coordinate"),
            ("cdna", "cDNA"),
            ("protein", "protein"),
        ],
    )


class BatchVariantForm(forms.Form):
    """VCF アップロードによる複数変異の一括解析（FR-BATCH）。"""

    vcf = forms.FileField(label=_("VCF ファイル（.vcf / .vcf.gz）"))
    assembly = forms.ChoiceField(
        label=_("アセンブリ"),
        choices=Assembly.choices,
        initial=Assembly.GRCH38,
    )

    mode = forms.ChoiceField(
        label=_("評価モード"),
        choices=[
            ("germline", _("生殖細胞系列のみ（ACMG/AMP 2015）")),
            ("somatic", _("生殖細胞系列＋体細胞（Oncogenicity / AMP tier）の統合")),
        ],
        initial="germline",
        widget=forms.RadioSelect,
    )
    tumor_type = forms.CharField(
        label=_("腫瘍型（OncoTree コード、統合のみ・任意）"),
        max_length=32,
        required=False,
        widget=forms.TextInput(attrs={"list": "oncotree-list", "size": 14,
                                      "placeholder": "SKCM", "autocomplete": "off"}),
    )

    def clean_tumor_type(self):
        from .engine import normalize_tumor_type, oncotree_choices
        code = normalize_tumor_type(self.cleaned_data.get("tumor_type", ""))
        if not code:
            return ""
        known = {o["code"].upper() for o in oncotree_choices()}
        # 選択肢を読めない（エンジン未導入）ときはジョブ側の検証に任せる
        if known and code not in known:
            raise forms.ValidationError(_("不明な OncoTree コードです。"))
        return code

    def clean(self):
        data = super().clean()
        if data.get("mode") != "somatic":
            data["tumor_type"] = ""
        return data

    def clean_vcf(self):
        f = self.cleaned_data["vcf"]
        name = (f.name or "").lower()
        if not (name.endswith(".vcf") or name.endswith(".vcf.gz")):
            raise forms.ValidationError(_("VCF ファイル（.vcf または .vcf.gz）を指定してください。"))
        # 上限 50MB（nginx client_max_body_size と整合）
        if f.size and f.size > 50 * 1024 * 1024:
            raise forms.ValidationError(_("ファイルサイズが大きすぎます（上限 50MB）。"))
        return f
