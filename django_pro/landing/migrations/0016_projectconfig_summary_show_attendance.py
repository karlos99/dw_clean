from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('landing', '0015_alter_projectconfig_summary_access_rules'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectconfig',
            name='summary_show_attendance',
            field=models.BooleanField(
                default=True,
                help_text='Show the Attendance tab (absences by type and tardies by school year) in the student summary.',
            ),
        ),
    ]
