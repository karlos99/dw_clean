#!/usr/bin/env python
"""Create the College Pathway DataViz datasets, charts, and page.

The source is the external ``student_pathways`` extract.  The path column
``school_and_college_history`` is a JSON document containing the elementary,
grade 8, high school, and college trajectory for each student.

This creates/refreshes:

  * ``Student Pathways`` dataset (visual, one row per student, DISTINCT off)
  * ``Student Pathway Colleges`` dataset (SQL, every college + school/cohort
    columns for page filters)
  * ``Student Pathways — Elementary to College`` Sankey chart
  * ``All Colleges — Student Pathway`` summary table
  * ``College Pathway`` DataViz page with school/cohort filters and both charts

Run through Compose so the Django environment is available:

    docker compose exec django python /app/scripts/create_college_pathway_dataviz.py

Optional flags:

    --owner you@example.com   own the created records
    --draft                   leave the page as a draft (default: publish)
    --no-menu                 do not attach the page to College & Career Readiness

The script is idempotent: re-running refreshes the same datasets, charts, and
page.
"""
import argparse
import os
import sys
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Django bootstrap (must happen before importing any models)
# ─────────────────────────────────────────────────────────────────────────────

def bootstrap_django() -> str:
    """Locate the Django project, add it to sys.path, and django.setup()."""
    project_dir = os.environ.get('DJANGO_PROJECT_DIR', '').strip()
    if not project_dir or not (Path(project_dir) / 'manage.py').exists():
        start_dirs = [Path(__file__).resolve().parent, Path.cwd()]
        for start in start_dirs:
            for parent in [start, *start.parents]:
                if (parent / 'manage.py').exists():
                    project_dir = str(parent)
                    break
                if (parent / 'django_pro' / 'manage.py').exists():
                    project_dir = str(parent / 'django_pro')
                    break
            if project_dir:
                break
    if not project_dir:
        raise SystemExit(
            'Could not locate the Django project. Set DJANGO_PROJECT_DIR to the '
            'directory containing manage.py (e.g. /app/django_pro).'
        )

    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

    import django  # noqa: WPS433 (import after sys.path is set up)

    django.setup()
    return project_dir


# ─────────────────────────────────────────────────────────────────────────────
# Specs
# ─────────────────────────────────────────────────────────────────────────────

PATHWAY_DATASET = 'Student Pathways'
COLLEGES_DATASET = 'Student Pathway Colleges'
SANKEY_CHART = 'Student Pathways — Elementary to College'
COLLEGES_CHART = 'All Colleges — Student Pathway'
PAGE_TITLE = 'College Pathway'
PAGE_SLUG = 'college-pathway'
PAGE_FOLDER = 'College & Career Readiness'
MENU_PARENT_SLUG = 'college-career-readiness'
MENU_ORDER = 57

# Bare column names for the visual dataset. ``upsert_dataset`` prefixes them
# with ``student_pathways.``.
PATHWAY_COLUMNS = [
    'school_and_college_history',
    'first_post_hs_college_name',
    'first_post_hs_college_type',
    'first_post_hs_college_control',
    'first_post_hs_college_state',
    'latest_college_name',
    'latest_college_type',
    'latest_college_control',
    'latest_college_state',
    'grade_5_school',
    'grade_8_school',
    'high_school_name',
    'cohort',
]

COLLEGES_COLUMNS = [
    'college',
    'college_sequence',
    'grade_5_school',
    'grade_8_school',
    'high_school_name',
    'cohort',
    'students',
]

PAGE_DESCRIPTION = (
    'Student flow from elementary school through grade 8 and high school '
    'into the college trajectory. Filter by elementary, grade 8, or high '
    'school and cohort year at the top. Click any college node to trace the '
    'elementary, grade 8, and high schools that feed it. Click an Other node '
    'to expand its grouped colleges directly in the chart.'
)

PAGE_FILTERS = [
    {
        'id': 'elem_school',
        'label': 'Elementary School',
        'source_table': 'student_pathways',
        'source_col': 'grade_5_school',
        'widget': 'multi',
        'default_val': '',
    },
    {
        'id': 'grade8_school',
        'label': 'Grade 8 School',
        'source_table': 'student_pathways',
        'source_col': 'grade_8_school',
        'widget': 'multi',
        'default_val': '',
    },
    {
        'id': 'high_school',
        'label': 'High School',
        'source_table': 'student_pathways',
        'source_col': 'high_school_name',
        'widget': 'multi',
        'default_val': '',
    },
    {
        'id': 'cohort',
        'label': 'Cohort Year',
        'source_table': 'student_pathways',
        'source_col': 'cohort',
        'widget': 'multi',
        'default_val': '',
    },
]

PAGE_SPEC = {
    'slug': PAGE_SLUG,
    'title': PAGE_TITLE,
    'description': PAGE_DESCRIPTION,
    'folder': PAGE_FOLDER,
    'page_filters': PAGE_FILTERS,
    'rows': [
        {
            'label': '',
            'sections': [
                {'chart': SANKEY_CHART, 'gs_w': 12, 'gs_h': 12},
            ],
        },
        {
            'label': 'All Colleges',
            'sections': [
                {'chart': COLLEGES_CHART, 'gs_w': 12, 'gs_h': 10},
            ],
        },
    ],
}


def dataset_specs():
    """Dataset definitions, importing the shared SQL from the seed command."""
    from dataviz.management.commands.seed_student_pathways import ALL_COLLEGES_SQL

    return [
        {
            'name': PATHWAY_DATASET,
            'source_type': 'visual',
            'base_table': 'student_pathways',
            'columns': PATHWAY_COLUMNS,
            # One row per student: duplicate pathway strings must still count.
            'distinct': False,
            'description': (
                'Student pathway extract: elementary, grade 8, high school and '
                'the college trajectory stored in school_and_college_history.'
            ),
        },
        {
            'name': COLLEGES_DATASET,
            'source_type': 'sql',
            'base_table': 'student_pathways',
            'columns': COLLEGES_COLUMNS,
            'sql': ALL_COLLEGES_SQL,
            'description': (
                'Every college in school_and_college_history, with student '
                'counts grouped by college sequence and school/cohort columns '
                'for page filters.'
            ),
        },
    ]


def chart_specs():
    """Chart definitions, reusing the shared seed configs."""
    from dataviz.management.commands.seed_student_pathways import (
        ALL_COLLEGES_CONFIG, SANKEY_CONFIG,
    )

    return [
        {
            'name': SANKEY_CHART,
            'chart_type': 'sankey',
            'dataset': PATHWAY_DATASET,
            'config': dict(SANKEY_CONFIG),
            'column_defs': {},
            'drill_through_columns': [],
            'drill_through_filter_map': {},
            'description': (
                'Elementary → Grade 8 → High School → College trajectory. '
                'Click a college to trace upstream; click Other to expand it.'
            ),
        },
        {
            'name': COLLEGES_CHART,
            'chart_type': 'summary_table',
            'dataset': COLLEGES_DATASET,
            'config': dict(ALL_COLLEGES_CONFIG),
            'column_defs': {
                'college': 'College',
                'college_sequence': 'College #',
                'students': 'Students',
            },
            'drill_through_columns': COLLEGES_COLUMNS,
            'drill_through_filter_map': {},
            'description': (
                'Every college in the student pathway, with counts by college '
                'sequence (1 = first college after high school).'
            ),
        },
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def resolve_owner(owner_ref):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    if owner_ref:
        user = (
            User.objects.filter(username=owner_ref).first()
            or User.objects.filter(email__iexact=owner_ref).first()
        )
        if not user:
            raise SystemExit(f'No user found for "{owner_ref}".')
        return user

    user = User.objects.filter(roles__name='Project Admin').order_by('id').first()
    if not user:
        user = User.objects.filter(is_superuser=True).order_by('id').first()
    if not user:
        user = User.objects.order_by('id').first()
    if not user:
        raise SystemExit('No users exist to own the College Pathway page.')
    return user


def enable_dataset_tables():
    """Add student_pathways to the dataset-builder allowlist if it is restricted."""
    from landing.models import ProjectConfig

    cfg = ProjectConfig.get()
    enabled = list(cfg.enabled_dataset_tables or [])
    if not enabled:
        print('enabled_dataset_tables is unrestricted; nothing to add.')
        return
    if 'student_pathways' in enabled:
        return
    cfg.enabled_dataset_tables = enabled + ['student_pathways']
    cfg.save()
    print('Enabled dataset table: student_pathways')


def upsert_dataset(spec, owner):
    from dataviz.models import Dataset

    dataset = (
        Dataset.objects.filter(name=spec['name'], owner=owner)
        .order_by('id')
        .first()
    )
    if dataset is None:
        dataset = Dataset(name=spec['name'], owner=owner)

    source_type = spec.get('source_type', Dataset.SOURCE_VISUAL)
    dataset.source_type = source_type
    dataset.description = spec['description']
    dataset.joins = []
    dataset.filters = spec.get('filters', [])
    dataset.status_filter = []
    dataset.order_by = []
    dataset.distinct = bool(spec.get('distinct', True))

    if source_type == Dataset.SOURCE_SQL:
        dataset.source_sql = spec['sql']
        dataset.base_table = spec['base_table']
        dataset.distinct = False
        dataset.columns = list(spec['columns'])
        dataset.column_mappings = {c: c for c in spec['columns']}
    else:
        base = spec['base_table']
        dataset.source_sql = ''
        dataset.base_table = base
        dataset.columns = [f'{base}.{c}' for c in spec['columns']]
        dataset.column_mappings = {
            f'{base}.{c}': c for c in spec['columns']
        }

    dataset.save()
    print(f'  dataset ready: {dataset.name} (#{dataset.pk})')
    return dataset


def upsert_chart(spec, datasets, owner):
    from dataviz.models import SavedChart

    dataset = datasets[spec['dataset']]
    chart, _ = SavedChart.objects.update_or_create(
        name=spec['name'],
        owner=owner,
        defaults={
            'chart_type': spec['chart_type'],
            'config': spec['config'],
            'source_type': SavedChart.SOURCE_DATASET,
            'source_id': dataset.pk,
            'source_sql': '',
            'source_filters': spec.get('source_filters', {}),
            'column_defs': spec.get('column_defs', {}),
            'school_filter_mode': 'enrolled',
            'drill_through_columns': spec.get('drill_through_columns', []),
            'drill_through_filter_map': spec.get('drill_through_filter_map', {}),
            'description': spec.get('description', ''),
        },
    )
    print(f'  chart ready:   {chart.name} (#{chart.pk})')
    return chart


def attach_to_menu(page):
    """Nest the published page under College & Career Readiness."""
    if not page.linked_site_page_id:
        return
    from users.models_pages import Page

    parent = Page.objects.filter(slug=MENU_PARENT_SLUG).first()
    if parent is None:
        print(f'  menu category "{MENU_PARENT_SLUG}" not found; page left at root.')
        return
    site_page = Page.objects.filter(pk=page.linked_site_page_id).first()
    if site_page is None:
        return
    site_page.parent_page = parent
    site_page.menu_order = MENU_ORDER
    site_page.show_in_menu = True
    site_page.save(update_fields=['parent_page', 'menu_order', 'show_in_menu'])
    print(f'  menu:          {page.title} under "{parent.name}" (order {MENU_ORDER})')


def upsert_page(spec, charts, owner, *, draft=False):
    from django.db import transaction
    from dataviz.models import (
        DataVizPage, DataVizPageRow, DataVizPageSection,
    )
    from dataviz.utils.page_publish import publish_page, unpublish_page

    page, _ = DataVizPage.objects.update_or_create(
        slug=spec['slug'],
        defaults={
            'title': spec['title'],
            'description': spec['description'],
            'folder': spec['folder'],
            'container_width': DataVizPage.CONTAINER_FULL,
            'show_header': True,
            'owner': owner,
            'page_filters': spec['page_filters'],
            'status': (
                DataVizPage.STATUS_DRAFT if draft
                else DataVizPage.STATUS_ACTIVE
            ),
        },
    )

    with transaction.atomic():
        page.rows.all().delete()
        page.tabs.all().delete()
        for r_idx, row_spec in enumerate(spec['rows']):
            row = DataVizPageRow.objects.create(
                page=page,
                label=row_spec.get('label', ''),
                position=r_idx,
            )
            for s_idx, sec_spec in enumerate(row_spec['sections']):
                DataVizPageSection.objects.create(
                    page=page,
                    row=row,
                    element_type=DataVizPageSection.TYPE_CHART,
                    chart=charts[sec_spec['chart']],
                    show_title=True,
                    gs_x=sec_spec.get('gs_x', 0),
                    gs_y=sec_spec.get('gs_y', 0),
                    gs_w=sec_spec.get('gs_w', 12),
                    gs_h=sec_spec.get('gs_h', 8),
                    position=s_idx,
                )

    if draft:
        unpublish_page(page)
        print(f'  page draft:    {page.title} -> /dataviz/pages/{page.slug}/')
    else:
        publish_page(page, owner)
        print(f'  page published: {page.title} -> /dataviz/pages/{page.slug}/')
    return page


def main():
    parser = argparse.ArgumentParser(
        description='Create the College Pathway DataViz datasets, charts, and page.',
    )
    parser.add_argument(
        '--owner', default='',
        help='Username or email of the records owner (default: first Project Admin).',
    )
    parser.add_argument(
        '--draft', action='store_true',
        help='Leave the page as a draft instead of publishing it.',
    )
    parser.add_argument(
        '--no-menu', action='store_true',
        help='Do not attach the published page to College & Career Readiness.',
    )
    args = parser.parse_args()

    project_dir = bootstrap_django()
    print(f'Django project: {project_dir}')

    owner = resolve_owner(args.owner)
    print(f'Using owner: {owner}')
    enable_dataset_tables()

    datasets = {}
    for spec in dataset_specs():
        datasets[spec['name']] = upsert_dataset(spec, owner)

    charts = {}
    for spec in chart_specs():
        charts[spec['name']] = upsert_chart(spec, datasets, owner)

    page = upsert_page(PAGE_SPEC, charts, owner, draft=args.draft)
    if not args.draft and not args.no_menu:
        attach_to_menu(page)

    print()
    print(
        f'Done. {len(datasets)} datasets, {len(charts)} charts, '
        f'1 page (#{page.pk}).'
    )


if __name__ == '__main__':
    main()
