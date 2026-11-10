#!/usr/bin/env python
"""Create the CAST / CAA Science DataViz datasets, charts, and page.

Uses read-only ClickHouse SQL over ``analytics_student_results`` (no new
analytics tables). CAST = ``test_name`` CAST + subject Science; CAA Science =
``test_name`` CAA + subject Science.

Prerequisite sync:

    docker compose exec django python manage.py sync_to_clickhouse \\
        --tables results,tests,results_with_tests,current_student,school

Run:

    docker compose exec django python /app/scripts/create_cast_dataviz.py

Optional: ``--owner you@example.com`` (default: first superuser).
``--discover`` prints ClickHouse test names and CAST ``other_info`` keys, then exits.
``--qa`` runs a read-only check of dataset counts, chart aggregation, filters and
drill-through mappings, then exits (no database writes).
"""
import argparse
import os
import sys
from pathlib import Path

CAST_PREFIX = 'CAST •'
CAA_PREFIX = 'CAA Science •'
CHART_FOLDER = 'cast'
DATASET_FOLDER = 'Assessments / CAST & CAA Science'
DEFAULT_OWNER_NAME = ('Carlos', 'Silva')
DEFAULT_OWNER_EMAIL = 'carlos.silva@desertsands.us'
SOURCE_TABLE = 'results_with_tests'

# Confirmed via ClickHouse discovery (Desert Sands); override if ``--discover`` differs.
CAST_TEST_MATCH = "lower(trim(r.test_name)) = 'cast'"
CAA_TEST_MATCH = "lower(trim(r.test_name)) = 'caa'"
CAA_SUBJECT_MATCH = "lower(trim(r.subject)) = 'science'"
CAST_SUBJECT_MATCH = "lower(trim(r.subject)) = 'science'"

# (display name, short chart label, primary other_info key, CAST DomainNLevel fallback)
DOMAIN_SPECS = (
    ('Earth & Space Sciences', 'Earth & Space', 'Earth & Space Sciences', 'Domain1Level'),
    ('Life Sciences', 'Life', 'Life Sciences', 'Domain2Level'),
    ('Physical Sciences', 'Physical Sciences', 'Physical Science', 'Domain3Level'),
)

DOMAIN_CHART_ORDER = [d[1] for d in DOMAIN_SPECS]
TEST_GRADE_ORDER = ['05', '08', '11', '12']
GRADE_GROUP_ORDER = ['Grade 05', 'Grade 08', 'Grade 11-12']

# ECharts stacks series in array order (first = bottom). stack_order is reversed in builder.
PL_STACK_ORDER = ['4', '3', '2', '1']
PL_LEGEND_ORDER = ['1', '2', '3', '4']
PL_COLORS = {
    '1': '#e15759',
    '2': '#edc948',
    '3': '#59a14f',
    '4': '#4e79a7',
}

MULTI_YEAR_SKIP_FILTERS = ['test_year', 'school_year']
PROGRAM_ORDER = ['ELL', 'GATE', 'SED', 'SPED', 'Migrant', 'Foster']
RACE_ORDER = [
    'Hispanic', 'White', 'Asian', 'Black or African American', 'Filipino',
    'Multiple', 'American Indian or Alaska Native',
    'Hawaiian/Pacific Islander', 'Missing',
]

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

# CAST/CAA science tabs follow the grade the student tested at, not K–12 enrollment bands.
_LEVEL_BAND = f"""
multiIf(
    ({_TEST_GRADE_LABEL}) IN ('05'), 'Elementary',
    ({_TEST_GRADE_LABEL}) IN ('08'), 'Middle',
    ({_TEST_GRADE_LABEL}) IN ('09','10','11','12'), 'High',
    'Other'
)"""

# Three reporting grades for the domain charts. Grade 12 is grouped with 11 so
# every domain chart has the same three grade categories.
_TEST_GRADE_GROUP = f"""
multiIf(
    ({_TEST_GRADE_LABEL}) = '05', 'Grade 05',
    ({_TEST_GRADE_LABEL}) = '08', 'Grade 08',
    ({_TEST_GRADE_LABEL}) IN ('11', '12'), 'Grade 11-12',
    'Other'
)"""

_SCHOOL_NAME_SQL = """
multiIf(
    lower(trim(ifNull(tg.school, ''))) IN ('', 'unknown', 'n/a', 'na', 'nan', 'none', 'null'),
    'Non-DS School',
    trim(tg.school)
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

# CAST science is taken at grades 5, 8, and high school — drop other/unknown test grades.
_CAST_TESTED_GRADES = f"({_TEST_GRADE_LABEL}) IN ('05', '08', '11', '12')"

# Page filter option lists: CAST/CAA science rows with scorable PL.
_PAGE_FILTER_SCIENCE_WHERE = f"""
(
    ({CAST_TEST_MATCH} AND {CAST_SUBJECT_MATCH})
    OR ({CAA_TEST_MATCH} AND {CAA_SUBJECT_MATCH})
)
AND {_STUDENT_STATUS_MATCH}
AND r.ssid IS NOT NULL AND trim(r.ssid) != ''
AND {_HAS_PL}
"""


def _page_filter_test_year_sql() -> str:
    return f"""
SELECT DISTINCT r.school_year AS v
{_RESULTS_JOINS}
WHERE {_PAGE_FILTER_SCIENCE_WHERE}
  AND trim(coalesce(r.school_year, '')) != ''
ORDER BY v
"""


def _page_filter_school_sql() -> str:
    return f"""
SELECT DISTINCT ({_SCHOOL_NAME_SQL}) AS v
{_RESULTS_JOINS}
WHERE {_PAGE_FILTER_SCIENCE_WHERE}
HAVING trim(v) != ''
ORDER BY v
"""


def _page_filter_grade_sql() -> str:
    return f"""
SELECT DISTINCT ({_TEST_GRADE_LABEL}) AS v
{_RESULTS_JOINS}
WHERE {_PAGE_FILTER_SCIENCE_WHERE}
HAVING trim(v) != '' AND v != 'Unknown'
ORDER BY v
"""


def _page_filter_demographic_sql(col: str) -> str:
    return f"""
SELECT DISTINCT coalesce(cs.{col}, '') AS v
{_RESULTS_JOINS}
WHERE {_PAGE_FILTER_SCIENCE_WHERE}
  AND trim(coalesce(cs.{col}, '')) != ''
ORDER BY v
"""

_STUDENT_SELECT = f"""
    r.ssid AS ssid,
    cs.stu_annual_key AS stu_annual_key,
    coalesce(cs.student_name, '') AS student_name,
    coalesce(tg.grade, '') AS grade,
    ({_TEST_GRADE_LABEL}) AS grade_label,
    ({_TEST_GRADE_GROUP}) AS tested_grade_group,
    ({_LEVEL_BAND}) AS level_band,
    ({_SCHOOL_NAME_SQL}) AS school,
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
    coalesce(cs.gate, '') AS gate,
    coalesce(cs.foster, '') AS foster,
    coalesce(cs.status, '') AS status
"""

_STUDENT_DRILL = [
    'ssid', 'student_name', 'grade', 'grade_label', 'tested_grade_group',
    'level_band', 'school',
    'school_year', 'test_year', 'test_name', 'test_window', 'pl', 'ss',
    'gender', 'race', 'ell', 'sed', 'sped', 'gate', 'foster',
]

_PROGRAM_DRILL = _STUDENT_DRILL + ['migrant', 'program']

_DOMAIN_DRILL = _STUDENT_DRILL + [
    'science_domain', 'science_domain_short', 'year_domain_label', 'domain_pl',
]

_DETAIL_DRILL = _DOMAIN_DRILL + ['school_domain']


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


def discover_cast_metadata() -> None:
    from dataviz.utils.data_loader import run_raw_sql

    print('=== CAST / CAA test names (analytics_student_results) ===')
    r = run_raw_sql("""
        SELECT lower(trim(test_name)) AS tn, lower(trim(subject)) AS subj, count() AS n
        FROM analytics_student_results
        WHERE lower(trim(test_name)) IN ('cast', 'caa')
        GROUP BY tn, subj
        ORDER BY n DESC
    """)
    for row in r.get('rows') or []:
        print(f"  {row}")

    print('=== CAST other_info keys (top) ===')
    r2 = run_raw_sql("""
        SELECT arrayJoin(JSONExtractKeys(assumeNotNull(other_info))) AS k, count() AS n
        FROM analytics_student_results
        WHERE lower(trim(test_name)) = 'cast'
          AND other_info IS NOT NULL AND other_info != ''
        GROUP BY k ORDER BY n DESC LIMIT 20
    """)
    for row in r2.get('rows') or []:
        print(f"  {row}")


def _student_results_sql(test_match: str, subject_match: str, *, cast_grades_only: bool = False) -> str:
    grade_clause = f'\n  AND {_CAST_TESTED_GRADES}' if cast_grades_only else ''
    return f"""
SELECT DISTINCT
    r.test_id AS test_id,
{_STUDENT_SELECT}
{_RESULTS_JOINS}
WHERE {test_match}
  AND {subject_match}
  AND {_PAGE_FILTER_SCIENCE_WHERE}{grade_clause}
"""


def _program_results_sql(base_sql: str) -> str:
    """Long-format program membership rows on top of a student-result query."""
    return f"""
SELECT d.*, program
FROM (
    SELECT d.*, coalesce(m.migrant, '') AS migrant
    FROM (
{base_sql}
    ) AS d
    LEFT JOIN (
        SELECT ssid, school_year, any(migrant) AS migrant
        FROM student_enroll
        WHERE ssid IS NOT NULL AND trim(ssid) != ''
        GROUP BY ssid, school_year
    ) AS m ON m.ssid = d.ssid AND m.school_year = d.test_year
) AS d
ARRAY JOIN arrayConcat(
    if(upper(trim(coalesce(d.ell, ''))) = 'EL', ['ELL'], []),
    if(upper(trim(coalesce(d.gate, ''))) = 'Y', ['GATE'], []),
    if(upper(trim(coalesce(d.sed, ''))) = 'Y', ['SED'], []),
    if(upper(trim(coalesce(d.sped, ''))) = 'Y', ['SPED'], []),
    if(upper(trim(coalesce(d.migrant, ''))) = 'Y', ['Migrant'], []),
    if(upper(trim(coalesce(d.foster, ''))) = 'Y', ['Foster'], [])
) AS program
"""


def _domain_pl_expr(primary_key: str, fallback_key: str) -> str:
    return f"""
coalesce(
    nullIf(trim(JSONExtractString(assumeNotNull(r.other_info), '{primary_key}')), ''),
    nullIf(trim(JSONExtractString(assumeNotNull(r.other_info), '{fallback_key}')), '')
)"""


def _domain_branch(label: str, short_label: str, primary_key: str, fallback_key: str, test_match: str) -> str:
    domain_pl = _domain_pl_expr(primary_key, fallback_key)
    blank = _SCORE_BLANK.format(col='domain_pl')
    valid = _VALID_PL.format(col='domain_pl')
    return f"""
SELECT DISTINCT
    r.test_id AS test_id,
{_STUDENT_SELECT},
    '{label}' AS science_domain,
    '{short_label}' AS science_domain_short,
    concat(coalesce(r.school_year, ''), char(10), '{short_label}') AS year_domain_label,
    {domain_pl} AS domain_pl
{_RESULTS_JOINS}
WHERE {test_match}
  AND {CAST_SUBJECT_MATCH}
  AND {_PAGE_FILTER_SCIENCE_WHERE}
  AND {_CAST_TESTED_GRADES}
  AND r.other_info IS NOT NULL AND trim(r.other_info) != ''
HAVING domain_pl IS NOT NULL AND NOT ({blank}) AND {valid}
"""


def _domain_results_sql() -> str:
    branches = [
        _domain_branch(label, short, primary, fallback, CAST_TEST_MATCH)
        for label, short, primary, fallback in DOMAIN_SPECS
    ]
    return '\nUNION ALL\n'.join(branches)


def _overall_detail_branch() -> str:
    """Overall PL rows for the combined detail table (all tested grades)."""
    return f"""
SELECT DISTINCT
    r.test_id AS test_id,
{_STUDENT_SELECT},
    'Overall' AS science_domain,
    'Overall' AS science_domain_short,
    concat(coalesce(r.school_year, ''), char(10), 'Overall') AS year_domain_label,
    r.pl AS domain_pl
{_RESULTS_JOINS}
WHERE {CAST_TEST_MATCH}
  AND {CAST_SUBJECT_MATCH}
  AND {_PAGE_FILTER_SCIENCE_WHERE}
"""


def _detail_results_sql() -> str:
    """Overall PL + each science domain in one long table source."""
    union_sql = f"{_overall_detail_branch()}\nUNION ALL\n{_domain_results_sql()}"
    return f"""
SELECT
    d.*,
    concat(d.school, ' — ', d.science_domain_short) AS school_domain
FROM (
{union_sql}
) AS d
"""


def _recent_test_years(limit: int = 5) -> list[str]:
    from dataviz.utils.data_loader import run_raw_sql

    r = run_raw_sql(f"""
        SELECT DISTINCT r.school_year AS test_year
        {_RESULTS_JOINS}
        WHERE {CAST_TEST_MATCH}
          AND {CAST_SUBJECT_MATCH}
          AND {_PAGE_FILTER_SCIENCE_WHERE}
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
        f'{year}\n{domain}'
        for year in test_years
        for domain in DOMAIN_CHART_ORDER
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
        'name': f'{CAST_PREFIX} Student Results',
        'description': (
            'One row per scorable CAST Science result for students on the '
            'current school-year roster (Active/Inactive). Grade and school are '
            'taken from the student\'s enrollment row in the testing year; an '
            'unknown testing-year school is reported as Non-DS School.'
        ),
        'columns': _STUDENT_DRILL,
        'sql': _student_results_sql(CAST_TEST_MATCH, CAST_SUBJECT_MATCH),
    },
    {
        'name': f'{CAA_PREFIX} Student Results',
        'description': (
            'Same current-roster layout as CAST, for CAA Science PL results.'
        ),
        'columns': _STUDENT_DRILL,
        'sql': _student_results_sql(CAA_TEST_MATCH, CAA_SUBJECT_MATCH),
    },
    {
        'name': f'{CAST_PREFIX} Science Domain Results',
        'description': (
            'Long format: one row per student, year, and CAST science domain '
            '(Earth & Space, Life, Physical) when a domain PL is present in other_info.'
        ),
        'columns': _DOMAIN_DRILL,
        'sql': _domain_results_sql(),
    },
    {
        'name': f'{CAST_PREFIX} Student Program Results',
        'description': (
            'Long format: one row per CAST result per student program membership '
            '(ELL, GATE, SED, SPED, Migrant, Foster). A student can appear in '
            'more than one program row.'
        ),
        'columns': _PROGRAM_DRILL,
        'sql': _program_results_sql(
            _student_results_sql(CAST_TEST_MATCH, CAST_SUBJECT_MATCH)
        ),
    },
    {
        'name': f'{CAST_PREFIX} PL & Domain Detail',
        'description': (
            'Combined detail source: one Overall PL row plus one row for each '
            'CAST science domain, per student and test year.'
        ),
        'columns': _DETAIL_DRILL,
        'sql': _detail_results_sql(),
    },
]


def _level_charts(prefix: str, dataset: str, level: str) -> list[dict]:
    return [
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
        {
            'name': f'{prefix} {level} PL by School',
            'chart_type': 'stacked_bar',
            'dataset': dataset,
            'source_filters': {'level_band': [level]},
            'drill_columns': _STUDENT_DRILL,
            'drill_through_filter_map': {'x_col': 'school', 'series_col': 'pl'},
            'column_defs': {'school': 'School', 'pl': 'Performance Level', 'ssid': 'Students'},
            'config': _base_cfg(
                'stacked_bar', f'{prefix.strip(" •")} — {level}: PL by School',
                x_col='school', y_col='ssid', stack_col='pl', series_col='pl',
                stack_order=PL_STACK_ORDER, color_map=PL_COLORS,
                show_percent=True, percent_decimals=0,
                y_axis_max=100, sort_col='ssid', sort_dir='desc',
            ),
        },
    ]


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


def _domain_grade_charts() -> list[dict]:
    """One chart per science domain, each showing the same three tested grades."""
    charts = []
    for label, short, _primary, _fallback in DOMAIN_SPECS:
        charts.append({
            'name': f'{CAST_PREFIX} {short} PL by Grade',
            'chart_type': 'stacked_bar',
            'dataset': f'{CAST_PREFIX} Science Domain Results',
            'source_filters': {'science_domain_short': [short]},
            'drill_columns': _DOMAIN_DRILL,
            'drill_through_filter_map': {
                'x_col': 'tested_grade_group',
                'series_col': 'domain_pl',
            },
            'column_defs': {
                'tested_grade_group': 'Grade (tested)',
                'science_domain': 'Science Domain',
                'domain_pl': 'Domain PL',
                'ssid': 'Students',
            },
            'config': _base_cfg(
                'stacked_bar', f'CAST: {label} PL by Grade',
                x_col='tested_grade_group', y_col='ssid',
                stack_col='domain_pl', series_col='domain_pl',
                stack_order=PL_STACK_ORDER, color_map=PL_COLORS,
                sort_col='__manual__', category_order=GRADE_GROUP_ORDER,
                show_percent=True, percent_decimals=0,
                y_axis_max=100,
            ),
        })
    return charts


def _demographic_charts() -> list[dict]:
    ds = f'{CAST_PREFIX} Student Results'
    program_ds = f'{CAST_PREFIX} Student Program Results'
    return [
        {
            'name': f'{CAST_PREFIX} PL by Race',
            'chart_type': 'stacked_bar',
            'dataset': ds,
            'drill_columns': _STUDENT_DRILL,
            'drill_through_filter_map': {'x_col': 'race', 'series_col': 'pl'},
            'column_defs': {'race': 'Race', 'pl': 'Performance Level', 'ssid': 'Students'},
            'config': _base_cfg(
                'stacked_bar', 'CAST: PL by Race',
                x_col='race', y_col='ssid', stack_col='pl', series_col='pl',
                stack_order=PL_STACK_ORDER, color_map=PL_COLORS,
                sort_col='__manual__', category_order=RACE_ORDER, x_label_rotation=25,
            ),
        },
        {
            'name': f'{CAST_PREFIX} PL by Student Program',
            'chart_type': 'stacked_bar',
            'dataset': program_ds,
            'drill_columns': _PROGRAM_DRILL,
            'drill_through_filter_map': {'x_col': 'program', 'series_col': 'pl'},
            'column_defs': {
                'program': 'Student Program', 'pl': 'Performance Level',
                'ssid': 'Students',
            },
            'config': _base_cfg(
                'stacked_bar', 'CAST: PL by Student Program',
                x_col='program', y_col='ssid', stack_col='pl', series_col='pl',
                stack_order=PL_STACK_ORDER, color_map=PL_COLORS,
                sort_col='__manual__', category_order=PROGRAM_ORDER,
            ),
        },
    ]


CHART_SPECS = [
    _overall_pl_chart(CAST_PREFIX, f'{CAST_PREFIX} Student Results', 'CAST'),
    _overall_pl_chart(CAA_PREFIX, f'{CAA_PREFIX} Student Results', 'CAA Science'),
    *_level_charts(CAST_PREFIX, f'{CAST_PREFIX} Student Results', 'Elementary'),
    *_level_charts(CAA_PREFIX, f'{CAA_PREFIX} Student Results', 'Elementary'),
    *_level_charts(CAST_PREFIX, f'{CAST_PREFIX} Student Results', 'Middle'),
    *_level_charts(CAA_PREFIX, f'{CAA_PREFIX} Student Results', 'Middle'),
    *_level_charts(CAST_PREFIX, f'{CAST_PREFIX} Student Results', 'High'),
    *_level_charts(CAA_PREFIX, f'{CAA_PREFIX} Student Results', 'High'),
    {
        'name': f'{CAST_PREFIX} Domain PL Mix',
        'chart_type': 'stacked_bar',
        'dataset': f'{CAST_PREFIX} Science Domain Results',
        'drill_columns': _DOMAIN_DRILL,
        'drill_through_filter_map': {'x_col': 'science_domain_short', 'series_col': 'domain_pl'},
        'column_defs': {
            'science_domain_short': 'Science Domain',
            'domain_pl': 'Domain PL',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'stacked_bar', 'CAST: Domain Performance Level Mix',
            x_col='science_domain_short', y_col='ssid', stack_col='domain_pl', series_col='domain_pl',
            stack_order=PL_STACK_ORDER, color_map=PL_COLORS,
            sort_col='__manual__', category_order=DOMAIN_CHART_ORDER,
        ),
    },
    {
        'name': f'{CAST_PREFIX} Domain PL by Year',
        'chart_type': 'stacked_bar',
        'dataset': f'{CAST_PREFIX} Science Domain Results',
        'drill_columns': _DOMAIN_DRILL,
        'drill_through_filter_map': {
            'x_col': 'test_year,science_domain_short', 'series_col': 'domain_pl',
        },
        'column_defs': {
            'year_domain_label': 'Test Year / Domain',
            'test_year': 'Test Year',
            'science_domain_short': 'Science Domain',
            'domain_pl': 'Domain PL',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'stacked_bar', 'CAST: Domain PL by Year',
            x_col='year_domain_label', y_col='ssid', stack_col='domain_pl', series_col='domain_pl',
            color_map=PL_COLORS,
            sort_col='__manual__',
            category_order=[],  # filled in main() from recent test years
            x_label_font_size=10,
            show_percent=True, percent_decimals=0,
            y_axis_max=100,
            **_multi_year_cfg(),
        ),
    },
    *_domain_grade_charts(),
    *_demographic_charts(),
    {
        'name': f'{CAST_PREFIX} PL & Domain by School Table',
        'chart_type': 'summary_table',
        'dataset': f'{CAST_PREFIX} PL & Domain Detail',
        'drill_columns': _DETAIL_DRILL,
        'column_defs': {
            'school_domain': 'School / Domain',
            'domain_pl': 'Performance Level',
            'ssid': 'Students',
        },
        'config': _base_cfg(
            'summary_table', 'CAST: PL & Domains by School',
            st_row_col='school_domain', st_col_col='domain_pl', st_col_mode='values',
            st_agg_func='count', st_value_col='',
        ),
    },
]

PAGE_SPEC = {
    'title': 'CAST & CAA Science',
    'slug': 'cast-science',
    'folder': CHART_FOLDER,
    'description': (
        'California Science Test (CAST) and CAA Science results. '
        'Test Year selects the assessment administration (defaults to last '
        'school year). Students are limited to the current school-year roster '
        '(Active or Inactive; No_show excluded), matching Data Explorer. '
        'Grade and school come from the student\'s enrollment row in the '
        'testing year; when that school is unknown the student is grouped under '
        'Non-DS School. Elementary, Middle, and High tabs use the grade at '
        'test time (typically 5, 8, and 11–12). Only scorable performance '
        'levels (PL) are included. Domain charts use Additional Info domain PLs. '
        'The domain grade charts show one chart per science domain with Grade 05, '
        'Grade 08, and Grade 11-12. Multi-year trend charts ignore the Test Year '
        'filter. The detail table combines overall PL and each science domain PL '
        'by school. Only PL 1–4 are shown (PL 1 at the '
        'bottom of stacks, PL 4 at the top). The Demographics tab compares '
        'performance levels by race and by student program membership '
        '(ELL, GATE, SED, SPED, Migrant, Foster). Click any chart to drill.'
    ),
    'page_filters': [
        {
            'id': 'test_year', 'label': 'Test Year', 'source_col': 'test_year',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_test_year_sql(),
            'widget': 'select', 'default_val': '__sy_prev1__', 'hidden': False,
        },
        {
            'id': 'school', 'label': 'School', 'source_col': 'school',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_school_sql(),
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
        'Science Domains',
        'Demographics',
        'Detail Tables',
    ],
    'rows': [
        {'tab': 'Overview', 'label': 'Performance levels', 'sections': [
            {'chart': f'{CAST_PREFIX} Overall PL by Year', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
            {'chart': f'{CAA_PREFIX} Overall PL by Year', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
        ]},
        {'tab': 'Elementary', 'label': 'CAST', 'sections': [
            {'chart': f'{CAST_PREFIX} Elementary PL by Year', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
            {'chart': f'{CAST_PREFIX} Elementary PL by School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
        ]},
        {'tab': 'Elementary', 'label': 'CAA Science', 'sections': [
            {'chart': f'{CAA_PREFIX} Elementary PL by Year', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
            {'chart': f'{CAA_PREFIX} Elementary PL by School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
        ]},
        {'tab': 'Middle', 'label': 'CAST', 'sections': [
            {'chart': f'{CAST_PREFIX} Middle PL by Year', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
            {'chart': f'{CAST_PREFIX} Middle PL by School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
        ]},
        {'tab': 'Middle', 'label': 'CAA Science', 'sections': [
            {'chart': f'{CAA_PREFIX} Middle PL by Year', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
            {'chart': f'{CAA_PREFIX} Middle PL by School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
        ]},
        {'tab': 'High', 'label': 'CAST', 'sections': [
            {'chart': f'{CAST_PREFIX} High PL by Year', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
            {'chart': f'{CAST_PREFIX} High PL by School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
        ]},
        {'tab': 'High', 'label': 'CAA Science', 'sections': [
            {'chart': f'{CAA_PREFIX} High PL by Year', 'gs_x': 0, 'gs_w': 6, 'gs_h': 6},
            {'chart': f'{CAA_PREFIX} High PL by School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 6},
        ]},
        {'tab': 'Science Domains', 'label': 'CAST domains', 'sections': [
            {'chart': f'{CAST_PREFIX} Domain PL Mix', 'gs_x': 0, 'gs_w': 6, 'gs_h': 7},
            {'chart': f'{CAST_PREFIX} Domain PL by Year', 'gs_x': 6, 'gs_w': 6, 'gs_h': 7},
        ]},
        {'tab': 'Science Domains', 'label': 'By tested grade', 'sections': [
            {'chart': f'{CAST_PREFIX} Earth & Space PL by Grade', 'gs_x': 0, 'gs_w': 4, 'gs_h': 7},
            {'chart': f'{CAST_PREFIX} Life PL by Grade', 'gs_x': 4, 'gs_w': 4, 'gs_h': 7},
            {'chart': f'{CAST_PREFIX} Physical Sciences PL by Grade', 'gs_x': 8, 'gs_w': 4, 'gs_h': 7},
        ]},
        {'tab': 'Demographics', 'label': 'Race and student programs', 'sections': [
            {'chart': f'{CAST_PREFIX} PL by Race', 'gs_x': 0, 'gs_w': 6, 'gs_h': 8},
            {'chart': f'{CAST_PREFIX} PL by Student Program', 'gs_x': 6, 'gs_w': 6, 'gs_h': 8},
        ]},
        {'tab': 'Detail Tables', 'label': 'Counts', 'sections': [
            {'chart': f'{CAST_PREFIX} PL & Domain by School Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
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
    user = (
        User.objects.filter(
            first_name__iexact=DEFAULT_OWNER_NAME[0],
            last_name__iexact=DEFAULT_OWNER_NAME[1],
        ).first()
        or User.objects.filter(email__iexact=DEFAULT_OWNER_EMAIL).first()
        or User.objects.filter(is_superuser=True).order_by('id').first()
        or User.objects.order_by('id').first()
    )
    if not user:
        raise SystemExit('No users exist to own the CAST page.')
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
    from dataviz.models import Dataset, DataVizDatasetFolder

    mappings = {c: c for c in spec['columns']}
    folder = spec.get('folder', DATASET_FOLDER)
    dataset = Dataset.objects.filter(name=spec['name']).order_by('id').first()
    if dataset is None:
        dataset = Dataset(name=spec['name'], owner=owner)
    else:
        dataset.owner = owner
    dataset.source_type = Dataset.SOURCE_SQL
    dataset.source_sql = spec['sql']
    dataset.base_table = SOURCE_TABLE
    dataset.distinct = False
    dataset.columns = spec['columns']
    dataset.column_mappings = mappings
    dataset.joins = []
    dataset.filters = []
    dataset.status_filter = []
    dataset.order_by = []
    dataset.description = spec['description']
    dataset.folder = folder
    dataset.save()
    if folder:
        parts = folder.split(' / ')
        for i in range(1, len(parts) + 1):
            DataVizDatasetFolder.objects.get_or_create(path=' / '.join(parts[:i]))
    print(f'  dataset ready: {dataset.name} (#{dataset.pk}) folder={dataset.folder or "(root)"}')
    return dataset


def upsert_chart(spec, datasets, owner):
    from dataviz.models import SavedChart

    raw_name = spec['name']
    allowed_prefixes = (CAST_PREFIX, CAA_PREFIX)
    if not raw_name.startswith(allowed_prefixes):
        raise SystemExit(f'Refusing to write chart outside CAST/CAA set: {raw_name}')
    folder_name = f'{CHART_FOLDER} / {raw_name}'
    dataset = datasets[spec['dataset']]
    chart = SavedChart.objects.filter(name=raw_name).order_by('id').first()
    if chart is None:
        chart = SavedChart.objects.filter(name=folder_name).order_by('id').first()
    if chart is None:
        chart = SavedChart(name=folder_name)
    else:
        # Rename legacy root-level charts into the assessment folder.
        chart.name = folder_name
    chart.owner = owner
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
    """Move any remaining root-level CAST/CAA charts into the cast folder."""
    from dataviz.models import SavedChart

    moved = 0
    for chart in SavedChart.objects.filter(owner=owner):
        if chart.name.startswith(f'{CHART_FOLDER} / '):
            continue
        if chart.name.startswith((CAST_PREFIX, CAA_PREFIX)):
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
            'container_width': DataVizPage.CONTAINER_XL,
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
        site.menu_order = 1
        site.show_in_menu = True
        site.save(update_fields=['parent_page', 'menu_order', 'show_in_menu'])
    print('  menu category "Assessments" updated.')


def _prepare_chart_specs(recent_years: list[str]) -> list[dict]:
    """Resolve the multi-year filters/order that depend on the live data."""
    chart_specs = []
    for spec in CHART_SPECS:
        item = dict(spec)
        cfg = dict(item.get('config') or {})
        skip = set(cfg.get('skip_page_filters') or [])
        if 'test_year' in skip and recent_years:
            sf = dict(item.get('source_filters') or {})
            sf['test_year'] = recent_years
            item['source_filters'] = sf
        if item.get('name') == f'{CAST_PREFIX} Domain PL by Year':
            cfg['category_order'] = _year_domain_category_order(recent_years)
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


def qa_cast_dataviz() -> int:
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
        (f'{CAST_PREFIX} Student Results', CAST_TEST_MATCH, CAST_SUBJECT_MATCH),
        (f'{CAA_PREFIX} Student Results', CAA_TEST_MATCH, CAA_SUBJECT_MATCH),
    ]
    for ds_name, test_match, subject_match in student_checks:
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
            WHERE {test_match} AND {subject_match} AND {_HAS_PL}
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
    domain_sql = spec_by_name[f'{CAST_PREFIX} Science Domain Results']['sql']
    ds_domain = run_raw_sql(domain_sql)
    if ds_domain.get('error'):
        fail(f'domain dataset: {ds_domain["error"]}')
        domain_rows = []
    else:
        domain_rows = ds_domain.get('rows') or []
    domain_counts: dict[tuple[str, str], int] = {}
    for row in domain_rows:
        key = (str(row.get('test_year') or ''), str(row.get('science_domain_short') or ''))
        domain_counts[key] = domain_counts.get(key, 0) + 1
    for label, short, primary, fallback in DOMAIN_SPECS:
        domain_pl = _domain_pl_expr(primary, fallback)
        blank = _SCORE_BLANK.format(col='domain_pl')
        valid = _VALID_PL.format(col='domain_pl')
        src_res = run_raw_sql(f"""
            SELECT test_year, count() AS n_results
            FROM (
                SELECT r.school_year AS test_year, {domain_pl} AS domain_pl
                {_RESULTS_JOINS}
                WHERE {CAST_TEST_MATCH} AND {CAST_SUBJECT_MATCH}
                  AND {_HAS_PL}
                  AND {_CAST_TESTED_GRADES}
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
        fail('no recent CAST test years found')
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
        (s for s in prepared if s['name'] == f'{CAST_PREFIX} Domain PL Mix'), None,
    )
    if not domain_mix:
        fail('Domain PL Mix chart spec missing')
    else:
        rows = dataset_rows.get(domain_mix['dataset']) or []
        for year in recent_years:
            work = [r for r in rows if r.get('test_year') == year]
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
                    if r.get('science_domain_short') == category
                }
                expected = sum(
                    len({
                        r.get('ssid') for r in work
                        if r.get('science_domain_short') == category
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
    parser = argparse.ArgumentParser(description='Create the CAST / CAA Science DataViz page.')
    parser.add_argument('--owner', default='', help='Username or email of the owner.')
    parser.add_argument(
        '--discover', action='store_true',
        help='Print ClickHouse CAST/CAA metadata and exit.',
    )
    parser.add_argument(
        '--qa', action='store_true',
        help='Run read-only data, chart, filter and drill QA, then exit.',
    )
    args = parser.parse_args()

    project_dir = bootstrap_django()
    if args.discover:
        discover_cast_metadata()
        return
    if args.qa:
        raise SystemExit(qa_cast_dataviz())

    print(f'Django project: {project_dir}')
    discover_cast_metadata()
    owner = resolve_owner(args.owner)
    print(f'Using owner: {owner}')

    enable_dataset_tables()

    recent_years = _recent_test_years(5)
    print(f'Recent CAST test years for multi-year charts: {recent_years}')

    chart_specs = _prepare_chart_specs(recent_years)

    datasets = {spec['name']: upsert_dataset(spec, owner) for spec in DATASET_SPECS}
    charts = {spec['name']: upsert_chart(spec, datasets, owner) for spec in chart_specs}
    move_assessment_charts_to_folder(owner)
    page = upsert_page(PAGE_SPEC, charts, owner)
    attach_to_menu(page)
    print('CAST / CAA Science page ready.')


if __name__ == '__main__':
    main()
