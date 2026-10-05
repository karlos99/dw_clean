from django.db import migrations


def create_504_admin_role(apps, schema_editor):
    Role = apps.get_model('users', 'Role')
    Role.objects.get_or_create(
        name='504_Admin',
        defaults={
            'description': (
                'School-scoped role that may edit DESI supports for students with a '
                '504 plan (in_504 = Y). Add-on role: works regardless of the active role.'
            ),
        },
    )


def remove_504_admin_role(apps, schema_editor):
    Role = apps.get_model('users', 'Role')
    Role.objects.filter(name='504_Admin').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0022_create_testing_coordinator_role'),
    ]

    operations = [
        migrations.RunPython(create_504_admin_role, remove_504_admin_role),
    ]
