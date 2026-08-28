from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dataviz', '0022_datavizpagefolder'),
    ]

    operations = [
        migrations.AddField(
            model_name='dashboardchart',
            name='is_forced',
            field=models.BooleanField(default=False, help_text='System-forced slot (configured in Project Settings) - users can rearrange but not delete it.'),
        ),
    ]
