#!/usr/bin/env python
"""
Create superuser if it doesn't exist.
This script is run during Docker container initialization.
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'DSdata.settings_production')
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()

email = 'rage1968@gmail.com'
password = 'Hidalgo#1'
username = 'rage1968'  # Username derived from email

if not User.objects.filter(email=email).exists():
    user = User.objects.create_superuser(
        username=username,
        email=email,
        password=password,
        first_name='Admin',
        last_name='User'
    )
    # Set additional attributes if they exist
    if hasattr(user, 'is_app_admin'):
        user.is_app_admin = True
    if hasattr(user, 'is_district_access'):
        user.is_district_access = True
    user.save()
    print(f'✓ Superuser created: {email}')
else:
    print(f'✓ Superuser already exists: {email}')
