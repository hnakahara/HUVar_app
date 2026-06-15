from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


def default_expiry():
    days = getattr(settings, "JOB_ARTIFACT_RETENTION_DAYS", 1)
    return timezone.now() + timedelta(days=days)


class Assembly(models.TextChoices):
    GRCH37 = "GRCh37", "GRCh37 (hg19)"
    GRCH38 = "GRCh38", "GRCh38 (hg38)"


class AnalysisJob(models.Model):
    """単一変異 / バッチ(VCF) の解析ジョブ。成果物は保持期間(既定1日)で自動削除。"""

    class Kind(models.TextChoices):
        SINGLE = "single", "Single variant"
        BATCH = "batch", "Batch (VCF)"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="jobs")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    assembly = models.CharField(max_length=10, choices=Assembly.choices,
                                default=Assembly.GRCH38)
    input_text = models.CharField(max_length=255, blank=True)  # 単一変異の入力文字列
    input_file = models.FileField(upload_to="uploads/", null=True, blank=True)  # VCF
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    celery_task_id = models.CharField(max_length=255, blank=True)
    result_file = models.FileField(upload_to="results/", null=True, blank=True)  # TSV
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_expiry)  # 成果物の有効期限

    def __str__(self) -> str:
        return f"Job#{self.pk} {self.kind} {self.status}"


class ReferenceDataVersion(models.Model):
    """使用中参照データの版署名（ファイルごとの ハッシュ＋サイズ＋mtime）。
    いずれか変化でキャッシュ無効化（FR-CACHE-3）。"""

    name = models.CharField(max_length=255, unique=True)  # 例: hg38.fasta, OSAI_MANE, phyloP
    sha256 = models.CharField(max_length=64, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    mtime = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def signature(self) -> str:
        ts = self.mtime.isoformat() if self.mtime else ""
        return f"{self.sha256}:{self.size_bytes}:{ts}"

    def __str__(self) -> str:
        return f"{self.name} [{self.signature}]"


class VariantResultCache(models.Model):
    """変異の自動判定結果キャッシュ（FR-CACHE）。参照データ更新まで永続保持。"""

    assembly = models.CharField(max_length=10, choices=Assembly.choices)
    chrom = models.CharField(max_length=16)
    pos = models.BigIntegerField()
    ref = models.CharField(max_length=512)
    alt = models.CharField(max_length=512)
    engine_version = models.CharField(max_length=64)
    # 全参照データ版署名の集約ハッシュ（ReferenceDataVersion 群から算出）
    refdata_signature = models.CharField(max_length=64)
    result_json = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (
            "assembly", "chrom", "pos", "ref", "alt",
            "engine_version", "refdata_signature",
        )
        indexes = [
            models.Index(fields=["assembly", "chrom", "pos", "ref", "alt"]),
        ]

    def __str__(self) -> str:
        return f"{self.assembly} {self.chrom}:{self.pos}{self.ref}>{self.alt}"


class VariantResult(models.Model):
    """単一変異の最終結果（ジョブ/ユーザー紐づけ。手動編集の対象）。"""

    job = models.ForeignKey(AnalysisJob, on_delete=models.CASCADE, related_name="results")
    variant_id = models.CharField(max_length=255)
    gene_symbol = models.CharField(max_length=64, blank=True)
    transcript_id = models.CharField(max_length=64, blank=True)
    hgvs_c = models.CharField(max_length=128, blank=True)
    hgvs_p = models.CharField(max_length=128, blank=True)
    classification = models.CharField(max_length=64, blank=True)
    bayesian_score = models.IntegerField(default=0)
    result_json = models.JSONField(default=dict)  # 全クライテリア＋根拠
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.variant_id


class CriterionEdit(models.Model):
    """クライテリアの手動編集（重み=strength・evidence の上書き）。supplement 相当。"""

    variant_result = models.ForeignKey(VariantResult, on_delete=models.CASCADE,
                                       related_name="edits")
    criterion = models.CharField(max_length=8)   # 例: PS3, PM1
    strength = models.CharField(max_length=16)   # 例: Strong, Moderate
    triggered = models.BooleanField(default=True)
    evidence = models.TextField(blank=True)
    editor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                               null=True, related_name="criterion_edits")
    created_at = models.DateTimeField(auto_now_add=True)


class AuditLog(models.Model):
    """監査ログ（ログイン・ユーザー作成・解析実行・結果編集・トークン操作）。"""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                             null=True, blank=True, related_name="audit_logs")
    action = models.CharField(max_length=64)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"
