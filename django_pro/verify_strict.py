import os
import django
import sys
from django.test import RequestFactory
from django.template import Context, Template
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from users.models import User, Role
from users.views import get_filtered_users_queryset

def verify_strict_filter():
    # 1. Create/Find a School Admin
    try:
        admin_user = User.objects.get(username='David.Sitko')
        # Ensure role
        sa_role = Role.objects.get(name='School Admin')
        if sa_role not in admin_user.roles.all():
            admin_user.roles.add(sa_role)
    except User.DoesNotExist:
        print("David.Sitko not found. Skipping.")
        return

    # 2. Check/Create an Orphan user
    orphan, created = User.objects.get_or_create(
        username='test.orphan', 
        email='test.orphan@example.com',
        defaults={'name': 'Test Orphan', 'schools': []}
    )
    if not created:
        orphan.schools = []
        orphan.save()
        
    print(f"Orphan user: {orphan.username}, Schools: {orphan.schools}")
    
    # 3. Check a user IN the school
    valid_user = User.objects.filter(schools__contains=['6118855']).first()
    print(f"Valid user: {valid_user.username if valid_user else 'None'}")

    # 4. Simulate Request
    factory = RequestFactory()
    request = factory.get('/users/manage/')
    request.user = admin_user
    request.session = {'active_role_id': sa_role.id}
    request.user.active_role = sa_role
    request.user.active_school = None # All schools

    # 5. Run Query
    qs = get_filtered_users_queryset(request)
    
    print(f"Admin: {admin_user.username}")
    print(f"QS contains orphan? {qs.filter(id=orphan.id).exists()}")
    if valid_user:
        print(f"QS contains school user? {qs.filter(id=valid_user.id).exists()}")
    print(f"Total Count: {qs.count()}")
    
    # Clean up (optional)
    # orphan.delete()

if __name__ == '__main__':
    verify_strict_filter()
