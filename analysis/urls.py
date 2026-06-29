from django.urls import path

from . import views

app_name = "analysis"

urlpatterns = [
    path("", views.index, name="index"),
    path("help/", views.help_page, name="help"),
    # 単一変異
    path("single/", views.single_input, name="single_input"),
    path("single/analyze/", views.single_analyze, name="single_analyze"),
    path("single/result/<int:pk>/", views.single_result, name="single_result"),
    path("single/result/<int:pk>/cspec/<str:cspec_id>/", views.single_cspec, name="single_cspec"),
    path("single/result/<int:pk>/edit/", views.single_edit, name="single_edit"),
    path("single/result/<int:pk>/export.json", views.single_export, name="single_export"),
    # バッチ（VCF）
    path("batch/", views.batch_upload, name="batch_upload"),
    path("batch/jobs/", views.batch_list, name="batch_list"),
    path("batch/jobs/<int:pk>/", views.batch_status, name="batch_status"),
    path("batch/jobs/<int:pk>/download.tsv", views.batch_download, name="batch_download"),
]
