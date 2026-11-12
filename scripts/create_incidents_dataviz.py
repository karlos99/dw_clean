#!/usr/bin/env python
"""Seed DataViz datasets, charts, and pages for discipline incident analytics.

Requires ClickHouse table ``analytics_student_incidents`` (see sync_to_clickhouse).

    docker compose exec django python manage.py sync_to_clickhouse --tables analytics_student_incidents
    docker compose exec django python /app/scripts/create_incidents_dataviz.py
"""
import argparse
import os
import sys
from pathlib import Path


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
        raise SystemExit('Could not locate Django project (manage.py).')
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    return project_dir


INCIDENTS_TABLE = 'analytics_student_incidents'

STUDENT_COLUMNS = [
    'ssid',
    'student_name',
    'grade',
    'school',
    'school_state_id',
    'status',
    'school_year',
]

INCIDENT_COLUMNS = [
    'severity',
    'incident_id',
    'incident_date',
    'violation',
    'ed_code',
    'review_status',
    'description',
    'suspension_days',
    'is_suspended',
    'school_week',
    'week_start_date',
    'week_end_date',
    'week_label',
]

ALL_COLUMNS = STUDENT_COLUMNS + INCIDENT_COLUMNS

DRILL_COLUMNS = [
    'ssid', 'student_name', 'grade', 'school', 'school_year',
    'severity', 'incident_id', 'incident_date', 'violation', 'ed_code',
    'suspension_days', 'week_label',
]


def _base_cfg(chart_type, title, **overrides):
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
        'animation': True,
        'palette': '',
        'color_map': {},
        'label_position': 'top',
        'x_label_rotation': 0,
    }
    cfg.update(overrides)
    return cfg


DATASET_SPECS = [
    {
        'name': 'Discipline Incidents',
        'base_table': INCIDENTS_TABLE,
        'columns': ALL_COLUMNS,
        'description': (
            'One row per student discipline incident (Major/Minor), enriched with '
            'student demographics and dw_cal school-week fields (school_week, '
            'week_start_date, week_end_date, week_label).'
        ),
        'status_filter': ['Active'],
    },
]

CHART_SPECS = [
    {
        'name': 'Discipline • Major Incidents by School',
        'chart_type': 'bar',
        'dataset': 'Discipline Incidents',
        'source_filters': {'severity': ['Major']},
        'config': _base_cfg(
            'bar', 'Major Incidents by School',
            x_col='school', agg='count', top_n=25,
        ),
    },
    {
        'name': 'Discipline • Students with Major Incident by School',
        'chart_type': 'bar',
        'dataset': 'Discipline Incidents',
        'source_filters': {'severity': ['Major']},
        'config': _base_cfg(
            'bar', 'Students with a Major Incident by School',
            x_col='school', y_col='ssid', agg='count_unique', top_n=25,
        ),
    },
    {
        'name': 'Discipline • Major Incidents by Grade',
        'chart_type': 'bar',
        'dataset': 'Discipline Incidents',
        'source_filters': {'severity': ['Major']},
        'config': _base_cfg(
            'bar', 'Major Incidents by Grade',
            x_col='grade', agg='count',
        ),
    },
    {
        'name': 'Discipline • Top Major Violations',
        'chart_type': 'bar',
        'dataset': 'Discipline Incidents',
        'source_filters': {'severity': ['Major']},
        'config': _base_cfg(
            'bar', 'Top Major Violations',
            x_col='violation', agg='count', top_n=15,
        ),
    },
    {
        'name': 'Discipline • Major vs Minor',
        'chart_type': 'donut',
        'dataset': 'Discipline Incidents',
        'config': _base_cfg(
            'donut', 'Major vs Minor Incidents',
            x_col='severity', agg='count',
        ),
    },
    {
        'name': 'Discipline • Suspended Students by School',
        'chart_type': 'bar',
        'dataset': 'Discipline Incidents',
        'source_filters': {'is_suspended': ['1']},
        'config': _base_cfg(
            'bar', 'Suspended Students by School',
            x_col='school', y_col='ssid', agg='count_unique', top_n=25,
        ),
    },
    {
        'name': 'Discipline • Suspension Days by School',
        'chart_type': 'bar',
        'dataset': 'Discipline Incidents',
        'source_filters': {'is_suspended': ['1']},
        'config': _base_cfg(
            'bar', 'Suspension Days by School',
            x_col='school', y_col='suspension_days', agg='sum', top_n=25,
        ),
    },
    {
        'name': 'Discipline • Suspension Days Distribution',
        'chart_type': 'histogram',
        'dataset': 'Discipline Incidents',
        'source_filters': {'is_suspended': ['1']},
        'config': _base_cfg(
            'histogram', 'Suspension Days per Incident',
            x_col='suspension_days', agg='count',
        ),
    },
    {
        'name': 'Discipline • Incidents by School Week',
        'chart_type': 'line',
        'dataset': 'Discipline Incidents',
        'config': _base_cfg(
            'line', 'Incidents by School Week',
            x_col='week_label', agg='count',
            sort_col='school_week', sort_dir='asc', data_labels=False,
        ),
    },
    {
        'name': 'Discipline • Major vs Minor by Week',
        'chart_type': 'stacked_bar',
        'dataset': 'Discipline Incidents',
        'config': _base_cfg(
            'stacked_bar', 'Major vs Minor Incidents by School Week',
            x_col='week_label', stack_col='severity', series_col='severity',
            agg='count', sort_col='school_week', sort_dir='asc', data_labels=False,
        ),
    },
    {
        'name': 'Discipline • Suspension Days by Week',
        'chart_type': 'line',
        'dataset': 'Discipline Incidents',
        'source_filters': {'is_suspended': ['1']},
        'config': _base_cfg(
            'line', 'Suspension Days by School Week',
            x_col='week_label', y_col='suspension_days', agg='sum',
            sort_col='school_week', sort_dir='asc', data_labels=False,
        ),
    },
]

PAGE_SPECS = [
    {
        'title': 'Discipline Incidents Dashboard',
        'slug': 'discipline-incidents',
        'description': (
            'Major and minor discipline incidents, suspended students, and '
            'suspension days over the school year calendar (dw_cal weeks).'
        ),
        'page_filters': [
            {
                'id': 'school_year', 'label': 'School Year', 'source_col': 'school_year',
                'source_table': INCIDENTS_TABLE,
                'widget': 'select', 'default_val': '__sy_this__', 'hidden': False,
            },
            {
                'id': 'school', 'label': 'School', 'source_col': 'school',
                'source_table': INCIDENTS_TABLE,
                'widget': 'multi', 'default_val': '', 'hidden': False,
            },
            {
                'id': 'grade', 'label': 'Grade', 'source_col': 'grade',
                'source_table': INCIDENTS_TABLE,
                'widget': 'multi', 'default_val': '', 'hidden': False,
            },
        ],
        'tabs': ['Overview', 'Suspensions', 'Weekly trends'],
        'rows': [
            {'tab': 'Overview', 'label': 'Major incidents', 'sections': [
                {'chart': 'Discipline • Major Incidents by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
            ]},
            {'tab': 'Overview', 'label': 'Students & grades', 'sections': [
                {'chart': 'Discipline • Students with Major Incident by School', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
                {'chart': 'Discipline • Major Incidents by Grade', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
            ]},
            {'tab': 'Overview', 'label': 'Violations & mix', 'sections': [
                {'chart': 'Discipline • Top Major Violations', 'gs_x': 0, 'gs_w': 8, 'gs_h': 6},
                {'chart': 'Discipline • Major vs Minor', 'gs_x': 8, 'gs_w': 4, 'gs_h': 6},
            ]},
            {'tab': 'Suspensions', 'label': 'Suspended students', 'sections': [
                {'chart': 'Discipline • Suspended Students by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
            ]},
            {'tab': 'Suspensions', 'label': 'Suspension days', 'sections': [
                {'chart': 'Discipline • Suspension Days by School', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
                {'chart': 'Discipline • Suspension Days Distribution', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
            ]},
            {'tab': 'Weekly trends', 'label': 'Incidents over the school year', 'sections': [
                {'chart': 'Discipline • Incidents by School Week', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
            ]},
            {'tab': 'Weekly trends', 'label': 'By severity and suspensions', 'sections': [
                {'chart': 'Discipline • Major vs Minor by Week', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
                {'chart': 'Discipline • Suspension Days by Week', 'gs_x': 0, 'gs_w': 12, 'gs_h': 5},
            ]},
        ],
    },
]


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
        raise SystemExit('No users exist to own the discipline dashboards.')
    return user


def enable_dataset_tables():
    from landing.models import ProjectConfig
    cfg = ProjectConfig.get()
    enabled = list(cfg.enabled_dataset_tables or [])
    if not enabled:
        print('enabled_dataset_tables is empty; leaving unrestricted.')
        return
    if INCIDENTS_TABLE not in enabled:
        cfg.enabled_dataset_tables = enabled + [INCIDENTS_TABLE]
        cfg.save()
        print(f'Enabled dataset table: {INCIDENTS_TABLE}')


def upsert_dataset(spec, owner):
    from dataviz.models import Dataset
    base = spec['base_table']
    columns = [f'{base}.{c}' for c in spec['columns']]
    mappings = {f'{base}.{c}': c for c in spec['columns']}
    dataset, _ = Dataset.objects.update_or_create(
        name=spec['name'],
        owner=owner,
        defaults={
            'source_type': Dataset.SOURCE_VISUAL,
            'base_table': base,
            'distinct': True,
            'columns': columns,
            'column_mappings': mappings,
            'joins': [],
            'filters': spec.get('filters', []),
            'status_filter': spec.get('status_filter', ['Active']),
            'order_by': [],
            'description': spec['description'],
        },
    )
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
            'column_defs': {},
            'school_filter_mode': 'enrolled',
            'drill_through_columns': spec.get('drill_through_columns', DRILL_COLUMNS),
            'drill_through_filter_map': spec.get('drill_through_filter_map', {}),
            'description': spec['config'].get('chart_title', ''),
        },
    )
    print(f'  chart ready:   {chart.name} (#{chart.pk})')
    return chart


def upsert_page(spec, charts, owner):
    from django.db import transaction
    from dataviz.models import (
        DataVizPage, DataVizPageRow, DataVizPageSection, DataVizPageTab,
    )
    from dataviz.utils.page_publish import publish_page

    page, _ = DataVizPage.objects.update_or_create(
        slug=spec['slug'],
        defaults={
            'title': spec['title'],
            'description': spec['description'],
            'folder': 'Behavior / Discipline',
            'container_width': DataVizPage.CONTAINER_XL,
            'show_header': True,
            'owner': owner,
            'page_filters': spec['page_filters'],
            'status': DataVizPage.STATUS_ACTIVE,
        },
    )
    with transaction.atomic():
        page.rows.all().delete()
        page.tabs.all().delete()
        tab_map = {}
        for t_idx, tab_name in enumerate(spec.get('tabs', [])):
            tab_map[tab_name] = DataVizPageTab.objects.create(
                page=page, name=tab_name, position=t_idx,
            )
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
                    show_title=True,
                    gs_x=sec_spec.get('gs_x', 0),
                    gs_y=sec_spec.get('gs_y', 0),
                    gs_w=sec_spec.get('gs_w', 6),
                    gs_h=sec_spec.get('gs_h', 6),
                    position=s_idx,
                )
    publish_page(page, owner)
    print(f'  page published: {page.title} -> /dataviz/pages/{page.slug}/')
    return page


def main():
    parser = argparse.ArgumentParser(description='Create discipline incident DataViz content.')
    parser.add_argument('--owner', default='', help='Owner username or email.')
    args = parser.parse_args()

    project_dir = bootstrap_django()
    print(f'Django project: {project_dir}')
    owner = resolve_owner(args.owner)
    print(f'Using owner: {owner}')

    enable_dataset_tables()

    datasets = {}
    for spec in DATASET_SPECS:
        datasets[spec['name']] = upsert_dataset(spec, owner)

    charts = {}
    for spec in CHART_SPECS:
        charts[spec['name']] = upsert_chart(spec, datasets, owner)

    pages = []
    for spec in PAGE_SPECS:
        pages.append(upsert_page(spec, charts, owner))

    print()
    print(f'Done. {len(datasets)} datasets, {len(charts)} charts, {len(pages)} pages.')


if __name__ == '__main__':
    main()
