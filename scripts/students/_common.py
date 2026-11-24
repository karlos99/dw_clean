"""Shared helpers for the Student section DataViz seed scripts.

These scripts follow the same pattern as the existing ``scripts/create_*_dataviz.py``
builders: define SQL datasets and chart specs, upsert them, then upsert and publish
a DataViz page and attach its linked site page to a menu category.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

STUDENTS_MENU_SLUG = 'students'
STUDENTS_MENU_NAME = 'Students'
STUDENTS_MENU_ORDER = 58
CHART_ROOT_FOLDER = 'Student_pages'

GRADE_ORDER = [
    'TK', 'KN', '01', '02', '03', '04', '05', '06', '07', '08',
    '09', '10', '11', '12', 'US.', 'Unknown',
]

SCHOOL_TYPE_ORDER = [
    'Elementary School',
    'Middle School',
    'High School',
    'Special School',
    'Unknown',
]

ELL_ORDER = ['EL', 'IFEP', 'RFEP', 'EO', 'TBD', 'ADEL']

PROGRAM_ORDER = [
    'SED',
    'SPED',
    'Foster',
    'Homeless',
    'Migrant',
    '504',
    'GATE',
    'DLI',
    'LTEL',
]


def bootstrap_django() -> str:
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
    import django

    django.setup()
    return project_dir


def resolve_owner(owner_ref: str = ''):
    from users.models import User

    if owner_ref:
        user = (
            User.objects.filter(username=owner_ref).first()
            or User.objects.filter(email__iexact=owner_ref).first()
        )
        if not user:
            raise SystemExit(f'No user found for "{owner_ref}".')
        return user
    user = User.objects.filter(is_superuser=True).order_by('id').first()
    if not user:
        user = User.objects.order_by('id').first()
    if not user:
        raise SystemExit('No users exist to own the Student pages.')
    return user


def enable_dataset_tables(tables: list[str]) -> None:
    from landing.models import ProjectConfig

    cfg = ProjectConfig.get()
    enabled = list(cfg.enabled_dataset_tables or [])
    if not enabled:
        print('enabled_dataset_tables is empty; leaving unrestricted.')
        return
    added = []
    for table in tables:
        if table and table not in enabled:
            enabled.append(table)
            added.append(table)
    if added:
        cfg.enabled_dataset_tables = enabled
        cfg.save()
        print(f'Enabled dataset table(s): {", ".join(added)}')


def ensure_dataset_folder(folder: str) -> None:
    if not folder:
        return
    from dataviz.models import DataVizDatasetFolder

    parts = [p.strip() for p in folder.split(' / ') if p.strip()]
    for i in range(1, len(parts) + 1):
        DataVizDatasetFolder.objects.get_or_create(path=' / '.join(parts[:i]))


def ensure_page_folder(folder: str) -> None:
    if not folder:
        return
    from dataviz.models import DataVizPageFolder

    parts = [p.strip() for p in folder.split(' / ') if p.strip()]
    for i in range(1, len(parts) + 1):
        DataVizPageFolder.objects.get_or_create(path=' / '.join(parts[:i]))


def upsert_dataset(spec: dict, owner, *, default_folder: str = 'Students'):
    from dataviz.models import Dataset

    folder = spec.get('folder', default_folder)
    dataset = Dataset.objects.filter(name=spec['name']).order_by('id').first()
    if dataset is None:
        dataset = Dataset(name=spec['name'], owner=owner)
    else:
        dataset.owner = owner

    columns = list(spec['columns'])
    dataset.source_type = spec.get('source_type', Dataset.SOURCE_SQL)
    dataset.source_sql = spec.get('sql', '')
    dataset.base_table = spec.get('base_table', 'current_student')
    dataset.distinct = bool(spec.get('distinct', False))
    dataset.columns = columns
    dataset.column_mappings = {c: c for c in columns}
    dataset.joins = []
    dataset.filters = []
    dataset.status_filter = []
    dataset.order_by = []
    dataset.description = spec.get('description', '')
    dataset.folder = folder
    dataset.save()
    ensure_dataset_folder(folder)
    print(f'  dataset ready: {dataset.name} (#{dataset.pk}) folder={folder or "(root)"}')
    return dataset


def chart_storage_name(chart_root: str, chart_folder: str, raw_name: str) -> str:
    return ' / '.join(p for p in (chart_root, chart_folder, raw_name) if p)


def upsert_chart(spec: dict, datasets: dict, owner, *, chart_root: str, chart_folder: str,
                 allowed_prefix: str):
    from dataviz.models import SavedChart

    raw_name = spec['name']
    if not raw_name.startswith(allowed_prefix):
        raise SystemExit(f'Refusing to write chart outside {allowed_prefix!r}: {raw_name}')

    storage_name = chart_storage_name(chart_root, chart_folder, raw_name)
    dataset = datasets[spec['dataset']]
    chart = (
        SavedChart.objects.filter(name=storage_name).order_by('id').first()
        or SavedChart.objects.filter(name=raw_name).order_by('id').first()
    )
    if chart is None:
        chart = SavedChart(name=storage_name)
    else:
        chart.name = storage_name

    chart.owner = owner
    chart.chart_type = spec['chart_type']
    chart.config = spec['config']
    chart.source_type = SavedChart.SOURCE_DATASET
    chart.source_id = dataset.pk
    chart.source_sql = ''
    chart.source_filters = spec.get('source_filters', {})
    chart.column_defs = spec.get('column_defs', {})
    chart.school_filter_mode = spec.get('school_filter_mode', 'enrolled')
    chart.drill_through_columns = list(spec.get('drill_columns') or [])
    chart.drill_through_filter_map = spec.get('drill_through_filter_map') or {}
    chart.description = spec['config'].get('chart_title', '')
    chart.save()
    print(f'  chart ready:   {chart.name} (#{chart.pk})')
    return chart


def upsert_page(spec: dict, charts: dict, owner):
    from django.db import transaction

    from dataviz.models import (
        DataVizPage,
        DataVizPageRow,
        DataVizPageSection,
        DataVizPageTab,
    )
    from dataviz.utils.page_publish import publish_page

    folder = spec.get('folder', 'Students')
    page, _ = DataVizPage.objects.update_or_create(
        slug=spec['slug'],
        defaults={
            'title': spec['title'],
            'description': spec.get('description', ''),
            'folder': folder,
            'container_width': spec.get('container_width', DataVizPage.CONTAINER_XL),
            'show_header': True,
            'owner': owner,
            'page_filters': spec.get('page_filters', []),
            'status': DataVizPage.STATUS_ACTIVE,
        },
    )
    ensure_page_folder(folder)
    with transaction.atomic():
        page.rows.all().delete()
        page.tabs.all().delete()
        tab_map = {}
        for t_idx, tab_name in enumerate(spec.get('tabs', [])):
            tab_map[tab_name] = DataVizPageTab.objects.create(
                page=page, name=tab_name, position=t_idx,
            )
        default_show_title = spec.get('show_chart_titles', True)
        for r_idx, row_spec in enumerate(spec['rows']):
            row = DataVizPageRow.objects.create(
                page=page,
                label=row_spec.get('label', ''),
                position=r_idx,
                tab=tab_map.get(row_spec.get('tab')),
            )
            for s_idx, sec_spec in enumerate(row_spec['sections']):
                DataVizPageSection.objects.create(
                    page=page,
                    row=row,
                    element_type=DataVizPageSection.TYPE_CHART,
                    chart=charts[sec_spec['chart']],
                    show_title=sec_spec.get('show_title', default_show_title),
                    gs_x=sec_spec.get('gs_x', 0),
                    gs_y=sec_spec.get('gs_y', 0),
                    gs_w=sec_spec.get('gs_w', 6),
                    gs_h=sec_spec.get('gs_h', 6),
                    position=s_idx,
                )
    publish_page(page, owner)
    print(f'  page published: {page.title} -> /dataviz/pages/{page.slug}/')
    return page


def ensure_students_menu() -> object:
    from users.models_pages import Page

    category, _ = Page.objects.update_or_create(
        slug=STUDENTS_MENU_SLUG,
        defaults={
            'name': STUDENTS_MENU_NAME,
            'app_reference': 'students',
            'description': 'Student enrollment, English learner, and behavior dashboards',
            'url_path': '',
            'status': 'active',
            'is_menu_category': True,
            'show_in_menu': True,
            'menu_order': STUDENTS_MENU_ORDER,
            'required_roles': [],
            'is_public': False,
        },
    )
    return category


def attach_page_to_students_menu(page, order: int) -> None:
    from users.models_pages import Page

    category = ensure_students_menu()
    if not page.linked_site_page_id:
        return
    site = Page.objects.get(pk=page.linked_site_page_id)
    site.parent_page = category
    site.menu_order = order
    site.show_in_menu = True
    site.status = 'active'
    site.save(update_fields=['parent_page', 'menu_order', 'show_in_menu', 'status'])
    print(f'  menu: Students -> {site.name} (order {order})')


def base_chart_cfg(chart_type: str, title: str, **overrides) -> dict:
    cfg = {
        'chart_type': chart_type,
        'chart_title': title,
        'agg': 'count',
        'x_col': '',
        'y_col': '',
        'stack_col': '',
        'group_col': '',
        'series_col': '',
        'sort_col': '_value',
        'sort_dir': 'desc',
        'bar_layout': 'stacked' if chart_type == 'stacked_bar' else 'grouped',
        'bar_orientation': 'vertical',
        'background': 'white',
        'legend_pos': 'bottom',
        'data_labels': True,
        'show_grid': True,
        'show_tooltip': True,
        'show_zoom': False,
        'animation': True,
        'palette': '',
        'color_map': {},
        'label_position': 'top',
        'x_label_rotation': 0,
    }
    if chart_type == 'line':
        cfg['smooth'] = True
    cfg.update(overrides)
    return cfg


def summary_cfg(title: str, row_col: str, col_col: str, agg: str = 'count',
                value_col: str = '', **overrides) -> dict:
    cfg = {
        'chart_type': 'summary_table',
        'chart_title': title,
        'st_row_col': row_col,
        'st_col_col': col_col,
        'st_col_mode': 'values',
        'st_agg_func': agg,
        'st_value_col': value_col,
    }
    cfg.update(overrides)
    return cfg


def recent_school_years(limit: int = 4) -> list[str]:
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT school_year
            FROM current_student
            WHERE school_year IS NOT NULL AND trim(school_year) != ''
            ORDER BY school_year DESC
            LIMIT %s
            """,
            [limit],
        )
        years = [str(r[0]).strip() for r in cur.fetchall() if r and r[0]]
    return list(reversed(years))


def current_school_year() -> str:
    years = recent_school_years(1)
    return years[0] if years else ''
