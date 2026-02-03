import os
import django
import sys
from django.db.models import Q

# Setup
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from users.models import User, Role
from studata.models import School as StudataSchool
from users.views import get_base_users_queryset, apply_filters
from django.test import RequestFactory
from django.contrib.auth.models import AnonymousUser

def debug_facets_v2():
    print("--- Testing Faceted Logic ---")
    
    # Setup request factory
    factory = RequestFactory()
    
    # Simulate Admin User
    admin = User.objects.filter(is_superuser=True).first()
    if not admin:
        print("No superuser found.")
        return

    # Scenario: School '6118855' Selected (Assume it exists and has users)
    # Check if school 6118855 has distinct job titles vs global
    
    school_filter = '6118855'
    
    print(f"\nScenario: School '{school_filter}' Selected")
    request = factory.get('/users/manage/', {'school': school_filter})
    request.user = admin
    
    # We can't easily run the full 'manage_users' view and inspect context variables 
    # without mocking render or using client, but we can replicate the logic.
    
    base_qs = get_base_users_queryset(request)
    
    # 1. Job Titles (Should be filtered by school)
    qs_for_jobs = apply_filters(base_qs, '', '', school_filter, '')
    titles = list(qs_for_jobs.exclude(job_title='').values_list('job_title', flat=True).distinct())
    print(f"Job Titles in School {school_filter}: {len(titles)}")
    print(f"Sample: {titles[:5]}")
    
    # 2. Global Job Titles (No school filter)
    qs_global = apply_filters(base_qs, '', '', '', '')
    titles_global = list(qs_global.exclude(job_title='').values_list('job_title', flat=True).distinct())
    print(f"Job Titles Global: {len(titles_global)}")
    
    if len(titles) < len(titles_global):
        print("SUCCESS: Job titles filtered dynamically.")
    else:
        print("WARNING: Job titles count same (might be coincidence or all users in one school).")

    # 3. Roles
    qs_for_roles = apply_filters(base_qs, '', '', school_filter, '')
    roles = list(Role.objects.filter(users__in=qs_for_roles).distinct().values_list('name', flat=True))
    print(f"Roles in School {school_filter}: {roles}")

if __name__ == '__main__':
    debug_facets_v2()
