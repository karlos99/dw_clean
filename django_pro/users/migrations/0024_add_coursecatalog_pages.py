from django.db import migrations


PAGE_DEFAULTS = [
    {
        'slug': 'coursecatalog',
        'name': 'Course Catalog',
        'description': 'Organize district course catalog by category, discipline, and level',
        'url_path': '#',
        'parent_slug': None,
        'menu_order': 160,
    },
    {
        'slug': 'coursecatalog-overview',
        'name': 'Course Catalog Overview',
        'description': 'Course catalog dashboard',
        'url_path': '/coursecatalog/',
        'parent_slug': 'coursecatalog',
        'menu_order': 10,
    },
    {
        'slug': 'coursecatalog-categories',
        'name': 'Catalog Categories',
        'description': 'Categories, disciplines, and class levels',
        'url_path': '/coursecatalog/categories/',
        'parent_slug': 'coursecatalog',
        'menu_order': 20,
    },
    {
        'slug': 'coursecatalog-placements',
        'name': 'Catalog Placements',
        'description': 'Assign catalog courses to category and level',
        'url_path': '/coursecatalog/placements/',
        'parent_slug': 'coursecatalog',
        'menu_order': 30,
    },
    {
        'slug': 'coursecatalog-paths',
        'name': 'Catalog Paths',
        'description': 'Course progression paths',
        'url_path': '/coursecatalog/paths/',
        'parent_slug': 'coursecatalog',
        'menu_order': 40,
    },
    {
        'slug': 'coursecatalog-enrollment',
        'name': 'Catalog Enrollment Preview',
        'description': 'Preview students by catalog placement',
        'url_path': '/coursecatalog/enrollment/',
        'parent_slug': 'coursecatalog',
        'menu_order': 50,
    },
]


def create_coursecatalog_pages(apps, schema_editor):
    try:
        Page = apps.get_model('users', 'Page')
    except LookupError:
        return

    parents = {}
    for spec in PAGE_DEFAULTS:
        parent = None
        if spec['parent_slug']:
            parent = parents.get(spec['parent_slug']) or Page.objects.filter(slug=spec['parent_slug']).first()
        page, _ = Page.objects.get_or_create(
            slug=spec['slug'],
            defaults={
                'name': spec['name'],
                'description': spec['description'],
                'url_path': spec['url_path'],
                'app_reference': 'coursecatalog',
                'status': 'draft',
                'required_roles': ['District Admin', 'Project Admin'],
                'is_public': False,
                'show_in_menu': False,
                'menu_order': spec['menu_order'],
                'parent_page': parent,
            },
        )
        page.name = spec['name']
        page.description = spec['description']
        page.url_path = spec['url_path']
        page.app_reference = 'coursecatalog'
        page.status = 'draft'
        page.required_roles = ['District Admin', 'Project Admin']
        page.is_public = False
        page.show_in_menu = False
        page.menu_order = spec['menu_order']
        page.parent_page = parent
        page.save(
            update_fields=[
                'name',
                'description',
                'url_path',
                'app_reference',
                'status',
                'required_roles',
                'is_public',
                'show_in_menu',
                'menu_order',
                'parent_page',
                'updated_at',
            ]
        )
        parents[spec['slug']] = page


def delete_coursecatalog_pages(apps, schema_editor):
    try:
        Page = apps.get_model('users', 'Page')
    except LookupError:
        return
    Page.objects.filter(slug__in=[p['slug'] for p in PAGE_DEFAULTS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0023_create_504_admin_role'),
        ('coursecatalog', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_coursecatalog_pages, delete_coursecatalog_pages),
    ]
