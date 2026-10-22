from django.db import migrations


SCHOOL_VIEW_ROLE = 'School View'


def add_school_view_access(apps, schema_editor):
    """Expose the DESI student list to School View users (read-only in the views)."""
    try:
        Page = apps.get_model('users', 'Page')
    except LookupError:
        return

    page = Page.objects.filter(slug='desi-students').first()
    if page is None:
        return

    roles = list(page.required_roles or [])
    if SCHOOL_VIEW_ROLE not in roles:
        roles.append(SCHOOL_VIEW_ROLE)
        page.required_roles = roles
        page.save(update_fields=['required_roles'])


def remove_school_view_access(apps, schema_editor):
    try:
        Page = apps.get_model('users', 'Page')
    except LookupError:
        return

    page = Page.objects.filter(slug='desi-students').first()
    if page is None:
        return

    roles = [role for role in (page.required_roles or []) if role != SCHOOL_VIEW_ROLE]
    page.required_roles = roles
    page.save(update_fields=['required_roles'])


class Migration(migrations.Migration):

    dependencies = [
        ('desi', '0034_alter_supportdefaultrulecriteria_result_operator'),
        ('users', '0024_add_coursecatalog_pages'),
    ]

    operations = [
        migrations.RunPython(add_school_view_access, remove_school_view_access),
    ]
