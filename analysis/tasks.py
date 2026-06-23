"""Celery タスク（バッチ VCF 解析）。worker が concurrency=1 で投入順に直列処理する。

単一変異解析は同期実行のため、ここには含めない（FR-SINGLE-1）。
"""
import os

from celery import shared_task
from django.conf import settings
from django.utils import timezone


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

        cached_classify_batch(in_path, out_abs, job.assembly)

        job.result_file.name = out_rel
        job.status = AnalysisJob.Status.DONE
        job.error = ""
        job.save(update_fields=["result_file", "status", "error"])
    except EngineUnavailable as exc:
        job.status = AnalysisJob.Status.FAILED
        job.error = str(exc)
        job.save(update_fields=["status", "error"])
    except Exception as exc:  # noqa: BLE001
        job.status = AnalysisJob.Status.FAILED
        job.error = f"想定外のエラー: {exc}"
        job.save(update_fields=["status", "error"])
    return job.status


def cleanup_expired_jobs() -> int:
    """保持期間（既定1時間）を過ぎたジョブと成果物ファイルを削除する。

    ビューからの随時呼び出しを想定（将来 Celery beat に移行可）。"""
    from .models import AnalysisJob

    expired = AnalysisJob.objects.filter(expires_at__lt=timezone.now())
    count = 0
    for job in expired:
        for f in (job.input_file, job.result_file):
            try:
                if f:
                    f.delete(save=False)
            except Exception:  # noqa: BLE001
                pass
        job.delete()
        count += 1
    return count
