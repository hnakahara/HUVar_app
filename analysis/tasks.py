"""Celery タスク（バッチ VCF 解析）。worker が concurrency=1 で投入順に直列処理する。

単一変異解析は同期実行のため、ここには含めない（FR-SINGLE-1）。
"""
import os
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone


def _expiry_from_now():
    """解析終了時点からの有効期限（成果物の保持期間）。"""
    return timezone.now() + timedelta(hours=getattr(settings, "JOB_ARTIFACT_RETENTION_HOURS", 1))


@shared_task(bind=True)
def run_batch_classification(self, job_id: int) -> str:
    """VCF を HUVar(run_pipeline) で解析し、全クライテリア列の TSV を生成する。"""
    from .cache import cached_classify_batch
    from .engine import EngineUnavailable
    from .models import AnalysisJob

    try:
        job = AnalysisJob.objects.get(pk=job_id)
    except AnalysisJob.DoesNotExist:
        return "job not found"

    job.status = AnalysisJob.Status.RUNNING
    job.celery_task_id = self.request.id or ""
    job.save(update_fields=["status", "celery_task_id"])

    try:
        in_path = job.input_file.path
        out_rel = f"results/job_{job.id}.tsv"
        out_abs = os.path.join(settings.MEDIA_ROOT, out_rel)
        os.makedirs(os.path.dirname(out_abs), exist_ok=True)

        # 体細胞モードは生殖細胞系列（ACMG）と体細胞（oncogenicity ＋ AMP tier）の統合 TSV。
        cached_classify_batch(in_path, out_abs, job.assembly,
                              somatic=job.mode == AnalysisJob.Mode.SOMATIC,
                              tumor_type=job.tumor_type,
                              insilico_tool=job.insilico_tool)

        job.result_file.name = out_rel
        job.status = AnalysisJob.Status.DONE
        job.error = ""
        job.expires_at = _expiry_from_now()
        job.save(update_fields=["result_file", "status", "error", "expires_at"])
    except (EngineUnavailable, ValueError) as exc:
        job.status = AnalysisJob.Status.FAILED
        job.error = str(exc)
        job.expires_at = _expiry_from_now()
        job.save(update_fields=["status", "error", "expires_at"])
    except Exception as exc:  # noqa: BLE001
        job.status = AnalysisJob.Status.FAILED
        job.error = f"想定外のエラー: {exc}"
        job.expires_at = _expiry_from_now()
        job.save(update_fields=["status", "error", "expires_at"])
    return job.status


def cleanup_expired_jobs() -> int:
    """保持期間（解析終了から既定 1 時間）を過ぎたジョブと成果物ファイルを削除する。

    待ち・解析中のジョブは削除しない（以前は投入から 1 時間で削除され、順番待ちや
    長時間の解析の途中で入力 VCF が消えることがあった）。ただし worker の異常終了などで
    その状態のまま JOB_STALE_DAYS 日を過ぎたものは片付ける。
    ビューからの随時呼び出しを想定（将来 Celery beat に移行可）。"""
    from .models import AnalysisJob

    now = timezone.now()
    finished = [AnalysisJob.Status.DONE, AnalysisJob.Status.FAILED]
    stale = now - timedelta(days=getattr(settings, "JOB_STALE_DAYS", 7))
    expired = AnalysisJob.objects.filter(
        Q(status__in=finished, expires_at__lt=now)
        | (~Q(status__in=finished) & Q(created_at__lt=stale)))
    count = 0
    for job in expired:
        # 結果 TSV に添付した使用データ版情報(JSON)も削除する
        try:
            if job.result_file:
                from .cache import provenance_path
                side = provenance_path(job.result_file.path)
                if os.path.exists(side):
                    os.remove(side)
        except Exception:  # noqa: BLE001
            pass
        for f in (job.input_file, job.result_file):
            try:
                if f:
                    f.delete(save=False)
            except Exception:  # noqa: BLE001
                pass
        job.delete()
        count += 1
    return count
