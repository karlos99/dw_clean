from django.core.management.base import BaseCommand
from users.models_pages import Page
from django.utils.text import slugify


class Command(BaseCommand):
    help = 'Add a new page to the system'

    def add_arguments(self, parser):
        parser.add_argument('name', type=str, help='Page name')
        parser.add_argument('url_path', type=str, help='URL path (e.g., /my-page/)')
        parser.add_argument('--slug', type=str, help='URL slug (auto-generated if not provided)')
        parser.add_argument('--description', type=str, default='', help='Page description')
        parser.add_argument('--icon', type=str, default='', help='Icon class (e.g., fa-home)')
        parser.add_argument('--status', type=str, default='active', choices=['active', 'inactive', 'draft'])
        parser.add_argument('--show-in-menu', action='store_true', default=True, help='Show in navigation menu')
        parser.add_argument('--hide-from-menu', dest='show_in_menu', action='store_false', help='Hide from navigation menu')
        parser.add_argument('--public', action='store_true', help='Allow unauthenticated access')
        parser.add_argument('--menu-order', type=int, default=0, help='Menu display order')
        parser.add_argument('--roles', type=str, nargs='*', help='Required roles (space-separated)')

    def handle(self, *args, **options):
        name = options['name']
        url_path = options['url_path']
        slug = options['slug'] or slugify(name)
        
        # Check if page already exists
        if Page.objects.filter(slug=slug).exists():
            self.stdout.write(
                self.style.ERROR(f'Page with slug "{slug}" already exists!')
            )
            return
        
        # Create the page
        page = Page.objects.create(
            name=name,
            slug=slug,
            description=options['description'],
            url_path=url_path,
            icon=options['icon'],
            status=options['status'],
            is_public=options['public'],
            show_in_menu=options['show_in_menu'],
            menu_order=options['menu_order'],
            required_roles=options['roles'] or [],
        )
        
        self.stdout.write(
            self.style.SUCCESS(f'Successfully created page: {page.name}')
        )
        self.stdout.write(f'  Slug: {page.slug}')
        self.stdout.write(f'  URL: {page.url_path}')
        self.stdout.write(f'  Show in menu: {page.show_in_menu}')
        if page.required_roles:
            self.stdout.write(f'  Required roles: {", ".join(page.required_roles)}')
