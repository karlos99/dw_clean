#!/usr/bin/env python
"""Create the Attendance DataViz datasets, charts, and page.

Absence levels are derived from ``atendace_rate`` on ``current_student`` (100 =
perfect attendance, no absences):

  * No absence — 0.0% absence
  * Moderate absence — 0.1% to 4.9%
  * High absence — 5.0% to 9.9%
  * Chronic absence — 10.0% or above

Run inside the Django container:

    docker compose exec django python manage.py sync_to_clickhouse --tables student_attendance,analytics_student_attendance_weekly
    docker compose exec django python /app/scripts/create_attendance_dataviz.py

Optional: ``--owner you@example.com`` (default: first superuser).
"""
import argparse
import os
import sys
from pathlib import Path

PREFIX = 'Attendance •'


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


# ClickHouse analytics SQL (one row per active student with a known attendance rate).
_STUDENTS_SQL = """
SELECT
    cs.stu_annual_key AS stu_annual_key,
    cs.ssid AS ssid,
    cs.student_name AS student_name,
    cs.grade AS grade,
    cs.school AS school,
    cs.school_state_id AS school_state_id,
    cs.status AS status,
    cs.school_year AS school_year,
    cs.atendace_rate AS attendance_rate,
    greatest(0.0, 100.0 - ifNull(cs.atendace_rate, 100.0)) AS absence_pct,
    multiIf(
        ifNull(cs.atendace_rate, 100.0) >= 100.0
            OR greatest(0.0, 100.0 - ifNull(cs.atendace_rate, 100.0)) < 0.1,
        'No Absence',
        greatest(0.0, 100.0 - ifNull(cs.atendace_rate, 100.0)) < 5.0,
        'Moderate Absence',
        greatest(0.0, 100.0 - ifNull(cs.atendace_rate, 100.0)) < 10.0,
        'High Absence',
        'Chronic Absence'
    ) AS absence_category,
    multiIf(
        upper(trimBoth(ifNull(cs.grade, ''))) IN ('TK'), 'TK',
        upper(trimBoth(ifNull(cs.grade, ''))) IN ('KN', 'K', 'KG'), 'KN',
        upper(trimBoth(ifNull(cs.grade, ''))) IN ('US', '250'), 'US.',
        match(trimBoth(ifNull(cs.grade, '')), '^[0-9]+$'),
        lpad(toString(toInt32OrZero(trimBoth(cs.grade))), 2, '0'),
        if(trimBoth(ifNull(cs.grade, '')) = '', 'Unknown', trimBoth(cs.grade))
    ) AS grade_label
FROM current_student AS cs
WHERE cs.atendace_rate IS NOT NULL
  AND cs.status = 'Active'
  AND cs.ssid IS NOT NULL
  AND trimBoth(cs.ssid) != ''
"""

_STUDENT_COLUMNS = [
    'stu_annual_key',
    'ssid',
    'student_name',
    'grade',
    'grade_label',
    'school',
    'school_state_id',
    'status',
    'school_year',
    'attendance_rate',
    'absence_pct',
    'absence_category',
]

_DRILL_COLUMNS = [
    'ssid', 'student_name', 'grade', 'grade_label', 'school', 'school_year',
    'attendance_rate', 'absence_pct', 'absence_category',
]

ABSENCE_COLORS = {
    'No Absence': '#4e79a7',
    'Moderate Absence': '#59a14f',
    'High Absence': '#edc948',
    'Chronic Absence': '#e15759',
}
# Chart builder draws stack_order last-to-first; Chronic ends up on the bottom.
ABSENCE_STACK = ['No Absence', 'Moderate Absence', 'High Absence', 'Chronic Absence']
ABSENCE_CATEGORY_ORDER = ['No Absence', 'Moderate Absence', 'High Absence', 'Chronic Absence']
GRADE_ORDER = [
    'TK', 'KN', '01', '02', '03', '04', '05', '06', '07', '08',
    '09', '10', '11', '12', 'US.', 'Unknown',
]

WEEKLY_TABLE = 'analytics_student_attendance_weekly'

INSTRUCTIONAL_WEEK_ORDER = [str(w) for w in range(1, 43)]

YEAR_LINE_COLORS = {
    '2024-2025': '#4e79a7',
    '2025-2026': '#f28e2b',
    '2026-2027': '#59a14f',
}

ATTENDANCE_Y_BANDS = [
    {'y0': 96, 'y1': 100, 'color': 'rgba(89, 161, 79, 0.35)'},
    {'y0': 87, 'y1': 96, 'color': 'rgba(237, 201, 72, 0.35)'},
    {'y0': 85, 'y1': 87, 'color': 'rgba(225, 87, 89, 0.35)'},
]

_WEEKLY_DRILL_COLUMNS = [
    'ssid',
    'student_name',
    'grade',
    'grade_label',
    'school',
    'school_year',
    'school_week',
    'week_label',
    'attendance_rate_week',
]

_WEEKLY_COLUMNS = [
    'ssid',
    'stu_annual_key',
    'student_name',
    'grade',
    'grade_label',
    'school',
    'school_state_id',
    'status',
    'school_year',
    'school_week',
    'week_start_date',
    'week_end_date',
    'week_label',
    'attendance_rate_week',
]


def _recent_school_years(limit: int = 3) -> list[str]:
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
    return list(reversed(years)) if years else []


def _base_cfg(chart_type, title, **overrides):
    cfg = {
        'chart_type': chart_type,
        'chart_title': title,
        'agg': 'count_unique',
        'x_col': '',
        'y_col': 'ssid',
        'stack_col': '',
        'group_col': '',
        'series_col': '',
        'sort_col': '',
        'sort_dir': 'asc',
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
        'color_map': ABSENCE_COLORS,
        'label_position': 'inside',
        'x_label_rotation': 0,
        'y_axis_min': 0,
    }
    cfg.update(overrides)
    return cfg


DATASET_SPECS = [
    {
        'name': f'{PREFIX} Weekly by School Week',
        'base_table': WEEKLY_TABLE,
        'columns': _WEEKLY_COLUMNS,
        'description': (
            'One row per active student and instructional school week. '
            'Attendance rate is cumulative through that week (instructional days '
            'from dw_cal minus absences parsed from absence_dates).'
        ),
        'status_filter': ['Active'],
    },
    {
        'name': f'{PREFIX} Students',
        'description': (
            'One row per active student with a known attendance rate. '
            'Absence percent is 100 minus attendance rate; categories follow '
            'chronic absence reporting bands (0%, 0.1–4.9%, 5–9.9%, 10%+).'
        ),
        'columns': _STUDENT_COLUMNS,
        'sql': _STUDENTS_SQL,
    },
]

CHART_SPECS = [
    {
        'name': f'{PREFIX} Weekly Attendance Trend',
        'chart_type': 'line',
        'dataset': f'{PREFIX} Weekly by School Week',
        'source_filters': {},  # filled in main() with last three school years
        'drill_columns': _WEEKLY_DRILL_COLUMNS,
        'drill_through_filter_map': {
            'x_col': 'school_week',
            'series_col': 'school_year',
        },
        'column_defs': {
            'school_week': 'School Instructional Week',
            'attendance_rate_week': '% Attendance',
            'school_year': 'School Year',
        },
        'config': _base_cfg(
            'line',
            'The weekly attendance trend for students over the last 3 years.',
            x_col='school_week',
            y_col='attendance_rate_week',
            stack_col='school_year',
            agg='avg',
            agg_decimals=2,
            sort_col='__manual__',
            category_order=INSTRUCTIONAL_WEEK_ORDER,
            show_grid=False,
            data_labels=True,
            show_points=True,
            smooth=False,
            y_axis_min=85,
            y_axis_max=100,
            skip_page_filters=['school_year'],
            color_map=YEAR_LINE_COLORS,
            y_band_zones=ATTENDANCE_Y_BANDS,
            legend_pos='bottom',
        ),
    },
    {
        'name': f'{PREFIX} Share by Absence Level',
        'chart_type': 'bar',
        'dataset': f'{PREFIX} Students',
        'drill_columns': _DRILL_COLUMNS,
        'column_defs': {
            'absence_category': 'Absence Level',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'bar', 'Share of Students by Absence Level',
            x_col='absence_category',
            y_col='ssid',
            agg='count_unique',
            sort_col='__manual__',
            category_order=ABSENCE_CATEGORY_ORDER,
            show_percent=True,
            student_share_percent=True,
            percent_decimals=1,
            show_percent_symbol=True,
            y_axis_max=100,
            one_row_per_stu_annual_key=True,
            one_row_per_ssid=True,
            data_labels=True,
            label_position='top',
        ),
    },
    {
        'name': f'{PREFIX} Share by Grade',
        'chart_type': 'stacked_bar',
        'dataset': f'{PREFIX} Students',
        'drill_columns': _DRILL_COLUMNS,
        'column_defs': {
            'grade_label': 'Grade',
            'absence_category': 'Absence Level',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'stacked_bar', 'Share of Students by Grade and Absence Level',
            x_col='grade_label',
            y_col='ssid',
            agg='count_unique',
            stack_col='absence_category',
            series_col='absence_category',
            sort_col='__manual__',
            category_order=GRADE_ORDER,
            stack_order=ABSENCE_STACK,
            show_percent=True,
            percent_decimals=1,
            show_percent_symbol=True,
            y_axis_max=100,
            one_row_per_stu_annual_key=True,
            one_row_per_ssid=True,
            data_labels=True,
            label_position='inside',
        ),
    },
]

PAGE_SPEC = {
    'title': 'Attendance',
    'slug': 'attendance',
    'description': (
        'Student attendance summarized as absence rate (100 minus the stored '
        'attendance rate, where 100 means no absences). Students are grouped '
        'into no, moderate, high, and chronic absence bands. Use the school '
        'year and school filters to match CALPADS-style reporting views.'
    ),
    'page_filters': [
        {
            'id': 'school_year',
            'label': 'School Year',
            'source_col': 'school_year',
            'source_table': 'current_student',
            'widget': 'select',
            'default_val': '__sy_this__',
            'hidden': False,
        },
        {
            'id': 'school',
            'label': 'School',
            'source_col': 'school',
            'source_table': 'current_student',
            'widget': 'multi',
            'default_val': '',
            'hidden': False,
        },
        {
            'id': 'grade',
            'label': 'Grade',
            'source_col': 'grade_label',
            'source_table': 'current_student',
            'widget': 'multi',
            'default_val': '',
            'hidden': False,
        },
    ],
    'rows': [
        {'sections': [
            {'chart': f'{PREFIX} Weekly Attendance Trend', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'sections': [
            {'chart': f'{PREFIX} Share by Absence Level', 'gs_x': 0, 'gs_w': 12, 'gs_h': 7},
        ]},
        {'sections': [
            {'chart': f'{PREFIX} Share by Grade', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
    ],
}


def resolve_owner(owner_ref):
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
        raise SystemExit('No users exist to own the attendance page.')
    return user


def enable_dataset_tables():
    from landing.models import ProjectConfig

    cfg = ProjectConfig.get()
    enabled = list(cfg.enabled_dataset_tables or [])
    if not enabled:
        print('enabled_dataset_tables is empty; leaving unrestricted.')
        return
    added = []
    for table in (WEEKLY_TABLE,):
        if table not in enabled:
            enabled.append(table)
            added.append(table)
    if added:
        cfg.enabled_dataset_tables = enabled
        cfg.save()
        print(f'Enabled dataset table(s): {", ".join(added)}')


def upsert_dataset(spec, owner):
    from dataviz.models import Dataset

    if spec.get('base_table'):
        base = spec['base_table']
        columns = [f'{base}.{c}' for c in spec['columns']]
        mappings = {f'{base}.{c}': c for c in spec['columns']}
    else:
        columns = spec['columns']
        mappings = {c: c for c in columns}
    if spec.get('base_table'):
        defaults = {
            'source_type': Dataset.SOURCE_VISUAL,
            'source_sql': '',
            'base_table': spec['base_table'],
            'distinct': True,
            'columns': columns,
            'column_mappings': mappings,
            'joins': [],
            'filters': spec.get('filters', []),
            'status_filter': spec.get('status_filter', ['Active']),
            'order_by': [],
            'description': spec['description'],
        }
    else:
        defaults = {
            'source_type': Dataset.SOURCE_SQL,
            'source_sql': spec['sql'],
            'base_table': 'current_student',
            'distinct': False,
            'columns': columns,
            'column_mappings': mappings,
            'joins': [],
            'filters': [],
            'status_filter': [],
            'order_by': [],
            'description': spec['description'],
        }
    dataset, _ = Dataset.objects.update_or_create(
        name=spec['name'],
        owner=owner,
        defaults=defaults,
    )
    print(f'  dataset ready: {dataset.name} (#{dataset.pk})')
    return dataset


def upsert_chart(spec, datasets, owner):
    from dataviz.models import SavedChart

    if not spec['name'].startswith(PREFIX):
        raise SystemExit(f'Refusing to write a chart outside the attendance set: {spec["name"]}')
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
            'drill_through_columns': list(spec.get('drill_columns') or []),
            'drill_through_filter_map': spec.get('drill_through_filter_map')
            or {
                'x_col': 'grade_label' if spec['chart_type'] == 'stacked_bar' else 'absence_category',
                'series_col': 'absence_category',
            },
            'description': spec['config'].get('chart_title', ''),
        },
    )
    print(f'  chart ready:   {chart.name} (#{chart.pk})')
    return chart


def upsert_page(spec, charts, owner):
    from django.db import transaction
    from dataviz.models import (
        DataVizPage, DataVizPageRow, DataVizPageSection,
    )
    from dataviz.utils.page_publish import publish_page

    page, _ = DataVizPage.objects.update_or_create(
        slug=spec['slug'],
        defaults={
            'title': spec['title'],
            'description': spec['description'],
            'folder': 'Attendance',
            'container_width': DataVizPage.CONTAINER_WIDE,
            'show_header': True,
            'owner': owner,
            'page_filters': spec.get('page_filters', []),
            'status': DataVizPage.STATUS_ACTIVE,
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
                tab=None,
            )
            for s_idx, sec_spec in enumerate(row_spec['sections']):
                DataVizPageSection.objects.create(
                    page=page,
                    row=row,
                    element_type=DataVizPageSection.TYPE_CHART,
                    chart=charts[sec_spec['chart']],
                    show_title=False,
                    gs_x=sec_spec.get('gs_x', 0),
                    gs_y=0,
                    gs_w=sec_spec.get('gs_w', 6),
                    gs_h=sec_spec.get('gs_h', 6),
                    position=s_idx,
                )
    publish_page(page, owner)
    print(f'  page published: {page.title} -> /dataviz/pages/{page.slug}/')
    return page


def attach_to_menu(page):
    from users.models_pages import Page

    category, _ = Page.objects.update_or_create(
        slug='attendance',
        defaults={
            'name': 'Attendance',
            'app_reference': 'attendance',
            'description': 'Student absence level dashboards',
            'url_path': '',
            'status': 'active',
            'is_menu_category': True,
            'show_in_menu': True,
            'menu_order': 56,
            'required_roles': [],
            'is_public': False,
        },
    )
    if page.linked_site_page_id:
        site = Page.objects.get(pk=page.linked_site_page_id)
        site.parent_page = category
        site.menu_order = 0
        site.show_in_menu = True
        site.save(update_fields=['parent_page', 'menu_order', 'show_in_menu'])
    print('  menu category "Attendance" updated.')


def main():
    parser = argparse.ArgumentParser(description='Create the Attendance DataViz page.')
    parser.add_argument('--owner', default='', help='Username or email of the owner.')
    args = parser.parse_args()

    project_dir = bootstrap_django()
    print(f'Django project: {project_dir}')
    owner = resolve_owner(args.owner)
    print(f'Using owner: {owner}')

    enable_dataset_tables()

    recent_years = _recent_school_years(3)
    chart_specs = []
    for spec in CHART_SPECS:
        item = dict(spec)
        if item['name'].endswith('Weekly Attendance Trend') and recent_years:
            item['source_filters'] = {'school_year': recent_years}
        chart_specs.append(item)

    datasets = {spec['name']: upsert_dataset(spec, owner) for spec in DATASET_SPECS}
    charts = {spec['name']: upsert_chart(spec, datasets, owner) for spec in chart_specs}
    page = upsert_page(PAGE_SPEC, charts, owner)
    attach_to_menu(page)
    print('Attendance page ready.')


if __name__ == '__main__':
    main()
