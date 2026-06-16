from django import forms

from .models import Assembly


class SingleVariantForm(forms.Form):
    """単一変異入力。genome / cDNA / protein に対応（c./p. はあってもなくても可）。"""

    query = forms.CharField(
        label="変異",
        max_length=255,
        widget=forms.TextInput(attrs={
            "placeholder": "chr17:7674221G>A  /  TP53:c.742C>T  /  TP53:p.R248W",
            "size": 48,
        }),
    )
    assembly = forms.ChoiceField(
        label="アセンブリ",
        choices=Assembly.choices,
        initial=Assembly.GRCH38,
    )
    kind = forms.ChoiceField(
        label="入力種別",
        required=False,
        choices=[
            ("", "自動判定"),
            ("genome", "genome coordinate"),
            ("cdna", "cDNA"),
            ("protein", "protein"),
        ],
    )


class BatchVariantForm(forms.Form):
    """VCF アップロードによる複数変異の一括解析（FR-BATCH）。"""

    vcf = forms.FileField(label="VCF ファイル（.vcf / .vcf.gz）")
    assembly = forms.ChoiceField(
        label="アセンブリ",
        choices=Assembly.choices,
        initial=Assembly.GRCH38,
    )

    def clean_vcf(self):
        f = self.cleaned_data["vcf"]
        name = (f.name or "").lower()
        if not (name.endswith(".vcf") or name.endswith(".vcf.gz")):
            raise forms.ValidationError("VCF ファイル（.vcf または .vcf.gz）を指定してください。")
        # 上限 50MB（nginx client_max_body_size と整合）
        if f.size and f.size > 50 * 1024 * 1024:
            raise forms.ValidationError("ファイルサイズが大きすぎます（上限 50MB）。")
        return f
