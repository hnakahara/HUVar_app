"""Celery タスク（バッチ VCF 解析）。worker が concurrency=1 で投入順に直列処理する。

単一変異解析は同期実行のため、ここには含めない（FR-SINGLE-1）。
"""
from celery import shared_task


@shared_task(bind=True)
def run_batch_classification(self, job_id: int) -> str:
    """VCF を HUHVar (acmg-classify classify) で解析し、TSV を生成する。

    M5 で実装:
      1. AnalysisJob を取得し status=running に更新
      2. 変異ごとに VariantResultCache を参照（参照データ未更新ならキャッシュ利用）
      3. 未キャッシュ分のみ HUHVar を実行し、結果をキャッシュへ登録
      4. 全クライテリア列を含む TSV を生成し result_file に保存
      5. status=done に更新
    """
    # TODO(M5): 実装
    return f"queued job {job_id} (not implemented)"
