from django.core.management.base import BaseCommand
from users.models import Role

class Command(BaseCommand):
    help = 'Initialize roles'

    def handle(self, *args, **options):
        roles = [
            'Project Admin',
            'District Admin',
            'District View',
            'School Admin',
            'School View',
            'School Gr View',
            'Teacher'
        ]
        
        for r_name in roles:
            role, created = Role.objects.get_or_create(name=r_name)
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created role: {r_name}'))
            else:
                self.stdout.write(f'Role already exists: {r_name}')
