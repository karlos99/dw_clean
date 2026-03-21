from django.core.management.base import BaseCommand
from users.models_pages import Page


class Command(BaseCommand):
    help = 'Initialize sample pages'

    def handle(self, *args, **options):
        pages = [
            {
                'name': 'Home',
                'slug': 'home',
                'description': 'Dashboard home page',
                'url_path': '/',
                'icon': 'fa-home',
                'status': 'active',
                'show_in_menu': True,
                'menu_order': 1,
                'is_public': False,
            },
            {
                'name': 'Data Exploration',
                'slug': 'data-exploration',
                'description': 'Student data exploration and analysis',
                'url_path': '/dataexploration/student-tests/',
                'icon': 'fa-chart-bar',
                'status': 'active',
                'show_in_menu': True,
                'menu_order': 2,
                'is_public': False,
            },
            {
                'name': 'User Management',
                'slug': 'user-management',
                'description': 'Manage system users and permissions',
                'url_path': '/users/manage/',
                'icon': 'fa-users',
                'status': 'active',
                'show_in_menu': True,
                'menu_order': 10,
                'is_public': False,
                'required_roles': ['Project Admin', 'School Admin'],
            },
            {
                'name': 'Page Management',
                'slug': 'page-management',
                'description': 'Manage system pages and views',
                'url_path': '/users/pages/',
                'icon': 'fa-file-alt',
                'status': 'active',
                'show_in_menu': True,
                'menu_order': 11,
                'is_public': False,
                'required_roles': ['Project Admin'],
            },
        ]
        
        for page_data in pages:
            page, created = Page.objects.get_or_create(
                slug=page_data['slug'],
                defaults=page_data
            )
            if created:
                self.stdout.write(
                    self.style.SUCCESS(f'Created page: {page.name}')
                )
            else:
                self.stdout.write(
                    self.style.WARNING(f'Page already exists: {page.name}')
                )
        
        self.stdout.write(
            self.style.SUCCESS('Successfully initialized pages!')
        )
