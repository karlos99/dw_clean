from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('landing', '0006_projectconfig_home_chart_controls'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectconfig',
            name='home_show_chart_location',
            field=models.BooleanField(
                default=True,
                help_text="Show the chart name bar (location) on every user's home dashboard. When off, home charts show only the visual and its in-chart title.",
            ),
        ),
    ]
