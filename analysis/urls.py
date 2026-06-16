from django.urls import path

from . import views

app_name = "analysis"

urlpatterns = [
    path("", views.index, name="index"),
    path("single/", views.single_input, name="single_input"),
    path("single/analyze/", views.single_analyze, name="single_analyze"),
    path("single/result/<int:pk>/", views.single_result, name="single_result"),
    path("single/result/<int:pk>/edit/", views.single_edit, name="single_edit"),
    path("single/result/<int:pk>/export.json", views.single_export, name="single_export"),
]
