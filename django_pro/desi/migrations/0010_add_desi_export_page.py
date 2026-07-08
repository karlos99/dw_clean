from django.db import migrations


def add_desi_export_page(apps, schema_editor):
    try:
        Page = apps.get_model('users', 'Page')
    except LookupError:
        return

    parent_page = Page.objects.filter(slug='desi').first()
    if not parent_page:
        parent_page, _ = Page.objects.get_or_create(
            slug='desi',
            defaults={
                'name': 'DESI',
                'description': 'Student assessment accommodation tools',
                'url_path': '#',
                'status': 'active',
                'required_roles': [],
                'is_public': False,
                'show_in_menu': True,
                'menu_order': 150,
            },
        )

    export_page, _ = Page.objects.get_or_create(
        slug='desi-export',
        defaults={
            'name': 'Export Data',
            'description': 'Export DESI student/support configuration data',
            'url_path': '/desi/admin-dashboard/export/',
            'status': 'active',
            'required_roles': ['Project Admin'],
            'is_public': False,
            'show_in_menu': True,
            'menu_order': 30,
            'parent_page': parent_page,
        },
    )

    export_page.parent_page = parent_page
    export_page.url_path = '/desi/admin-dashboard/export/'
    export_page.status = 'active'
    export_page.show_in_menu = True
    export_page.required_roles = ['Project Admin']
    export_page.menu_order = 30
    export_page.save(update_fields=['parent_page', 'url_path', 'status', 'show_in_menu', 'required_roles', 'menu_order'])


def remove_desi_export_page(apps, schema_editor):
    try:
        Page = apps.get_model('users', 'Page')
    except LookupError:
        return
    Page.objects.filter(slug='desi-export').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('desi', '0009_accommodation_dependency_assessments'),
    ]

    operations = [
        migrations.RunPython(add_desi_export_page, remove_desi_export_page),
    ]
