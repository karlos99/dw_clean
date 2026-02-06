from django.core.management.base import BaseCommand
from dataexploration.models import ReportFolder
from studata.models import School as StudataSchool
from users.models import School as UserSchool

class Command(BaseCommand):
    help = 'Initialize hierarchical school folders (Schools -> School Name)'

    def handle(self, *args, **options):
        # 1. Create or get "Schools" root folder
        schools_root, created = ReportFolder.objects.get_or_create(
            name='Schools',
            folder_type='DISTRICT',
            defaults={
                'description': 'Container for all school folders',
                'is_public_to_scope': True
            }
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'Created Schools root folder: {schools_root}'))
        else:
            self.stdout.write(f'Using existing Schools root folder: {schools_root}')

        # 2. Iterate Studata Schools
        studata_schools = StudataSchool.objects.all()
        
        for s_school in studata_schools:
            if '@err' in s_school.school_name:
                self.stdout.write(self.style.WARNING(f'Skipping {s_school.school_name} (found @err)'))
                continue
            
            # Use short_name if available, else school_name
            folder_name = s_school.short_name if s_school.short_name else s_school.school_name
            
            # Find matching UserSchool (needed for permissions)
            user_school, created_school = UserSchool.objects.get_or_create(
                name=s_school.school_name
            )
            if created_school:
                self.stdout.write(f'Created users.School for {s_school.school_name}')

            # Create/Update the school folder inside "Schools"
            # we use get_or_create to avoid duplicates if run multiple times
            school_folder, created = ReportFolder.objects.get_or_create(
                name=folder_name,
                parent=schools_root,
                defaults={
                    'folder_type': 'SCHOOL',
                    'school': user_school,
                    'description': f'Reports for {folder_name}',
                    'is_public_to_scope': True
                }
            )
            
            # Use defaults only works for creation. We might want to ensure properties are set if it exists?
            # The prompt says "creation ... will only hapenn once". So likely defaults is fine.
            # But let's ensuring parent is correct if it already existed elsewhere?
            if not created:
                if school_folder.parent != schools_root:
                    school_folder.parent = schools_root
                    school_folder.save()
                    self.stdout.write(f'Moved {school_folder} to Schools folder')

                if school_folder.folder_type != 'SCHOOL':
                    school_folder.folder_type = 'SCHOOL'
                    school_folder.save()
            
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created folder for {folder_name}'))
            else:
                 pass # Found existing

        self.stdout.write(self.style.SUCCESS('School hierarchy initialization complete.'))
