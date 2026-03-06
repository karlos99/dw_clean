from django.db import migrations


def create_placement_configuration_page(apps, schema_editor):
    Page = apps.get_model('users', 'Page')

    page, created = Page.objects.get_or_create(
        slug='placement-cards-configuration',
        defaults={
            'name': 'Placement Configuration',
            'app_reference': 'placementcards',
            'description': 'Configure classrooms, tags, and questions for student placement',
            'url_path': '/placementcards/configuration/',
            'status': 'active',
            'required_roles': ['School Admin', 'Project Admin'],
            'is_public': False,
            'show_in_menu': False,
            'menu_order': 30,
        },
    )

    if not created:
        page.name = 'Placement Configuration'
        page.app_reference = 'placementcards'
        page.description = 'Configure classrooms, tags, and questions for student placement'
        page.url_path = '/placementcards/configuration/'
        page.status = 'active'
        page.required_roles = ['School Admin', 'Project Admin']
        page.is_public = False
        page.show_in_menu = False
        page.menu_order = 30
        page.save(
            update_fields=[
                'name',
                'app_reference',
                'description',
                'url_path',
                'status',
                'required_roles',
                'is_public',
                'show_in_menu',
                'menu_order',
                'updated_at',
            ]
        )


def reverse_placement_configuration_page(apps, schema_editor):
    Page = apps.get_model('users', 'Page')
    Page.objects.filter(slug='placement-cards-configuration').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0013_page_app_reference_and_menu_targeting'),
    ]

    operations = [
        migrations.RunPython(create_placement_configuration_page, reverse_placement_configuration_page),
    ]
