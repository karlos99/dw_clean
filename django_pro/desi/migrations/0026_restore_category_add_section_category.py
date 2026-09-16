import django.db.models.deletion
from django.db import migrations, models
from django.db.models import F


def restore_category(apps, schema_editor):
    """Restore the original free-text category from the retained legacy column."""
    Accommodation = apps.get_model('desi', 'Accommodation')
    Accommodation.objects.all().update(category=F('category_legacy'))


def reverse_restore_category(apps, schema_editor):
    """Reverse: copy the restored category back into the legacy column."""
    Accommodation = apps.get_model('desi', 'Accommodation')
    Accommodation.objects.all().update(category_legacy=F('category'))


class Migration(migrations.Migration):
    """
    Keep the current free-text ``category`` (restored from ``category_legacy``)
    and rename the FK that was introduced earlier to ``section_category`` so
    the two concepts coexist:

    * ``category``         -> the original source/type label (kept as-is).
    * ``section_category`` -> the reusable section used to group supports in
                              the student workspace matrix.

    No support rows or data are deleted; the existing category rows become the
    initial section categories.
    """

    dependencies = [
        ('desi', '0025_finalize_accommodation_category'),
    ]

    operations = [
        # Neutral ordering first so the rename never leaves a dangling
        # ``category__order`` reference in the intermediate migration state.
        migrations.AlterModelOptions(
            name='accommodation',
            options={'ordering': ['order', 'name']},
        ),
        migrations.RenameField(
            model_name='accommodation',
            old_name='category',
            new_name='section_category',
        ),
        migrations.AlterField(
            model_name='accommodation',
            name='section_category',
            field=models.ForeignKey(
                blank=True,
                help_text='Section used to group this support in the student workspace.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='accommodations',
                to='desi.accommodationcategory',
            ),
        ),
        migrations.AlterField(
            model_name='accommodationcategory',
            name='order',
            field=models.IntegerField(default=0, help_text='Order in which this section is displayed'),
        ),
        migrations.AddField(
            model_name='accommodation',
            name='category',
            field=models.CharField(
                blank=True,
                default='',
                help_text='e.g., Presentation, Response, Setting, Timing',
                max_length=100,
            ),
            preserve_default=False,
        ),
        migrations.RunPython(restore_category, reverse_restore_category),
        migrations.RemoveField(
            model_name='accommodation',
            name='category_legacy',
        ),
        migrations.AlterModelOptions(
            name='accommodation',
            options={'ordering': ['order', 'section_category__order', 'section_category__name', 'name']},
        ),
        migrations.AlterModelOptions(
            name='accommodationcategory',
            options={
                'ordering': ['order', 'name'],
                'verbose_name': 'Designated Support Section Category',
                'verbose_name_plural': 'Designated Support Section Categories',
            },
        ),
    ]
