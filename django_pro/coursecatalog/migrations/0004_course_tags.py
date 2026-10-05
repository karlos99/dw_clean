# Course tags under category (+ discipline when applicable); optional on placements.

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ('coursecatalog', '0003_alter_coursecatalogentry_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='catalogcategory',
            name='uses_tags',
            field=models.BooleanField(
                default=False,
                help_text='When enabled, placements can use tags (e.g. Dance under Performing Arts).',
            ),
        ),
        migrations.CreateModel(
            name='CatalogTag',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('order', models.IntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                (
                    'category',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='tags',
                        to='coursecatalog.catalogcategory',
                    ),
                ),
                (
                    'discipline',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='tags',
                        to='coursecatalog.catalogdiscipline',
                    ),
                ),
            ],
            options={
                'ordering': ['order', 'name'],
            },
        ),
        migrations.AddField(
            model_name='catalogplacement',
            name='tag',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='placements',
                to='coursecatalog.catalogtag',
            ),
        ),
        migrations.RemoveConstraint(
            model_name='catalogplacement',
            name='uniq_placement_entry_cat_level_no_disc',
        ),
        migrations.RemoveConstraint(
            model_name='catalogplacement',
            name='uniq_placement_entry_cat_disc_level',
        ),
        migrations.AddConstraint(
            model_name='catalogtag',
            constraint=models.UniqueConstraint(
                condition=Q(('discipline__isnull', True)),
                fields=('category', 'name'),
                name='uniq_catalog_tag_category_name',
            ),
        ),
        migrations.AddConstraint(
            model_name='catalogtag',
            constraint=models.UniqueConstraint(
                condition=Q(('discipline__isnull', False)),
                fields=('discipline', 'name'),
                name='uniq_catalog_tag_discipline_name',
            ),
        ),
        migrations.AddConstraint(
            model_name='catalogplacement',
            constraint=models.UniqueConstraint(
                condition=Q(('discipline__isnull', True), ('tag__isnull', True)),
                fields=('catalog_entry', 'category', 'level'),
                name='uniq_placement_entry_cat_level',
            ),
        ),
        migrations.AddConstraint(
            model_name='catalogplacement',
            constraint=models.UniqueConstraint(
                condition=Q(('discipline__isnull', True), ('tag__isnull', False)),
                fields=('catalog_entry', 'category', 'tag', 'level'),
                name='uniq_placement_entry_cat_tag_level',
            ),
        ),
        migrations.AddConstraint(
            model_name='catalogplacement',
            constraint=models.UniqueConstraint(
                condition=Q(('discipline__isnull', False), ('tag__isnull', True)),
                fields=('catalog_entry', 'category', 'discipline', 'level'),
                name='uniq_placement_entry_cat_disc_level',
            ),
        ),
        migrations.AddConstraint(
            model_name='catalogplacement',
            constraint=models.UniqueConstraint(
                condition=Q(('discipline__isnull', False), ('tag__isnull', False)),
                fields=('catalog_entry', 'category', 'discipline', 'tag', 'level'),
                name='uniq_placement_entry_cat_disc_tag_level',
            ),
        ),
    ]
