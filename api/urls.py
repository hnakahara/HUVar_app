from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("health/", views.HealthView.as_view(), name="health"),
    path("whoami/", views.WhoAmIView.as_view(), name="whoami"),
    # path("classify/", ...)   # M7: 単一変異解析
    # path("jobs/", ...)       # M7: バッチ解析ジョブ
]
