from django.contrib.auth import views as auth_views
from django.urls import path
from django.views.generic import RedirectView

from . import views
from .forms import ApprovalAwareAuthenticationForm

app_name = "accounts"

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(
        template_name="registration/login.html",
        authentication_form=ApprovalAwareAuthenticationForm), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="accounts:login"), name="logout"),
    path("signup/", views.signup, name="signup"),
    path("signup/done/", views.signup_done, name="signup_done"),
    path("verify/<uidb64>/<token>/", views.verify_email, name="verify_email"),
    # 旧「アカウント発行リクエスト」の URL は登録フォームへ転送（既存リンク・ブックマーク用）
    path("request/", RedirectView.as_view(pattern_name="accounts:signup", permanent=False),
         name="account_request"),
    path("token-request/", views.token_request, name="token_request"),
    path("mfa/setup/", views.mfa_setup, name="mfa_setup"),
    path("mfa/verify/", views.mfa_verify, name="mfa_verify"),
]
