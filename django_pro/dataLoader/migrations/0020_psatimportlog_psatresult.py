# Generated manually for PSAT loader

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dataLoader', '0019_alter_ibexamresult_options_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PSATImportLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='User-provided name for this import', max_length=255)),
                ('school_year', models.CharField(help_text="School year (e.g., '2025-2026')", max_length=9)),
                ('psat_type', models.CharField(choices=[('PSAT89', 'PSAT 8/9'), ('PSATNM', 'PSAT/NMSQT')], help_text='Detected from file: PSAT 8/9 or PSAT/NMSQT', max_length=10)),
                ('filename', models.CharField(help_text='Original filename uploaded', max_length=255)),
                ('file_hash', models.CharField(blank=True, help_text='SHA256 hash for duplicate detection', max_length=64)),
                ('uploaded_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('PROCESSING', 'Processing'), ('COMPLETED', 'Completed'), ('FAILED', 'Failed'), ('PARTIAL', 'Partially Completed')], default='PENDING', max_length=20)),
                ('total_students', models.PositiveIntegerField(default=0)),
                ('records_detected', models.PositiveIntegerField(default=0)),
                ('records_created', models.PositiveIntegerField(default=0)),
                ('records_skipped', models.PositiveIntegerField(default=0)),
                ('students_matched', models.PositiveIntegerField(default=0)),
                ('students_unmatched', models.PositiveIntegerField(default=0)),
                ('records_processed', models.PositiveIntegerField(default=0)),
                ('manual_overrides', models.JSONField(default=dict, help_text="Manually-entered SSID overrides (e.g., {'record_key': 'ssid'})")),
                ('error_count', models.PositiveIntegerField(default=0)),
                ('error_log', models.JSONField(default=list)),
                ('warnings', models.JSONField(default=list)),
                ('is_published', models.BooleanField(default=False, help_text="When enabled, this import's PSAT results are available in the Data Explorer")),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('published_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='published_psat_imports', to=settings.AUTH_USER_MODEL)),
                ('uploaded_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='psat_data_imports', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'PSAT Import Log',
                'verbose_name_plural': 'PSAT Import Logs',
                'ordering': ['-uploaded_at'],
            },
        ),
        migrations.CreateModel(
            name='PSATResult',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('record_key', models.CharField(db_index=True, max_length=120, unique=True)),
                ('psat_type', models.CharField(choices=[('PSAT89', 'PSAT 8/9'), ('PSATNM', 'PSAT/NMSQT')], db_index=True, max_length=10)),
                ('ssid', models.CharField(blank=True, db_index=True, max_length=50)),
                ('state_student_id', models.CharField(blank=True, db_index=True, max_length=50)),
                ('district_student_id', models.CharField(blank=True, db_index=True, max_length=50)),
                ('cb_id', models.CharField(blank=True, db_index=True, max_length=50)),
                ('last_name', models.CharField(blank=True, max_length=100)),
                ('first_name', models.CharField(blank=True, max_length=100)),
                ('middle_initial', models.CharField(blank=True, max_length=10)),
                ('birth_date', models.DateField(blank=True, null=True)),
                ('gender', models.CharField(blank=True, max_length=20)),
                ('school_code', models.CharField(blank=True, max_length=50)),
                ('school_name', models.CharField(blank=True, max_length=255)),
                ('test_date', models.DateField(blank=True, null=True)),
                ('test_grade', models.PositiveSmallIntegerField(blank=True, null=True)),
                ('school_year', models.CharField(max_length=9)),
                ('total_score', models.PositiveIntegerField(blank=True, null=True)),
                ('ebrw_score', models.PositiveIntegerField(blank=True, null=True)),
                ('math_score', models.PositiveIntegerField(blank=True, null=True)),
                ('response_data', models.JSONField(blank=True, default=dict, help_text='Non-demographic PSAT response fields (percentiles, benchmarks, AP eligibility, etc.)')),
                ('match_method', models.CharField(blank=True, max_length=40)),
                ('match_confidence', models.FloatField(default=0.0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('import_log', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='results', to='dataLoader.psatimportlog')),
            ],
            options={
                'verbose_name': 'PSAT Result',
                'verbose_name_plural': 'PSAT Results',
                'ordering': ['-test_date', 'last_name', 'first_name'],
                'indexes': [
                    models.Index(fields=['ssid', 'school_year', 'psat_type'], name='dataLoader__psat_ssid_idx'),
                    models.Index(fields=['school_year', 'test_date'], name='dataLoader__psat_sy_dt_idx'),
                ],
            },
        ),
    ]
