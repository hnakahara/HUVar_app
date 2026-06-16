"""OpenAPI スキーマ表示用のシリアライザ（drf-spectacular 用）。

API ビューは APIView で素の dict を返すため、ここで定義するシリアライザは
主に Swagger ドキュメントの入出力モデル生成のために使用する。
"""
from rest_framework import serializers


class ClassifyRequestSerializer(serializers.Serializer):
    query = serializers.CharField(
        required=False,
        help_text="変異表記（例: chr17:7674221G>A / TP53:c.742C>T / TP53 R248W）。"
        "chrom/pos/ref/alt を直接指定する場合は不要。",
    )
    chrom = serializers.CharField(required=False)
    pos = serializers.IntegerField(required=False)
    ref = serializers.CharField(required=False)
    alt = serializers.CharField(required=False)
    gene = serializers.CharField(required=False)
    transcript = serializers.CharField(required=False)
    hgvs_c = serializers.CharField(required=False)
    hgvs_p = serializers.CharField(required=False)
    assembly = serializers.ChoiceField(
        choices=["GRCh38", "GRCh37"], default="GRCh38", required=False)
    kind = serializers.ChoiceField(
        choices=["genome", "cdna", "protein"], required=False, allow_null=True,
        help_text="入力種別。未指定なら自動判定。")


class VariantSerializer(serializers.Serializer):
    assembly = serializers.CharField()
    chrom = serializers.CharField()
    pos = serializers.IntegerField()
    ref = serializers.CharField()
    alt = serializers.CharField()
    gene = serializers.CharField(allow_blank=True, required=False)
    transcript = serializers.CharField(allow_blank=True, required=False)
    hgvs_c = serializers.CharField(allow_blank=True, required=False)
    hgvs_p = serializers.CharField(allow_blank=True, required=False)


class ClassifyResponseSerializer(serializers.Serializer):
    variant = VariantSerializer()
    classification_2015 = serializers.CharField()
    rules = serializers.CharField(allow_blank=True, required=False)
    bayesian_score = serializers.FloatField()
    classification_bayesian = serializers.CharField()
    warnings = serializers.ListField(child=serializers.CharField(), required=False)
    criteria = serializers.ListField(
        child=serializers.DictField(), help_text="全クライテリアの判定詳細。")


class WhoAmISerializer(serializers.Serializer):
    username = serializers.CharField()
    role = serializers.CharField(allow_null=True)
    is_administrator = serializers.BooleanField()


class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()


class JobCreateRequestSerializer(serializers.Serializer):
    vcf = serializers.FileField(help_text="VCF ファイル（.vcf / .vcf.gz、上限 50MB）。")
    assembly = serializers.ChoiceField(
        choices=["GRCh38", "GRCh37"], default="GRCh38", required=False)


class JobCreateResponseSerializer(serializers.Serializer):
    job_id = serializers.IntegerField()
    status = serializers.CharField()
    status_url = serializers.CharField()


class JobStatusResponseSerializer(serializers.Serializer):
    job_id = serializers.IntegerField()
    status = serializers.CharField()
    error = serializers.CharField(allow_blank=True)
    expires_at = serializers.DateTimeField()
    result_url = serializers.CharField(required=False)
