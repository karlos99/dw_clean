from django.db import migrations


LIMITED_ROLE = 'Limited Student Access'

# Pages a limited-student user needs in order to actually reach the student
# records their assignment grants. Row-level filtering is handled separately by
# User.get_accessible_student_keys().
PAGE_SLUGS = (
    'placement-cards-teacher-notes',
    'desi-students',
    'nextyearlist',
)


def add_limited_role_to_pages(apps, schema_editor):
    Page = apps.get_model('users', 'Page')
    for page in Page.objects.filter(slug__in=PAGE_SLUGS):
        roles = list(page.required_roles or [])
        if LIMITED_ROLE not in roles:
            roles.append(LIMITED_ROLE)
            page.required_roles = roles
            page.save(update_fields=['required_roles'])


def remove_limited_role_from_pages(apps, schema_editor):
    Page = apps.get_model('users', 'Page')
    for page in Page.objects.filter(slug__in=PAGE_SLUGS):
        roles = [r for r in (page.required_roles or []) if r != LIMITED_ROLE]
        page.required_roles = roles
        page.save(update_fields=['required_roles'])


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0026_role_assignment_expiration'),
    ]

    operations = [
        migrations.RunPython(
            add_limited_role_to_pages,
            remove_limited_role_from_pages,
        ),
    ]
