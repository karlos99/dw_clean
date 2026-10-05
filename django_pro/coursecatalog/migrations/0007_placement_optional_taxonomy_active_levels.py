# Optional category/discipline/level on placements; activate class levels for UI.

from django.db import migrations, models


def activate_all_levels(apps, schema_editor):
    CatalogLevel = apps.get_model('coursecatalog', 'CatalogLevel')
    CatalogLevel.objects.filter(is_active=False).update(is_active=True)


class Migration(migrations.Migration):

    dependencies = [
        ('coursecatalog', '0006_fix_pathstep_fk_state'),
    ]

    operations = [
        migrations.RunPython(activate_all_levels, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='catalogplacement',
            name='uniq_placement_entry_taxonomy_level',
        ),
        migrations.AlterField(
            model_name='catalogplacement',
            name='category',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name='placements',
                to='coursecatalog.catalogcategory',
            ),
        ),
        migrations.AlterField(
            model_name='catalogplacement',
            name='discipline',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name='placements',
                to='coursecatalog.catalogdiscipline',
            ),
        ),
        migrations.AlterField(
            model_name='catalogplacement',
            name='level',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name='placements',
                to='coursecatalog.cataloglevel',
            ),
        ),
        migrations.AddConstraint(
            model_name='catalogplacement',
            constraint=models.UniqueConstraint(
                fields=('catalog_entry', 'tag', 'category', 'discipline', 'level'),
                name='uniq_placement_entry_taxonomy_level',
                nulls_distinct=False,
            ),
        ),
    ]
