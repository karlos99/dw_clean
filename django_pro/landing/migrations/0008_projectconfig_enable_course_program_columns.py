from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('landing', '0007_projectconfig_home_show_chart_location'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectconfig',
            name='enable_course_program_columns',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'When enabled, the Data Explorer shows a Course Programs column group '
                    '(staff catalog placements and synced CTE pathways from schedule courses).'
                ),
            ),
        ),
    ]
