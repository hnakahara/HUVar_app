"""自己登録 → メールアドレス確認 → 管理者承認 のフロー。"""
from datetime import timedelta
import re

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import User

PASSWORD = "correct-horse-battery-42"


def _signup_data(**kw):
    data = {
        "username": "alice",
        "email": "alice@example.org",
        "email_confirm": "alice@example.org",
        "institution": "Example University",
        "registration_purpose": "Variant interpretation research",
        "password1": PASSWORD,
        "password2": PASSWORD,
        "website": "",
    }
    data.update(kw)
    return data


@override_settings(ADMIN_ADDRESS="admin@example.org")
class SignupFlowTests(TestCase):
    def setUp(self):
        cache.clear()

    def _signup(self, **kw):
        return self.client.post(reverse("accounts:signup"), _signup_data(**kw))

    def _verify_link(self):
        body = mail.outbox[-1].body
        m = re.search(r"https?://\S+/accounts/verify/\S+/", body)
        self.assertIsNotNone(m, body)
        return m.group(0).split("testserver", 1)[1]

    def _login(self, username="alice", password=PASSWORD):
        return self.client.post(reverse("accounts:login"),
                                {"username": username, "password": password})

    def _admin(self):
        admin = User.objects.create_superuser("root", "root@example.org", PASSWORD)
        admin.mfa_exempt = True
        admin.save()
        c = self.client_class()
        c.force_login(admin)
        return c

    def _action(self, admin_client, action, user):
        return admin_client.post(reverse("admin:accounts_user_changelist"), {
            "action": action, "_selected_action": [user.pk]}, follow=True)

    # --- registration -----------------------------------------------------
    def test_signup_creates_inactive_account_and_sends_confirmation(self):
        resp = self._signup()
        self.assertRedirects(resp, reverse("accounts:signup_done"))
        u = User.objects.get(username="alice")
        self.assertFalse(u.is_active)
        self.assertFalse(u.is_staff)
        self.assertEqual(u.role, User.Role.GENERAL)
        self.assertEqual(u.approval_status, User.ApprovalStatus.EMAIL_PENDING)
        self.assertTrue(u.check_password(PASSWORD))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["alice@example.org"])  # not the admin yet

    def test_duplicate_email_mismatch_and_honeypot_are_rejected(self):
        User.objects.create_user("bob", "Alice@Example.org", PASSWORD)
        self.assertContains(self._signup(), "既に登録されています")
        self.assertContains(self._signup(username="carol", email="c@example.org",
                                         email_confirm="d@example.org"), "一致しません")
        self.assertContains(self._signup(username="dave", email="dave@example.org",
                                         email_confirm="dave@example.org",
                                         website="spam"), "送信できませんでした")
        self.assertFalse(User.objects.filter(username__in=["alice", "carol", "dave"]).exists())

    def test_old_request_url_redirects_to_signup(self):
        self.assertRedirects(self.client.get("/accounts/request/"), reverse("accounts:signup"))

    # --- email confirmation -------------------------------------------------
    def test_verification_link_moves_to_pending_once_and_notifies_admin(self):
        self._signup()
        link = self._verify_link()
        resp = self.client.get(link)
        self.assertEqual(resp.context["state"], "verified")
        u = User.objects.get(username="alice")
        self.assertEqual(u.approval_status, User.ApprovalStatus.PENDING)
        self.assertIsNotNone(u.email_verified_at)
        self.assertFalse(u.is_active)
        self.assertEqual(mail.outbox[-1].to, ["admin@example.org"])
        self.assertIn("approval_status__exact=pending", mail.outbox[-1].body)
        # the link cannot be used again
        self.assertEqual(self.client.get(link).context["state"], "already")

    def test_bad_token_is_invalid(self):
        self._signup()
        link = self._verify_link()
        bad = link.rstrip("/")[:-3] + "xyz/"
        self.assertEqual(self.client.get(bad).context["state"], "invalid")
        self.assertEqual(User.objects.get(username="alice").approval_status,
                         User.ApprovalStatus.EMAIL_PENDING)

    def test_stale_unverified_registration_is_purged_on_next_signup(self):
        self._signup()
        User.objects.filter(username="alice").update(
            date_joined=timezone.now() - timedelta(days=4))
        cache.clear()
        resp = self._signup()
        self.assertRedirects(resp, reverse("accounts:signup_done"))
        self.assertEqual(User.objects.filter(username="alice").count(), 1)

    # --- login before / after approval ------------------------------------
    def test_login_messages_before_approval(self):
        self._signup()
        self.assertContains(self._login(), "メールアドレスの確認が完了していません")
        self.client.get(self._verify_link())
        self.assertContains(self._login(), "管理者の承認待ち")
        # wrong password: the generic error, nothing about the account state
        resp = self._login(password="wrong-password-123")
        self.assertNotContains(resp, "承認待ち")
        self.assertFalse(resp.wsgi_request.user.is_authenticated)

    def test_admin_approval_activates_and_notifies(self):
        self._signup()
        self.client.get(self._verify_link())
        admin = self._admin()
        self._action(admin, "approve_registrations", User.objects.get(username="alice"))
        u = User.objects.get(username="alice")
        self.assertTrue(u.is_active)
        self.assertEqual(u.approval_status, User.ApprovalStatus.APPROVED)
        self.assertIsNotNone(u.approval_processed_at)
        self.assertEqual(mail.outbox[-1].to, ["alice@example.org"])
        self.assertIn(reverse("accounts:login"), mail.outbox[-1].body)
        resp = self._login()
        self.assertRedirects(resp, reverse("accounts:mfa_setup"), fetch_redirect_response=False)

    def test_approval_requires_confirmed_email(self):
        self._signup()
        admin = self._admin()
        self._action(admin, "approve_registrations", User.objects.get(username="alice"))
        u = User.objects.get(username="alice")
        self.assertFalse(u.is_active)
        self.assertEqual(u.approval_status, User.ApprovalStatus.EMAIL_PENDING)

    def test_rejection_notifies_only_confirmed_addresses(self):
        self._signup()
        self.client.get(self._verify_link())
        admin = self._admin()
        n = len(mail.outbox)
        self._action(admin, "reject_registrations", User.objects.get(username="alice"))
        u = User.objects.get(username="alice")
        self.assertFalse(u.is_active)
        self.assertEqual(u.approval_status, User.ApprovalStatus.REJECTED)
        self.assertEqual(len(mail.outbox), n + 1)
        self.assertEqual(mail.outbox[-1].to, ["alice@example.org"])
        self.assertContains(self._login(), "承認されませんでした")
        # an unconfirmed registration is rejected silently
        cache.clear()
        self._signup(username="eve", email="eve@example.org", email_confirm="eve@example.org")
        n = len(mail.outbox)
        self._action(admin, "reject_registrations", User.objects.get(username="eve"))
        self.assertEqual(len(mail.outbox), n)

    def test_reject_does_not_touch_existing_accounts(self):
        admin = self._admin()
        bob = User.objects.create_user("bob", "bob@example.org", PASSWORD)
        self._action(admin, "reject_registrations", bob)
        bob.refresh_from_db()
        self.assertTrue(bob.is_active)
        self.assertEqual(bob.approval_status, User.ApprovalStatus.APPROVED)

    def test_activating_in_admin_form_marks_approved(self):
        self._signup()
        u = User.objects.get(username="alice")
        u.is_active = True
        u.save(update_fields=["is_active"])
        u.refresh_from_db()
        self.assertEqual(u.approval_status, User.ApprovalStatus.APPROVED)
        self.assertIsNotNone(u.approval_processed_at)

    def test_admin_created_users_are_approved(self):
        u = User.objects.create_user("frank", "frank@example.org", PASSWORD)
        self.assertEqual(u.approval_status, User.ApprovalStatus.APPROVED)
        self.assertTrue(u.is_active)

    # --- pages render -------------------------------------------------------
    def test_pages_render(self):
        resp = self.client.get(reverse("accounts:signup"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(list(resp.context["form"].fields)[:5],
                         ["username", "email", "email_confirm", "institution",
                          "registration_purpose"])
        self.assertContains(self.client.get(reverse("accounts:login")),
                            reverse("accounts:signup"))
        self._signup()
        admin = self._admin()
        u = User.objects.get(username="alice")
        page = admin.get(reverse("admin:accounts_user_change", args=[u.pk]))
        self.assertContains(page, "registration_purpose")
        lst = admin.get(reverse("admin:accounts_user_changelist")
                        + "?approval_status__exact=email_pending")
        self.assertContains(lst, "alice")

    def test_signup_purpose_help_is_for_the_registrant(self):
        form = self.client.get(reverse("accounts:signup")).context["form"]
        self.assertNotIn("自己登録時に入力された", str(form["registration_purpose"].help_text))

    def test_errors_are_marked_in_red(self):
        User.objects.create_user("alice", "other@example.org", PASSWORD)
        resp = self._signup(institution="")
        self.assertContains(resp, 'class="form-errors"')
        self.assertContains(resp, 'form-row has-error')
        self.assertContains(resp, 'class="field-errors"')
        self.assertContains(resp, "赤字の項目を確認してください")
        # login: a form-level error only, without the "check the red fields" line
        resp = self._login(password="wrong-password-123")
        self.assertContains(resp, 'class="form-errors"')
        self.assertNotContains(resp, "赤字の項目を確認してください")
