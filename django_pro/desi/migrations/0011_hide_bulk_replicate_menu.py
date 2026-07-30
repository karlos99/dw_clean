from django.db import migrations
from django.db.models import Q


def hide_bulk_replicate_menu_link(apps, schema_editor):
    try:
        Page = apps.get_model('users', 'Page')
    except LookupError:
        return

    desi_parent = Page.objects.filter(slug='desi').first()
    if not desi_parent:
        return

    candidates = Page.objects.filter(parent_page=desi_parent).filter(
        Q(slug__icontains='bulk') |
        Q(slug__icontains='replicate') |
        Q(name__icontains='bulk') |
        Q(name__icontains='replicate') |
        Q(url_path__icontains='bulk') |
        Q(url_path__icontains='replicate')
    )
    for page in candidates:
        page.show_in_menu = False
        page.save(update_fields=['show_in_menu'])


def unhide_bulk_replicate_menu_link(apps, schema_editor):
    try:
        Page = apps.get_model('users', 'Page')
    except LookupError:
        return

    desi_parent = Page.objects.filter(slug='desi').first()
    if not desi_parent:
        return

    candidates = Page.objects.filter(parent_page=desi_parent).filter(
        Q(slug__icontains='bulk') |
        Q(slug__icontains='replicate') |
        Q(name__icontains='bulk') |
        Q(name__icontains='replicate') |
        Q(url_path__icontains='bulk') |
        Q(url_path__icontains='replicate')
    )
    for page in candidates:
        page.show_in_menu = True
        page.save(update_fields=['show_in_menu'])


class Migration(migrations.Migration):

    dependencies = [
        ('desi', '0010_add_desi_export_page'),
    ]

    operations = [
        migrations.RunPython(hide_bulk_replicate_menu_link, unhide_bulk_replicate_menu_link),
    ]
