from django.core.management.base import BaseCommand
from dataexploration.models import ReportFolder
from users.models import School

class Command(BaseCommand):
    help = 'Initialize default folders for District and Schools'

    def handle(self, *args, **options):
        # District Folder
        district_folder, created = ReportFolder.objects.get_or_create(
            folder_type='DISTRICT',
            defaults={
                'name': 'District Reports',
                'description': 'Reports shared with the entire district',
                'is_public_to_scope': True
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'Created District Folder: {district_folder}'))
        else:
            self.stdout.write(f'District Folder exists: {district_folder}')

        # School Folders
        schools = School.objects.all()
        for school in schools:
            school_folder, created = ReportFolder.objects.get_or_create(
                folder_type='SCHOOL',
                school=school,
                defaults={
                    'name': f'{school.name} Reports',
                    'description': f'Reports shared with {school.name}',
                    'is_public_to_scope': True
                }
            )
            
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created School Folder: {school_folder}'))
        
        self.stdout.write(self.style.SUCCESS('Folder initialization complete.'))
