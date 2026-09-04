from django.db import migrations


def rename_page(apps, schema_editor):
    try:
        Page = apps.get_model('users', 'Page')
    except LookupError:
        return
    Page.objects.filter(slug='desi-coordinator').update(
        name='Support Admin',
        description='Manage teacher access and view/export DESI reports by school',
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('desi', '0019_seed_resources_from_video_url'),
    ]

    operations = [
        migrations.RunPython(rename_page, noop),
    ]
