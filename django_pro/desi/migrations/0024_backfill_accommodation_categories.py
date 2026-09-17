from django.db import migrations


PRIORITY_ORDER = [
    'presentation',
    'response',
    'setting',
    'timing',
]


def backfill_categories(apps, schema_editor):
    """
    Step 2 of 3: create one AccommodationCategory per distinct existing
    free-text category and point each support's new FK at it.

    Additive only — the original ``category`` strings are never modified or
    deleted here, so this migration is safe to run more than once.
    """
    Accommodation = apps.get_model('desi', 'Accommodation')
    AccommodationCategory = apps.get_model('desi', 'AccommodationCategory')

    raw_values = (
        Accommodation.objects.exclude(category__isnull=True)
        .exclude(category__exact='')
        .values_list('category', flat=True)
        .distinct()
    )
    # Preserve the first-seen spelling for each case-insensitive label.
    labels = {}
    for raw in raw_values:
        label = (raw or '').strip()
        if not label:
            continue
        labels.setdefault(label.casefold(), label)

    def sort_key(item):
        key, _label = item
        if key in PRIORITY_ORDER:
            return (0, PRIORITY_ORDER.index(key), key)
        return (1, 0, key)

    ordered = sorted(labels.items(), key=sort_key)

    for index, (_key, label) in enumerate(ordered, start=1):
        category, _created = AccommodationCategory.objects.get_or_create(
            name=label,
            defaults={'order': index},
        )

    for accommodation in Accommodation.objects.exclude(category__isnull=True).exclude(category__exact=''):
        label = (accommodation.category or '').strip()
        if not label:
            continue
        category = AccommodationCategory.objects.filter(name__iexact=label).first()
        if category is None:
            continue
        if accommodation.category_new_id != category.id:
            accommodation.category_new = category
            accommodation.save(update_fields=['category_new'])


def noop(apps, schema_editor):
    """Reverse is intentionally a no-op so rollback never drops category data."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('desi', '0023_accommodationcategory_accommodation_category_new'),
    ]

    operations = [
        migrations.RunPython(backfill_categories, noop),
    ]
