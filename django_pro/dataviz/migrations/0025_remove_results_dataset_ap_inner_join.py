"""Remove erroneous INNER JOIN to analytics_ap_results on results datasets.

That join restricted CAASPP (and other assessment) charts to students who
also had AP rows — e.g. chart 181 showed 602 instead of 1978 for 2025-2026.
"""

from django.db import migrations


def remove_ap_inner_join_from_results_datasets(apps, schema_editor):
    Dataset = apps.get_model('dataviz', 'Dataset')
    for ds in Dataset.objects.all():
        joins = list(ds.joins or [])
        without_ap = [j for j in joins if j.get('table') != 'analytics_ap_results']
        if len(without_ap) == len(joins):
            continue
        columns = [
            c for c in (ds.columns or [])
            if not str(c).startswith('analytics_ap_results.')
        ]
        ds.joins = without_ap
        ds.columns = columns
        ds.save(update_fields=['joins', 'columns', 'updated_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('dataviz', '0024_dataset_folder'),
    ]

    operations = [
        migrations.RunPython(
            remove_ap_inner_join_from_results_datasets,
            migrations.RunPython.noop,
        ),
    ]