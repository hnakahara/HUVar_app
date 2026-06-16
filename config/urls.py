"""ルート URLConf。nginx が /acmg/ を除去して渡すため、ここではルート基準で定義する。
URL 生成時は FORCE_SCRIPT_NAME=/acmg により /acmg が前置される。"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),  # set_language（言語切替）
    path("api/", include("api.urls")),
    path("accounts/", include("accounts.urls")),
    path("", include("analysis.urls")),
]
