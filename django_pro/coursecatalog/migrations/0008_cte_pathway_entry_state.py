# Unmanaged map to coursecatalog_cte_pathways (ETL-owned).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('coursecatalog', '0007_placement_optional_taxonomy_active_levels'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='CtePathwayEntry',
                    fields=[
                        ('pathway_row_key', models.TextField(primary_key=True, serialize=False)),
                        ('cte_program_key', models.TextField(blank=True, null=True)),
                        ('cte_pathway', models.TextField(blank=True, null=True)),
                        ('pathway_code', models.TextField(blank=True, null=True)),
                        ('pathway_state_code', models.TextField(blank=True, null=True)),
                        ('career_cluster_code', models.TextField(blank=True, null=True)),
                        ('career_cluster', models.TextField(blank=True, null=True)),
                        ('course_key', models.TextField(blank=True, null=True)),
                        ('course_id', models.TextField(blank=True, null=True)),
                        ('course_title', models.TextField(blank=True, null=True)),
                        ('state_course_code', models.TextField(blank=True, null=True)),
                        ('cte_course_level', models.TextField(blank=True, null=True)),
                    ],
                    options={
                        'db_table': 'coursecatalog_cte_pathways',
                        'managed': False,
                        'verbose_name': 'CTE pathway row',
                        'verbose_name_plural': 'CTE pathway rows',
                    },
                ),
            ],
            database_operations=[],
        ),
    ]
