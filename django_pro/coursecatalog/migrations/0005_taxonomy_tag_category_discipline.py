# Restructure taxonomy: Tag → Category → Discipline (was Category → Discipline → label-tag).

import django.db.models.deletion
from django.db import migrations, models
from django.utils.text import slugify


def _slug(base: str, max_len: int = 100) -> str:
    s = slugify(base) or 'item'
    return s[:max_len]


def restructure_taxonomy(apps, schema_editor):
    Tag = apps.get_model('coursecatalog', 'CatalogTag')
    Category = apps.get_model('coursecatalog', 'CatalogCategory')
    Discipline = apps.get_model('coursecatalog', 'CatalogDiscipline')
    Placement = apps.get_model('coursecatalog', 'CatalogPlacement')
    LegacyCat = apps.get_model('coursecatalog', 'CatalogCategoryLegacy')
    LegacyDisc = apps.get_model('coursecatalog', 'CatalogDisciplineLegacy')
    LegacyLabel = apps.get_model('coursecatalog', 'CatalogLegacyLabel')
    LegacyPath = apps.get_model('coursecatalog', 'CatalogPathLegacy')
    Path = apps.get_model('coursecatalog', 'CatalogPath')
    PathStep = apps.get_model('coursecatalog', 'CatalogPathStep')

    tag_by_legacy_cat = {}
    for c in LegacyCat.objects.all().order_by('id'):
        tag, _ = Tag.objects.get_or_create(
            slug=c.slug,
            defaults={
                'name': c.name,
                'order': c.order,
                'is_active': c.is_active,
                'description': c.description or '',
            },
        )
        tag_by_legacy_cat[c.id] = tag

    cat_by_legacy_disc = {}
    general_cat_by_legacy_cat = {}
    general_disc_by_legacy_cat = {}

    for d in LegacyDisc.objects.all().order_by('id'):
        tag = tag_by_legacy_cat[d.category_id]
        slug = _slug(f'{tag.slug}-{d.name}')
        cat = Category.objects.create(
            tag=tag,
            name=d.name,
            slug=slug,
            order=d.order,
            is_active=d.is_active,
        )
        cat_by_legacy_disc[d.id] = cat

    for c in LegacyCat.objects.all().order_by('id'):
        if LegacyDisc.objects.filter(category_id=c.id).exists():
            continue
        tag = tag_by_legacy_cat[c.id]
        cat = Category.objects.create(
            tag=tag,
            name='General',
            slug=_slug(f'{c.slug}-general'),
            order=c.order,
            is_active=c.is_active,
        )
        general_cat_by_legacy_cat[c.id] = cat
        general_disc_by_legacy_cat[c.id] = Discipline.objects.create(
            category=cat,
            name='General',
            order=0,
            is_active=True,
        )

    disc_by_legacy_label = {}
    for label in LegacyLabel.objects.all().order_by('id'):
        if label.discipline_id and label.discipline_id in cat_by_legacy_disc:
            parent_cat = cat_by_legacy_disc[label.discipline_id]
        elif label.category_id in general_cat_by_legacy_cat:
            parent_cat = general_cat_by_legacy_cat[label.category_id]
        elif label.category_id in tag_by_legacy_cat:
            tag = tag_by_legacy_cat[label.category_id]
            parent_cat, _ = Category.objects.get_or_create(
                tag=tag,
                slug=_slug(f'{tag.slug}-misc'),
                defaults={'name': 'General', 'order': 0, 'is_active': True},
            )
        else:
            continue
        nd = Discipline.objects.create(
            category=parent_cat,
            name=label.name,
            order=label.order,
            is_active=label.is_active,
        )
        disc_by_legacy_label[label.id] = nd

    general_disc_by_legacy_disc = {}
    for d in LegacyDisc.objects.all().order_by('id'):
        cat = cat_by_legacy_disc[d.id]
        gd, _ = Discipline.objects.get_or_create(
            category=cat,
            name='General',
            defaults={'order': 0, 'is_active': True},
        )
        general_disc_by_legacy_disc[d.id] = gd

    for p in Placement.objects.all().iterator():
        lc = LegacyCat.objects.filter(pk=p.category_id).first()
        if not lc:
            continue
        tag = tag_by_legacy_cat[lc.id]
        if p.discipline_id and p.discipline_id in cat_by_legacy_disc:
            cat = cat_by_legacy_disc[p.discipline_id]
        else:
            cat = general_cat_by_legacy_cat[lc.id]
        legacy_label_id = getattr(p, 'legacy_label_id', None)
        if legacy_label_id and legacy_label_id in disc_by_legacy_label:
            disc = disc_by_legacy_label[legacy_label_id]
        elif p.discipline_id and p.discipline_id in general_disc_by_legacy_disc:
            disc = general_disc_by_legacy_disc[p.discipline_id]
        else:
            disc = general_disc_by_legacy_cat[lc.id]
        p.new_tag_id = tag.id
        p.new_category_id = cat.id
        p.new_discipline_id = disc.id
        p.save(update_fields=['new_tag_id', 'new_category_id', 'new_discipline_id'])

    legacy_to_new_path = {}
    for lp in LegacyPath.objects.all().order_by('id'):
        lc = LegacyCat.objects.filter(pk=lp.category_id).first()
        if not lc:
            continue
        tag = tag_by_legacy_cat[lc.id]
        if lp.discipline_id and lp.discipline_id in cat_by_legacy_disc:
            cat = cat_by_legacy_disc[lp.discipline_id]
            disc = general_disc_by_legacy_disc.get(lp.discipline_id)
            if not disc:
                disc, _ = Discipline.objects.get_or_create(
                    category=cat,
                    name='General',
                    defaults={'order': 0, 'is_active': True},
                )
        else:
            cat = general_cat_by_legacy_cat.get(lc.id)
            disc = general_disc_by_legacy_cat.get(lc.id)
            if not cat or not disc:
                continue
        new_path = Path.objects.create(
            tag=tag,
            category=cat,
            discipline=disc,
            name=lp.name,
            description=lp.description or '',
            is_active=lp.is_active,
        )
        legacy_to_new_path[lp.id] = new_path.id

    for step in PathStep.objects.all().iterator():
        new_path_id = legacy_to_new_path.get(step.path_id)
        if new_path_id:
            step.path_id = new_path_id
            step.save(update_fields=['path_id'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('coursecatalog', '0004_course_tags'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='CatalogTag',
            new_name='CatalogLegacyLabel',
        ),
        migrations.RemoveConstraint(
            model_name='catalogplacement',
            name='uniq_placement_entry_cat_level',
        ),
        migrations.RemoveConstraint(
            model_name='catalogplacement',
            name='uniq_placement_entry_cat_tag_level',
        ),
        migrations.RemoveConstraint(
            model_name='catalogplacement',
            name='uniq_placement_entry_cat_disc_level',
        ),
        migrations.RemoveConstraint(
            model_name='catalogplacement',
            name='uniq_placement_entry_cat_disc_tag_level',
        ),
        migrations.RenameField(
            model_name='catalogplacement',
            old_name='tag',
            new_name='legacy_label',
        ),
        migrations.CreateModel(
            name='CatalogTag',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('slug', models.SlugField(max_length=100, unique=True)),
                ('order', models.IntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('description', models.TextField(blank=True)),
            ],
            options={
                'ordering': ['order', 'name'],
            },
        ),
        migrations.RenameModel(
            old_name='CatalogCategory',
            new_name='CatalogCategoryLegacy',
        ),
        migrations.RemoveConstraint(
            model_name='catalogcategorylegacy',
            name='uniq_catalog_category_parent_name',
        ),
        migrations.RenameModel(
            old_name='CatalogDiscipline',
            new_name='CatalogDisciplineLegacy',
        ),
        migrations.RemoveConstraint(
            model_name='catalogdisciplinelegacy',
            name='uniq_catalog_discipline_category_name',
        ),
        migrations.CreateModel(
            name='CatalogCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('slug', models.SlugField(max_length=100)),
                ('order', models.IntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('description', models.TextField(blank=True)),
                (
                    'tag',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='categories',
                        to='coursecatalog.catalogtag',
                    ),
                ),
            ],
            options={
                'verbose_name_plural': 'Catalog categories',
                'ordering': ['order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='CatalogDiscipline',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('order', models.IntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                (
                    'category',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='disciplines',
                        to='coursecatalog.catalogcategory',
                    ),
                ),
            ],
            options={
                'ordering': ['order', 'name'],
            },
        ),
        migrations.AddConstraint(
            model_name='catalogcategory',
            constraint=models.UniqueConstraint(fields=('tag', 'slug'), name='uniq_catalog_category_tag_slug'),
        ),
        migrations.AddConstraint(
            model_name='catalogcategory',
            constraint=models.UniqueConstraint(fields=('tag', 'name'), name='uniq_catalog_category_tag_name'),
        ),
        migrations.AddConstraint(
            model_name='catalogdiscipline',
            constraint=models.UniqueConstraint(fields=('category', 'name'), name='uniq_catalog_discipline_category_name'),
        ),
        migrations.AddField(
            model_name='catalogplacement',
            name='new_tag',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='+',
                to='coursecatalog.catalogtag',
            ),
        ),
        migrations.AddField(
            model_name='catalogplacement',
            name='new_category',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='+',
                to='coursecatalog.catalogcategory',
            ),
        ),
        migrations.AddField(
            model_name='catalogplacement',
            name='new_discipline',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='+',
                to='coursecatalog.catalogdiscipline',
            ),
        ),
        migrations.RenameModel(
            old_name='CatalogPath',
            new_name='CatalogPathLegacy',
        ),
        migrations.RemoveConstraint(
            model_name='catalogpathlegacy',
            name='uniq_catalog_path_scope_name',
        ),
        migrations.CreateModel(
            name='CatalogPath',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                (
                    'category',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='paths',
                        to='coursecatalog.catalogcategory',
                    ),
                ),
                (
                    'discipline',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='paths',
                        to='coursecatalog.catalogdiscipline',
                    ),
                ),
                (
                    'tag',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='paths',
                        to='coursecatalog.catalogtag',
                    ),
                ),
            ],
            options={
                'ordering': ['tag__order', 'category__order', 'name'],
            },
        ),
        migrations.AddConstraint(
            model_name='catalogpath',
            constraint=models.UniqueConstraint(
                fields=('tag', 'category', 'discipline', 'name'),
                name='uniq_catalog_path_scope_name',
            ),
        ),
        migrations.RunPython(restructure_taxonomy, noop),
        migrations.AlterField(
            model_name='catalogpathstep',
            name='path',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='steps',
                to='coursecatalog.catalogpath',
            ),
        ),
        migrations.RemoveField(
            model_name='catalogplacement',
            name='category',
        ),
        migrations.RemoveField(
            model_name='catalogplacement',
            name='discipline',
        ),
        migrations.RemoveField(
            model_name='catalogplacement',
            name='legacy_label',
        ),
        migrations.RenameField(
            model_name='catalogplacement',
            old_name='new_tag',
            new_name='tag',
        ),
        migrations.RenameField(
            model_name='catalogplacement',
            old_name='new_category',
            new_name='category',
        ),
        migrations.RenameField(
            model_name='catalogplacement',
            old_name='new_discipline',
            new_name='discipline',
        ),
        migrations.AlterField(
            model_name='catalogplacement',
            name='tag',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='placements',
                to='coursecatalog.catalogtag',
            ),
        ),
        migrations.AlterField(
            model_name='catalogplacement',
            name='category',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='placements',
                to='coursecatalog.catalogcategory',
            ),
        ),
        migrations.AlterField(
            model_name='catalogplacement',
            name='discipline',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='placements',
                to='coursecatalog.catalogdiscipline',
            ),
        ),
        migrations.AddConstraint(
            model_name='catalogplacement',
            constraint=models.UniqueConstraint(
                fields=('catalog_entry', 'tag', 'category', 'discipline', 'level'),
                name='uniq_placement_entry_taxonomy_level',
            ),
        ),
        migrations.DeleteModel(
            name='CatalogLegacyLabel',
        ),
        migrations.DeleteModel(
            name='CatalogCategoryLegacy',
        ),
        migrations.DeleteModel(
            name='CatalogDisciplineLegacy',
        ),
        migrations.DeleteModel(
            name='CatalogPathLegacy',
        ),
    ]
