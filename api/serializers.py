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
    evaluation_transcript = serializers.CharField(
        required=False,
        help_text="MANE Plus Clinical の transcript（例: NM_033360.4）で評価した結果を返す。"
        "未指定なら MANE Select での評価。指定できる transcript は応答の available_transcripts。")


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
    # 入力表記の transcript（MANE Plus Clinical など）が評価 transcript と異なる場合のみ
    input_transcript = serializers.CharField(allow_blank=True, required=False)
    input_hgvs_c = serializers.CharField(allow_blank=True, required=False)
    input_hgvs_p = serializers.CharField(allow_blank=True, required=False)


class ClassifyResponseSerializer(serializers.Serializer):
    variant = VariantSerializer()
    classification_2015 = serializers.CharField()
    rules = serializers.CharField(allow_blank=True, required=False)
    bayesian_score = serializers.FloatField()
    classification_bayesian = serializers.CharField()
    warnings = serializers.ListField(child=serializers.CharField(), required=False)
    criteria = serializers.ListField(
        child=serializers.DictField(), help_text="全クライテリアの判定詳細。各要素の source は由来"
        "（automated / erepo / expert_panel / text_mining / user、Met でなければ空）。")
    clinvar = serializers.DictField(
        required=False,
        help_text="ClinVar 分類サマリ（同一座標の exact-match）。該当が無ければ空 dict。"
        "significance / review_status / star_rating / variation_id / records を含む。")
    vcep = serializers.DictField(
        required=False,
        help_text="ClinGen VCEP による既存分類（eRepo スナップショット）。該当が無ければ空 dict。"
        "assertion / expert_panel / disease / approval_date / met_codes / erepo_url / snapshot を含む。"
        "専門家パネルの分類は通常この自動判定より優先される。")
    automated_only = serializers.DictField(
        required=False, allow_null=True,
        help_text="eRepo・ClinVar エキスパートパネル由来のクライテリアを除いた自動判定のみの結果"
        "（同じ形式）。除外対象が無い場合は null。")
    provenance = serializers.DictField(
        required=False,
        help_text="使用データの版情報（HUVar 版、ClinVar リリース日、VEP、gnomAD、ClinGen など）。")
    evaluated_transcript_role = serializers.CharField(
        required=False, allow_blank=True,
        help_text="評価に使った transcript の MANE 区分（\"MANE Select\"、該当なしは空）。")
    mane_plus_clinical = serializers.ListField(
        child=serializers.DictField(), required=False,
        help_text="同じ遺伝子の MANE Plus Clinical 上の表記（補足。評価は MANE Select）。"
        "transcript_id / hgvs_c / hgvs_p / consequence / consequence_differs を含む。")
    available_transcripts = serializers.ListField(
        child=serializers.DictField(), required=False,
        help_text="評価できる transcript の一覧（MANE Select と MANE Plus Clinical。Plus Clinical "
        "が無い遺伝子では空）。transcript_id / role / classification_2015 / "
        "classification_bayesian / bayesian_score を含む。")
    transcript_evaluations = serializers.DictField(
        required=False,
        help_text="MANE Plus Clinical の transcript で評価した結果（transcript_id → 同じ形式）。"
        "VCEP の位置規則と ClinVar の同一コドン照合は MANE Select の番号で行う。")
    plus_clinical_alternatives = serializers.ListField(
        child=serializers.DictField(), required=False,
        help_text="query の c./p. 番号を MANE Plus Clinical 上で解釈した別候補（ゲノム座標が"
        "異なる場合のみ）。解析は MANE Select の解釈で行われる。")


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
