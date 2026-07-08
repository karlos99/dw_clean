from django.db import migrations


def create_desi_pages(apps, schema_editor):
    try:
        Page = apps.get_model('users', 'Page')
    except LookupError:
        # If Page model doesn't exist (e.g. during clean test DB run with migrations disabled)
        return

    # Parent page container
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

    # Sub-page 1: Student List
    student_list_page, _ = Page.objects.get_or_create(
        slug='desi-students',
        defaults={
            'name': 'Student List',
            'description': 'Assign accommodations to students',
            'url_path': '/desi/',
            'status': 'active',
            'required_roles': ['Teacher', 'School Admin', 'Project Admin'],
            'is_public': False,
            'show_in_menu': True,
            'menu_order': 10,
            'parent_page': parent_page,
        },
    )

    # Sub-page 2: Admin Dashboard
    admin_dashboard_page, _ = Page.objects.get_or_create(
        slug='desi-admin',
        defaults={
            'name': 'Admin Setup',
            'description': 'Configure assessments, criteria, and questions',
            'url_path': '/desi/admin-dashboard/',
            'status': 'active',
            'required_roles': ['Project Admin'],
            'is_public': False,
            'show_in_menu': True,
            'menu_order': 20,
            'parent_page': parent_page,
        },
    )

    # Save details if already exists to ensure correct parameters
    parent_page.status = 'active'
    parent_page.show_in_menu = True
    parent_page.url_path = '#'
    parent_page.save(update_fields=['status', 'show_in_menu', 'url_path'])

    student_list_page.parent_page = parent_page
    student_list_page.url_path = '/desi/'
    student_list_page.status = 'active'
    student_list_page.show_in_menu = True
    student_list_page.required_roles = ['Teacher', 'School Admin', 'Project Admin']
    student_list_page.save(update_fields=['parent_page', 'url_path', 'status', 'show_in_menu', 'required_roles'])

    admin_dashboard_page.parent_page = parent_page
    admin_dashboard_page.url_path = '/desi/admin-dashboard/'
    admin_dashboard_page.status = 'active'
    admin_dashboard_page.show_in_menu = True
    admin_dashboard_page.required_roles = ['Project Admin']
    admin_dashboard_page.save(update_fields=['parent_page', 'url_path', 'status', 'show_in_menu', 'required_roles'])


def delete_desi_pages(apps, schema_editor):
    try:
        Page = apps.get_model('users', 'Page')
    except LookupError:
        return
    Page.objects.filter(slug__in=['desi-students', 'desi-admin', 'desi']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('desi', '0002_prepopulate_accommodations'),
    ]

    operations = [
        migrations.RunPython(create_desi_pages, delete_desi_pages),
    ]
