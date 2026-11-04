"""接続元 IP の判定・管理画面の接続元制限・監査ログ・セキュリティ通知・API トークン。"""
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from django.core import mail
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework.authtoken.models import Token

from analysis.models import AuditLog

from .models import User
from .security import client_ip

PASSWORD = "correct-horse-battery-42"
PROXY = "172.18.0.5"          # vas nginx コンテナ（REMOTE_ADDR）


def _subjects():
    return [m.subject for m in mail.outbox]


class ClientIpTests(TestCase):
    rf = RequestFactory()

    def _req(self, xff=None, remote=PROXY):
        kw = {"REMOTE_ADDR": remote}
        if xff is not None:
            kw["HTTP_X_FORWARDED_FOR"] = xff
        return self.rf.get("/", **kw)

    def test_rightmost_forwarded_address_is_used(self):
        # nginx の $proxy_add_x_forwarded_for: クライアント申告値の後ろに実際の接続元
        self.assertEqual(client_ip(self._req("1.2.3.4, 203.0.113.7")), "203.0.113.7")
        self.assertEqual(client_ip(self._req("203.0.113.7")), "203.0.113.7")

    def test_fallbacks(self):
        self.assertEqual(client_ip(self._req()), PROXY)                     # no header
        self.assertEqual(client_ip(self._req("not-an-ip")), PROXY)          # garbage
        # a public REMOTE_ADDR is a direct connection: its X-Forwarded-For is not trusted
        # (documentation ranges such as 198.51.100.0/24 count as private in ipaddress)
        self.assertEqual(client_ip(self._req("1.2.3.4", remote="8.8.8.8")), "8.8.8.8")
        with self.settings(TRUSTED_PROXY_COUNT=0):
            self.assertEqual(client_ip(self._req("203.0.113.7")), PROXY)

    def test_form_rate_limit_uses_the_same_address(self):
        from .views import _client_ip
        self.assertEqual(_client_ip(self._req("9.9.9.9, 203.0.113.7")), "203.0.113.7")


@override_settings(ADMIN_ADDRESS="admin@example.org")
class SecurityNotificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("alice", "alice@example.org", PASSWORD)

    def _login(self, username="alice", password=PASSWORD, ip="203.0.113.7", client=None):
        c = client or self.client
        return c.post(reverse("accounts:login"), {"username": username, "password": password},
                      REMOTE_ADDR=PROXY, HTTP_X_FORWARDED_FOR=ip)

    # --- axes / login ---------------------------------------------------------
    def test_lockout_is_per_client_not_site_wide(self):
        for i in range(5):
            self._login(username=f"nobody{i}", password="wrong-password-1", ip="198.51.100.1")
        self.assertTrue(any("ロックアウト" in s for s in _subjects()))
        # a different client logs in normally (before the fix every client shared one IP)
        resp = self._login(ip="203.0.113.7", client=Client())
        self.assertRedirects(resp, reverse("accounts:mfa_setup"), fetch_redirect_response=False)

    def test_every_login_is_notified_and_flags_new_address(self):
        self._login(ip="203.0.113.7")
        self.assertIn("[HUVar] ログイン通知", _subjects()[-1])          # first login
        self.assertIn("初回ログイン", mail.outbox[-1].body)
        self.assertIn("203.0.113.7", mail.outbox[-1].body)
        Client().post(reverse("accounts:logout"))
        self._login(ip="203.0.113.7", client=Client())
        self.assertEqual(_subjects()[-1], "[HUVar] ログイン通知")          # known address
        self._login(ip="192.0.2.44", client=Client())
        self.assertIn("【要確認】", _subjects()[-1])
        self.assertIn("初めての接続元", _subjects()[-1])

    def test_admin_login_is_flagged(self):
        User.objects.create_superuser("root", "root@example.org", PASSWORD)
        self._login(username="root")
        self.assertIn("管理者権限ユーザー", _subjects()[-1])

    def test_audit_log_records_client_address(self):
        self._login(ip="203.0.113.7")
        row = AuditLog.objects.filter(action="login").latest("created_at")
        self.assertEqual(row.ip_address, "203.0.113.7")

    @override_settings(SECURITY_LOGIN_FAILURES_PER_IP=3)
    def test_repeated_failures_from_one_address_alert_once(self):
        for i in range(4):
            self._login(username=f"guess{i}", password="wrong-password-1", ip="198.51.100.2")
        hits = [s for s in _subjects() if "ログイン失敗の多発（接続元 198.51.100.2）" in s]
        self.assertEqual(len(hits), 1)

    # --- MFA ----------------------------------------------------------------------
    def test_repeated_mfa_failures_alert(self):
        self.client.force_login(self.user)
        self.client.get(reverse("accounts:mfa_setup"))
        for _ in range(3):
            self.client.post(reverse("accounts:mfa_setup"), {"token": "000000"})
        self.assertTrue(any("MFA コードの連続失敗" in s for s in _subjects()))
        self.assertEqual(AuditLog.objects.filter(action="mfa_failed").count(), 3)

    def test_mfa_device_deletion_and_reregistration_alert(self):
        dev = TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        self.assertFalse(any("MFA の再登録" in s for s in _subjects()))   # first: no alert
        dev.delete()
        self.assertTrue(any("MFA デバイスの削除" in s for s in _subjects()))
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        self.assertTrue(any("MFA の再登録" in s for s in _subjects()))

    # --- privilege / password changes -----------------------------------------
    def test_privilege_and_password_changes_alert(self):
        self.user.is_staff = True
        self.user.save()
        self.assertIn("【要確認】権限の変更", _subjects()[-1])
        self.assertIn("スタッフ権限", mail.outbox[-1].body)
        n = len(mail.outbox)
        self.user.set_password("another-strong-pass-77")
        self.user.save()
        self.assertEqual(len(mail.outbox), n + 1)
        self.assertIn("パスワードの変更", mail.outbox[-1].body)
        n = len(mail.outbox)
        self.user.institution = "Somewhere"
        self.user.save()
        self.assertEqual(len(mail.outbox), n)          # ordinary edits: no alert

    def test_ordinary_user_creation_is_quiet_admin_creation_is_not(self):
        n = len(mail.outbox)
        User.objects.create_user("bob", "bob@example.org", PASSWORD)
        self.assertEqual(len(mail.outbox), n)
        User.objects.create_superuser("root2", "root2@example.org", PASSWORD)
        self.assertIn("管理者権限を持つユーザーの作成", mail.outbox[-1].body)

    def test_non_staff_admin_access_alerts(self):
        self.user.mfa_exempt = True
        self.user.save()
        self.client.force_login(self.user)
        self.client.get("/admin/")
        self.client.get("/admin/")
        hits = [s for s in _subjects() if "一般ユーザーによる管理画面へのアクセス" in s]
        self.assertEqual(len(hits), 1)

    # --- API tokens -----------------------------------------------------------------
    def test_token_issue_alert_expiry_and_last_use(self):
        token = Token.objects.create(user=self.user)
        self.assertTrue(any("API トークンの発行" in s for s in _subjects()))
        auth = {"HTTP_AUTHORIZATION": f"Token {token.key}", "REMOTE_ADDR": PROXY,
                "HTTP_X_FORWARDED_FOR": "203.0.113.9"}
        from .authentication import ExpiringTokenAuthentication
        req = RequestFactory().get("/", **auth)
        ExpiringTokenAuthentication().authenticate(req)
        self.user.refresh_from_db()
        self.assertEqual(self.user.api_token_last_ip, "203.0.113.9")
        self.assertIsNotNone(self.user.api_token_last_used_at)
        Token.objects.filter(pk=token.pk).update(created=timezone.now() - timedelta(days=400))
        from rest_framework.exceptions import AuthenticationFailed
        with self.assertRaises(AuthenticationFailed):
            ExpiringTokenAuthentication().authenticate(RequestFactory().get("/", **auth))
        with self.settings(API_TOKEN_MAX_AGE_DAYS=0):
            ExpiringTokenAuthentication().authenticate(RequestFactory().get("/", **auth))


class AdminNetworkTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_unrestricted_by_default(self):
        resp = Client().get("/admin/login/", REMOTE_ADDR=PROXY, HTTP_X_FORWARDED_FOR="198.51.100.1")
        self.assertEqual(resp.status_code, 200)

    @override_settings(ADMIN_ALLOWED_NETWORKS="133.41.0.0/16, 10.0.0.0/8")
    def test_admin_only_from_allowed_networks(self):
        c = Client()  # middleware is built with the overridden setting
        self.assertEqual(c.get("/admin/login/", REMOTE_ADDR=PROXY,
                               HTTP_X_FORWARDED_FOR="198.51.100.1").status_code, 404)
        # a forged left-most address does not help
        self.assertEqual(c.get("/admin/login/", REMOTE_ADDR=PROXY,
                               HTTP_X_FORWARDED_FOR="133.41.1.1, 198.51.100.1").status_code, 404)
        self.assertEqual(c.get("/admin/login/", REMOTE_ADDR=PROXY,
                               HTTP_X_FORWARDED_FOR="133.41.103.50").status_code, 200)
        # the rest of the site is unaffected
        self.assertEqual(c.get(reverse("accounts:login"), REMOTE_ADDR=PROXY,
                               HTTP_X_FORWARDED_FOR="198.51.100.1").status_code, 200)


class ProductionSettingsTests(TestCase):
    def _import_prod(self, secret):
        env = {k: v for k, v in os.environ.items() if k != "SECRET_KEY"}
        if secret is not None:
            env["SECRET_KEY"] = secret
        env["DJANGO_SETTINGS_MODULE"] = "config.settings.prod"
        root = Path(__file__).resolve().parent.parent
        return subprocess.run(
            [sys.executable, "-c", "import config.settings.prod"],
            cwd=root, env=env, capture_output=True, text=True)

    def test_prod_refuses_missing_or_weak_secret_key(self):
        for secret in (None, "insecure-dev-key", "short"):
            r = self._import_prod(secret)
            self.assertNotEqual(r.returncode, 0, secret)
            self.assertIn("ImproperlyConfigured", r.stderr)
        self.assertEqual(self._import_prod("x" * 50).returncode, 0)


class ApiTokenEndToEndTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("carol", "carol@example.org", PASSWORD)
        self.token = Token.objects.create(user=self.user)

    def _whoami(self):
        return self.client.get(reverse("api:whoami"), REMOTE_ADDR=PROXY,
                               HTTP_X_FORWARDED_FOR="203.0.113.20",
                               HTTP_AUTHORIZATION=f"Token {self.token.key}")

    def test_valid_and_expired_token(self):
        self.assertEqual(self._whoami().status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.api_token_last_ip, "203.0.113.20")
        Token.objects.filter(pk=self.token.pk).update(
            created=timezone.now() - timedelta(days=366))
        resp = self._whoami()
        self.assertEqual(resp.status_code, 401)
        self.assertIn("有効期限", resp.json()["detail"])
