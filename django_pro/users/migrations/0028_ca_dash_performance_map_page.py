from django.db import migrations


def create_ca_dash_page(apps, schema_editor):
    Page = apps.get_model('users', 'Page')

    page, created = Page.objects.get_or_create(
        slug='ca-dash-performance-map',
        defaults={
            'name': 'CA Dashboard Map',
            'app_reference': 'ca_dash',
            'description': 'CDE Dashboard 5×5 Performance Map for district and school indicators',
            'url_path': '/ca_dash/',
            'status': 'active',
            'required_roles': [],
            'is_public': False,
            'show_in_menu': True,
            'menu_order': 45,
        },
    )
    if not created:
        page.name = 'CA Dashboard Map'
        page.app_reference = 'ca_dash'
        page.url_path = '/ca_dash/'
        page.status = 'active'
        page.required_roles = []
        page.show_in_menu = True
        page.save(
            update_fields=[
                'name',
                'app_reference',
                'url_path',
                'status',
                'required_roles',
                'show_in_menu',
                'updated_at',
            ]
        )


def reverse_ca_dash_page(apps, schema_editor):
    Page = apps.get_model('users', 'Page')
    Page.objects.filter(slug='ca-dash-performance-map').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0027_limited_student_page_access'),
    ]

    operations = [
        migrations.RunPython(create_ca_dash_page, reverse_ca_dash_page),
    ]