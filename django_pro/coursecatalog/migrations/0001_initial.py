# Generated manually for coursecatalog Layer B models.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='CourseCatalogEntry',
                    fields=[
                        ('course_key', models.TextField(primary_key=True, serialize=False)),
                        ('course_id', models.TextField(blank=True, null=True)),
                        ('state_course_code', models.TextField(blank=True, null=True)),
                        ('course_title', models.TextField(blank=True, null=True)),
                        ('state_course_name', models.TextField(blank=True, null=True)),
                        ('course_level', models.TextField(blank=True, null=True)),
                        ('credit', models.TextField(blank=True, null=True)),
                        ('subject_area_1_desc', models.TextField(blank=True, null=True)),
                        ('subject_area_2_desc', models.TextField(blank=True, null=True)),
                        ('university_area_1', models.TextField(blank=True, null=True)),
                        ('university_area_2', models.TextField(blank=True, null=True)),
                        ('college_area_1', models.TextField(blank=True, null=True)),
                        ('college_area_2', models.TextField(blank=True, null=True)),
                        ('is_cte', models.TextField(blank=True, null=True)),
                        ('cte_course_level', models.TextField(blank=True, null=True)),
                        ('ap_indicator', models.TextField(blank=True, null=True)),
                        ('ib_indicator', models.TextField(blank=True, null=True)),
                        ('dual_credit', models.TextField(blank=True, null=True)),
                        ('technical_course', models.TextField(blank=True, null=True)),
                        ('status', models.TextField(blank=True, null=True)),
                    ],
                    options={
                        'db_table': 'courses_catalog',
                        'managed': False,
                    },
                ),
            ],
            database_operations=[],
        ),
        migrations.CreateModel(
            name='CatalogCategory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('slug', models.SlugField(max_length=100, unique=True)),
                ('order', models.IntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('uses_discipline', models.BooleanField(default=False, help_text='When enabled, courses are organized category → discipline → level.')),
                ('description', models.TextField(blank=True)),
                ('parent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='children', to='coursecatalog.catalogcategory')),
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
                ('category', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='disciplines', to='coursecatalog.catalogcategory')),
            ],
            options={
                'ordering': ['order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='CatalogLevel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('code', models.CharField(blank=True, max_length=20)),
                ('order', models.IntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('category', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='levels', to='coursecatalog.catalogcategory')),
                ('discipline', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='levels', to='coursecatalog.catalogdiscipline')),
            ],
            options={
                'ordering': ['order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='CatalogPath',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('category', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='paths', to='coursecatalog.catalogcategory')),
                ('discipline', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='paths', to='coursecatalog.catalogdiscipline')),
            ],
            options={
                'ordering': ['category__order', 'name'],
            },
        ),
        migrations.AddConstraint(
            model_name='catalogcategory',
            constraint=models.UniqueConstraint(fields=('parent', 'name'), name='uniq_catalog_category_parent_name'),
        ),
        migrations.AddConstraint(
            model_name='catalogdiscipline',
            constraint=models.UniqueConstraint(fields=('category', 'name'), name='uniq_catalog_discipline_category_name'),
        ),
        migrations.AddConstraint(
            model_name='cataloglevel',
            constraint=models.UniqueConstraint(condition=models.Q(('discipline__isnull', True)), fields=('category', 'name'), name='uniq_catalog_level_category_name_no_disc'),
        ),
        migrations.AddConstraint(
            model_name='cataloglevel',
            constraint=models.UniqueConstraint(condition=models.Q(('discipline__isnull', False)), fields=('discipline', 'name'), name='uniq_catalog_level_discipline_name'),
        ),
        migrations.CreateModel(
            name='CatalogPlacement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('notes', models.TextField(blank=True)),
                ('effective_from', models.CharField(blank=True, max_length=20)),
                ('effective_to', models.CharField(blank=True, max_length=20)),
                ('catalog_entry', models.ForeignKey(db_constraint=False, on_delete=django.db.models.deletion.CASCADE, related_name='placements', to='coursecatalog.coursecatalogentry')),
                ('level', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='placements', to='coursecatalog.cataloglevel')),
            ],
        ),
        migrations.CreateModel(
            name='CatalogPathStep',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sequence', models.PositiveIntegerField(default=1)),
                ('label', models.CharField(blank=True, max_length=255)),
                ('catalog_entry', models.ForeignKey(db_constraint=False, on_delete=django.db.models.deletion.CASCADE, related_name='path_steps', to='coursecatalog.coursecatalogentry')),
                ('path', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='steps', to='coursecatalog.catalogpath')),
            ],
            options={
                'ordering': ['sequence'],
            },
        ),
        migrations.AddConstraint(
            model_name='catalogpath',
            constraint=models.UniqueConstraint(fields=('category', 'discipline', 'name'), name='uniq_catalog_path_scope_name'),
        ),
        migrations.AddConstraint(
            model_name='catalogplacement',
            constraint=models.UniqueConstraint(fields=('catalog_entry', 'level'), name='uniq_catalog_placement_entry_level'),
        ),
        migrations.AddConstraint(
            model_name='catalogpathstep',
            constraint=models.UniqueConstraint(fields=('path', 'sequence'), name='uniq_catalog_path_step_sequence'),
        ),
        migrations.AddConstraint(
            model_name='catalogpathstep',
            constraint=models.UniqueConstraint(fields=('path', 'catalog_entry'), name='uniq_catalog_path_step_entry'),
        ),
    ]
