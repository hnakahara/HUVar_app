from django.urls import path

from . import views

app_name = "analysis"

urlpatterns = [
    path("", views.index, name="index"),
    path("single/", views.single_input, name="single_input"),
    path("single/analyze/", views.single_analyze, name="single_analyze"),
]
