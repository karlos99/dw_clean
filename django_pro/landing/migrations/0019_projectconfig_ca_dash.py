from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('landing', '0018_enable_student_pathways_table'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectconfig',
            name='ca_dash_district_cds',
            field=models.CharField(
                blank=True,
                default='',
                help_text=(
                    '14-digit district CDS code for the Performance Map. '
                    'Leave blank to derive from school state IDs.'
                ),
                max_length=14,
            ),
        ),
        migrations.AddField(
            model_name='projectconfig',
            name='ca_dash_district_short_name',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Short district label in the map UI (e.g. DSUSD).',
                max_length=50,
            ),
        ),
        migrations.AddField(
            model_name='projectconfig',
            name='ca_dash_district_full_name',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Full district name shown in map subtitles.',
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name='projectconfig',
            name='ca_dash_enabled_reporting_years',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    'Reporting years to expose in the CA Dashboard year toggles '
                    '(e.g. [2022, 2023, 2024]). Empty means all years in dash_indi_* tables.'
                ),
            ),
        ),
    ]