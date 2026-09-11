from django.db import migrations


def create_testing_coordinator_role(apps, schema_editor):
    Role = apps.get_model('users', 'Role')
    Role.objects.get_or_create(
        name='Testing Coordinator',
        defaults={'description': 'School-scoped coordinator for the DESI designated-support application. Add-on role: works regardless of the active role.'},
    )


def remove_testing_coordinator_role(apps, schema_editor):
    Role = apps.get_model('users', 'Role')
    Role.objects.filter(name='Testing Coordinator').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0021_navbarconfig_dedupe'),
    ]

    operations = [
        migrations.RunPython(create_testing_coordinator_role, remove_testing_coordinator_role),
    ]
