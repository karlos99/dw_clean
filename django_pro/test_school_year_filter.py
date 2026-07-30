import json, os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'DSdata.settings_production')
django.setup()
from django.conf import settings
settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']
from django.test import Client
from django.contrib.auth import get_user_model
User = get_user_model()
u = User.objects.filter(email='rage1968@gmail.com').first()
print("user:", u, getattr(u, 'is_superuser', None))
c = Client()
c.force_login(u)

r = c.get('/dataexploration/api/students/', {'format': 'json', 'school_year': '', 'columns': json.dumps(['student_name','ssid','grade','school_year','status'])})
print("status all years:", r.status_code)
data = r.json()
print("filter_options keys (all years):", list(data.get('filter_options', {}).keys()))
print("school_year options (all years):", data.get('filter_options', {}).get('school_year'))

r2 = c.get('/dataexploration/api/students/', {'format': 'json', 'school_year': '2025-2026', 'columns': json.dumps(['student_name','ssid','grade','school_year','status'])})
print("status specific year:", r2.status_code)
data2 = r2.json()
print("filter_options keys (specific year):", list(data2.get('filter_options', {}).keys()))
print("school_year options (specific year):", data2.get('filter_options', {}).get('school_year'))
