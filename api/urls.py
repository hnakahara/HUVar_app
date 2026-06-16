from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("health/", views.HealthView.as_view(), name="health"),
    path("whoami/", views.WhoAmIView.as_view(), name="whoami"),
    # 単一変異解析（同期）
    path("classify/", views.ClassifyView.as_view(), name="classify"),
    # バッチ（VCF）解析ジョブ
    path("jobs/", views.JobCreateView.as_view(), name="job_create"),
    path("jobs/<int:pk>/", views.JobStatusView.as_view(), name="job_status"),
    path("jobs/<int:pk>/result.tsv", views.JobResultView.as_view(), name="job_result"),
]
