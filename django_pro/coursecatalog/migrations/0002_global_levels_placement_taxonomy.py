# Levels become district-wide; placements store category + optional discipline + level.

from django.db import migrations, models
import django.db.models.deletion


def migrate_levels_and_placements(apps, schema_editor):
    CatalogLevel = apps.get_model('coursecatalog', 'CatalogLevel')
    CatalogPlacement = apps.get_model('coursecatalog', 'CatalogPlacement')

    canonical_by_name = {}
    for lv in CatalogLevel.objects.all().order_by('id'):
        if lv.name not in canonical_by_name:
            canonical_by_name[lv.name] = lv

    for placement in CatalogPlacement.objects.all().iterator():
        old_level = CatalogLevel.objects.get(pk=placement.level_id)
        placement.category_id = old_level.category_id
        placement.discipline_id = old_level.discipline_id
        canonical = canonical_by_name.get(old_level.name, old_level)
        placement.level_id = canonical.id
        placement.save(update_fields=['category_id', 'discipline_id', 'level_id'])

    keep_ids = {lv.id for lv in canonical_by_name.values()}
    CatalogLevel.objects.exclude(id__in=keep_ids).delete()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('coursecatalog', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='catalogplacement',
            name='category',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='placements',
                to='coursecatalog.catalogcategory',
            ),
        ),
        migrations.AddField(
            model_name='catalogplacement',
            name='discipline',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='placements',
                to='coursecatalog.catalogdiscipline',
            ),
        ),
        migrations.RunPython(migrate_levels_and_placements, noop),
        migrations.AlterField(
            model_name='catalogplacement',
            name='category',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='placements',
                to='coursecatalog.catalogcategory',
            ),
        ),
        migrations.RemoveConstraint(
            model_name='catalogplacement',
            name='uniq_catalog_placement_entry_level',
        ),
        migrations.RemoveConstraint(
            model_name='cataloglevel',
            name='uniq_catalog_level_category_name_no_disc',
        ),
        migrations.RemoveConstraint(
            model_name='cataloglevel',
            name='uniq_catalog_level_discipline_name',
        ),
        migrations.RemoveField(
            model_name='cataloglevel',
            name='category',
        ),
        migrations.RemoveField(
            model_name='cataloglevel',
            name='discipline',
        ),
        migrations.AddConstraint(
            model_name='cataloglevel',
            constraint=models.UniqueConstraint(fields=('name',), name='uniq_catalog_level_name'),
        ),
        migrations.AddConstraint(
            model_name='catalogplacement',
            constraint=models.UniqueConstraint(
                condition=models.Q(('discipline__isnull', True)),
                fields=('catalog_entry', 'category', 'level'),
                name='uniq_placement_entry_cat_level_no_disc',
            ),
        ),
        migrations.AddConstraint(
            model_name='catalogplacement',
            constraint=models.UniqueConstraint(
                condition=models.Q(('discipline__isnull', False)),
                fields=('catalog_entry', 'category', 'discipline', 'level'),
                name='uniq_placement_entry_cat_disc_level',
            ),
        ),
    ]
