from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProjectConfig',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('default_school_year', models.CharField(
                    blank=True,
                    default='',
                    help_text="The reference school year for the project (e.g. '2025-2026'). Used to resolve school-year aliases.",
                    max_length=20,
                )),
                ('default_columns', models.JSONField(
                    blank=True,
                    null=True,
                    help_text='Ordered list of column keys shown by default in the Data Explorer.',
                )),
                ('enabled_columns', models.JSONField(
                    blank=True,
                    null=True,
                    help_text='Allowlist of column keys available in the Data Explorer. Null = all allowed.',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='project_config_updates',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Project Configuration',
                'verbose_name_plural': 'Project Configuration',
            },
        ),
    ]
