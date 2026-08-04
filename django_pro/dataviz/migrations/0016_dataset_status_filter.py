from django.db import migrations, models
import dataviz.models


class Migration(migrations.Migration):

    dependencies = [
        ('dataviz', '0015_dataset_order_by'),
    ]

    operations = [
        migrations.AddField(
            model_name='dataset',
            name='status_filter',
            field=models.JSONField(blank=True, default=dataviz.models._default_status_filter),
        ),
    ]
