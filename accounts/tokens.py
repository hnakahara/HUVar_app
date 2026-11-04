"""自己登録のメールアドレス確認リンク用トークン。

PasswordResetTokenGenerator と同じ仕組み（HMAC + タイムスタンプ、有効期限は
settings.PASSWORD_RESET_TIMEOUT、既定 3 日）。ハッシュに approval_status を含めるため、
確認が済んで状態が email_pending から変わった時点でリンクは無効になる（使い回し不可）。
"""
from django.contrib.auth.tokens import PasswordResetTokenGenerator


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    key_salt = "accounts.tokens.EmailVerificationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{user.email}{user.approval_status}{timestamp}"


email_verification_token = EmailVerificationTokenGenerator()
