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


def _descendants(pid: int) -> list[int]:
    """``pid`` の子孫プロセス（Linux の /proc から。親→子の順）。"""
    children: dict[int, list[int]] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", encoding="utf-8", errors="replace") as fh:
                stat = fh.read()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(int(entry))
    out, stack = [], list(children.get(pid, []))
    while stack:
        p = stack.pop(0)
        out.append(p)
        stack.extend(children.get(p, []))
    return out


def _kill_descendants(sig) -> int:
    """このプロセスの子孫（VEP・OpenSpliceAI 等）をすべて止める。止めた数を返す。"""
    n = 0
    for p in reversed(_descendants(os.getpid())):
        try:
            os.kill(p, sig)
            n += 1
        except (ProcessLookupError, PermissionError):
            pass
    return n


def _limit_message(seconds: int) -> str:
    hours = seconds / 3600
    shown = f"{hours:g}"
    return (f"制限時間（{shown} 時間）を超えたため解析を中止しました。"
            "VCF を分割するか、管理者に制限時間（BATCH_TIME_LIMIT_HOURS）の変更を依頼してください。")


@shared_task(bind=True)
def run_batch_classification(self, job_id: int) -> str:
    """VCF を HUVar(run_pipeline) で解析し、全クライテリア列の TSV を生成する。

    * 待ち状態（PENDING）のジョブだけを実行する。Redis からの配り直しや重複投入で同じ
      ジョブが再び届いても、解析中・終了済みなら何もしない。
    * BATCH_TIME_LIMIT 秒を過ぎたら、監視スレッドが解析の子プロセス（VEP・OpenSpliceAI
      等）を止めて解析を打ち切り、ジョブを「失敗（制限時間超過）」として記録する。
    """
    import signal
    import threading
    import time

    from .cache import cached_classify_batch
    from .engine import EngineUnavailable
    from .models import AnalysisJob

    try:
        job = AnalysisJob.objects.get(pk=job_id)
    except AnalysisJob.DoesNotExist:
        return "job not found"
    if job.status != AnalysisJob.Status.PENDING:
        return f"skipped: job {job_id} is already {job.status}"

    job.status = AnalysisJob.Status.RUNNING
    job.celery_task_id = self.request.id or ""
    job.started_at = timezone.now()
    job.save(update_fields=["status", "celery_task_id", "started_at"])

    limit = int(getattr(settings, "BATCH_TIME_LIMIT", 12 * 3600))
    timed_out = threading.Event()

    def _on_timeout():
        # 子プロセスが止まると解析エンジンはエラーで戻り、下の except で「失敗」になる
        timed_out.set()
        _kill_descendants(signal.SIGTERM)
        time.sleep(10)                      # 終了しなかったものだけ強制終了
        _kill_descendants(signal.SIGKILL)

    watchdog = threading.Timer(limit, _on_timeout)
    watchdog.daemon = True
    watchdog.start()

    def _fail(message: str) -> None:
        job.status = AnalysisJob.Status.FAILED
        job.error = _limit_message(limit) if timed_out.is_set() else message
        job.expires_at = _expiry_from_now()
        job.save(update_fields=["status", "error", "expires_at"])

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
        if timed_out.is_set():
            raise RuntimeError("time limit")

        job.result_file.name = out_rel
        job.status = AnalysisJob.Status.DONE
        job.error = ""
        job.expires_at = _expiry_from_now()
        job.save(update_fields=["result_file", "status", "error", "expires_at"])
    except (EngineUnavailable, ValueError) as exc:
        _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        _fail(f"想定外のエラー: {exc}")
    finally:
        watchdog.cancel()
    return job.status


def fail_orphaned_jobs(reason: str, started_before=None) -> int:
    """「解析中」のまま取り残されたジョブを「失敗」にする（件数を返す）。

    ``started_before`` を指定するとそれより前に始まったものだけ（制限時間を大きく
    超えたもの）、省略するとすべて（worker の起動時。worker は 1 つで、起動した時点で
    実行中の解析は無い）。"""
    from .models import AnalysisJob

    qs = AnalysisJob.objects.filter(status=AnalysisJob.Status.RUNNING)
    if started_before is not None:
        qs = qs.filter(Q(started_at__lt=started_before)
                       | Q(started_at__isnull=True, created_at__lt=started_before))
    return qs.update(status=AnalysisJob.Status.FAILED, error=reason,
                     expires_at=_expiry_from_now())


try:  # worker の起動時に、前回の worker が残した「解析中」を片付ける
    from celery.signals import worker_ready

    @worker_ready.connect
    def _on_worker_ready(**kwargs):
        n = fail_orphaned_jobs("worker の再起動により解析が中断されました。もう一度投入してください。")
        if n:
            import logging
            logging.getLogger(__name__).warning("marked %d orphaned running job(s) failed", n)
except Exception:  # noqa: BLE001
    pass


def cleanup_expired_jobs() -> int:
    """保持期間（解析終了から既定 1 時間）を過ぎたジョブと成果物ファイルを削除する。

    待ち・解析中のジョブは削除しない（以前は投入から 1 時間で削除され、順番待ちや
    長時間の解析の途中で入力 VCF が消えることがあった）。ただし worker の異常終了などで
    その状態のまま JOB_STALE_DAYS 日を過ぎたものは片付ける。
    ビューからの随時呼び出しを想定（将来 Celery beat に移行可）。"""
    from .models import AnalysisJob

    now = timezone.now()
    # 制限時間を大きく超えて「解析中」のままのもの（強制終了された等）を先に失敗にする
    hard = int(getattr(settings, "CELERY_TASK_TIME_LIMIT", 0) or 0)
    if hard:
        fail_orphaned_jobs("解析が制限時間内に終了しなかったため中止されました。",
                           started_before=now - timedelta(seconds=hard + 600))
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
