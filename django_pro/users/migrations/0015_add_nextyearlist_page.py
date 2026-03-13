from django.db import migrations


def create_nextyearlist_page(apps, schema_editor):
    Page = apps.get_model('users', 'Page')

    # Fetch the "Next School Year" parent page.
    try:
        next_sy_page = Page.objects.get(slug='next-sy')
    except Page.DoesNotExist:
        next_sy_page = None

    page, created = Page.objects.get_or_create(
        slug='nextyearlist',
        defaults={
            'name': 'Next Year Student List',
            'app_reference': 'nextyearlist',
            'description': 'View and filter students placed for next school year',
            'url_path': '/nextyearlist/',
            'status': 'active',
            'required_roles': [],
            'is_public': False,
            'show_in_menu': True,
            'menu_order': 5,
            'parent_page': next_sy_page,
        },
    )

    if not created:
        page.name = 'Next Year Student List'
        page.app_reference = 'nextyearlist'
        page.description = 'View and filter students placed for next school year'
        page.url_path = '/nextyearlist/'
        page.status = 'active'
        page.required_roles = []
        page.is_public = False
        page.show_in_menu = True
        page.menu_order = 5
        if next_sy_page:
            page.parent_page = next_sy_page
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
                'parent_page',
                'updated_at',
            ]
        )


def reverse_nextyearlist_page(apps, schema_editor):
    Page = apps.get_model('users', 'Page')
    Page.objects.filter(slug='nextyearlist').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0014_add_placement_configuration_page'),
    ]

    operations = [
        migrations.RunPython(create_nextyearlist_page, reverse_nextyearlist_page),
    ]
