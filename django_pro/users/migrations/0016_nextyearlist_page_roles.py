from django.db import migrations


def set_nextyearlist_roles(apps, schema_editor):
    Page = apps.get_model('users', 'Page')
    Page.objects.filter(slug='nextyearlist').update(
        required_roles=['School View', 'School Admin', 'District Admin', 'Project Admin'],
    )


def reverse_nextyearlist_roles(apps, schema_editor):
    Page = apps.get_model('users', 'Page')
    Page.objects.filter(slug='nextyearlist').update(required_roles=[])


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0015_add_nextyearlist_page'),
    ]

    operations = [
        migrations.RunPython(set_nextyearlist_roles, reverse_nextyearlist_roles),
    ]
