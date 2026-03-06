from django.db import migrations


def create_next_sy_pages(apps, schema_editor):
    Page = apps.get_model('users', 'Page')

    next_sy_page, _ = Page.objects.get_or_create(
        slug='next-sy',
        defaults={
            'name': 'Next SY',
            'description': 'Next school year tools',
            'url_path': '#',
            'status': 'active',
            'required_roles': [],
            'is_public': False,
            'show_in_menu': True,
            'menu_order': 140,
        },
    )

    if next_sy_page.status != 'active':
        next_sy_page.status = 'active'
    if not next_sy_page.show_in_menu:
        next_sy_page.show_in_menu = True
    if next_sy_page.url_path != '#':
        next_sy_page.url_path = '#'
    next_sy_page.save(update_fields=['status', 'show_in_menu', 'url_path', 'updated_at'])

    teacher_notes_page, _ = Page.objects.get_or_create(
        slug='placement-cards-teacher-notes',
        defaults={
            'name': 'Placement Cards',
            'description': 'Placement cards teacher notes workspace',
            'url_path': '/placementcards/teacher-notes/',
            'status': 'active',
            'required_roles': ['Teacher', 'School Admin', 'Project Admin'],
            'is_public': False,
            'show_in_menu': True,
            'menu_order': 10,
            'parent_page': next_sy_page,
        },
    )

    teacher_notes_page.parent_page = next_sy_page
    teacher_notes_page.url_path = '/placementcards/teacher-notes/'
    teacher_notes_page.status = 'active'
    teacher_notes_page.show_in_menu = True
    teacher_notes_page.required_roles = ['Teacher', 'School Admin', 'Project Admin']
    teacher_notes_page.save(update_fields=['parent_page', 'url_path', 'status', 'show_in_menu', 'required_roles', 'updated_at'])

    placement_view_page, _ = Page.objects.get_or_create(
        slug='placement-cards-place-students',
        defaults={
            'name': 'Placement View',
            'description': 'Place students into next year classrooms',
            'url_path': '/placementcards/place-students/',
            'status': 'active',
            'required_roles': ['School Admin', 'Project Admin'],
            'is_public': False,
            'show_in_menu': True,
            'menu_order': 20,
            'parent_page': next_sy_page,
        },
    )

    placement_view_page.parent_page = next_sy_page
    placement_view_page.url_path = '/placementcards/place-students/'
    placement_view_page.status = 'active'
    placement_view_page.show_in_menu = True
    placement_view_page.required_roles = ['School Admin', 'Project Admin']
    placement_view_page.save(update_fields=['parent_page', 'url_path', 'status', 'show_in_menu', 'required_roles', 'updated_at'])


def reverse_next_sy_pages(apps, schema_editor):
    Page = apps.get_model('users', 'Page')
    Page.objects.filter(slug='placement-cards-place-students').delete()
    Page.objects.filter(slug='placement-cards-teacher-notes').delete()
    Page.objects.filter(slug='next-sy').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0011_rename_users_pagev_started_0e07c1_idx_users_pagev_started_72ed6c_idx_and_more'),
    ]

    operations = [
        migrations.RunPython(create_next_sy_pages, reverse_next_sy_pages),
    ]
