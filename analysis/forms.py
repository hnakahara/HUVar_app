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
