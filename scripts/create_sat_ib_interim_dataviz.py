#!/usr/bin/env python
"""Create SAT, IB, and Interim Assessments DataViz pages.

Read-only ClickHouse SQL. Pages use sectioned tabs, wide layout, filters, and
drill-through.

Run:
    docker compose exec django python /app/scripts/create_sat_ib_interim_dataviz.py
Optional:
    --owner you@example.com
    --qa
"""
import argparse
import os
import sys
from pathlib import Path

DEFAULT_OWNER_NAME = ('Carlos', 'Silva')
DEFAULT_OWNER_EMAIL = 'carlos.silva@desertsands.us'

_STUDENT_STATUS_MATCH = "coalesce(cs.status, '') IN ('Active', 'Inactive')"

_GRADE_FROM = ['KN', '01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '12']
_GRADE_TO = [
    'Kindergarten', '1st', '2nd', '3rd', '4th', '5th', '6th',
    '7th', '8th', '9th', '10th', '11th', '12th',
]


def _grade_label_sql(col: str) -> str:
    return f"""
multiIf(
    upper(trimBoth(ifNull({col}, ''))) IN ('TK'), 'TK',
    upper(trimBoth(ifNull({col}, ''))) IN ('KN', 'K', 'KG'), 'KN',
    match(trimBoth(ifNull({col}, '')), '^[0-9]+$'),
    lpad(toString(toInt32OrZero(trimBoth({col}))), 2, '0'),
    if(trimBoth(ifNull({col}, '')) = '', 'Unknown', trimBoth({col}))
)"""


_TEST_GRADE_LABEL = _grade_label_sql('cs.grade')


def _grade_display_sql(expr: str) -> str:
    froms = ', '.join(repr(x) for x in _GRADE_FROM)
    tos = ', '.join(repr(x) for x in _GRADE_TO)
    return f"transform(({expr}), [{froms}], [{tos}], ({expr}))"


_TEST_GRADE_DISPLAY = _grade_display_sql(_TEST_GRADE_LABEL)

_LEVEL_BAND = f"""
multiIf(
    ({_TEST_GRADE_LABEL}) IN ('09','10','11','12'), 'High',
    ({_TEST_GRADE_LABEL}) IN ('06','07','08'), 'Middle',
    ({_TEST_GRADE_LABEL}) IN ('TK','KN','01','02','03','04','05'), 'Elementary',
    'Other'
)"""

_COMMON_STUDENT_SELECT = f"""
    coalesce(cs.student_name, cc.student_name, '') AS student_name,
    coalesce(cs.school, cc.school, 'Non-DS School') AS school,
    coalesce(cc.school, 'Non-DS School') AS enrolled_school,
    coalesce(cs.grade, '') AS grade,
    ({_TEST_GRADE_LABEL}) AS grade_label,
    ({_TEST_GRADE_DISPLAY}) AS grade_display,
    ({_LEVEL_BAND}) AS level_band,
    coalesce(cs.gender, cc.gender, '') AS gender,
    coalesce(cs.race, cc.race, '') AS race,
    coalesce(cs.ell, cc.ell, '') AS ell,
    coalesce(cs.sed, cc.sed, '') AS sed,
    coalesce(cs.sped, cc.sped, '') AS sped,
    coalesce(cs.gate, cc.gate, '') AS gate,
    coalesce(cs.foster, cc.foster, '') AS foster,
    coalesce(cs.status, cc.status, '') AS status,
    if(dsch.state_id != '', 1, 0) AS district_school
"""

_DISTRICT_JOIN = """
LEFT JOIN (
    SELECT state_id FROM school
    WHERE state_id IS NOT NULL AND trim(state_id) != ''
    GROUP BY state_id
) AS dsch ON dsch.state_id = coalesce(cs.school_state_id, cc.school_state_id)
"""


def _joins(table: str) -> str:
    return f"""
FROM {table} AS r
LEFT JOIN current_student AS cs
    ON cs.ssid = r.ssid AND cs.school_year = r.school_year
LEFT JOIN current_student AS cc
    ON cc.ssid = r.ssid AND cc.school_year = '{{{{This School Year}}}}'
{_DISTRICT_JOIN}
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


_STUDENT_COLS = [
    'ssid', 'student_name', 'school', 'enrolled_school', 'grade', 'grade_label',
    'grade_display', 'level_band', 'gender', 'race', 'ell', 'sed', 'sped',
    'gate', 'foster', 'status', 'district_school',
]

PROGRAM_ORDER = ['ELL', 'GATE', 'SED', 'SPED', 'Migrant', 'Foster']
RACE_ORDER = [
    'Hispanic', 'White', 'Asian', 'Black or African American', 'Filipino',
    'Multiple', 'American Indian or Alaska Native',
    'Hawaiian/Pacific Islander', 'Missing',
]

SAT_YEAR_LABEL = '2026-27'
IB_YEAR_LABEL = '2024-25 & 2025-26'
SCORE_BANDS = ['Below 800', '800-999', '1000-1199', '1200-1399', '1400+']
BAND_COLORS = {
    'Below 800': '#e15759',
    '800-999': '#f28e2b',
    '1000-1199': '#edc948',
    '1200-1399': '#59a14f',
    '1400+': '#4e79a7',
}
PL_COLORS = {'1': '#e15759', '2': '#edc948', '3': '#59a14f'}
PL_STACK_ORDER = ['3', '2', '1']
INTERIM_TESTS = ['Interim Math', 'Interim ELA', 'Interim ELPAC']
INTERIM_TEST_LABELS = {
    'Interim Math': 'Math',
    'Interim ELA': 'ELA',
    'Interim ELPAC': 'ELPAC',
}


def _year_alias(year: str) -> str:
    y = str(year or '').strip()
    return f'{y[2:4]}-{y[7:9]}' if len(y) >= 9 else y


def _base_cfg(title, **overrides):
    cfg = {
        'chart_type': 'bar',
        'chart_title': title,
        'agg': 'count_unique',
        'x_col': '',
        'y_col': 'ssid',
        'stack_col': '',
        'group_col': '',
        'series_col': '',
        'sort_col': '',
        'sort_dir': 'asc',
        'bar_layout': 'grouped',
        'bar_orientation': 'vertical',
        'background': 'white',
        'legend_pos': 'bottom',
        'data_labels': True,
        'show_grid': True,
        'show_tooltip': True,
        'show_zoom': False,
        'animation': True,
        'palette': '',
        'label_position': 'top',
        'x_label_rotation': 0,
        'y_axis_min': 0,
        'one_row_per_ssid': False,
    }
    cfg.update(overrides)
    return cfg


def _stacked_cfg(title, **overrides):
    cfg = _base_cfg(
        title,
        chart_type='stacked_bar',
        bar_layout='stacked',
        label_position='inside',
    )
    cfg.update({'show_percent': True, 'percent_decimals': 0, 'y_axis_max': 100})
    cfg.update(overrides)
    return cfg


def _page_filters(joins: str, where: str, *, grade_only=None) -> list[dict]:
    grade_clause = ''
    if grade_only:
        vals = ', '.join("'" + g.replace("'", "''") + "'" for g in grade_only)
        grade_clause = f" AND ({_TEST_GRADE_LABEL}) IN ({vals})"
    school_expr = "coalesce(cs.school, cc.school, 'Non-DS School')"
    enrolled_expr = "coalesce(cc.school, 'Non-DS School')"

    def distinct(expr: str) -> str:
        return f"""
SELECT DISTINCT ({expr}) AS v
{joins}
WHERE {where}{grade_clause}
HAVING trim(v) != ''
ORDER BY v
"""

    return [
        {
            'id': 'school', 'label': 'Tested School', 'source_col': 'school',
            'source_table': 'current_student',
            'source_distinct_sql': distinct(school_expr),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'enrolled_school', 'label': 'Enrolled School (current)',
            'source_col': 'enrolled_school', 'source_table': 'current_student',
            'source_distinct_sql': distinct(enrolled_expr),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'grade', 'label': 'Grade', 'source_col': 'grade_label',
            'source_table': 'current_student',
            'source_distinct_sql': f"""
SELECT DISTINCT ({_TEST_GRADE_LABEL}) AS v
{joins}
WHERE {where}{grade_clause}
HAVING trim(v) != '' AND v != 'Unknown'
ORDER BY v
""",
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'gender', 'label': 'Gender', 'source_col': 'gender',
            'source_table': 'current_student',
            'source_distinct_sql': distinct("coalesce(cs.gender, cc.gender, '')"),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'race', 'label': 'Race', 'source_col': 'race',
            'source_table': 'current_student',
            'source_distinct_sql': distinct("coalesce(cs.race, cc.race, '')"),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'ell', 'label': 'ELL', 'source_col': 'ell',
            'source_table': 'current_student',
            'source_distinct_sql': distinct("coalesce(cs.ell, cc.ell, '')"),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'sed', 'label': 'SED', 'source_col': 'sed',
            'source_table': 'current_student',
            'source_distinct_sql': distinct("coalesce(cs.sed, cc.sed, '')"),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'sped', 'label': 'SPED', 'source_col': 'sped',
            'source_table': 'current_student',
            'source_distinct_sql': distinct("coalesce(cs.sped, cc.sped, '')"),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
    ]


# ── SAT ──────────────────────────────────────────────────────────────────────
SAT_TABLE = 'analytics_sat_results'
SAT_JOINS = _joins(SAT_TABLE)
SAT_WHERE = "r.school_year IS NOT NULL AND trim(r.school_year) != '' AND r.total_score IS NOT NULL"
SAT_SELECT = f"""
SELECT
    r.ssid AS ssid,
    r.school_year AS test_year,
    r.test_date AS test_date,
    r.total_score AS total_score,
    r.ebrw_score AS ebrw_score,
    r.math_score AS math_score,
    coalesce(r.percentile_state_total, 0) AS percentile_state_total,
    multiIf(
        r.total_score >= 1400, '1400+',
        r.total_score >= 1200, '1200-1399',
        r.total_score >= 1000, '1000-1199',
        r.total_score >= 800, '800-999',
        'Below 800'
    ) AS total_band,
{_COMMON_STUDENT_SELECT}
{SAT_JOINS}
WHERE {SAT_WHERE}
"""
SAT_DRILL = _STUDENT_COLS + [
    'test_year', 'test_date', 'total_score', 'ebrw_score', 'math_score',
    'percentile_state_total', 'total_band',
]
SAT_PROGRAM_DRILL = SAT_DRILL + ['migrant', 'program']


# ── IB ───────────────────────────────────────────────────────────────────────
IB_TABLE = 'analytics_ib_results'
IB_JOINS = _joins(IB_TABLE)
IB_WHERE = (
    "r.school_year IS NOT NULL AND trim(r.school_year) != '' "
    "AND r.subject IS NOT NULL AND trim(r.subject) != ''"
)
IB_SELECT = f"""
SELECT
    r.ssid AS ssid,
    r.school_year AS test_year,
    r.session_year AS session_year,
    r.category AS category,
    r.subject AS subject,
    r.level AS level,
    concat(r.subject, ' — ', r.level) AS subject_level,
    concat(r.category, ' — ', r.level) AS category_level,
    r.grade AS final_grade,
    r.predicted_grade AS predicted_grade,
    if(match(trim(ifNull(r.grade, '')), '^[1-7]$'), toInt32OrZero(r.grade), NULL) AS grade_num,
    if(match(trim(ifNull(r.predicted_grade, '')), '^[1-7]$'), toInt32OrZero(r.predicted_grade), NULL) AS predicted_num,
    if(match(trim(ifNull(r.grade, '')), '^[1-7]$'), 1, 0) AS grade_valid,
    if(match(trim(ifNull(r.grade, '')), '^[1-7]$'), if(toInt32OrZero(r.grade) >= 4, 100, 0), 0) AS pass_pct,
    1 AS exam_count,
{_COMMON_STUDENT_SELECT}
{IB_JOINS}
WHERE {IB_WHERE}
"""
IB_DRILL = _STUDENT_COLS + [
    'test_year', 'session_year', 'category', 'subject', 'level', 'subject_level',
    'category_level', 'final_grade', 'predicted_grade', 'grade_num', 'predicted_num',
    'grade_valid', 'pass_pct', 'exam_count',
]
IB_PROGRAM_DRILL = IB_DRILL + ['migrant', 'program']


# ── Interim ─────────────────────────────────────────────────────────────────
INTERIM_TABLE = 'results_with_tests'
INTERIM_JOINS = _joins(INTERIM_TABLE)
INTERIM_WHERE = (
    "lower(trim(r.test_name)) LIKE 'interim%' "
    "AND lower(trim(r.pl)) IN ('1','2','3') "
    "AND r.school_year IS NOT NULL AND trim(r.school_year) != ''"
)
INTERIM_SELECT = f"""
SELECT
    r.ssid AS ssid,
    r.school_year AS test_year,
    r.test_name AS test_name,
    r.subject AS subject,
    concat(r.test_name, ' — ', r.subject) AS test_subject,
    trim(toString(r.pl)) AS pl,
{_COMMON_STUDENT_SELECT}
{INTERIM_JOINS}
WHERE {INTERIM_WHERE}
"""
INTERIM_DRILL = _STUDENT_COLS + ['test_year', 'test_name', 'subject', 'test_subject', 'pl']
INTERIM_PROGRAM_DRILL = INTERIM_DRILL + ['migrant', 'program']


def _chart_specs_sat() -> list[dict]:
    ds = 'SAT • Student Results'
    return [
        {
            'name': 'SAT • Students by School',
            'chart_type': 'bar',
            'dataset': ds,
            'source_filters': {'district_school': [1]},
            'drill_columns': SAT_DRILL,
            'drill_through_filter_map': {'x_col': 'school', 'series_col': ''},
            'column_defs': {'school': 'School', 'ssid': 'Students'},
            'config': _base_cfg(
                f'SAT {SAT_YEAR_LABEL}: Students Tested by School',
                x_col='school', y_col='ssid',
                sort_col='_value', sort_dir='desc', x_label_rotation=30,
            ),
        },
        {
            'name': 'SAT • Average Scores by School',
            'chart_type': 'bar',
            'dataset': ds,
            'source_filters': {'district_school': [1]},
            'drill_columns': SAT_DRILL,
            'drill_through_filter_map': {'x_col': 'school', 'series_col': ''},
            'column_defs': {
                'school': 'School', 'total_score': 'Total',
                'ebrw_score': 'EBRW', 'math_score': 'Math',
            },
            'config': _base_cfg(
                f'SAT {SAT_YEAR_LABEL}: Average Scores by School',
                x_col='school', y_col='total_score',
                y_cols=['total_score', 'ebrw_score', 'math_score'],
                agg='avg', agg_decimals=0,
                sort_col='_value', sort_dir='desc', x_label_rotation=30,
            ),
        },
        {
            'name': 'SAT • Total Score Bands',
            'chart_type': 'bar',
            'dataset': ds,
            'source_filters': {'district_school': [1]},
            'drill_columns': SAT_DRILL,
            'drill_through_filter_map': {'x_col': 'total_band', 'series_col': ''},
            'column_defs': {'total_band': 'Total Score Band', 'ssid': 'Students'},
            'config': _base_cfg(
                f'SAT {SAT_YEAR_LABEL}: Total Score Distribution',
                x_col='total_band', y_col='ssid',
                sort_col='__manual__', category_order=SCORE_BANDS,
                color_map=BAND_COLORS,
            ),
        },
        {
            'name': 'SAT • Score Bands by School Table',
            'chart_type': 'summary_table',
            'dataset': ds,
            'source_filters': {'district_school': [1]},
            'drill_columns': SAT_DRILL,
            'column_defs': {'school': 'School', 'total_band': 'Score Band', 'ssid': 'Students'},
            'config': {
                'chart_type': 'summary_table',
                'chart_title': f'SAT {SAT_YEAR_LABEL}: Score Bands by School',
                'st_row_col': 'school', 'st_col_col': 'total_band',
                'st_col_mode': 'values', 'st_agg_func': 'count', 'st_value_col': '',
                'st_col_order': SCORE_BANDS,
            },
        },
        {
            'name': 'SAT • Average Total Score by Race',
            'chart_type': 'bar',
            'dataset': ds,
            'drill_columns': SAT_DRILL,
            'drill_through_filter_map': {'x_col': 'race', 'series_col': ''},
            'column_defs': {'race': 'Race', 'total_score': 'Average Total Score'},
            'config': _base_cfg(
                f'SAT {SAT_YEAR_LABEL}: Average Total Score by Race',
                x_col='race', y_col='total_score', agg='avg', agg_decimals=0,
                y_axis_min=0, sort_col='__manual__', category_order=RACE_ORDER, x_label_rotation=25,
            ),
        },
        {
            'name': 'SAT • Average Total Score by Student Program',
            'chart_type': 'bar',
            'dataset': 'SAT • Student Program Results',
            'drill_columns': SAT_PROGRAM_DRILL,
            'drill_through_filter_map': {'x_col': 'program', 'series_col': ''},
            'column_defs': {'program': 'Student Program', 'total_score': 'Average Total Score'},
            'config': _base_cfg(
                f'SAT {SAT_YEAR_LABEL}: Average Total Score by Student Program',
                x_col='program', y_col='total_score', agg='avg', agg_decimals=0,
                y_axis_min=0, sort_col='__manual__', category_order=PROGRAM_ORDER,
            ),
        },
    ]


def _chart_specs_ib() -> list[dict]:
    ds = 'IB • Exam Results'
    return [
        {
            'name': 'IB • Students by Year',
            'chart_type': 'bar',
            'dataset': ds,
            'drill_columns': IB_DRILL,
            'drill_through_filter_map': {'x_col': 'test_year', 'series_col': ''},
            'column_defs': {'test_year': 'School Year', 'ssid': 'Students'},
            'config': _base_cfg(
                f'IB {IB_YEAR_LABEL}: Students Who Took an IB Exam',
                x_col='test_year', y_col='ssid',
                sort_col='test_year', sort_dir='asc',
            ),
        },
        {
            'name': 'IB • Exams by Level',
            'chart_type': 'bar',
            'dataset': ds,
            'drill_columns': IB_DRILL,
            'drill_through_filter_map': {'x_col': 'level', 'series_col': ''},
            'column_defs': {'level': 'Level', 'exam_count': 'Exams'},
            'config': _base_cfg(
                f'IB {IB_YEAR_LABEL}: Exams by Level',
                x_col='level', y_col='exam_count', agg='sum',
                sort_col='_value', sort_dir='desc',
            ),
        },
        {
            'name': 'IB • Students by Category',
            'chart_type': 'bar',
            'dataset': ds,
            'drill_columns': IB_DRILL,
            'drill_through_filter_map': {'x_col': 'category', 'series_col': ''},
            'column_defs': {'category': 'Category', 'ssid': 'Students'},
            'config': _base_cfg(
                f'IB {IB_YEAR_LABEL}: Students by Category',
                x_col='category', y_col='ssid',
                sort_col='_value', sort_dir='desc',
            ),
        },
        {
            'name': 'IB • Average Final Grade by Subject',
            'chart_type': 'bar',
            'dataset': ds,
            'source_filters': {'grade_valid': [1]},
            'drill_columns': IB_DRILL,
            'drill_through_filter_map': {'x_col': 'subject_level', 'series_col': ''},
            'column_defs': {'subject_level': 'Subject / Level', 'grade_num': 'Average Grade'},
            'config': _base_cfg(
                f'IB {IB_YEAR_LABEL}: Average Final Grade by Subject',
                x_col='subject_level', y_col='grade_num', agg='avg',
                agg_decimals=2, y_axis_max=7, y_axis_min=0,
                sort_col='_value', sort_dir='desc', x_label_rotation=35,
            ),
        },
        {
            'name': 'IB • Percent Scoring 4+ by Subject',
            'chart_type': 'bar',
            'dataset': ds,
            'source_filters': {'grade_valid': [1]},
            'drill_columns': IB_DRILL,
            'drill_through_filter_map': {'x_col': 'subject_level', 'series_col': ''},
            'column_defs': {'subject_level': 'Subject / Level', 'pass_pct': '% Scoring 4+'},
            'config': _base_cfg(
                f'IB {IB_YEAR_LABEL}: Percent Scoring 4+ by Subject',
                x_col='subject_level', y_col='pass_pct', agg='avg',
                agg_decimals=1, y_axis_max=100, y_axis_min=0,
                sort_col='_value', sort_dir='desc', x_label_rotation=35,
            ),
        },
        {
            'name': 'IB • Performance by Subject Table',
            'chart_type': 'summary_table',
            'dataset': ds,
            'source_filters': {'grade_valid': [1]},
            'drill_columns': IB_DRILL,
            'column_defs': {'subject_level': 'Subject / Level', 'final_grade': 'Grade', 'ssid': 'Students'},
            'config': {
                'chart_type': 'summary_table',
                'chart_title': f'IB {IB_YEAR_LABEL}: Performance by Subject',
                'st_row_col': 'subject_level', 'st_col_col': 'final_grade',
                'st_col_mode': 'values', 'st_agg_func': 'count', 'st_value_col': '',
            },
        },
        {
            'name': 'IB • Average Final Grade by Race',
            'chart_type': 'bar',
            'dataset': ds,
            'source_filters': {'grade_valid': [1]},
            'drill_columns': IB_DRILL,
            'drill_through_filter_map': {'x_col': 'race', 'series_col': ''},
            'column_defs': {'race': 'Race', 'grade_num': 'Average Final Grade'},
            'config': _base_cfg(
                f'IB {IB_YEAR_LABEL}: Average Final Grade by Race',
                x_col='race', y_col='grade_num', agg='avg', agg_decimals=2,
                y_axis_max=7, y_axis_min=0, sort_col='__manual__',
                category_order=RACE_ORDER, x_label_rotation=25,
            ),
        },
        {
            'name': 'IB • Average Final Grade by Student Program',
            'chart_type': 'bar',
            'dataset': 'IB • Student Program Results',
            'source_filters': {'grade_valid': [1]},
            'drill_columns': IB_PROGRAM_DRILL,
            'drill_through_filter_map': {'x_col': 'program', 'series_col': ''},
            'column_defs': {'program': 'Student Program', 'grade_num': 'Average Final Grade'},
            'config': _base_cfg(
                f'IB {IB_YEAR_LABEL}: Average Final Grade by Student Program',
                x_col='program', y_col='grade_num', agg='avg', agg_decimals=2,
                y_axis_max=7, y_axis_min=0, sort_col='__manual__',
                category_order=PROGRAM_ORDER,
            ),
        },
    ]


def _chart_specs_interim() -> list[dict]:
    ds = 'Interim • Results'
    charts = [
        {
            'name': 'Interim • Students by Test',
            'chart_type': 'bar',
            'dataset': ds,
            'drill_columns': INTERIM_DRILL,
            'drill_through_filter_map': {'x_col': 'test_name', 'series_col': ''},
            'column_defs': {'test_name': 'Interim Test', 'ssid': 'Students'},
            'config': _base_cfg(
                'Interim: Students by Assessment',
                x_col='test_name', y_col='ssid',
                sort_col='__manual__', category_order=INTERIM_TESTS,
            ),
        },
        {
            'name': 'Interim • PL Mix by Test',
            'chart_type': 'stacked_bar',
            'dataset': ds,
            'drill_columns': INTERIM_DRILL,
            'drill_through_filter_map': {'x_col': 'test_name', 'series_col': 'pl'},
            'column_defs': {'test_name': 'Interim Test', 'pl': 'Performance Level', 'ssid': 'Students'},
            'config': _stacked_cfg(
                'Interim: PL Mix by Assessment',
                x_col='test_name', y_col='ssid', stack_col='pl', series_col='pl',
                stack_order=PL_STACK_ORDER, color_map=PL_COLORS,
                sort_col='__manual__', category_order=INTERIM_TESTS,
            ),
        },
    ]
    for test_name in INTERIM_TESTS:
        label = INTERIM_TEST_LABELS[test_name]
        charts.append({
            'name': f'Interim • {label} PL by Subject',
            'chart_type': 'stacked_bar',
            'dataset': ds,
            'source_filters': {'test_name': [test_name]},
            'drill_columns': INTERIM_DRILL,
            'drill_through_filter_map': {'x_col': 'subject', 'series_col': 'pl'},
            'column_defs': {'subject': 'Subject / Skill', 'pl': 'Performance Level', 'ssid': 'Students'},
            'config': _stacked_cfg(
                f'Interim {label}: PL by Subject',
                x_col='subject', y_col='ssid', stack_col='pl', series_col='pl',
                stack_order=PL_STACK_ORDER, color_map=PL_COLORS,
                sort_col='_value', sort_dir='desc',
                x_label_rotation=35, x_label_font_size=10,
            ),
        })
        charts.append({
            'name': f'Interim • {label} PL by School',
            'chart_type': 'stacked_bar',
            'dataset': ds,
            'source_filters': {'test_name': [test_name], 'district_school': [1]},
            'drill_columns': INTERIM_DRILL,
            'drill_through_filter_map': {'x_col': 'school', 'series_col': 'pl'},
            'column_defs': {'school': 'School', 'pl': 'Performance Level', 'ssid': 'Students'},
            'config': _stacked_cfg(
                f'Interim {label}: PL by School',
                x_col='school', y_col='ssid', stack_col='pl', series_col='pl',
                stack_order=PL_STACK_ORDER, color_map=PL_COLORS,
                sort_col='_value', sort_dir='desc', x_label_rotation=30,
            ),
        })
        charts.append({
            'name': f'Interim • {label} PL by Race',
            'chart_type': 'stacked_bar',
            'dataset': ds,
            'source_filters': {'test_name': [test_name]},
            'drill_columns': INTERIM_DRILL,
            'drill_through_filter_map': {'x_col': 'race', 'series_col': 'pl'},
            'column_defs': {'race': 'Race', 'pl': 'Performance Level', 'ssid': 'Students'},
            'config': _stacked_cfg(
                f'Interim {label}: PL by Race',
                x_col='race', y_col='ssid', stack_col='pl', series_col='pl',
                stack_order=PL_STACK_ORDER, color_map=PL_COLORS,
                sort_col='__manual__', category_order=RACE_ORDER, x_label_rotation=25,
            ),
        })
        charts.append({
            'name': f'Interim • {label} PL by Student Program',
            'chart_type': 'stacked_bar',
            'dataset': 'Interim • Student Program Results',
            'source_filters': {'test_name': [test_name]},
            'drill_columns': INTERIM_PROGRAM_DRILL,
            'drill_through_filter_map': {'x_col': 'program', 'series_col': 'pl'},
            'column_defs': {
                'program': 'Student Program', 'pl': 'Performance Level',
                'ssid': 'Students',
            },
            'config': _stacked_cfg(
                f'Interim {label}: PL by Student Program',
                x_col='program', y_col='ssid', stack_col='pl', series_col='pl',
                stack_order=PL_STACK_ORDER, color_map=PL_COLORS,
                sort_col='__manual__', category_order=PROGRAM_ORDER,
            ),
        })
    charts.extend([
        {
            'name': 'Interim • PL by Test and Subject Table',
            'chart_type': 'summary_table',
            'dataset': ds,
            'drill_columns': INTERIM_DRILL,
            'column_defs': {'test_subject': 'Interim Test / Subject', 'pl': 'PL', 'ssid': 'Students'},
            'config': {
                'chart_type': 'summary_table',
                'chart_title': 'Interim PL by Test and Subject',
                'st_row_col': 'test_subject', 'st_col_col': 'pl',
                'st_col_mode': 'values', 'st_agg_func': 'count', 'st_value_col': '',
            },
        },
        {
            'name': 'Interim • PL by School Table',
            'chart_type': 'summary_table',
            'dataset': ds,
            'source_filters': {'district_school': [1]},
            'drill_columns': INTERIM_DRILL,
            'column_defs': {'school': 'School', 'pl': 'PL', 'ssid': 'Students'},
            'config': {
                'chart_type': 'summary_table',
                'chart_title': 'Interim PL by School',
                'st_row_col': 'school', 'st_col_col': 'pl',
                'st_col_mode': 'values', 'st_agg_func': 'count', 'st_value_col': '',
            },
        },
    ])
    return charts


def _page_sat() -> dict:
    return {
        'title': 'SAT Results', 'slug': 'sat-results', 'folder': 'sat', 'menu_order': 7,
        'description': (
            f'Assessment year: {SAT_YEAR_LABEL}. SAT participation, average Total/EBRW/Math '
            'scores, score distribution, school-level score bands, and results by '
            'race and student program membership (ELL, GATE, SED, SPED, Migrant, Foster). '
            'School is the enrollment school for the SAT year.'
        ),
        'page_filters': _page_filters(SAT_JOINS, SAT_WHERE),
        'tabs': ['Overview', 'Score Bands', 'Demographics', 'Detail Tables'],
        'rows': [
            {'tab': 'Overview', 'label': 'Participation and averages', 'sections': [
                {'chart': 'SAT • Students by School', 'gs_x': 0, 'gs_w': 6, 'gs_h': 7},
                {'chart': 'SAT • Average Scores by School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 7},
            ]},
            {'tab': 'Score Bands', 'label': 'Total score distribution', 'sections': [
                {'chart': 'SAT • Total Score Bands', 'gs_x': 0, 'gs_w': 12, 'gs_h': 7},
            ]},
            {'tab': 'Demographics', 'label': 'Race and student programs', 'sections': [
                {'chart': 'SAT • Average Total Score by Race', 'gs_x': 0, 'gs_w': 6, 'gs_h': 8},
                {'chart': 'SAT • Average Total Score by Student Program', 'gs_x': 6, 'gs_w': 6, 'gs_h': 8},
            ]},
            {'tab': 'Detail Tables', 'label': 'Score bands by school', 'sections': [
                {'chart': 'SAT • Score Bands by School Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
            ]},
        ],
    }


def _page_ib() -> dict:
    return {
        'title': 'IB Results', 'slug': 'ib-results', 'folder': 'ib', 'menu_order': 8,
        'description': (
            f'Assessment years: {IB_YEAR_LABEL}. IB participation, exam counts by '
            'level/category, average final grades, percent scoring 4+, subject '
            'performance, and results by race and student program membership '
            '(ELL, GATE, SED, SPED, Migrant, Foster).'
        ),
        'page_filters': _page_filters(IB_JOINS, IB_WHERE),
        'tabs': ['Overview', 'Participation', 'Performance', 'Demographics'],
        'rows': [
            {'tab': 'Overview', 'label': 'Participation by year and level', 'sections': [
                {'chart': 'IB • Students by Year', 'gs_x': 0, 'gs_w': 6, 'gs_h': 7},
                {'chart': 'IB • Exams by Level', 'gs_x': 6, 'gs_w': 6, 'gs_h': 7},
            ]},
            {'tab': 'Participation', 'label': 'Categories and years', 'sections': [
                {'chart': 'IB • Students by Category', 'gs_x': 0, 'gs_w': 12, 'gs_h': 7},
            ]},
            {'tab': 'Performance', 'label': 'Grades by subject', 'sections': [
                {'chart': 'IB • Average Final Grade by Subject', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
                {'chart': 'IB • Percent Scoring 4+ by Subject', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
                {'chart': 'IB • Performance by Subject Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
            ]},
            {'tab': 'Demographics', 'label': 'Race and student programs', 'sections': [
                {'chart': 'IB • Average Final Grade by Race', 'gs_x': 0, 'gs_w': 6, 'gs_h': 8},
                {'chart': 'IB • Average Final Grade by Student Program', 'gs_x': 6, 'gs_w': 6, 'gs_h': 8},
            ]},
        ],
    }


def _page_interim() -> dict:
    rows = [
        {'tab': 'Overview', 'label': 'Assessment overview', 'sections': [
            {'chart': 'Interim • Students by Test', 'gs_x': 0, 'gs_w': 6, 'gs_h': 7},
            {'chart': 'Interim • PL Mix by Test', 'gs_x': 6, 'gs_w': 6, 'gs_h': 7},
        ]},
    ]
    for test_name in INTERIM_TESTS:
        label = INTERIM_TEST_LABELS[test_name]
        rows.append({'tab': label, 'label': 'PL by subject', 'sections': [
            {'chart': f'Interim • {label} PL by Subject', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]})
        rows.append({'tab': label, 'label': 'PL by school', 'sections': [
            {'chart': f'Interim • {label} PL by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]})
    for test_name in INTERIM_TESTS:
        label = INTERIM_TEST_LABELS[test_name]
        rows.append({'tab': 'Demographics', 'label': f'{label}: race and student programs', 'sections': [
            {'chart': f'Interim • {label} PL by Race', 'gs_x': 0, 'gs_w': 6, 'gs_h': 8},
            {'chart': f'Interim • {label} PL by Student Program', 'gs_x': 6, 'gs_w': 6, 'gs_h': 8},
        ]})
    rows.extend([
        {'tab': 'Detail Tables', 'label': 'PL by test and subject', 'sections': [
            {'chart': 'Interim • PL by Test and Subject Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
        ]},
        {'tab': 'Detail Tables', 'label': 'PL by school', 'sections': [
            {'chart': 'Interim • PL by School Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
        ]},
    ])
    return {
        'title': 'Interim Assessments', 'slug': 'interim-assessments',
        'folder': 'interim_assessments', 'menu_order': 9,
        'description': (
            'Interim Math, Interim ELA, and Interim ELPAC performance levels by subject '
            'and school, plus results by race and student program membership '
            '(ELL, GATE, SED, SPED, Migrant, Foster). Use the Math/ELA/ELPAC tabs '
            'for subject-level breakdowns.'
        ),
        'page_filters': _page_filters(INTERIM_JOINS, INTERIM_WHERE),
        'tabs': ['Overview', 'Math', 'ELA', 'ELPAC', 'Demographics', 'Detail Tables'],
        'rows': rows,
    }


DATASET_SPECS = [
    {
        'name': 'SAT • Student Results',
        'description': 'One row per SAT result with score bands and student demographics.',
        'columns': SAT_DRILL,
        'sql': SAT_SELECT,
        'folder': 'Assessments / SAT Results',
    },
    {
        'name': 'SAT • Student Program Results',
        'description': 'One row per SAT result per student program membership.',
        'columns': SAT_PROGRAM_DRILL,
        'sql': _program_results_sql(SAT_SELECT),
        'folder': 'Assessments / SAT Results',
    },
    {
        'name': 'IB • Exam Results',
        'description': 'One row per IB exam result with final and predicted grades.',
        'columns': IB_DRILL,
        'sql': IB_SELECT,
        'folder': 'Assessments / IB Results',
    },
    {
        'name': 'IB • Student Program Results',
        'description': 'One row per IB exam per student program membership.',
        'columns': IB_PROGRAM_DRILL,
        'sql': _program_results_sql(IB_SELECT),
        'folder': 'Assessments / IB Results',
    },
    {
        'name': 'Interim • Results',
        'description': 'One row per Interim Math/ELA/ELPAC result with PL and subject.',
        'columns': INTERIM_DRILL,
        'sql': INTERIM_SELECT,
        'folder': 'Assessments / Interim Assessments',
    },
    {
        'name': 'Interim • Student Program Results',
        'description': 'One row per Interim result per student program membership.',
        'columns': INTERIM_PROGRAM_DRILL,
        'sql': _program_results_sql(INTERIM_SELECT),
        'folder': 'Assessments / Interim Assessments',
    },
]
CHART_SPECS = _chart_specs_sat() + _chart_specs_ib() + _chart_specs_interim()
PAGE_SPECS = [_page_sat(), _page_ib(), _page_interim()]

# Charts removed from the IB page; delete them so they do not linger unused.
OBSOLETE_CHART_NAMES = [
    'ib / IB • Predicted vs Final Grade',
    'ib / IB • Scores by Category Table',
    'ib / IB • Exams by Subject and Year Table',
]


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
        raise SystemExit('Could not locate the Django project.')
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    return project_dir


def resolve_owner(ref: str):
    from users.models import User

    if ref:
        user = (
            User.objects.filter(username=ref).first()
            or User.objects.filter(email__iexact=ref).first()
        )
        if not user:
            raise SystemExit(f'No user found for "{ref}".')
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
        raise SystemExit('No users exist to own these pages.')
    return user


def enable_dataset_tables():
    from landing.models import ProjectConfig

    cfg = ProjectConfig.get()
    enabled = list(cfg.enabled_dataset_tables or [])
    if not enabled:
        return
    added = []
    for table in ('analytics_sat_results', 'analytics_ib_results', 'results_with_tests', 'current_student', 'school'):
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
    folder = spec.get('folder', '')
    dataset = Dataset.objects.filter(name=spec['name']).order_by('id').first()
    if dataset is None:
        dataset = Dataset(name=spec['name'], owner=owner)
    else:
        dataset.owner = owner
    dataset.source_type = Dataset.SOURCE_SQL
    dataset.source_sql = spec['sql']
    dataset.base_table = spec['columns'] and 'current_student'
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

    folder = PAGE_SPECS_BY_CHART[spec['name']]['folder']
    folder_name = f'{folder} / {spec["name"]}'
    dataset = datasets[spec['dataset']]
    chart = SavedChart.objects.filter(name=folder_name).order_by('id').first()
    if chart is None:
        chart = SavedChart(name=folder_name)
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
        for idx, name in enumerate(spec.get('tabs', [])):
            tab_map[name] = DataVizPageTab.objects.create(page=page, name=name, position=idx)
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
            'name': 'Assessments', 'app_reference': 'assessments',
            'description': 'State and local assessment dashboards',
            'url_path': '', 'status': 'active', 'is_menu_category': True,
            'show_in_menu': True, 'menu_order': 54,
            'required_roles': [], 'is_public': False,
        },
    )
    if page.linked_site_page_id:
        site = Page.objects.get(pk=page.linked_site_page_id)
        site.parent_page = category
        site.menu_order = order
        site.show_in_menu = True
        site.save(update_fields=['parent_page', 'menu_order', 'show_in_menu'])
    print(f'  menu: Assessments -> {page.title} (order {order})')


PAGE_SPECS_BY_CHART = {}
for _page in PAGE_SPECS:
    for _row in _page['rows']:
        for _sec in _row['sections']:
            PAGE_SPECS_BY_CHART[_sec['chart']] = _page


def delete_obsolete_charts(owner):
    from dataviz.models import SavedChart

    deleted, _ = SavedChart.objects.filter(
        owner=owner, name__in=OBSOLETE_CHART_NAMES,
    ).delete()
    if deleted:
        print(f'  removed {deleted} obsolete IB chart(s)')


def qa_dataviz() -> int:
    from dataviz.utils.data_loader import run_raw_sql

    failures = []

    def fail(msg):
        failures.append(msg)
        print(f'  FAIL {msg}')

    print('=== QA: dataset counts ===')
    for spec in DATASET_SPECS:
        res = run_raw_sql(f"""
            SELECT test_year, count() AS n_rows, uniqExact(ssid) AS n_stu
            FROM ({spec['sql']}) GROUP BY test_year ORDER BY test_year
        """)
        if res.get('error'):
            fail(f'{spec["name"]}: {res["error"]}')
        else:
            for r in res.get('rows') or []:
                print(f'  ok  {spec["name"]} {r["test_year"]}: {r["n_rows"]} rows / {r["n_stu"]} students')

    print('=== QA: chart references and drill maps ===')
    columns = {s['name']: set(s['columns']) for s in DATASET_SPECS}
    for spec in CHART_SPECS:
        name = spec['name']
        cols = columns.get(spec['dataset'])
        if not cols:
            fail(f'{name}: unknown dataset')
            continue
        refs = set(spec.get('drill_columns') or [])
        for key in ('x_col', 'y_col', 'stack_col', 'series_col', 'group_col',
                    'st_row_col', 'st_col_col', 'st_value_col'):
            val = spec['config'].get(key)
            if isinstance(val, str) and val.strip():
                refs.update(p.strip() for p in val.split(',') if p.strip())
        refs.update(spec['config'].get('y_cols') or [])
        missing = sorted(refs - cols)
        if missing:
            fail(f'{name}: unknown columns {missing}')
        for key in ('x_col', 'series_col', 'group_col'):
            val = (spec.get('drill_through_filter_map') or {}).get(key)
            if isinstance(val, str):
                bad = [p for p in val.split(',') if p.strip() and p.strip() not in cols]
                if bad:
                    fail(f'{name}: drill map unknown columns {bad}')

    print('=== QA: page filter options ===')
    for page in PAGE_SPECS:
        for f in page.get('page_filters') or []:
            res = run_raw_sql(f['source_distinct_sql'])
            n = len(res.get('rows') or [])
            if res.get('error') or n == 0:
                fail(f'{page["slug"]} filter {f["id"]}: {res.get("error") or "no values"}')
            else:
                print(f'  ok  {page["slug"]} filter {f["id"]}: {n} values')

    if failures:
        print(f'\nQA FAILED ({len(failures)} issue(s)).')
        return 1
    print('\nQA PASSED.')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Create SAT, IB, and Interim Assessment pages.')
    parser.add_argument('--owner', default='')
    parser.add_argument('--qa', action='store_true')
    args = parser.parse_args()

    bootstrap_django()
    if args.qa:
        raise SystemExit(qa_dataviz())

    owner = resolve_owner(args.owner)
    if not owner:
        raise SystemExit('No users exist to own these pages.')
    print(f'Using owner: {owner}')

    enable_dataset_tables()
    datasets = {spec['name']: upsert_dataset(spec, owner) for spec in DATASET_SPECS}
    charts = {spec['name']: upsert_chart(spec, datasets, owner) for spec in CHART_SPECS}
    delete_obsolete_charts(owner)
    for spec in PAGE_SPECS:
        page = upsert_page(spec, charts, owner)
        attach_to_menu(page, spec['menu_order'])
    print('SAT / IB / Interim pages ready.')


if __name__ == '__main__':
    main()
