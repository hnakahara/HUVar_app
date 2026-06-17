"""REST API（トークン認証）。VAS など外部クライアント向け。

認証は DRF TokenAuthentication（settings 既定）。トークンは administrator が
Django admin で発行/失効する（公開のトークン取得エンドポイントは設けない）。
レート制限は nginx(limit_req) ＋ DRF throttling(既定 user 60/min)。
"""
import logging

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone

from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema

from accounts.notifications import notify_admin
from analysis.cache import cached_classify_single
from analysis.engine import EngineUnavailable
from analysis.models import AnalysisJob, AuditLog
from analysis.tasks import run_batch_classification
from transvar_client.client import TransvarError
from transvar_client.client import convert as transvar_convert

from .serializers import (
    ClassifyRequestSerializer,
    ClassifyResponseSerializer,
    HealthSerializer,
    JobCreateRequestSerializer,
    JobCreateResponseSerializer,
    JobStatusResponseSerializer,
    WhoAmISerializer,
)

logger = logging.getLogger(__name__)


class HealthView(APIView):
    """ヘルスチェック（認証不要）。"""
    permission_classes = [AllowAny]

    @extend_schema(summary="ヘルスチェック", description="認証不要。サービス稼働確認に使用。",
                   auth=[], responses=HealthSerializer, tags=["meta"])
    def get(self, request):
        return Response({"status": "ok"})


class WhoAmIView(APIView):
    """トークン認証の疎通確認用。"""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="認証確認 (whoami)",
                   description="トークン認証の疎通確認。認証ユーザー情報を返す。",
                   responses=WhoAmISerializer, tags=["meta"])
    def get(self, request):
        u = request.user
        return Response({
            "username": u.get_username(),
            "role": getattr(u, "role", None),
            "is_administrator": getattr(u, "is_administrator", False),
        })


class ClassifyView(APIView):
    """単一変異の ACMG 分類（同期）。

    入力（いずれか）:
      - {"query": "chr17:7674221G>A" | "TP53:c.742C>T" | "TP53:p.R248W",
         "assembly": "GRCh38", "kind": null}   ← TransVar で MANE 限定変換
      - {"chrom","pos","ref","alt","assembly", ...}  ← 変換済み座標を直接指定

    出力: {"variant": {...}, "classification_2015", "rules", "bayesian_score",
           "classification_bayesian", "warnings", "criteria":[...全28項目...]}
    複数候補時: {"status":"multiple_candidates","candidates":[...]}（呼び出し側で選択）
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="単一バリアント解析 (classify)",
        description="変異1件を ACMG 2015 + ClinGen SVI 基準で分類する（同期）。"
        "`query` で表記を渡すか、`chrom/pos/ref/alt` で座標を直接指定する。",
        request=ClassifyRequestSerializer,
        responses={200: ClassifyResponseSerializer},
        tags=["classify"],
        examples=[
            OpenApiExample(
                "query 指定（cDNA）",
                value={"query": "TP53:c.742C>T", "assembly": "GRCh38"},
                request_only=True),
            OpenApiExample(
                "query 指定（空白区切り protein）",
                value={"query": "TP53 R248W", "assembly": "GRCh38"},
                request_only=True),
            OpenApiExample(
                "座標直接指定",
                value={"chrom": "chr17", "pos": 7674221, "ref": "G", "alt": "A",
                       "assembly": "GRCh38"},
                request_only=True),
        ],
    )
    def post(self, request):
        data = request.data
        assembly = data.get("assembly", "GRCh38")
        chrom, pos = data.get("chrom"), data.get("pos")
        ref, alt = data.get("ref"), data.get("alt")

        if chrom and pos and ref and alt:
            variant = {
                "assembly": assembly, "chrom": chrom, "pos": pos, "ref": ref, "alt": alt,
                "gene": data.get("gene", ""), "transcript": data.get("transcript", ""),
                "hgvs_c": data.get("hgvs_c", ""), "hgvs_p": data.get("hgvs_p", ""),
            }
        else:
            query = data.get("query")
            if not query:
                return Response(
                    {"error": "query または chrom/pos/ref/alt が必要です"}, status=400)
            try:
                conv = transvar_convert(query, assembly=assembly, kind=data.get("kind") or None)
            except TransvarError as exc:
                return Response({"error": f"TransVar サービスエラー: {exc}"}, status=502)
            if not conv.get("ok"):
                return Response({"error": conv.get("error") or "変換に失敗しました"}, status=400)
            candidates = conv.get("candidates", [])
            if len(candidates) > 1:
                return Response({"status": "multiple_candidates", "candidates": candidates})
            c = candidates[0]
            variant = {
                "assembly": assembly, "chrom": c.get("chrom"), "pos": c.get("pos"),
                "ref": c.get("ref"), "alt": c.get("alt"), "gene": c.get("gene"),
                "transcript": c.get("transcript"), "hgvs_c": c.get("hgvs_c"),
                "hgvs_p": c.get("hgvs_p"),
            }

        try:
            display, _hit = cached_classify_single(
                variant["assembly"], variant["chrom"], int(variant["pos"]),
                variant["ref"], variant["alt"],
            )
        except (EngineUnavailable, ValueError) as exc:
            logger.warning("API classify engine error: %s", exc)
            return Response(
                {"error": "解析エンジンが一時的に利用できません。時間をおいて再度お試しください。"},
                status=503)

        AuditLog.objects.create(
            user=request.user, action="api_classify",
            detail=f'{variant["chrom"]}:{variant["pos"]}:{variant["ref"]}:{variant["alt"]}')
        notify_admin(
            "API 単一バリアント解析(classify)実行",
            f"ユーザー: {request.user.get_username()}\n"
            f"アセンブリ: {variant.get('assembly') or '-'}\n"
            f"遺伝子: {variant.get('gene') or '-'}\n"
            f"バリアント: {variant.get('hgvs_c') or '-'} {variant.get('hgvs_p') or ''}\n"
            f'座標: {variant["chrom"]}:{variant["pos"]}:{variant["ref"]}:{variant["alt"]}\n'
            f"分類(ACMG 2015): {display.get('classification_2015', '-')}\n"
            f"分類(Bayesian): {display.get('classification_bayesian', '-')}"
            f"（score {display.get('bayesian_score', '-')}）\n",
        )
        return Response({"variant": variant, **display})


class JobCreateView(APIView):
    """バッチ（VCF）解析ジョブの投入。multipart で vcf を送る。"""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        summary="バッチ解析ジョブの投入 (jobs)",
        description="VCF をアップロードして解析ジョブを投入する（multipart/form-data）。"
        "月あたりの実行上限あり（既定 5 回/月、ユーザーごとに変更可）。",
        request=JobCreateRequestSerializer,
        responses={201: JobCreateResponseSerializer},
        tags=["batch"],
    )
    def post(self, request):
        # 月あたりの API バッチ実行上限（ユーザーごと、既定 5）
        limit = getattr(request.user, "api_batch_monthly_limit", 5)
        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        used = AuditLog.objects.filter(
            user=request.user, action="api_batch_submit", created_at__gte=month_start,
        ).count()
        if used >= limit:
            return Response({
                "error": f"今月の API バッチ実行上限（{limit} 回/月）に達しています。",
                "limit": limit, "used": used,
            }, status=429)

        f = request.FILES.get("vcf")
        if not f:
            return Response({"error": "vcf ファイル（multipart）が必要です"}, status=400)
        name = (f.name or "").lower()
        if not (name.endswith(".vcf") or name.endswith(".vcf.gz")):
            return Response({"error": "VCF（.vcf / .vcf.gz）が必要です"}, status=400)
        if f.size and f.size > 50 * 1024 * 1024:
            return Response({"error": "ファイルサイズが大きすぎます（上限 50MB）"}, status=400)
        assembly = request.data.get("assembly", "GRCh38")
        job = AnalysisJob.objects.create(
            owner=request.user, kind=AnalysisJob.Kind.BATCH, assembly=assembly,
            input_file=f, input_text=f.name, status=AnalysisJob.Status.PENDING,
        )
        run_batch_classification.delay(job.id)
        AuditLog.objects.create(user=request.user, action="api_batch_submit",
                                detail=f.name)
        notify_admin(
            "API バッチ解析(jobs)投入",
            f"ユーザー: {request.user.get_username()}\n"
            f"アセンブリ: {assembly}\n"
            f"API バッチ解析が投入されました（内容は非開示）。\n",
        )
        return Response({
            "job_id": job.id,
            "status": job.status,
            "status_url": request.build_absolute_uri(reverse("api:job_status", args=[job.id])),
        }, status=201)


class JobStatusView(APIView):
    """バッチジョブの状態取得。"""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="バッチジョブの状態取得",
                   description="ジョブの進捗を取得。完了時は result_url を含む。",
                   responses=JobStatusResponseSerializer, tags=["batch"])
    def get(self, request, pk: int):
        job = get_object_or_404(AnalysisJob, pk=pk, owner=request.user,
                                kind=AnalysisJob.Kind.BATCH)
        body = {"job_id": job.id, "status": job.status, "error": job.error,
                "expires_at": job.expires_at.isoformat()}
        if job.status == AnalysisJob.Status.DONE and job.result_file:
            body["result_url"] = request.build_absolute_uri(
                reverse("api:job_result", args=[job.id]))
        return Response(body)


class JobResultView(APIView):
    """バッチ結果 TSV の取得（認証＋所有者チェック）。"""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="バッチ結果 TSV の取得",
        description="完了済みジョブの結果 TSV をダウンロードする。",
        responses={200: OpenApiResponse(description="TSV ファイル（添付ダウンロード）")},
        tags=["batch"],
    )
    def get(self, request, pk: int):
        job = get_object_or_404(AnalysisJob, pk=pk, owner=request.user,
                                kind=AnalysisJob.Kind.BATCH)
        if job.status != AnalysisJob.Status.DONE or not job.result_file:
            return Response({"error": "結果がまだありません", "status": job.status}, status=409)
        resp = FileResponse(job.result_file.open("rb"),
                            content_type="text/tab-separated-values")
        resp["Content-Disposition"] = f'attachment; filename="acmg_batch_{job.id}.tsv"'
        return resp
