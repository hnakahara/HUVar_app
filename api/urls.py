from django.urls import path

from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from . import views

app_name = "api"

urlpatterns = [
    path("health/", views.HealthView.as_view(), name="health"),
    path("whoami/", views.WhoAmIView.as_view(), name="whoami"),
    # 単一バリアント解析（同期）
    path("classify/", views.ClassifyView.as_view(), name="classify"),
    # バッチ（VCF）解析ジョブ
    path("jobs/", views.JobCreateView.as_view(), name="job_create"),
    path("jobs/<int:pk>/", views.JobStatusView.as_view(), name="job_status"),
    path("jobs/<int:pk>/result.tsv", views.JobResultView.as_view(), name="job_result"),
    # OpenAPI スキーマ + Swagger UI / Redoc（デモ用ドキュメント）
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="api:schema"), name="swagger-ui"),
    path("redoc/", SpectacularRedocView.as_view(url_name="api:schema"), name="redoc"),
]
