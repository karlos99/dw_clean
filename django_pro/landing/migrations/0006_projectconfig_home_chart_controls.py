# Generated manually to add forced-chart and max-chart controls to the home dashboard.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('landing', '0005_projectconfig_disabled_school_years'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectconfig',
            name='home_forced_charts',
            field=models.JSONField(blank=True, default=list, help_text='List of SavedChart IDs that are forced onto every user\'s home dashboard. Users cannot delete these charts, but they can rearrange and resize them alongside charts they add themselves.'),
        ),
        migrations.AddField(
            model_name='projectconfig',
            name='home_max_charts',
            field=models.PositiveIntegerField(default=0, help_text='Maximum number of charts a user may add to their own home dashboard. Forced charts do not count toward this limit. 0 = unlimited.'),
        ),
    ]
