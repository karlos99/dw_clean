from django.db import migrations


def add_coordinator_pages(apps, schema_editor):
    try:
        Page = apps.get_model('users', 'Page')
    except LookupError:
        return

    # Include the Testing Coordinator in the DESI student list menu.
    student_page = Page.objects.filter(slug='desi-students').first()
    if student_page:
        roles = list(student_page.required_roles or [])
        if 'Testing Coordinator' not in roles:
            roles.append('Testing Coordinator')
        student_page.required_roles = roles
        student_page.save(update_fields=['required_roles'])

    # Coordinator DESI admin page (membership-based access is enforced by views).
    parent_page = Page.objects.filter(slug='desi').first()
    coordinator_page, _ = Page.objects.get_or_create(
        slug='desi-coordinator',
        defaults={
            'name': 'Testing Coordinator',
            'description': 'Manage teacher access and completion reports for DESI',
            'url_path': '/desi/coordinator/',
            'status': 'active',
            'required_roles': ['Testing Coordinator', 'Project Admin'],
            'is_public': False,
            'show_in_menu': True,
            'menu_order': 15,
            'parent_page': parent_page,
        },
    )
    coordinator_page.parent_page = parent_page
    coordinator_page.url_path = '/desi/coordinator/'
    coordinator_page.status = 'active'
    coordinator_page.show_in_menu = True
    coordinator_page.required_roles = ['Testing Coordinator', 'Project Admin']
    coordinator_page.save(update_fields=['parent_page', 'url_path', 'status', 'show_in_menu', 'required_roles'])


def remove_coordinator_pages(apps, schema_editor):
    try:
        Page = apps.get_model('users', 'Page')
    except LookupError:
        return
    Page.objects.filter(slug='desi-coordinator').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('desi', '0016_accommodation_tag_assessment_tag_and_more'),
        ('users', '0020_page_menu_category'),
    ]

    operations = [
        migrations.RunPython(add_coordinator_pages, remove_coordinator_pages),
    ]
