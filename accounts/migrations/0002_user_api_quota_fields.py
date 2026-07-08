# Generated for HUVar: MFA 免除フラグ・API/Web 利用上限・当月残り回数の追加
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="mfa_exempt",
            field=models.BooleanField(
                default=False,
                help_text="レビュー等のため MFA を免除する（セキュリティ上、通常は無効）。",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="api_single_monthly_limit",
            field=models.PositiveIntegerField(
                default=100,
                help_text="API 単一解析(classify)の月あたり実行回数の上限。",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="api_single_remaining",
            field=models.PositiveIntegerField(
                default=100,
                help_text="API 単一解析(classify)の当月残り回数。月初に上限へ自動リセット。",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="api_batch_monthly_limit",
            field=models.PositiveIntegerField(
                default=5,
                help_text="API バッチ(jobs)の月あたり実行回数の上限。",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="api_batch_remaining",
            field=models.PositiveIntegerField(
                default=5,
                help_text="API バッチ(jobs)の当月残り回数。月初に上限へ自動リセット。",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="api_usage_period",
            field=models.CharField(
                blank=True,
                default="",
                help_text="API 残数を管理している対象月（YYYY-MM）。内部管理用。",
                max_length=7,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="web_batch_monthly_limit",
            field=models.PositiveIntegerField(
                default=50,
                help_text="Web バッチ(VCF)の月あたり実行回数の上限。",
            ),
        ),
    ]
