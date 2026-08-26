from django.db import migrations, models


def remove_legacy_default_active(apps, schema_editor):
    Dataset = apps.get_model('dataviz', 'Dataset')
    # Migration 0016 populated the new field with ['Active'] for existing
    # records. Those values were implicit defaults, not user selections.
    Dataset.objects.filter(status_filter=['Active']).update(status_filter=[])


class Migration(migrations.Migration):
    dependencies = [
        ('dataviz', '0016_dataset_status_filter'),
    ]

    operations = [
        migrations.RunPython(remove_legacy_default_active, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='dataset',
            name='status_filter',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
