"""Enable the student_pathways table in the Dataset Builder.

The student pathway extract is maintained outside this Django project but lives
in the same PostgreSQL database.  Adding it to the persisted allowlist makes it
selectable when authoring a Sankey pathway dataset without requiring an admin to
change Project Settings by hand.
"""
from django.db import migrations


TABLE = 'student_pathways'


def enable_table(apps, schema_editor):
    ProjectConfig = apps.get_model('landing', 'ProjectConfig')
    cfg = ProjectConfig.objects.filter(pk=1).first()
    if cfg is None:
        return
    enabled = cfg.enabled_dataset_tables
    # ``None`` means "all whitelisted tables" — nothing to do.
    if enabled is None:
        return
    if TABLE in enabled:
        return
    cfg.enabled_dataset_tables = list(enabled) + [TABLE]
    cfg.save(update_fields=['enabled_dataset_tables'])


def disable_table(apps, schema_editor):
    ProjectConfig = apps.get_model('landing', 'ProjectConfig')
    cfg = ProjectConfig.objects.filter(pk=1).first()
    if cfg is None or cfg.enabled_dataset_tables is None:
        return
    cfg.enabled_dataset_tables = [t for t in cfg.enabled_dataset_tables if t != TABLE]
    cfg.save(update_fields=['enabled_dataset_tables'])


class Migration(migrations.Migration):

    dependencies = [
        ('landing', '0017_alter_projectconfig_summary_access_rules'),
    ]

    operations = [
        migrations.RunPython(enable_table, disable_table),
    ]
