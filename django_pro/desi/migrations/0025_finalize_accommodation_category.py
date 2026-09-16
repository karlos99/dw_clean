from django.db import migrations, models
from django.db.models import F


def copy_to_legacy(apps, schema_editor):
    """Preserve the original free-text category on every support before the old column is removed."""
    Accommodation = apps.get_model('desi', 'Accommodation')
    for accommodation in Accommodation.objects.all().only('id', 'category', 'category_legacy'):
        raw = accommodation.category or ''
        if raw and not accommodation.category_legacy:
            accommodation.category_legacy = raw
            accommodation.save(update_fields=['category_legacy'])


def copy_from_legacy(apps, schema_editor):
    """Reverse: restore the legacy string back onto the re-added category column."""
    Accommodation = apps.get_model('desi', 'Accommodation')
    Accommodation.objects.all().update(category=F('category_legacy'))


class Migration(migrations.Migration):
    """
    Step 3 of 3: keep the old string in ``category_legacy`` for reference,
    then swap ``category_new`` into the ``category`` name. No support rows or
    other data are deleted.
    """

    dependencies = [
        ('desi', '0024_backfill_accommodation_categories'),
    ]

    operations = [
        migrations.AddField(
            model_name='accommodation',
            name='category_legacy',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Original free-text category value, retained for reference after the category model was introduced.',
                max_length=100,
            ),
        ),
        migrations.RunPython(copy_to_legacy, copy_from_legacy),
        migrations.RemoveField(
            model_name='accommodation',
            name='category',
        ),
        migrations.RenameField(
            model_name='accommodation',
            old_name='category_new',
            new_name='category',
        ),
        migrations.AlterModelOptions(
            name='accommodation',
            options={'ordering': ['order', 'category__order', 'category__name', 'name']},
        ),
    ]
