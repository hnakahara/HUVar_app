from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """ヘルスチェック（認証不要）。"""
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"status": "ok"})


class WhoAmIView(APIView):
    """トークン認証の疎通確認用。"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        u = request.user
        return Response({
            "username": u.get_username(),
            "role": getattr(u, "role", None),
            "is_administrator": getattr(u, "is_administrator", False),
        })


# 単一変異 / バッチ解析エンドポイントは M7 で実装する。
