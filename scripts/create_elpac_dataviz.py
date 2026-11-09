#!/usr/bin/env python
"""Create the ELPAC Summative DataViz datasets, charts, and page.

Uses read-only ClickHouse SQL over ``analytics_student_results`` (no new
analytics tables). ELPAC Summative = ``test_name`` ``ELPAC_summative``.
Overall PL charts include both ``Regular`` and ``Alternative`` results;
language-domain charts use the Regular test's Oral Language and Written
Language PLs from ``other_info``.

Prerequisite sync:

    docker compose exec django python manage.py sync_to_clickhouse \\
        --tables results,tests,results_with_tests,current_student,school

Run:

    docker compose exec django python /app/scripts/create_elpac_dataviz.py

Optional: ``--owner you@example.com`` (default: first superuser).
``--discover`` prints ClickHouse ELPAC test names and ``other_info`` keys, then exits.
``--qa`` runs a read-only check of dataset counts, chart aggregation, filters and
drill-through mappings, then exits (no database writes).
"""
import argparse
import os
import sys
from pathlib import Path

ELPAC_PREFIX = 'ELPAC Summative •'
CHART_FOLDER = 'elpac_summative'
SOURCE_TABLE = 'results_with_tests'

# Confirmed via ClickHouse discovery (Desert Sands); override if ``--discover`` differs.
ELPAC_TEST_MATCH = "lower(trim(r.test_name)) = 'elpac_summative'"
REGULAR_SUBJECT_MATCH = "lower(trim(r.subject)) = 'regular'"

# (display name, short chart label, other_info PL key, domain group)
DOMAIN_SPECS = (
    ('Oral Language', 'Oral Language', 'OralLanguagePL', 'Language'),
    ('Written Language', 'Written Language', 'WrittenLanguagePL', 'Language'),
    ('Listening', 'Listening', 'ListeningPL', 'Skill'),
    ('Speaking', 'Speaking', 'SpeakingPL', 'Skill'),
    ('Reading', 'Reading', 'ReadingPL', 'Skill'),
    ('Writing', 'Writing', 'WritingPL', 'Skill'),
)

DOMAIN_CHART_ORDER = [d[1] for d in DOMAIN_SPECS]
LANGUAGE_DOMAIN_ORDER = [d[1] for d in DOMAIN_SPECS if d[3] == 'Language']
SKILL_DOMAIN_ORDER = [d[1] for d in DOMAIN_SPECS if d[3] == 'Skill']
GRADE_BAND_ORDER = ['Elementary', 'Middle', 'High']
SCHOOL_ALPHA_GROUPS = ['A-M', 'N-Z']


def _year_alias(year: str) -> str:
    """2025-2026 -> 25-26."""
    y = str(year or '').strip()
    return f'{y[2:4]}-{y[7:9]}' if len(y) >= 9 else y

# ECharts stacks series in array order (first = bottom). stack_order is reversed in builder.
PL_STACK_ORDER = ['4', '3', '2', '1']
PL_LEGEND_ORDER = ['1', '2', '3', '4']
PL_COLORS = {
    '1': '#e15759',
    '2': '#edc948',
    '3': '#59a14f',
    '4': '#4e79a7',
}

ELPI_STACK_ORDER = [
    'Level 4', 'Level 3H', 'Level 3L', 'Level 3',
    'Level 2H', 'Level 2L', 'Level 2', 'Level 1',
]
ELPI_COLORS = {
    'Level 1': '#e15759',
    'Level 2': '#f28e2b',
    'Level 2L': '#f1ce63',
    'Level 2H': '#edc948',
    'Level 3': '#9ac97a',
    'Level 3L': '#76b7b2',
    'Level 3H': '#59a14f',
    'Level 4': '#4e79a7',
}

MULTI_YEAR_SKIP_FILTERS = ['test_year', 'school_year']

# Match Data Explorer's default population: current_student rows that are
# Active or Inactive. No_show rows (and results with no current_student row)
# are excluded, exactly as append_de_student_status_where() does in the
# explorer. Restricting to Active alone silently drops historical test takers.
_STUDENT_STATUS_MATCH = "coalesce(cs.status, '') IN ('Active', 'Inactive')"

_SCORE_BLANK = (
    "trim(toString({col})) = '' OR lower(trim(toString({col}))) IN "
    "('nan','ns','n/a','na','n/s','null','none')"
)

def _grade_label_sql(grade_col: str) -> str:
    return f"""
multiIf(
    upper(trimBoth(ifNull({grade_col}, ''))) IN ('TK'), 'TK',
    upper(trimBoth(ifNull({grade_col}, ''))) IN ('KN', 'K', 'KG'), 'KN',
    match(trimBoth(ifNull({grade_col}, '')), '^[0-9]+$'),
    lpad(toString(toInt32OrZero(trimBoth({grade_col}))), 2, '0'),
    if(trimBoth(ifNull({grade_col}, '')) = '', 'Unknown', trimBoth({grade_col}))
)"""

# Grade at the time of the test (not enrollment grade in the selected school year).
_TEST_GRADE_LABEL = _grade_label_sql('tg.grade')

# ELPAC is K–12; keep the same Elementary/Middle/High reporting bands as CAST.
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

_SCHOOL_ALPHA_GROUP_SQL = f"""
multiIf(
    upper(substring(({_SCHOOL_NAME_SQL}), 1, 1)) <= 'M', 'A-M', 'N-Z'
)"""

_ENROLLED_SCHOOL_SQL = """
multiIf(
    lower(trim(ifNull(cs.school, ''))) IN ('', 'unknown', 'n/a', 'na', 'nan', 'none', 'null'),
    'Non-DS School',
    trim(cs.school)
)"""

def _short_year_sql(col: str) -> str:
    return f"concat(substring({col}, 3, 2), '-', substring({col}, 8, 2))"

_ELPI_RAW_SQL = "JSONExtractString(assumeNotNull(r.other_info), 'ELPI_level')"
_ELPI_LEVEL_SQL = f"""
multiIf(
    upper(trim(ifNull({_ELPI_RAW_SQL}, ''))) IN ('1', 'LEVEL 1'), 'Level 1',
    upper(trim(ifNull({_ELPI_RAW_SQL}, ''))) IN ('2L', 'LEVEL 2L'), 'Level 2L',
    upper(trim(ifNull({_ELPI_RAW_SQL}, ''))) IN ('2H', 'LEVEL 2H'), 'Level 2H',
    upper(trim(ifNull({_ELPI_RAW_SQL}, ''))) IN ('2', 'LEVEL 2'), 'Level 2',
    upper(trim(ifNull({_ELPI_RAW_SQL}, ''))) IN ('3L', 'LEVEL 3L'), 'Level 3L',
    upper(trim(ifNull({_ELPI_RAW_SQL}, ''))) IN ('3H', 'LEVEL 3H'), 'Level 3H',
    upper(trim(ifNull({_ELPI_RAW_SQL}, ''))) IN ('3', 'LEVEL 3'), 'Level 3',
    upper(trim(ifNull({_ELPI_RAW_SQL}, ''))) IN ('4', 'LEVEL 4'), 'Level 4',
    ''
)"""

# Membership / status / demographics come from the current school-year roster
# (matching Data Explorer's default School Year), while grade and school are
# taken from the student's enrollment row in the testing year.
_RESULTS_JOINS = f"""
FROM results_with_tests AS r
INNER JOIN current_student AS cs
    ON cs.ssid = r.ssid
   AND cs.school_year = '{{{{This School Year}}}}'
   AND {_STUDENT_STATUS_MATCH}
LEFT JOIN current_student AS tg
    ON tg.ssid = r.ssid AND tg.school_year = r.school_year
"""

_VALID_PL = "lower(trim(toString({col}))) IN ('1', '2', '3', '4')"

_HAS_PL = f"""
(r.pl IS NOT NULL AND NOT ({_SCORE_BLANK.format(col='r.pl')}) AND {_VALID_PL.format(col='r.pl')})"""

# Page filter option lists: ELPAC Summative rows with scorable overall PL.
_PAGE_FILTER_ELPAC_WHERE = f"""
{ELPAC_TEST_MATCH}
AND {_STUDENT_STATUS_MATCH}
AND r.ssid IS NOT NULL AND trim(r.ssid) != ''
AND {_HAS_PL}
"""


def _page_filter_test_year_sql() -> str:
    return f"""
SELECT DISTINCT r.school_year AS v
{_RESULTS_JOINS}
WHERE {_PAGE_FILTER_ELPAC_WHERE}
  AND trim(coalesce(r.school_year, '')) != ''
ORDER BY v
"""


def _page_filter_school_sql() -> str:
    return f"""
SELECT DISTINCT ({_SCHOOL_NAME_SQL}) AS v
{_RESULTS_JOINS}
WHERE {_PAGE_FILTER_ELPAC_WHERE}
HAVING trim(v) != ''
ORDER BY v
"""


def _page_filter_enrolled_school_sql() -> str:
    return f"""
SELECT DISTINCT ({_ENROLLED_SCHOOL_SQL}) AS v
{_RESULTS_JOINS}
WHERE {_PAGE_FILTER_ELPAC_WHERE}
HAVING trim(v) != ''
ORDER BY v
"""


def _page_filter_grade_sql() -> str:
    return f"""
SELECT DISTINCT ({_TEST_GRADE_LABEL}) AS v
{_RESULTS_JOINS}
WHERE {_PAGE_FILTER_ELPAC_WHERE}
HAVING trim(v) != '' AND v != 'Unknown'
ORDER BY v
"""


def _page_filter_demographic_sql(col: str) -> str:
    return f"""
SELECT DISTINCT coalesce(cs.{col}, '') AS v
{_RESULTS_JOINS}
WHERE {_PAGE_FILTER_ELPAC_WHERE}
  AND trim(coalesce(cs.{col}, '')) != ''
ORDER BY v
"""

_STUDENT_SELECT = f"""
    r.ssid AS ssid,
    cs.stu_annual_key AS stu_annual_key,
    coalesce(cs.student_name, '') AS student_name,
    coalesce(tg.grade, '') AS grade,
    ({_TEST_GRADE_LABEL}) AS grade_label,
    ({_LEVEL_BAND}) AS level_band,
    ({_SCHOOL_NAME_SQL}) AS school,
    ({_ENROLLED_SCHOOL_SQL}) AS enrolled_school,
    ({_SCHOOL_ALPHA_GROUP_SQL}) AS school_alpha_group,
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
    'ssid', 'student_name', 'grade', 'grade_label', 'level_band',
    'school', 'enrolled_school', 'school_alpha_group',
    'school_year', 'test_year', 'test_name', 'test_window', 'subject', 'pl', 'ss',
    'gender', 'race', 'ell', 'sed', 'sped',
]

_DOMAIN_DRILL = _STUDENT_DRILL + [
    'elpac_domain', 'elpac_domain_short', 'domain_group',
    'year_domain_label', 'domain_pl', 'school_domain',
]

_ELPI_DRILL = _STUDENT_DRILL + ['elpi_level']


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


def discover_elpac_metadata() -> None:
    from dataviz.utils.data_loader import run_raw_sql

    print('=== ELPAC test names (analytics_student_results) ===')
    r = run_raw_sql("""
        SELECT lower(trim(test_name)) AS tn, lower(trim(subject)) AS subj, count() AS n
        FROM analytics_student_results
        WHERE lower(trim(test_name)) LIKE '%elpac%'
        GROUP BY tn, subj
        ORDER BY n DESC
    """)
    for row in r.get('rows') or []:
        print(f"  {row}")

    print('=== ELPAC_summative other_info keys (top) ===')
    r2 = run_raw_sql("""
        SELECT arrayJoin(JSONExtractKeys(assumeNotNull(other_info))) AS k, count() AS n
        FROM analytics_student_results
        WHERE lower(trim(test_name)) = 'elpac_summative'
          AND other_info IS NOT NULL AND other_info != ''
        GROUP BY k ORDER BY n DESC LIMIT 20
    """)
    for row in r2.get('rows') or []:
        print(f"  {row}")


def _student_results_sql() -> str:
    return f"""
SELECT DISTINCT
    r.test_id AS test_id,
{_STUDENT_SELECT}
{_RESULTS_JOINS}
WHERE {_PAGE_FILTER_ELPAC_WHERE}
"""


def _domain_pl_expr(key: str) -> str:
    return f"""
nullIf(trim(JSONExtractString(assumeNotNull(r.other_info), '{key}')), '')"""


def _domain_branch(label: str, short_label: str, primary_key: str, group: str) -> str:
    domain_pl = _domain_pl_expr(primary_key)
    blank = _SCORE_BLANK.format(col='domain_pl')
    valid = _VALID_PL.format(col='domain_pl')
    return f"""
SELECT DISTINCT
    r.test_id AS test_id,
{_STUDENT_SELECT},
    '{label}' AS elpac_domain,
    '{short_label}' AS elpac_domain_short,
    '{group}' AS domain_group,
    concat({_short_year_sql('coalesce(r.school_year, \'\')')}, char(10), '{short_label}') AS year_domain_label,
    {domain_pl} AS domain_pl,
    concat(({_SCHOOL_NAME_SQL}), ' — ', '{short_label}') AS school_domain
{_RESULTS_JOINS}
WHERE {ELPAC_TEST_MATCH}
  AND {REGULAR_SUBJECT_MATCH}
  AND {_PAGE_FILTER_ELPAC_WHERE}
  AND r.other_info IS NOT NULL AND trim(r.other_info) != ''
HAVING domain_pl IS NOT NULL AND NOT ({blank}) AND {valid}
"""


def _domain_results_sql() -> str:
    branches = [
        _domain_branch(label, short, key, group)
        for label, short, key, group in DOMAIN_SPECS
    ]
    return '\nUNION ALL\n'.join(branches)


def _elpi_results_sql() -> str:
    return f"""
SELECT DISTINCT
    r.test_id AS test_id,
{_STUDENT_SELECT},
    ({_ELPI_LEVEL_SQL}) AS elpi_level
{_RESULTS_JOINS}
WHERE {_PAGE_FILTER_ELPAC_WHERE}
  AND r.other_info IS NOT NULL AND trim(r.other_info) != ''
HAVING elpi_level != ''
"""


def _recent_test_years(limit: int = 5) -> list[str]:
    from dataviz.utils.data_loader import run_raw_sql

    r = run_raw_sql(f"""
        SELECT DISTINCT r.school_year AS test_year
        {_RESULTS_JOINS}
        WHERE {ELPAC_TEST_MATCH}
          AND {_PAGE_FILTER_ELPAC_WHERE}
        ORDER BY test_year DESC
        LIMIT {int(limit)}
    """)
    years = [str(row['test_year']).strip() for row in (r.get('rows') or []) if row.get('test_year')]
    return list(reversed(years)) if years else []


def _multi_year_cfg(**overrides):
    """Trend charts: ignore year page filters; keep one row per student per test year."""
    base = {
        'skip_page_filters': list(MULTI_YEAR_SKIP_FILTERS),
        'one_row_per_ssid': False,
        'multi_year_trend': True,
        'stack_order': PL_STACK_ORDER,
    }
    base.update(overrides)
    return base


def _year_domain_category_order(test_years: list[str]) -> list[str]:
    return [
        f'{_year_alias(year)}\n{domain}'
        for year in test_years
        for domain in DOMAIN_CHART_ORDER
    ]


def _domain_year_category_order(test_years: list[str]) -> list[str]:
    return [
        f'{domain} · {year}'
        for domain in DOMAIN_CHART_ORDER
        for year in test_years
    ]


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
        'label_position': 'inside',
        'x_label_rotation': 0,
        'y_axis_min': 0,
        # Aggregations use count_unique(ssid) so a student is counted once per
        # (category, PL) cell. A global one-row-per-ssid dedupe is wrong for
        # long datasets (the domain dataset has one row per domain/student) and
        # is unnecessary for the wide student dataset.
        'one_row_per_ssid': False,
    }
    if chart_type == 'stacked_bar':
        # Every stacked bar is a 100% stacked chart.
        cfg.update({
            'show_percent': True,
            'percent_decimals': 0,
            'y_axis_max': 100,
        })
    cfg.update(overrides)
    return cfg


DATASET_SPECS = [
    {
        'name': f'{ELPAC_PREFIX} Student Results',
        'description': (
            'One row per scorable ELPAC Summative result for students on the '
            'current school-year roster (Active/Inactive). Includes Regular and '
            'Alternative ELPAC results. Grade and school are taken from the '
            'student\'s enrollment row in the testing year; an unknown '
            'testing-year school is reported as Non-DS School.'
        ),
        'columns': _STUDENT_DRILL,
        'sql': _student_results_sql(),
    },
    {
        'name': f'{ELPAC_PREFIX} Language Domain Results',
        'description': (
            'Long format: one row per student, year, and ELPAC domain. Includes '
            'Oral Language and Written Language for all years, plus Listening, '
            'Speaking, Reading, and Writing when present in Regular ELPAC '
            'other_info (2025-26 onward).'
        ),
        'columns': _DOMAIN_DRILL,
        'sql': _domain_results_sql(),
    },
    {
        'name': f'{ELPAC_PREFIX} ELPI Results',
        'description': (
            'One row per ELPAC Summative result with the normalized English '
            'Language Proficiency Indicator (ELPI) level.'
        ),
        'columns': _ELPI_DRILL,
        'sql': _elpi_results_sql(),
    },
]


def _level_charts(prefix: str, dataset: str, level: str) -> list[dict]:
    charts = [
        {
            'name': f'{prefix} {level} PL by Year',
            'chart_type': 'stacked_bar',
            'dataset': dataset,
            'source_filters': {'level_band': [level]},
            'drill_columns': _STUDENT_DRILL,
            'drill_through_filter_map': {'x_col': 'test_year', 'series_col': 'pl'},
            'column_defs': {
                'test_year': 'Test Year',
                'pl': 'Performance Level',
                'ssid': 'Students',
            },
            'config': _base_cfg(
                'stacked_bar', f'{prefix.strip(" •")} — {level}: PL by Year',
                x_col='test_year', y_col='ssid', stack_col='pl', series_col='pl',
                sort_col='test_year', sort_dir='asc',
                color_map=PL_COLORS,
                **_multi_year_cfg(),
            ),
        },
    ]
    charts.append({
        'name': f'{prefix} {level} PL by School',
        'chart_type': 'stacked_bar',
        'dataset': dataset,
        'source_filters': {'level_band': [level]},
        'split_schools_by_count': level == 'Elementary',
        'drill_columns': _STUDENT_DRILL,
        'drill_through_filter_map': {
            'x_col': 'school,test_year', 'series_col': 'pl',
        },
        'column_defs': {
            'school': 'School',
            'test_year': 'Test Year',
            'pl': 'Performance Level',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'stacked_bar',
            f'{prefix.strip(" •")} — {level}: PL by School',
            x_col='school,test_year', y_col='ssid', stack_col='pl', series_col='pl',
            color_map=PL_COLORS,
            group_gap=4, group_gap_size=1, bar_category_gap='4',
            center_group_labels=True, force_show_all_labels=True,
            show_axis_labels=False, show_axis_titles=False,
            x_label_rotation=0, x_label_font_size=14,
            **_multi_year_cfg(),
        ),
    })
    return charts


def _overall_pl_chart(prefix: str, dataset: str, short_title: str) -> dict:
    return {
        'name': f'{prefix} Overall PL by Year',
        'chart_type': 'stacked_bar',
        'dataset': dataset,
        'drill_columns': _STUDENT_DRILL,
        'drill_through_filter_map': {'x_col': 'test_year', 'series_col': 'pl'},
        'column_defs': {
            'test_year': 'Test Year',
            'pl': 'Performance Level',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'stacked_bar', f'{short_title}: Overall PL by Year',
            x_col='test_year', y_col='ssid', stack_col='pl', series_col='pl',
            sort_col='test_year', sort_dir='asc',
            color_map=PL_COLORS,
            show_percent=True, percent_decimals=0,
            y_axis_max=100,
            **_multi_year_cfg(),
        ),
    }


def _overall_school_charts() -> list[dict]:
    charts = []
    for group in SCHOOL_ALPHA_GROUPS:
        charts.append({
            'name': f'{ELPAC_PREFIX} Overall PL by School ({group})',
            'chart_type': 'stacked_bar',
            'dataset': f'{ELPAC_PREFIX} Student Results',
            'source_filters': {'school_alpha_group': [group]},
            'drill_columns': _STUDENT_DRILL,
            'drill_through_filter_map': {
                'x_col': 'school,test_year', 'series_col': 'pl',
            },
            'column_defs': {
                'school': 'School',
                'test_year': 'Test Year',
                'pl': 'Performance Level',
                'ssid': 'Students',
            },
            'config': _base_cfg(
                'stacked_bar', f'ELPAC Summative: Overall PL by School ({group})',
                x_col='school,test_year', y_col='ssid', stack_col='pl', series_col='pl',
                color_map=PL_COLORS,
                group_gap=4, group_gap_size=1, bar_category_gap='4',
                center_group_labels=True, force_show_all_labels=True,
                show_axis_labels=False, show_axis_titles=False,
                x_label_rotation=0, x_label_font_size=14,
                **_multi_year_cfg(),
            ),
        })
    return charts


def _elpi_school_charts() -> list[dict]:
    charts = []
    for group in SCHOOL_ALPHA_GROUPS:
        charts.append({
            'name': f'{ELPAC_PREFIX} ELPI by School ({group})',
            'chart_type': 'stacked_bar',
            'dataset': f'{ELPAC_PREFIX} ELPI Results',
            'source_filters': {'school_alpha_group': [group]},
            'drill_columns': _ELPI_DRILL,
            'drill_through_filter_map': {
                'x_col': 'school,test_year', 'series_col': 'elpi_level',
            },
            'column_defs': {
                'school': 'School',
                'test_year': 'Test Year',
                'elpi_level': 'ELPI Level',
                'ssid': 'Students',
            },
            'config': _base_cfg(
                'stacked_bar', f'ELPAC Summative: ELPI by School ({group})',
                x_col='school,test_year', y_col='ssid',
                stack_col='elpi_level', series_col='elpi_level',
                color_map=ELPI_COLORS,
                group_gap=4, group_gap_size=1, bar_category_gap='4',
                center_group_labels=True, force_show_all_labels=True,
                show_axis_labels=False, show_axis_titles=False,
                x_label_rotation=0, x_label_font_size=14,
                **_multi_year_cfg(stack_order=ELPI_STACK_ORDER),
            ),
        })
    return charts


def _domain_grade_charts() -> list[dict]:
    """One wide chart per grade level, showing every available ELPAC domain."""
    charts = []
    for level in GRADE_BAND_ORDER:
        charts.append({
            'name': f'{ELPAC_PREFIX} {level} All Domains PL',
            'chart_type': 'stacked_bar',
            'dataset': f'{ELPAC_PREFIX} Language Domain Results',
            'source_filters': {'level_band': [level]},
            'drill_columns': _DOMAIN_DRILL,
            'drill_through_filter_map': {
                'x_col': 'elpac_domain_short,test_year',
                'series_col': 'domain_pl',
            },
            'column_defs': {
                'elpac_domain_short': 'ELPAC Domain',
                'test_year': 'Test Year',
                'domain_pl': 'Domain PL',
                'ssid': 'Students',
            },
            'config': _base_cfg(
                'stacked_bar', f'ELPAC: {level} — All Domains PL by Year',
                x_col='elpac_domain_short,test_year', y_col='ssid',
                stack_col='domain_pl', series_col='domain_pl',
                color_map=PL_COLORS,
                sort_col='__manual__', category_order=[],
                group_gap=4, group_gap_size=1, bar_category_gap='4',
                center_group_labels=True, force_show_all_labels=True,
                show_axis_labels=False, show_axis_titles=False,
                x_label_rotation=0, x_label_font_size=14,
                **_multi_year_cfg(stack_order=PL_STACK_ORDER),
            ),
        })
    return charts


CHART_SPECS = [
    _overall_pl_chart(ELPAC_PREFIX, f'{ELPAC_PREFIX} Student Results', 'ELPAC Summative'),
    *_overall_school_charts(),
    *_level_charts(ELPAC_PREFIX, f'{ELPAC_PREFIX} Student Results', 'Elementary'),
    *_level_charts(ELPAC_PREFIX, f'{ELPAC_PREFIX} Student Results', 'Middle'),
    *_level_charts(ELPAC_PREFIX, f'{ELPAC_PREFIX} Student Results', 'High'),
    {
        'name': f'{ELPAC_PREFIX} Domain PL Mix',
        'chart_type': 'stacked_bar',
        'dataset': f'{ELPAC_PREFIX} Language Domain Results',
        'source_filters': {'domain_group': ['Language']},
        'drill_columns': _DOMAIN_DRILL,
        'drill_through_filter_map': {'x_col': 'elpac_domain_short', 'series_col': 'domain_pl'},
        'column_defs': {
            'elpac_domain_short': 'Language Domain',
            'domain_pl': 'Domain PL',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'stacked_bar', 'ELPAC Summative: Language Domain PL Mix',
            x_col='elpac_domain_short', y_col='ssid', stack_col='domain_pl', series_col='domain_pl',
            stack_order=PL_STACK_ORDER, color_map=PL_COLORS,
            sort_col='__manual__', category_order=DOMAIN_CHART_ORDER,
        ),
    },
    {
        'name': f'{ELPAC_PREFIX} Domain PL by Year',
        'chart_type': 'stacked_bar',
        'dataset': f'{ELPAC_PREFIX} Language Domain Results',
        'source_filters': {'domain_group': ['Language']},
        'drill_columns': _DOMAIN_DRILL,
        'drill_through_filter_map': {
            'x_col': 'test_year,elpac_domain_short', 'series_col': 'domain_pl',
        },
        'column_defs': {
            'year_domain_label': 'Test Year / Domain',
            'test_year': 'Test Year',
            'elpac_domain_short': 'Language Domain',
            'domain_pl': 'Domain PL',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'stacked_bar', 'ELPAC Summative: Domain PL by Year',
            x_col='year_domain_label', y_col='ssid', stack_col='domain_pl', series_col='domain_pl',
            color_map=PL_COLORS,
            sort_col='__manual__',
            category_order=[],  # filled in main() from recent test years
            x_label_font_size=10,
            **_multi_year_cfg(),
        ),
    },
    *_domain_grade_charts(),
    {
        'name': f'{ELPAC_PREFIX} ELPI by Year',
        'chart_type': 'stacked_bar',
        'dataset': f'{ELPAC_PREFIX} ELPI Results',
        'drill_columns': _ELPI_DRILL,
        'drill_through_filter_map': {'x_col': 'test_year', 'series_col': 'elpi_level'},
        'column_defs': {
            'test_year': 'Test Year',
            'elpi_level': 'ELPI Level',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'stacked_bar', 'ELPAC Summative: ELPI by Year',
            x_col='test_year', y_col='ssid', stack_col='elpi_level', series_col='elpi_level',
            color_map=ELPI_COLORS,
            sort_col='test_year', sort_dir='asc',
            **_multi_year_cfg(stack_order=ELPI_STACK_ORDER),
        ),
    },
    *_elpi_school_charts(),
    {
        'name': f'{ELPAC_PREFIX} ELPI by Grade Level',
        'chart_type': 'stacked_bar',
        'dataset': f'{ELPAC_PREFIX} ELPI Results',
        'drill_columns': _ELPI_DRILL,
        'drill_through_filter_map': {
            'x_col': 'level_band', 'series_col': 'elpi_level',
        },
        'column_defs': {
            'level_band': 'Grade Level (tested)',
            'elpi_level': 'ELPI Level',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'stacked_bar', 'ELPAC Summative: ELPI by Grade Level',
            x_col='level_band', y_col='ssid', stack_col='elpi_level', series_col='elpi_level',
            stack_order=ELPI_STACK_ORDER, color_map=ELPI_COLORS,
            sort_col='__manual__', category_order=GRADE_BAND_ORDER,
        ),
    },
    {
        'name': f'{ELPAC_PREFIX} Overall PL by School Table',
        'chart_type': 'summary_table',
        'dataset': f'{ELPAC_PREFIX} Student Results',
        'drill_columns': _STUDENT_DRILL,
        'column_defs': {
            'school': 'School',
            'pl': 'Performance Level',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'summary_table', 'ELPAC Summative: Overall PL by School',
            st_row_col='school', st_col_col='pl', st_col_mode='values',
            st_agg_func='count', st_value_col='',
        ),
    },
    {
        'name': f'{ELPAC_PREFIX} Language Domain PL by School Table',
        'chart_type': 'summary_table',
        'dataset': f'{ELPAC_PREFIX} Language Domain Results',
        'source_filters': {'domain_group': ['Language']},
        'drill_columns': _DOMAIN_DRILL,
        'column_defs': {
            'school_domain': 'School / Domain',
            'domain_pl': 'Performance Level',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'summary_table', 'ELPAC Summative: Language Domain PL by School',
            st_row_col='school_domain', st_col_col='domain_pl', st_col_mode='values',
            st_agg_func='count', st_value_col='',
        ),
    },
    {
        'name': f'{ELPAC_PREFIX} Skill Domain PL by School Table',
        'chart_type': 'summary_table',
        'dataset': f'{ELPAC_PREFIX} Language Domain Results',
        'source_filters': {'domain_group': ['Skill']},
        'drill_columns': _DOMAIN_DRILL,
        'column_defs': {
            'school_domain': 'School / Domain',
            'domain_pl': 'Performance Level',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'summary_table', 'ELPAC Summative: Skill Domain PL by School',
            st_row_col='school_domain', st_col_col='domain_pl', st_col_mode='values',
            st_agg_func='count', st_value_col='',
        ),
    },
]

PAGE_SPEC = {
    'title': 'ELPAC Summative',
    'slug': 'elpac-summative',
    'folder': CHART_FOLDER,
    'description': (
        'ELPAC Summative results. Test Year selects the assessment '
        'administration (defaults to last school year). Students are limited to '
        'the current school-year roster (Active or Inactive; No_show excluded), '
        'matching Data Explorer. Grade and school come from the student\'s '
        'enrollment row in the testing year; when that school is unknown the '
        'student is grouped under Non-DS School. Overall PL charts include both '
        'Regular and Alternative ELPAC results. Domain charts use Regular ELPAC '
        'Additional Info: Oral Language and Written Language in every year, plus '
        'Listening, Speaking, Reading, and Writing where available (2025-26). '
        'School charts show all available test years and are grouped by school '
        'and year. Domain-by-grade charts compare all available years side by side. '
        'Elementary schools are split into equal-sized alphabetical parts. All bar charts are '
        '100% stacked. The ELPI section shows the English Language Proficiency '
        'Indicator levels. Detail tables show Overall PL, Language Domains, and '
        'Skill Domains separately by school. The Enrolled School (current) filter limits '
        'students by their current school instead of the testing-year school. '
        'Click any chart to drill.'
    ),
    'page_filters': [
        {
            'id': 'test_year', 'label': 'Test Year', 'source_col': 'test_year',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_test_year_sql(),
            'widget': 'select', 'default_val': '__sy_prev1__', 'hidden': False,
        },
        {
            'id': 'school', 'label': 'Tested School', 'source_col': 'school',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_school_sql(),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'enrolled_school', 'label': 'Enrolled School (current)',
            'source_col': 'enrolled_school',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_enrolled_school_sql(),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'grade', 'label': 'Grade (tested)', 'source_col': 'grade_label',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_grade_sql(),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'gender', 'label': 'Gender', 'source_col': 'gender',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_demographic_sql('gender'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'race', 'label': 'Race', 'source_col': 'race',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_demographic_sql('race'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'ell', 'label': 'ELL', 'source_col': 'ell',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_demographic_sql('ell'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'sed', 'label': 'SED', 'source_col': 'sed',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_demographic_sql('sed'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'sped', 'label': 'SPED', 'source_col': 'sped',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_demographic_sql('sped'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
    ],
    'tabs': [
        'Overview',
        'Elementary',
        'Middle',
        'High',
        'Language Domains',
        'ELPI Results',
        'Detail Tables',
    ],
    'rows': [
        {'tab': 'Overview', 'label': 'Performance levels', 'sections': [
            {'chart': f'{ELPAC_PREFIX} Overall PL by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
        ]},
        {'tab': 'Overview', 'label': 'By school — A–M', 'sections': [
            {'chart': f'{ELPAC_PREFIX} Overall PL by School (A-M)', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'Overview', 'label': 'By school — N–Z', 'sections': [
            {'chart': f'{ELPAC_PREFIX} Overall PL by School (N-Z)', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'Elementary', 'label': 'By year', 'sections': [
            {'chart': f'{ELPAC_PREFIX} Elementary PL by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
        ]},
        {'tab': 'Elementary', 'label': 'By school — Part 1', 'sections': [
            {'chart': f'{ELPAC_PREFIX} Elementary PL by School (Part 1)', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'Elementary', 'label': 'By school — Part 2', 'sections': [
            {'chart': f'{ELPAC_PREFIX} Elementary PL by School (Part 2)', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'Middle', 'label': 'By year', 'sections': [
            {'chart': f'{ELPAC_PREFIX} Middle PL by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
        ]},
        {'tab': 'Middle', 'label': 'By school', 'sections': [
            {'chart': f'{ELPAC_PREFIX} Middle PL by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'High', 'label': 'By year', 'sections': [
            {'chart': f'{ELPAC_PREFIX} High PL by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
        ]},
        {'tab': 'High', 'label': 'By school', 'sections': [
            {'chart': f'{ELPAC_PREFIX} High PL by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'Language Domains', 'label': 'Oral & written language', 'sections': [
            {'chart': f'{ELPAC_PREFIX} Domain PL Mix', 'gs_x': 0, 'gs_w': 6, 'gs_h': 7},
            {'chart': f'{ELPAC_PREFIX} Domain PL by Year', 'gs_x': 6, 'gs_w': 6, 'gs_h': 7},
        ]},
        {'tab': 'Language Domains', 'label': 'Elementary — all domains (multi-year)', 'sections': [
            {'chart': f'{ELPAC_PREFIX} Elementary All Domains PL', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Language Domains', 'label': 'Middle — all domains (multi-year)', 'sections': [
            {'chart': f'{ELPAC_PREFIX} Middle All Domains PL', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Language Domains', 'label': 'High — all domains (multi-year)', 'sections': [
            {'chart': f'{ELPAC_PREFIX} High All Domains PL', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'ELPI Results', 'label': 'ELPI by year', 'sections': [
            {'chart': f'{ELPAC_PREFIX} ELPI by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 7},
        ]},
        {'tab': 'ELPI Results', 'label': 'ELPI by school — A–M', 'sections': [
            {'chart': f'{ELPAC_PREFIX} ELPI by School (A-M)', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'ELPI Results', 'label': 'ELPI by school — N–Z', 'sections': [
            {'chart': f'{ELPAC_PREFIX} ELPI by School (N-Z)', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'ELPI Results', 'label': 'ELPI by grade level', 'sections': [
            {'chart': f'{ELPAC_PREFIX} ELPI by Grade Level', 'gs_x': 0, 'gs_w': 12, 'gs_h': 7},
        ]},
        {'tab': 'Detail Tables', 'label': 'Overall counts', 'sections': [
            {'chart': f'{ELPAC_PREFIX} Overall PL by School Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
        ]},
        {'tab': 'Detail Tables', 'label': 'Language domains', 'sections': [
            {'chart': f'{ELPAC_PREFIX} Language Domain PL by School Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
        ]},
        {'tab': 'Detail Tables', 'label': 'Skill domains', 'sections': [
            {'chart': f'{ELPAC_PREFIX} Skill Domain PL by School Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
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
        raise SystemExit('No users exist to own the ELPAC page.')
    return user


def enable_dataset_tables():
    from landing.models import ProjectConfig

    cfg = ProjectConfig.get()
    enabled = list(cfg.enabled_dataset_tables or [])
    if not enabled:
        print('enabled_dataset_tables is empty; leaving unrestricted.')
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
        name=spec['name'],
        owner=owner,
        defaults={
            'source_type': Dataset.SOURCE_SQL,
            'source_sql': spec['sql'],
            'base_table': SOURCE_TABLE,
            'distinct': False,
            'columns': spec['columns'],
            'column_mappings': mappings,
            'joins': [],
            'filters': [],
            'status_filter': [],
            'order_by': [],
            'description': spec['description'],
        },
    )
    print(f'  dataset ready: {dataset.name} (#{dataset.pk})')
    return dataset


def upsert_chart(spec, datasets, owner):
    from dataviz.models import SavedChart

    raw_name = spec['name']
    allowed_prefixes = (ELPAC_PREFIX,)
    if not raw_name.startswith(allowed_prefixes):
        raise SystemExit(f'Refusing to write chart outside ELPAC set: {raw_name}')
    folder_name = f'{CHART_FOLDER} / {raw_name}'
    dataset = datasets[spec['dataset']]
    chart = SavedChart.objects.filter(owner=owner, name=raw_name).first()
    if chart is None:
        chart = SavedChart.objects.filter(owner=owner, name=folder_name).first()
    if chart is None:
        chart = SavedChart(owner=owner, name=folder_name)
    else:
        # Rename legacy root-level charts into the assessment folder.
        chart.name = folder_name
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


def move_assessment_charts_to_folder(owner):
    """Move any remaining root-level ELPAC charts into the elpac_summative folder."""
    from dataviz.models import SavedChart

    moved = 0
    for chart in SavedChart.objects.filter(owner=owner):
        if chart.name.startswith(f'{CHART_FOLDER} / '):
            continue
        if chart.name.startswith((ELPAC_PREFIX,)):
            chart.name = f'{CHART_FOLDER} / {chart.name}'
            chart.save(update_fields=['name'])
            moved += 1
    print(f'  moved {moved} legacy chart(s) into {CHART_FOLDER}/')


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
            'folder': spec.get('folder', CHART_FOLDER),
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
        slug='assessments',
        defaults={
            'name': 'Assessments',
            'app_reference': 'assessments',
            'description': 'State and local assessment dashboards',
            'url_path': '',
            'status': 'active',
            'is_menu_category': True,
            'show_in_menu': True,
            'menu_order': 54,
            'required_roles': [],
            'is_public': False,
        },
    )
    if page.linked_site_page_id:
        site = Page.objects.get(pk=page.linked_site_page_id)
        site.parent_page = category
        site.menu_order = 2
        site.show_in_menu = True
        site.save(update_fields=['parent_page', 'menu_order', 'show_in_menu'])
    print('  menu category "Assessments" updated.')


def _school_list(dataset_name: str, test_years: list[str],
                 level: str = '', alpha_group: str = '') -> list[str]:
    """Return the alphabetized schools present for the given slice."""
    from dataviz.utils.data_loader import run_raw_sql

    ds_sql = next(s['sql'] for s in DATASET_SPECS if s['name'] == dataset_name)
    years_sql = ', '.join("'" + str(y).replace("'", "''") + "'" for y in test_years)
    conds = [f"test_year IN ({years_sql})"]
    if level:
        conds.append("level_band = '" + str(level).replace("'", "''") + "'")
    if alpha_group:
        conds.append("school_alpha_group = '" + str(alpha_group).replace("'", "''") + "'")
    res = run_raw_sql(f"""
        SELECT DISTINCT school
        FROM ({ds_sql})
        WHERE {' AND '.join(conds)}
        ORDER BY school
    """)
    if res.get('error'):
        return []
    return [
        str(row.get('school')).strip()
        for row in res.get('rows') or []
        if str(row.get('school') or '').strip()
    ]


def _school_year_category_order(dataset_name: str, test_years: list[str],
                                level: str = '', alpha_group: str = '',
                                schools: list[str] | None = None) -> list[str]:
    """Build School · Year categories in school/year order (chart 112 style)."""
    school_values = schools if schools is not None else _school_list(
        dataset_name, test_years, level, alpha_group,
    )
    return [f'{school} · {year}' for school in school_values for year in test_years]


def _elementary_school_chunks(spec: dict, recent_years: list[str]) -> list[dict]:
    """Split an Elementary by-school chart into equal-sized alphabetical chunks."""
    sf = spec.get('source_filters') or {}
    level = (sf.get('level_band') or [''])[0]
    schools = _school_list(spec['dataset'], recent_years, level)
    if not schools:
        return [spec]
    chunk_count = 2
    size = max(1, (len(schools) + chunk_count - 1) // chunk_count)
    chunks = [schools[i:i + size] for i in range(0, len(schools), size)]
    out = []
    for idx, chunk in enumerate(chunks, start=1):
        item = dict(spec)
        item['name'] = f"{spec['name']} (Part {idx})"
        new_sf = dict(sf)
        new_sf['school'] = chunk
        item['source_filters'] = new_sf
        item.pop('split_schools_by_count', None)
        out.append(item)
    return out


def _prepare_chart_specs(recent_years: list[str]) -> list[dict]:
    """Resolve the multi-year filters/order that depend on the live data."""
    chart_specs = []
    for spec in CHART_SPECS:
        items = (
            _elementary_school_chunks(spec, recent_years)
            if spec.get('split_schools_by_count')
            else [spec]
        )
        for base_item in items:
            item = dict(base_item)
            cfg = dict(item.get('config') or {})
            if recent_years:
                cfg['x_value_map'] = {year: _year_alias(year) for year in recent_years}
            skip = set(cfg.get('skip_page_filters') or [])
            if 'test_year' in skip and recent_years:
                sf = dict(item.get('source_filters') or {})
                sf['test_year'] = recent_years
                item['source_filters'] = sf
            if item.get('name') == f'{ELPAC_PREFIX} Domain PL by Year':
                cfg['category_order'] = _year_domain_category_order(recent_years)
            if str(cfg.get('x_col') or '').replace(' ', '') == 'elpac_domain_short,test_year':
                cfg['category_order'] = _domain_year_category_order(recent_years)
                cfg['sort_col'] = '__manual__'
            if str(cfg.get('x_col') or '').replace(' ', '') == 'school,test_year':
                sf = item.get('source_filters') or {}
                level = (sf.get('level_band') or [''])[0]
                alpha_group = (sf.get('school_alpha_group') or [''])[0]
                years = sf.get('test_year') or recent_years
                cfg['category_order'] = _school_year_category_order(
                    item['dataset'], years, level, alpha_group,
                    schools=sf.get('school'),
                )
                cfg['sort_col'] = '__manual__'
            item['config'] = cfg
            chart_specs.append(item)
    return chart_specs


def _qa_compare_counts(label, dataset_rows, source_rows, key='test_year'):
    """Compare two {key: row} maps and print pass/fail lines."""
    failures = []
    for bucket in sorted(set(dataset_rows) | set(source_rows)):
        ds = dataset_rows.get(bucket) or {}
        src = source_rows.get(bucket) or {}
        if ds.get('n_rows') != src.get('n_results'):
            msg = (
                f'{label} {bucket}: dataset {ds.get("n_rows", 0)} rows != '
                f'source {src.get("n_results", 0)}'
            )
            failures.append(msg)
            print(f'  FAIL {msg}')
        else:
            extra = ''
            if ds.get('n_stu') is not None:
                extra = f' / {ds.get("n_stu")} students'
            print(f'  ok  {label} {bucket}: {ds.get("n_rows", 0)} rows{extra}')
    return failures


def qa_elpac_dataviz() -> int:
    """Read-only QA of dataset counts, chart config, filters, and drill maps."""
    from types import SimpleNamespace

    from django.http import QueryDict

    from dataviz.utils.data_loader import run_raw_sql
    from dataviz.utils.drill_through import build_drill_filter, extract_page_filters

    failures: list[str] = []

    def fail(msg):
        failures.append(msg)
        print(f'  FAIL {msg}')

    spec_by_name = {spec['name']: spec for spec in DATASET_SPECS}

    print('=== QA: student dataset counts vs ClickHouse source ===')
    student_checks = [
        (f'{ELPAC_PREFIX} Student Results', ELPAC_TEST_MATCH),
    ]
    for ds_name, test_match in student_checks:
        ds_sql = spec_by_name[ds_name]['sql']
        ds_res = run_raw_sql(f"""
            SELECT test_year, count() AS n_rows, uniqExact(ssid) AS n_stu,
                   uniqExact(test_id) AS n_tests
            FROM ({ds_sql})
            GROUP BY test_year ORDER BY test_year
        """)
        if ds_res.get('error'):
            fail(f'{ds_name}: {ds_res["error"]}')
            continue
        src_res = run_raw_sql(f"""
            SELECT r.school_year AS test_year, count() AS n_results,
                   uniqExact(r.test_id) AS n_tests
            {_RESULTS_JOINS}
            WHERE {test_match} AND {_HAS_PL}
            GROUP BY r.school_year ORDER BY r.school_year
        """)
        if src_res.get('error'):
            fail(f'{ds_name} source: {src_res["error"]}')
            continue
        ds_map = {r['test_year']: r for r in ds_res.get('rows') or []}
        src_map = {r['test_year']: r for r in src_res.get('rows') or []}
        failures.extend(_qa_compare_counts(ds_name, ds_map, src_map))
        for year, row in ds_map.items():
            if row.get('n_rows') != row.get('n_tests'):
                fail(f'{ds_name} {year}: {row.get("n_rows")} rows but {row.get("n_tests")} tests')

    print('=== QA: domain dataset counts vs ClickHouse source ===')
    domain_sql = spec_by_name[f'{ELPAC_PREFIX} Language Domain Results']['sql']
    ds_domain = run_raw_sql(domain_sql)
    if ds_domain.get('error'):
        fail(f'domain dataset: {ds_domain["error"]}')
        domain_rows = []
    else:
        domain_rows = ds_domain.get('rows') or []
    domain_counts: dict[tuple[str, str], int] = {}
    for row in domain_rows:
        key = (str(row.get('test_year') or ''), str(row.get('elpac_domain_short') or ''))
        domain_counts[key] = domain_counts.get(key, 0) + 1
    for label, short, key, _group in DOMAIN_SPECS:
        domain_pl = _domain_pl_expr(key)
        blank = _SCORE_BLANK.format(col='domain_pl')
        valid = _VALID_PL.format(col='domain_pl')
        src_res = run_raw_sql(f"""
            SELECT test_year, count() AS n_results
            FROM (
                SELECT r.school_year AS test_year, {domain_pl} AS domain_pl
                {_RESULTS_JOINS}
                WHERE {ELPAC_TEST_MATCH} AND {REGULAR_SUBJECT_MATCH}
                  AND {_HAS_PL}
                  AND r.other_info IS NOT NULL AND trim(r.other_info) != ''
            )
            WHERE domain_pl IS NOT NULL AND NOT ({blank}) AND {valid}
            GROUP BY test_year ORDER BY test_year
        """)
        if src_res.get('error'):
            fail(f'domain {short} source: {src_res["error"]}')
            continue
        src_map = {
            r['test_year']: {'n_results': r['n_results']}
            for r in src_res.get('rows') or []
        }
        ds_map = {
            year: {'n_rows': n}
            for (year, domain), n in domain_counts.items()
            if domain == short
        }
        failures.extend(_qa_compare_counts(f'Domain {short}', ds_map, src_map))

    print('=== QA: chart configuration and drill maps ===')
    recent_years = _recent_test_years(5)
    if not recent_years:
        fail('no recent ELPAC test years found')
    prepared = _prepare_chart_specs(recent_years)
    default_year = _resolve_qa_default_year()
    dataset_columns = {spec['name']: set(spec['columns']) for spec in DATASET_SPECS}

    # Load each dataset once for representative drill clicks.
    dataset_rows = {}
    for spec in DATASET_SPECS:
        res = run_raw_sql(spec['sql'])
        if res.get('error'):
            fail(f'{spec["name"]}: {res["error"]}')
            dataset_rows[spec['name']] = []
        else:
            dataset_rows[spec['name']] = res.get('rows') or []

    _BLANK = {'', 'nan', 'NaN', 'none', 'None', 'null', 'NULL', 'n/a', 'N/A'}

    def _matches(row, filters):
        for key, val in (filters or {}).items():
            allowed = {str(v).strip() for v in (val if isinstance(val, list) else [val])}
            if str(row.get(key) or '').strip() not in allowed:
                return False
        return True

    for spec in prepared:
        name = spec['name']
        cfg = spec['config']
        columns = dataset_columns.get(spec['dataset'])
        if not columns:
            fail(f'{name}: unknown dataset {spec["dataset"]}')
            continue

        refs = set(spec.get('drill_columns') or [])
        for key in (
            'x_col', 'y_col', 'stack_col', 'group_col', 'series_col',
            'st_row_col', 'st_col_col', 'st_value_col',
        ):
            val = cfg.get(key)
            if isinstance(val, str) and val.strip():
                refs.update(part.strip() for part in val.split(',') if part.strip())
        missing = sorted(refs - columns)
        if missing:
            fail(f'{name}: config references unknown columns {missing}')

        fmap = spec.get('drill_through_filter_map') or {}
        for key in ('x_col', 'series_col', 'group_col'):
            val = fmap.get(key)
            if not isinstance(val, str):
                continue
            bad = [part for part in val.split(',') if part.strip() and part.strip() not in columns]
            if bad:
                fail(f'{name}: drill map {key} references unknown columns {bad}')

        if spec['chart_type'] == 'stacked_bar' and cfg.get('one_row_per_ssid'):
            fail(f'{name}: global one-row-per-ssid dedupe collapses long-form data')
        if spec['chart_type'] == 'stacked_bar' and not cfg.get('show_percent'):
            fail(f'{name}: stacked bar is not configured as 100% stacked')
        skip = set(cfg.get('skip_page_filters') or [])
        if 'test_year' in skip and not (spec.get('source_filters') or {}).get('test_year'):
            fail(f'{name}: ignores Test Year but has no explicit source_filters')

        # Representative click using a real dataset row.
        rows = dataset_rows.get(spec['dataset']) or []
        source_filters = dict(spec.get('source_filters') or {})
        candidates = [
            row for row in rows
            if _matches(row, source_filters)
            and ('test_year' in skip or row.get('test_year') == default_year)
        ]
        params = {}
        row = None
        if spec['chart_type'] == 'summary_table':
            row_col = cfg.get('st_row_col', '')
            col_col = cfg.get('st_col_col', '')
            row = next(
                (r for r in candidates
                 if str(r.get(row_col) or '').strip() not in _BLANK
                 and str(r.get(col_col) or '').strip() not in _BLANK),
                None,
            )
            if row:
                params['st_row_val'] = row.get(row_col)
                params['st_col_val'] = row.get(col_col)
        else:
            x_cols = [
                part.strip()
                for part in str(fmap.get('x_col') or cfg.get('x_col') or '').split(',')
                if part.strip()
            ]
            group_col = fmap.get('group_col') or cfg.get('group_col') or ''
            series_col = fmap.get('series_col') or cfg.get('series_col') or cfg.get('stack_col') or ''
            row = next(
                (r for r in candidates
                 if all(str(r.get(c) or '').strip() not in _BLANK for c in x_cols)
                 and (not series_col or str(r.get(series_col) or '').strip() not in _BLANK)),
                None,
            )
            if row:
                parts = [str(row.get(c)).strip() for c in x_cols]
                params['x_val'] = '\n'.join(parts) if len(parts) > 1 else (parts[0] if parts else '')
                series_val = str(row.get(series_col)).strip() if series_col else ''
                if group_col and series_val:
                    series_val = f'{str(row.get(group_col)).strip()} — {series_val}'
                params['series_val'] = series_val

        if not row:
            fail(f'{name}: no representative row for a drill click')
            continue

        fake_chart = SimpleNamespace(
            chart_type=spec['chart_type'],
            config=cfg,
            drill_through_filter_map=fmap,
            column_defs=spec.get('column_defs') or {},
        )
        drill_filters = build_drill_filter(fake_chart, params)
        bad_keys = sorted(k for k in drill_filters if k not in columns)
        if bad_keys:
            fail(f'{name}: drill produces unknown filter keys {bad_keys}')
        elif not drill_filters:
            fail(f'{name}: drill produced no filters for {params}')
        else:
            print(f'  ok  {name}: drill {drill_filters}')

        pf = QueryDict(f'pf_test_year={default_year}&pf_grade=05')
        page_filters = extract_page_filters(fake_chart, pf)
        if 'test_year' in skip and 'test_year' in page_filters:
            fail(f'{name}: drill did not skip Test Year page filter')
        if 'test_year' not in skip and 'test_year' not in page_filters:
            fail(f'{name}: drill lost the Test Year page filter')

    print('=== QA: rendered domain chart totals ===')
    from dataviz.utils.chart_builder.build import build_option

    domain_mix = next(
        (s for s in prepared if s['name'] == f'{ELPAC_PREFIX} Domain PL Mix'), None,
    )
    if not domain_mix:
        fail('Domain PL Mix chart spec missing')
    else:
        rows = dataset_rows.get(domain_mix['dataset']) or []
        for year in recent_years:
            work = [
                r for r in rows
                if r.get('test_year') == year and r.get('domain_group') == 'Language'
            ]
            if not work:
                continue
            count_cfg = dict(domain_mix['config'])
            count_cfg['show_percent'] = False
            count_cfg.pop('y_axis_max', None)
            option = build_option(
                work, domain_mix.get('column_defs') or {}, count_cfg,
            )
            categories = (option.get('xAxis') or {}).get('data') or []
            series = option.get('series') or []
            pct_option = build_option(
                work, domain_mix.get('column_defs') or {}, domain_mix['config'],
            )
            pct_series = pct_option.get('series') or []
            for idx, category in enumerate(categories):
                actual = sum(
                    float((s.get('data') or [])[idx] or 0) for s in series
                )
                pct_total = sum(
                    float((s.get('data') or [])[idx] or 0) for s in pct_series
                )
                pls = {
                    r.get('domain_pl') for r in work
                    if r.get('elpac_domain_short') == category
                }
                expected = sum(
                    len({
                        r.get('ssid') for r in work
                        if r.get('elpac_domain_short') == category
                        and r.get('domain_pl') == pl
                    })
                    for pl in pls
                )
                if actual != expected:
                    fail(
                        f'Domain PL Mix {year} {category}: chart {actual} != '
                        f'dataset {expected}'
                    )
                elif not (97 <= pct_total <= 103):
                    fail(
                        f'Domain PL Mix {year} {category}: stacked total '
                        f'{pct_total} is not ~100%'
                    )
                else:
                    print(
                        f'  ok  Domain PL Mix {year} {category}: '
                        f'{actual} students / {pct_total:.0f}%'
                    )

    print('=== QA: page filter defaults ===')
    from dataexploration.school_year_utils import resolve_school_year

    default_test_year = resolve_school_year('__sy_prev1__')
    opts_res = run_raw_sql(_page_filter_test_year_sql())
    if opts_res.get('error'):
        fail(f'test-year options: {opts_res["error"]}')
    else:
        opts = [str(r.get('v')) for r in opts_res.get('rows') or []]
        if default_test_year not in opts:
            fail(f'default Test Year {default_test_year} not in filter options {opts}')
        else:
            print(f'  ok  default Test Year {default_test_year} is selectable')

    if failures:
        print(f'\nQA FAILED ({len(failures)} issue(s)).')
        return 1
    print('\nQA PASSED.')
    return 0


def _resolve_qa_default_year() -> str:
    try:
        from dataexploration.school_year_utils import resolve_school_year
        return resolve_school_year('__sy_prev1__')
    except Exception:
        return ''


def main():
    parser = argparse.ArgumentParser(description='Create the ELPAC Summative DataViz page.')
    parser.add_argument('--owner', default='', help='Username or email of the owner.')
    parser.add_argument(
        '--discover', action='store_true',
        help='Print ClickHouse ELPAC metadata and exit.',
    )
    parser.add_argument(
        '--qa', action='store_true',
        help='Run read-only data, chart, filter and drill QA, then exit.',
    )
    args = parser.parse_args()

    project_dir = bootstrap_django()
    if args.discover:
        discover_elpac_metadata()
        return
    if args.qa:
        raise SystemExit(qa_elpac_dataviz())

    print(f'Django project: {project_dir}')
    discover_elpac_metadata()
    owner = resolve_owner(args.owner)
    print(f'Using owner: {owner}')

    enable_dataset_tables()

    recent_years = _recent_test_years(5)
    print(f'Recent ELPAC test years for multi-year charts: {recent_years}')

    chart_specs = _prepare_chart_specs(recent_years)

    datasets = {spec['name']: upsert_dataset(spec, owner) for spec in DATASET_SPECS}
    charts = {spec['name']: upsert_chart(spec, datasets, owner) for spec in chart_specs}
    move_assessment_charts_to_folder(owner)
    page = upsert_page(PAGE_SPEC, charts, owner)
    attach_to_menu(page)
    print('ELPAC Summative page ready.')


if __name__ == '__main__':
    main()
