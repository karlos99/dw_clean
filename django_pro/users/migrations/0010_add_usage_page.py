from django.db import migrations


def create_usage_page(apps, schema_editor):
    Page = apps.get_model('users', 'Page')
    usage_page, created = Page.objects.get_or_create(
        slug='usage',
        defaults={
            'name': 'Usage',
            'description': 'Application usage monitoring',
            'url_path': '/users/usage/',
            'status': 'active',
            'required_roles': ['Project Admin'],
            'is_public': False,
            'show_in_menu': False,
            'menu_order': 999,
        },
    )

    if not created:
        updated = False
        if usage_page.url_path != '/users/usage/':
            usage_page.url_path = '/users/usage/'
            updated = True
        if usage_page.name != 'Usage':
            usage_page.name = 'Usage'
            updated = True
        if usage_page.status != 'active':
            usage_page.status = 'active'
            updated = True
        if usage_page.required_roles is None or usage_page.required_roles == []:
            usage_page.required_roles = ['Project Admin']
            updated = True
        if usage_page.show_in_menu:
            usage_page.show_in_menu = False
            updated = True
        if updated:
            usage_page.save()


def reverse_usage_page(apps, schema_editor):
    Page = apps.get_model('users', 'Page')
    Page.objects.filter(slug='usage').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0009_merge_0004_usage_tracking_0008_page_allowed_users'),
    ]

    operations = [
        migrations.RunPython(create_usage_page, reverse_usage_page),
    ]
