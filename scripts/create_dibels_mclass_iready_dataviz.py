#!/usr/bin/env python
"""Create DIBELS, mClass Math, and iReady DataViz datasets, charts, and pages.

Uses read-only ClickHouse SQL over ``results_with_tests``. Pages follow the
assessment-page pattern: current school-year roster membership, tested-school
and current-enrolled-school filters, test-year/window filters, 100% stacked PL
charts, grade breakdowns, and detail tables.

Run:
    docker compose exec django python /app/scripts/create_dibels_mclass_iready_dataviz.py

Optional:
    --owner you@example.com
    --qa      read-only data/filter QA, no writes
"""
import argparse
import os
import sys
from pathlib import Path

SOURCE_TABLE = 'results_with_tests'

_STUDENT_STATUS_MATCH = "coalesce(cs.status, '') IN ('Active', 'Inactive')"

_SCORE_BLANK = (
    "trim(toString({col})) = '' OR lower(trim(toString({col}))) IN "
    "('nan','ns','n/a','na','n/s','null','none')"
)

_GRADE_FROM = ['KN', '01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '12']
_GRADE_TO = [
    'Kindergarten', '1st', '2nd', '3rd', '4th', '5th', '6th',
    '7th', '8th', '9th', '10th', '11th', '12th',
]


def _grade_label_sql(grade_col: str) -> str:
    return f"""
multiIf(
    upper(trimBoth(ifNull({grade_col}, ''))) IN ('TK'), 'TK',
    upper(trimBoth(ifNull({grade_col}, ''))) IN ('KN', 'K', 'KG'), 'KN',
    match(trimBoth(ifNull({grade_col}, '')), '^[0-9]+$'),
    lpad(toString(toInt32OrZero(trimBoth({grade_col}))), 2, '0'),
    if(trimBoth(ifNull({grade_col}, '')) = '', 'Unknown', trimBoth({grade_col}))
)"""


_TEST_GRADE_LABEL = _grade_label_sql('tg.grade')


def _grade_display_sql(label_expr: str) -> str:
    froms = ', '.join(repr(x) for x in _GRADE_FROM)
    tos = ', '.join(repr(x) for x in _GRADE_TO)
    return f"transform(({label_expr}), [{froms}], [{tos}], ({label_expr}))"


_TEST_GRADE_DISPLAY = _grade_display_sql(_TEST_GRADE_LABEL)

_LEVEL_BAND = f"""
multiIf(
    ({_TEST_GRADE_LABEL}) IN ('TK','KN','01','02','03','04','05'), 'Elementary',
    ({_TEST_GRADE_LABEL}) IN ('06','07','08'), 'Middle',
    ({_TEST_GRADE_LABEL}) IN ('09','10','11','12'), 'High',
    'Other'
)"""

_SCHOOL_NAME_SQL = """
multiIf(
    lower(trim(ifNull(tg.school, ''))) IN ('', 'unknown', 'n/a', 'na', 'nan', 'none', 'null'),
    'Non-DS School',
    trim(tg.school)
)"""

_ENROLLED_SCHOOL_SQL = """
multiIf(
    lower(trim(ifNull(cs.school, ''))) IN ('', 'unknown', 'n/a', 'na', 'nan', 'none', 'null'),
    'Non-DS School',
    trim(cs.school)
)"""

_RESULTS_JOINS = f"""
FROM results_with_tests AS r
INNER JOIN current_student AS cs
    ON cs.ssid = r.ssid
   AND cs.school_year = '{{{{This School Year}}}}'
   AND {_STUDENT_STATUS_MATCH}
LEFT JOIN current_student AS tg
    ON tg.ssid = r.ssid AND tg.school_year = r.school_year
"""

_HAS_PL = f"""
(r.pl IS NOT NULL AND NOT ({_SCORE_BLANK.format(col='r.pl')}))"""

_STUDENT_SELECT = f"""
    r.ssid AS ssid,
    cs.stu_annual_key AS stu_annual_key,
    coalesce(cs.student_name, '') AS student_name,
    coalesce(tg.grade, '') AS grade,
    ({_TEST_GRADE_LABEL}) AS grade_label,
    ({_TEST_GRADE_DISPLAY}) AS grade_display,
    ({_LEVEL_BAND}) AS level_band,
    ({_SCHOOL_NAME_SQL}) AS school,
    concat(({_SCHOOL_NAME_SQL}), ' · ', coalesce(r.test_window, '')) AS school_window,
    ({_ENROLLED_SCHOOL_SQL}) AS enrolled_school,
    coalesce(tg.school_state_id, '') AS school_state_id,
    coalesce(cs.school_year, '') AS school_year,
    coalesce(r.school_year, '') AS test_year,
    coalesce(r.test_name, '') AS test_name,
    coalesce(r.test_window, '') AS test_window,
    coalesce(r.subject, '') AS subject,
    coalesce(r.pl, '') AS pl,
    coalesce(toString(r.ss), '') AS ss,
    coalesce(cs.gender, '') AS gender,
    coalesce(cs.race, '') AS race,
    coalesce(cs.ell, '') AS ell,
    coalesce(cs.sed, '') AS sed,
    coalesce(cs.sped, '') AS sped,
    coalesce(cs.status, '') AS status
"""

_STUDENT_DRILL = [
    'ssid', 'student_name', 'grade', 'grade_label', 'grade_display', 'level_band',
    'school', 'school_window', 'enrolled_school', 'school_year', 'test_year', 'test_window',
    'test_name', 'subject', 'pl', 'ss', 'gender', 'race', 'ell', 'sed', 'sped',
]


def _year_alias(year: str) -> str:
    y = str(year or '').strip()
    return f'{y[2:4]}-{y[7:9]}' if len(y) >= 9 else y


def _base_cfg(cfg, title, **overrides):
    out = {
        'chart_type': 'stacked_bar',
        'chart_title': title,
        'agg': 'count_unique',
        'x_col': '',
        'y_col': 'ssid',
        'stack_col': 'pl',
        'group_col': '',
        'series_col': 'pl',
        'sort_col': '',
        'sort_dir': 'asc',
        'bar_layout': 'stacked',
        'bar_orientation': 'vertical',
        'background': 'white',
        'legend_pos': 'bottom',
        'data_labels': True,
        'show_grid': True,
        'show_tooltip': True,
        'show_zoom': False,
        'animation': True,
        'palette': '',
        'label_position': 'inside',
        'x_label_rotation': 0,
        'y_axis_min': 0,
        'show_percent': True,
        'percent_decimals': 0,
        'y_axis_max': 100,
        'stack_order': list(cfg['stack_order']),
        'color_map': dict(cfg['colors']),
        'one_row_per_ssid': False,
    }
    out.update(overrides)
    return out


def _multi_year_cfg(cfg, **overrides):
    base = {
        'skip_page_filters': ['test_year', 'school_year'],
        'one_row_per_ssid': False,
        'multi_year_trend': True,
        'stack_order': list(cfg['stack_order']),
    }
    base.update(overrides)
    return base


CONFIGS = [
    {
        'key': 'dibels',
        'title': 'DIBELS',
        'prefix': 'DIBELS •',
        'slug': 'dibels',
        'folder': 'dibels',
        'menu_order': 3,
        'test_match': "lower(trim(r.test_name)) = 'dibbles'",
        'series': [('', None)],
        'test_year_default': '__sy_prev1__',
        'window_default': 'EOY',
        'window_order': ['BOY', 'MOY', 'EOY'],
        'grade_order': ['Kindergarten', '1st', '2nd', '3rd', '4th', '5th'],
        'grade_label_filter': ['KN', '01', '02', '03', '04', '05'],
        'stack_order': ['Above Benchmark', 'At Benchmark', 'Below Benchmark', 'Well Below Benchmark'],
        'colors': {
            'Above Benchmark': '#4e79a7',
            'At Benchmark': '#59a14f',
            'Below Benchmark': '#edc948',
            'Well Below Benchmark': '#e15759',
        },
        'description': (
            'DIBELS reading benchmark results for elementary grades K–5. '
            'Only the benchmark performance level (PL) is used. Use Window to '
            'select BOY/MOY/EOY; charts compare all available test years.'
        ),
    },
    {
        'key': 'mclass_math',
        'title': 'mClass Math',
        'prefix': 'mClass Math •',
        'slug': 'mclass-math',
        'folder': 'mclass_math',
        'menu_order': 4,
        'test_match': "lower(trim(r.test_name)) = 'mclass math'",
        'series': [('', "lower(trim(r.subject)) = 'math'")],
        'test_year_default': '__sy_this__',
        'window_default': 'BOY',
        'window_order': ['BOY', 'MOY'],
        'grade_order': ['Kindergarten', '1st', '2nd', '3rd', '4th', '5th'],
        'grade_label_filter': ['KN', '01', '02', '03', '04', '05'],
        'stack_order': ['Above Benchmark', 'At Benchmark', 'Below Benchmark', 'Well Below Benchmark'],
        'colors': {
            'Above Benchmark': '#4e79a7',
            'At Benchmark': '#59a14f',
            'Below Benchmark': '#edc948',
            'Well Below Benchmark': '#e15759',
        },
        'description': (
            'mClass Math benchmark results for elementary grades K–5. '
            'Use Window to select the assessment window; charts compare all '
            'available test years.'
        ),
    },
    {
        'key': 'iready',
        'title': 'iReady',
        'prefix': 'iReady •',
        'slug': 'iready',
        'folder': 'iready',
        'menu_order': 5,
        'test_match': "lower(trim(r.test_name)) = 'iready'",
        'series': [
            ('ELA', "lower(trim(r.subject)) = 'ela'"),
            ('Math', "lower(trim(r.subject)) = 'math'"),
        ],
        'test_year_default': '__sy_prev1__',
        'window_default': 'Spring',
        'window_order': ['Fall', 'Winter', 'Spring'],
        'grade_order': [
            'Kindergarten', '1st', '2nd', '3rd', '4th', '5th', '6th',
            '7th', '8th', '9th', '10th', '11th', '12th',
        ],
        'grade_label_filter': [
            'KN', '01', '02', '03', '04', '05', '06', '07', '08',
            '09', '10', '11', '12',
        ],
        'stack_order': [
            'Mid or Above Grade Level', 'Early On Grade Level',
            '1 Grade Level Below', '2 Grade Levels Below',
            '3 or More Grade Levels Below',
        ],
        'colors': {
            'Mid or Above Grade Level': '#4e79a7',
            'Early On Grade Level': '#59a14f',
            '1 Grade Level Below': '#edc948',
            '2 Grade Levels Below': '#f28e2b',
            '3 or More Grade Levels Below': '#e15759',
        },
        'description': (
            'iReady ELA and Math placement results. Use Window to select '
            'Fall/Winter/Spring; charts compare all available test years.'
        ),
    },
]


def _series_name(cfg, suffix: str) -> str:
    return f"{cfg['title']} {suffix}".strip()


def _dataset_name(cfg, suffix: str) -> str:
    return f"{_series_name(cfg, suffix)} • Student Results"


def _chart_prefix(cfg, suffix: str) -> str:
    return f"{_series_name(cfg, suffix)} •"


def _page_where(cfg, subject_match=None) -> str:
    clauses = [
        cfg['test_match'],
        _STUDENT_STATUS_MATCH,
        "r.ssid IS NOT NULL AND trim(r.ssid) != ''",
        _HAS_PL,
    ]
    if subject_match:
        clauses.append(subject_match)
    grade_values = cfg.get('grade_label_filter') or []
    if grade_values:
        quoted = ', '.join("'" + str(v).replace("'", "''") + "'" for v in grade_values)
        clauses.append(f"({_TEST_GRADE_LABEL}) IN ({quoted})")
    return '\n  AND '.join(clauses)


def _student_results_sql(cfg, subject_match=None) -> str:
    return f"""
SELECT DISTINCT
    r.test_id AS test_id,
{_STUDENT_SELECT}
{_RESULTS_JOINS}
WHERE {_page_where(cfg, subject_match)}
"""


def _page_filter_test_year_sql(cfg) -> str:
    return f"""
SELECT DISTINCT r.school_year AS v
{_RESULTS_JOINS}
WHERE {_page_where(cfg)}
  AND trim(coalesce(r.school_year, '')) != ''
ORDER BY v
"""


def _page_filter_window_sql(cfg) -> str:
    return f"""
SELECT DISTINCT r.test_window AS v
{_RESULTS_JOINS}
WHERE {_page_where(cfg)}
  AND trim(coalesce(r.test_window, '')) != ''
  AND lower(trim(r.test_window)) != 'nan'
ORDER BY v
"""


def _page_filter_school_sql(cfg) -> str:
    return f"""
SELECT DISTINCT ({_SCHOOL_NAME_SQL}) AS v
{_RESULTS_JOINS}
WHERE {_page_where(cfg)}
HAVING trim(v) != ''
ORDER BY v
"""


def _page_filter_enrolled_school_sql(cfg) -> str:
    return f"""
SELECT DISTINCT ({_ENROLLED_SCHOOL_SQL}) AS v
{_RESULTS_JOINS}
WHERE {_page_where(cfg)}
HAVING trim(v) != ''
ORDER BY v
"""


def _page_filter_grade_sql(cfg) -> str:
    return f"""
SELECT DISTINCT ({_TEST_GRADE_LABEL}) AS v
{_RESULTS_JOINS}
WHERE {_page_where(cfg)}
HAVING trim(v) != '' AND v != 'Unknown'
ORDER BY v
"""


def _page_filter_demographic_sql(cfg, col: str) -> str:
    return f"""
SELECT DISTINCT coalesce(cs.{col}, '') AS v
{_RESULTS_JOINS}
WHERE {_page_where(cfg)}
  AND trim(coalesce(cs.{col}, '')) != ''
ORDER BY v
"""


def _page_filters(cfg) -> list[dict]:
    return [
        {
            'id': 'test_year', 'label': 'Test Year', 'source_col': 'test_year',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_test_year_sql(cfg),
            'widget': 'select', 'default_val': cfg['test_year_default'], 'hidden': False,
        },
        {
            'id': 'school', 'label': 'Tested School', 'source_col': 'school',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_school_sql(cfg),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'enrolled_school', 'label': 'Enrolled School (current)',
            'source_col': 'enrolled_school', 'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_enrolled_school_sql(cfg),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'grade', 'label': 'Grade (tested)', 'source_col': 'grade_label',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_grade_sql(cfg),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'gender', 'label': 'Gender', 'source_col': 'gender',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_demographic_sql(cfg, 'gender'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'race', 'label': 'Race', 'source_col': 'race',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_demographic_sql(cfg, 'race'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'ell', 'label': 'ELL', 'source_col': 'ell',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_demographic_sql(cfg, 'ell'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'sed', 'label': 'SED', 'source_col': 'sed',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_demographic_sql(cfg, 'sed'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'sped', 'label': 'SPED', 'source_col': 'sped',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_demographic_sql(cfg, 'sped'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
    ]


def _dataset_specs(cfg) -> list[dict]:
    specs = []
    for suffix, subject_match in cfg['series']:
        specs.append({
            'name': _dataset_name(cfg, suffix),
            'description': f"{_series_name(cfg, suffix)} scorable PL results.",
            'columns': _STUDENT_DRILL,
            'sql': _student_results_sql(cfg, subject_match),
        })
    return specs


def _series_charts(cfg, suffix, subject_match, dataset) -> list[dict]:
    prefix = _chart_prefix(cfg, suffix)
    base_dataset = dataset
    window_order = list(cfg['window_order'])
    charts = [
        {
            'name': f'{prefix} Overall PL by Window',
            'chart_type': 'stacked_bar',
            'dataset': base_dataset,
            'drill_columns': _STUDENT_DRILL,
            'drill_through_filter_map': {'x_col': 'test_window', 'series_col': 'pl'},
            'column_defs': {
                'test_window': 'Window', 'pl': 'Performance Level', 'ssid': 'Students',
            },
            'config': _base_cfg(
                cfg, f'{_series_name(cfg, suffix)}: Overall PL by Window',
                x_col='test_window', y_col='ssid', stack_col='pl', series_col='pl',
                sort_col='__manual__', category_order=window_order,
            ),
        },
        {
            'name': f'{prefix} PL by Grade',
            'chart_type': 'stacked_bar',
            'dataset': base_dataset,
            'drill_columns': _STUDENT_DRILL,
            'drill_through_filter_map': {
                'x_col': 'grade_display,test_window', 'series_col': 'pl',
            },
            'column_defs': {
                'grade_display': 'Grade', 'test_window': 'Window',
                'pl': 'Performance Level', 'ssid': 'Students',
            },
            'config': _base_cfg(
                cfg, f'{_series_name(cfg, suffix)}: PL by Grade',
                x_col='grade_display,test_window', y_col='ssid', stack_col='pl', series_col='pl',
                sort_col='__manual__', category_order=[],
                group_gap=4, group_gap_size=1, bar_category_gap='4',
                center_group_labels=True, force_show_all_labels=True,
                show_axis_labels=False, show_axis_titles=False,
                x_label_rotation=0, x_label_font_size=14,
            ),
        },
        {
            'name': f'{prefix} PL by School',
            'chart_type': 'stacked_bar',
            'dataset': base_dataset,
            'drill_columns': _STUDENT_DRILL,
            'drill_through_filter_map': {
                'x_col': 'school,test_window', 'series_col': 'pl',
            },
            'column_defs': {
                'school': 'School', 'test_window': 'Window',
                'pl': 'Performance Level', 'ssid': 'Students',
            },
            'config': _base_cfg(
                cfg, f'{_series_name(cfg, suffix)}: PL by School',
                x_col='school,test_window', y_col='ssid', stack_col='pl', series_col='pl',
                sort_col='__manual__', category_order=[],
                group_gap=4, group_gap_size=1, bar_category_gap='4',
                center_group_labels=True, force_show_all_labels=True,
                show_axis_labels=False, show_axis_titles=False,
                x_label_rotation=0, x_label_font_size=14,
            ),
        },
        {
            'name': f'{prefix} PL by School Table',
            'chart_type': 'summary_table',
            'dataset': base_dataset,
            'drill_columns': _STUDENT_DRILL,
            'column_defs': {
                'school_window': 'School / Window', 'pl': 'Performance Level',
                'ssid': 'Students',
            },
            'config': {
                'chart_type': 'summary_table',
                'chart_title': f'{_series_name(cfg, suffix)}: PL by School and Window',
                'st_row_col': 'school_window', 'st_col_col': 'pl', 'st_col_mode': 'values',
                'st_agg_func': 'count', 'st_value_col': '',
            },
        },
    ]
    return charts


def _chart_specs(cfg) -> list[dict]:
    specs = []
    for suffix, subject_match in cfg['series']:
        specs.extend(_series_charts(cfg, suffix, subject_match, _dataset_name(cfg, suffix)))
    return specs


def _page_spec(cfg) -> dict:
    if cfg['key'] == 'iready':
        tabs = ['Overview', 'ELA', 'Math', 'Detail Tables']
        rows = [
            {'tab': 'Overview', 'label': 'ELA & Math by window', 'sections': [
                {'chart': 'iReady ELA • Overall PL by Window', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
                {'chart': 'iReady Math • Overall PL by Window', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
            ]},
            {'tab': 'ELA', 'label': 'By grade', 'sections': [
                {'chart': 'iReady ELA • PL by Grade', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'ELA', 'label': 'By school', 'sections': [
                {'chart': 'iReady ELA • PL by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'Math', 'label': 'By grade', 'sections': [
                {'chart': 'iReady Math • PL by Grade', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'Math', 'label': 'By school', 'sections': [
                {'chart': 'iReady Math • PL by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'Detail Tables', 'label': 'Counts', 'sections': [
                {'chart': 'iReady ELA • PL by School Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
                {'chart': 'iReady Math • PL by School Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
            ]},
        ]
    else:
        prefix = cfg['prefix']
        tabs = ['Overview', 'By Grade', 'By School', 'Detail Tables']
        rows = [
            {'tab': 'Overview', 'label': 'Performance levels by window', 'sections': [
                {'chart': f'{prefix} Overall PL by Window', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
            ]},
            {'tab': 'By Grade', 'label': 'Grade level breakdown', 'sections': [
                {'chart': f'{prefix} PL by Grade', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'By School', 'label': 'School breakdown', 'sections': [
                {'chart': f'{prefix} PL by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'Detail Tables', 'label': 'Counts', 'sections': [
                {'chart': f'{prefix} PL by School Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
            ]},
        ]
    return {
        'title': cfg['title'],
        'slug': cfg['slug'],
        'folder': cfg['folder'],
        'description': (
            cfg['description'] + ' Students are limited to the current school-year '
            'roster. Tested School is the testing-year school; Enrolled School '
            '(current) filters by the current enrollment school. Test Year filters the administration year; charts compare the available '
            'assessment windows. All bar '
            'charts are 100% stacked.'
        ),
        'page_filters': _page_filters(cfg),
        'tabs': tabs,
        'rows': rows,
    }


def bootstrap_django() -> str:
    project_dir = os.environ.get('DJANGO_PROJECT_DIR', '').strip()
    if not project_dir or not (Path(project_dir) / 'manage.py').exists():
        for start in (Path(__file__).resolve().parent, Path.cwd()):
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
        raise SystemExit('Could not locate the Django project. Set DJANGO_PROJECT_DIR.')
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    return project_dir


def _recent_test_years(cfg, limit: int = 5) -> list[str]:
    from dataviz.utils.data_loader import run_raw_sql

    res = run_raw_sql(f"""
        SELECT DISTINCT r.school_year AS test_year
        {_RESULTS_JOINS}
        WHERE {_page_where(cfg)}
        ORDER BY test_year DESC
        LIMIT {int(limit)}
    """)
    years = [str(r['test_year']).strip() for r in res.get('rows') or [] if r.get('test_year')]
    return list(reversed(years))


def _school_category_order(dataset_name: str, years: list[str]) -> list[str]:
    from dataviz.utils.data_loader import run_raw_sql

    ds_sql = next(s['sql'] for s in DATASET_SPECS if s['name'] == dataset_name)
    years_sql = ', '.join("'" + str(y).replace("'", "''") + "'" for y in years)
    res = run_raw_sql(f"""
        SELECT DISTINCT school
        FROM ({ds_sql})
        WHERE test_year IN ({years_sql})
        ORDER BY school
    """)
    if res.get('error'):
        return []
    schools = [
        str(r.get('school')).strip()
        for r in res.get('rows') or []
        if str(r.get('school') or '').strip()
    ]
    return [f'{school} · {year}' for school in schools for year in years]


def _school_window_category_order(dataset_name: str, windows: list[str]) -> list[str]:
    from dataviz.utils.data_loader import run_raw_sql

    ds_sql = next(s['sql'] for s in DATASET_SPECS if s['name'] == dataset_name)
    res = run_raw_sql(f"SELECT DISTINCT school FROM ({ds_sql}) ORDER BY school")
    if res.get('error'):
        return []
    schools = [
        str(r.get('school')).strip()
        for r in res.get('rows') or []
        if str(r.get('school') or '').strip()
    ]
    return [f'{school} · {window}' for school in schools for window in windows]


def _prepare_chart_specs(chart_specs: list[dict], cfg, years: list[str]) -> list[dict]:
    prepared = []
    alias_map = {y: _year_alias(y) for y in years}
    for spec in chart_specs:
        item = dict(spec)
        cfg_chart = dict(item.get('config') or {})
        cfg_chart['x_value_map'] = alias_map
        skip = set(cfg_chart.get('skip_page_filters') or [])
        if 'test_year' in skip:
            sf = dict(item.get('source_filters') or {})
            sf['test_year'] = years
            item['source_filters'] = sf
        x_col = str(cfg_chart.get('x_col') or '').replace(' ', '')
        if x_col == 'school,test_year':
            cfg_chart['category_order'] = _school_category_order(item['dataset'], years)
            cfg_chart['sort_col'] = '__manual__'
        elif x_col == 'school,test_window':
            cfg_chart['category_order'] = _school_window_category_order(
                item['dataset'], list(cfg['window_order'])
            )
            cfg_chart['sort_col'] = '__manual__'
        elif x_col == 'grade_display,test_window':
            cfg_chart['category_order'] = [
                f'{grade} · {window}'
                for grade in cfg['grade_order']
                for window in cfg['window_order']
            ]
            cfg_chart['sort_col'] = '__manual__'
        item['config'] = cfg_chart
        prepared.append(item)
    return prepared


DATASET_SPECS = []
CHART_SPECS = []
PAGE_SPECS = []
for _cfg in CONFIGS:
    DATASET_SPECS.extend(_dataset_specs(_cfg))
    CHART_SPECS.extend(_chart_specs(_cfg))
    PAGE_SPECS.append(_page_spec(_cfg))


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
    return user or User.objects.order_by('id').first()


def enable_dataset_tables():
    from landing.models import ProjectConfig

    cfg = ProjectConfig.get()
    enabled = list(cfg.enabled_dataset_tables or [])
    if not enabled:
        return
    added = []
    for table in (SOURCE_TABLE, 'current_student', 'school'):
        if table not in enabled:
            enabled.append(table)
            added.append(table)
    if added:
        cfg.enabled_dataset_tables = enabled
        cfg.save()
        print(f'Enabled dataset tables: {", ".join(added)}')


def upsert_dataset(spec, owner):
    from dataviz.models import Dataset

    mappings = {c: c for c in spec['columns']}
    dataset, _ = Dataset.objects.update_or_create(
        name=spec['name'], owner=owner,
        defaults={
            'source_type': Dataset.SOURCE_SQL,
            'source_sql': spec['sql'],
            'base_table': SOURCE_TABLE,
            'distinct': False,
            'columns': spec['columns'],
            'column_mappings': mappings,
            'joins': [], 'filters': [], 'status_filter': [], 'order_by': [],
            'description': spec['description'],
        },
    )
    print(f'  dataset ready: {dataset.name} (#{dataset.pk})')
    return dataset


def upsert_chart(spec, datasets, folder, owner):
    from dataviz.models import SavedChart

    raw_name = spec['name']
    folder_name = f'{folder} / {raw_name}'
    dataset = datasets[spec['dataset']]
    chart = SavedChart.objects.filter(owner=owner, name=folder_name).first()
    if chart is None:
        chart = SavedChart(owner=owner, name=folder_name)
    chart.chart_type = spec['chart_type']
    chart.config = spec['config']
    chart.source_type = SavedChart.SOURCE_DATASET
    chart.source_id = dataset.pk
    chart.source_sql = ''
    chart.source_filters = spec.get('source_filters', {})
    chart.column_defs = spec.get('column_defs', {})
    chart.school_filter_mode = 'enrolled'
    chart.drill_through_columns = list(spec.get('drill_columns') or [])
    chart.drill_through_filter_map = spec.get('drill_through_filter_map') or {}
    chart.description = spec['config'].get('chart_title', '')
    chart.save()
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
            'folder': spec['folder'],
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
        tab_map = {}
        for idx, tab_name in enumerate(spec.get('tabs', [])):
            tab_map[tab_name] = DataVizPageTab.objects.create(
                page=page, name=tab_name, position=idx,
            )
        for r_idx, row_spec in enumerate(spec['rows']):
            row = DataVizPageRow.objects.create(
                page=page, label=row_spec.get('label', ''),
                position=r_idx, tab=tab_map.get(row_spec.get('tab')),
            )
            for s_idx, sec in enumerate(row_spec['sections']):
                DataVizPageSection.objects.create(
                    page=page, row=row,
                    element_type=DataVizPageSection.TYPE_CHART,
                    chart=charts[sec['chart']],
                    show_title=False, gs_x=sec.get('gs_x', 0), gs_y=0,
                    gs_w=sec.get('gs_w', 6), gs_h=sec.get('gs_h', 6),
                    position=s_idx,
                )
    publish_page(page, owner)
    print(f'  page published: {page.title} -> /dataviz/pages/{page.slug}/')
    return page


def attach_to_menu(page, order: int):
    from users.models_pages import Page

    category, _ = Page.objects.update_or_create(
        slug='assessments',
        defaults={
            'name': 'Assessments',
            'app_reference': 'assessments',
            'description': 'State and local assessment dashboards',
            'url_path': '', 'status': 'active',
            'is_menu_category': True, 'show_in_menu': True,
            'menu_order': 54, 'required_roles': [], 'is_public': False,
        },
    )
    if page.linked_site_page_id:
        site = Page.objects.get(pk=page.linked_site_page_id)
        site.parent_page = category
        site.menu_order = order
        site.show_in_menu = True
        site.save(update_fields=['parent_page', 'menu_order', 'show_in_menu'])
    print(f'  menu: Assessments -> {page.title} (order {order})')


def qa_dataviz() -> int:
    from dataviz.utils.data_loader import run_raw_sql

    failures = []

    def fail(msg):
        failures.append(msg)
        print(f'  FAIL {msg}')

    print('=== QA: dataset counts ===')
    for cfg in CONFIGS:
        for suffix, subject_match in cfg['series']:
            ds_name = _dataset_name(cfg, suffix)
            ds_sql = next(s['sql'] for s in DATASET_SPECS if s['name'] == ds_name)
            ds_res = run_raw_sql(f"""
                SELECT test_year, count() AS n_rows, uniqExact(ssid) AS n_stu
                FROM ({ds_sql})
                GROUP BY test_year ORDER BY test_year
            """)
            if ds_res.get('error'):
                fail(f'{ds_name}: {ds_res["error"]}')
                continue
            src_res = run_raw_sql(f"""
                SELECT r.school_year AS test_year, count() AS n_results
                {_RESULTS_JOINS}
                WHERE {_page_where(cfg, subject_match)}
                GROUP BY r.school_year ORDER BY r.school_year
            """)
            if src_res.get('error'):
                fail(f'{ds_name} source: {src_res["error"]}')
                continue
            dm = {r['test_year']: r for r in ds_res.get('rows') or []}
            sm = {r['test_year']: r for r in src_res.get('rows') or []}
            for year in sorted(set(dm) | set(sm)):
                dv = dm.get(year, {})
                sv = sm.get(year, {})
                if dv.get('n_rows') != sv.get('n_results'):
                    fail(f'{ds_name} {year}: {dv.get("n_rows", 0)} != {sv.get("n_results", 0)}')
                else:
                    print(f'  ok  {ds_name} {year}: {dv.get("n_rows", 0)} rows / {dv.get("n_stu", 0)} students')

    print('=== QA: chart config and drill maps ===')
    dataset_columns = {s['name']: set(s['columns']) for s in DATASET_SPECS}
    for cfg in CONFIGS:
        years = _recent_test_years(cfg)
        charts = _prepare_chart_specs(_chart_specs(cfg), cfg, years)
        for spec in charts:
            name = spec['name']
            cols = dataset_columns.get(spec['dataset'])
            if not cols:
                fail(f'{name}: unknown dataset')
                continue
            refs = set(spec.get('drill_columns') or [])
            for key in ('x_col', 'y_col', 'stack_col', 'group_col', 'series_col',
                        'st_row_col', 'st_col_col'):
                val = spec['config'].get(key)
                if isinstance(val, str) and val.strip():
                    refs.update(p.strip() for p in val.split(',') if p.strip())
            missing = sorted(refs - cols)
            if missing:
                fail(f'{name}: unknown columns {missing}')
            fmap = spec.get('drill_through_filter_map') or {}
            for key in ('x_col', 'series_col', 'group_col'):
                val = fmap.get(key)
                if isinstance(val, str):
                    bad = [p for p in val.split(',') if p.strip() and p.strip() not in cols]
                    if bad:
                        fail(f'{name}: drill map unknown columns {bad}')

    print('=== QA: filter defaults ===')
    from dataexploration.school_year_utils import resolve_school_year
    for cfg in CONFIGS:
        default_year = resolve_school_year(cfg['test_year_default'])
        if not default_year:
            fail(f'{cfg["key"]}: cannot resolve test year default {cfg["test_year_default"]}')
            continue
        years = [str(r['v']) for r in (run_raw_sql(_page_filter_test_year_sql(cfg)).get('rows') or [])]
        windows = [str(r['v']) for r in (run_raw_sql(_page_filter_window_sql(cfg)).get('rows') or [])]
        if default_year not in years:
            fail(f'{cfg["key"]}: default Test Year {default_year} not in {years}')
        else:
            print(f'  ok  {cfg["key"]}: default Test Year {default_year}')

    if failures:
        print(f'\nQA FAILED ({len(failures)} issue(s)).')
        return 1
    print('\nQA PASSED.')
    return 0


def main():
    parser = argparse.ArgumentParser(
        description='Create DIBELS, mClass Math, and iReady DataViz pages.'
    )
    parser.add_argument('--owner', default='', help='Username or email of the owner.')
    parser.add_argument('--qa', action='store_true', help='Run read-only QA and exit.')
    args = parser.parse_args()

    bootstrap_django()
    if args.qa:
        raise SystemExit(qa_dataviz())

    owner = resolve_owner(args.owner)
    if not owner:
        raise SystemExit('No users exist to own these pages.')
    print(f'Using owner: {owner}')

    enable_dataset_tables()

    all_datasets = {spec['name']: upsert_dataset(spec, owner) for spec in DATASET_SPECS}

    for cfg in CONFIGS:
        recent_years = _recent_test_years(cfg)
        print(f'{cfg["title"]}: test years {recent_years}')
        chart_specs = _prepare_chart_specs(_chart_specs(cfg), cfg, recent_years)
        charts = {
            spec['name']: upsert_chart(spec, all_datasets, cfg['folder'], owner)
            for spec in chart_specs
        }
        page = upsert_page(_page_spec(cfg), charts, owner)
        attach_to_menu(page, cfg['menu_order'])

    print('DIBELS / mClass Math / iReady pages ready.')


if __name__ == '__main__':
    main()
