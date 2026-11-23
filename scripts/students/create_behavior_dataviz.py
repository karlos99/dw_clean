#!/usr/bin/env python
"""Create the Students -> Behavior DataViz page.

This is a student-focused discipline page. School analysis uses the **incident
school** from the incident JSON (``incident_school``), not the student's
enrollment school. The existing Discipline Incidents page is left unchanged.

Requires the ``incident_school`` / ``incident_location`` columns added to
``analytics_student_incidents`` (run ``sync_to_clickhouse --tables
analytics_student_incidents`` after applying the schema change).

Run inside the Django container:

    docker compose exec django python /app/scripts/students/create_behavior_dataviz.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    CHART_ROOT_FOLDER,
    GRADE_ORDER,
    SCHOOL_TYPE_ORDER,
    attach_page_to_students_menu,
    base_chart_cfg,
    bootstrap_django,
    enable_dataset_tables,
    resolve_owner,
    summary_cfg,
    upsert_chart,
    upsert_dataset,
    upsert_page,
)

PREFIX = 'Students • Behavior •'
PAGE_SLUG = 'students-behavior'
PAGE_FOLDER = 'Students / Behavior'
CHART_FOLDER = 'behavior'
DATASET_FOLDER = 'Students / Behavior'

WEEK_ORDER = [str(w) for w in range(1, 43)]

GRADE_SQL = """
multiIf(
    upper(trimBoth(ifNull({col}, ''))) IN ('TK'), 'TK',
    upper(trimBoth(ifNull({col}, ''))) IN ('KN', 'K', 'KG'), 'KN',
    upper(trimBoth(ifNull({col}, ''))) IN ('US', '250'), 'US.',
    match(trimBoth(ifNull({col}, '')), '^[0-9]+$'),
    lpad(toString(toInt32OrZero(trimBoth({col}))), 2, '0'),
    if(trimBoth(ifNull({col}, '')) = '', 'Unknown', trimBoth({col}))
)"""

SEVERITY_COLORS = {'Major': '#e15759', 'Minor': '#4e79a7'}
YEAR_COLORS = {
    '2023-2024': '#4e79a7',
    '2024-2025': '#f28e2b',
    '2025-2026': '#59a14f',
    '2026-2027': '#e15759',
}

_INCIDENTS_SQL = f"""
SELECT
    i.ssid AS ssid,
    i.stu_annual_key AS stu_annual_key,
    i.student_id AS student_id,
    i.student_name AS student_name,
    i.grade AS grade,
    ({GRADE_SQL.format(col='i.grade')}) AS grade_label,
    coalesce(nullif(trim(i.school), ''), 'Unknown') AS enrolled_school,
    coalesce(i.school_state_id, '') AS enrolled_school_state_id,
    coalesce(nullif(trim(i.incident_school), ''), 'Unknown') AS incident_school,
    coalesce(i.incident_location, '') AS incident_location,
    coalesce(nullif(trim(sch.school_group), ''), 'Unknown') AS incident_school_group,
    i.status AS status,
    i.school_year AS school_year,
    i.severity AS severity,
    i.incident_id AS incident_id,
    i.incident_date AS incident_date,
    coalesce(i.violation, '') AS violation,
    coalesce(i.ed_code, '') AS ed_code,
    coalesce(i.review_status, '') AS review_status,
    coalesce(i.description, '') AS description,
    i.suspension_days AS suspension_days,
    i.is_suspended AS is_suspended,
    i.school_week AS school_week,
    i.week_start_date AS week_start_date,
    i.week_end_date AS week_end_date,
    i.week_label AS week_label
FROM analytics_student_incidents AS i
LEFT JOIN (
    SELECT short_name, max(school_group) AS school_group
    FROM school
    WHERE short_name IS NOT NULL AND trim(short_name) != ''
    GROUP BY short_name
) AS sch ON sch.short_name = i.incident_school
"""

_INCIDENT_COLUMNS = [
    'ssid', 'stu_annual_key', 'student_id', 'student_name', 'grade',
    'grade_label', 'enrolled_school', 'enrolled_school_state_id',
    'incident_school', 'incident_location', 'incident_school_group', 'status',
    'school_year', 'severity', 'incident_id', 'incident_date', 'violation',
    'ed_code', 'review_status', 'description', 'suspension_days',
    'is_suspended', 'school_week', 'week_start_date', 'week_end_date',
    'week_label',
]
_INCIDENT_DRILL = _INCIDENT_COLUMNS

# One row per student with incident totals. Used for student rankings and the
# incident-count distribution.
_STUDENT_SUMMARY_SQL = f"""
SELECT
    ssid AS ssid,
    student_name AS student_name,
    concat(student_name, ' (', incident_school, ')') AS student_label,
    grade AS grade,
    grade_label AS grade_label,
    incident_school AS incident_school,
    incident_school_group AS incident_school_group,
    school_year AS school_year,
    count() AS incident_count,
    sum(suspension_days) AS suspension_days,
    countIf(severity = 'Major') AS major_incidents,
    countIf(severity = 'Minor') AS minor_incidents,
    countIf(is_suspended = 1) AS suspension_events
FROM ({_INCIDENTS_SQL}) AS i
GROUP BY
    ssid, student_name, grade, grade_label, incident_school,
    incident_school_group, school_year
"""

_STUDENT_SUMMARY_COLUMNS = [
    'ssid', 'student_name', 'student_label', 'grade', 'grade_label',
    'incident_school', 'incident_school_group', 'school_year', 'incident_count',
    'suspension_days', 'major_incidents', 'minor_incidents',
    'suspension_events',
]
_STUDENT_SUMMARY_DRILL = _STUDENT_SUMMARY_COLUMNS

# Long-format category facts: one row per incident per individual violation /
# ed code. The source analytics table stores multi-violation incidents as a
# single semicolon-separated string, so split it for category analysis.
_VIOLATIONS_SQL = f"""
SELECT
    ssid AS ssid,
    student_name AS student_name,
    grade AS grade,
    grade_label AS grade_label,
    incident_school AS incident_school,
    incident_school_group AS incident_school_group,
    school_year AS school_year,
    severity AS severity,
    incident_id AS incident_id,
    violation_value AS violation_value
FROM (
    SELECT
        i.ssid, i.student_name, i.grade, i.grade_label, i.incident_school,
        i.incident_school_group, i.school_year, i.severity, i.incident_id,
        arrayJoin(arrayFilter(
            x -> trim(x) != '',
            splitByString('; ', coalesce(i.violation, ''))
        )) AS violation_value
    FROM ({_INCIDENTS_SQL}) AS i
) AS v
WHERE violation_value != ''
"""

_VIOLATION_COLUMNS = [
    'ssid', 'student_name', 'grade', 'grade_label', 'incident_school',
    'incident_school_group', 'school_year', 'severity', 'incident_id',
    'violation_value',
]

_ED_CODES_SQL = f"""
SELECT
    ssid AS ssid,
    student_name AS student_name,
    grade AS grade,
    grade_label AS grade_label,
    incident_school AS incident_school,
    incident_school_group AS incident_school_group,
    school_year AS school_year,
    severity AS severity,
    incident_id AS incident_id,
    ed_code_value AS ed_code_value
FROM (
    SELECT
        i.ssid, i.student_name, i.grade, i.grade_label, i.incident_school,
        i.incident_school_group, i.school_year, i.severity, i.incident_id,
        arrayJoin(arrayFilter(
            x -> trim(x) != '',
            splitByString('; ', coalesce(i.ed_code, ''))
        )) AS ed_code_value
    FROM ({_INCIDENTS_SQL}) AS i
) AS e
WHERE ed_code_value != ''
"""

_ED_CODE_COLUMNS = [
    'ssid', 'student_name', 'grade', 'grade_label', 'incident_school',
    'incident_school_group', 'school_year', 'severity', 'incident_id',
    'ed_code_value',
]


def _filter_sql(dataset_sql: str, expr: str) -> str:
    return f"""
SELECT DISTINCT ({expr}) AS v
FROM ({dataset_sql}) AS d
HAVING trim(toString(v)) != ''
ORDER BY v
"""


DATASET_SPECS = [
    {
        'name': f'{PREFIX} Incidents',
        'sql': _INCIDENTS_SQL,
        'base_table': 'analytics_student_incidents',
        'columns': _INCIDENT_COLUMNS,
        'folder': DATASET_FOLDER,
        'description': (
            'One row per discipline incident. School fields include both the '
            'enrollment school and the incident school/location where the '
            'incident occurred.'
        ),
    },
    {
        'name': f'{PREFIX} Student Summary',
        'sql': _STUDENT_SUMMARY_SQL,
        'base_table': 'analytics_student_incidents',
        'columns': _STUDENT_SUMMARY_COLUMNS,
        'folder': DATASET_FOLDER,
        'description': (
            'One row per student with incident, suspension, and severity totals, '
            'using the incident school for school attribution.'
        ),
    },
    {
        'name': f'{PREFIX} Violations',
        'sql': _VIOLATIONS_SQL,
        'base_table': 'analytics_student_incidents',
        'columns': _VIOLATION_COLUMNS,
        'folder': DATASET_FOLDER,
        'description': (
            'Long format: one row per incident per individual violation. Used for '
            'top-violation and violation-category charts.'
        ),
    },
    {
        'name': f'{PREFIX} Ed Codes',
        'sql': _ED_CODES_SQL,
        'base_table': 'analytics_student_incidents',
        'columns': _ED_CODE_COLUMNS,
        'folder': DATASET_FOLDER,
        'description': (
            'Long format: one row per incident per individual education code.'
        ),
    },
]


def _chart_specs() -> list[dict]:
    incidents_ds = f'{PREFIX} Incidents'
    summary_ds = f'{PREFIX} Student Summary'
    violations_ds = f'{PREFIX} Violations'
    ed_codes_ds = f'{PREFIX} Ed Codes'

    return [
        {
            'name': f'{PREFIX} Incidents by Year',
            'chart_type': 'bar',
            'dataset': incidents_ds,
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {'school_year': 'School Year', 'incident_id': 'Incidents'},
            'config': base_chart_cfg(
                'bar', 'Incidents by School Year',
                x_col='school_year', y_col='incident_id', agg='count',
                sort_col='school_year', sort_dir='asc',
                skip_page_filters=['school_year'],
            ),
        },
        {
            'name': f'{PREFIX} Incidents by Incident School',
            'chart_type': 'bar',
            'dataset': incidents_ds,
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {'incident_school': 'Incident School', 'incident_id': 'Incidents'},
            'config': base_chart_cfg(
                'bar', 'Incidents by Incident School',
                x_col='incident_school', y_col='incident_id', agg='count',
                top_n=40, x_label_rotation=30,
            ),
        },
        {
            'name': f'{PREFIX} Students with Incidents by Incident School',
            'chart_type': 'bar',
            'dataset': incidents_ds,
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {'incident_school': 'Incident School', 'ssid': 'Students'},
            'config': base_chart_cfg(
                'bar', 'Students with Incidents by Incident School',
                x_col='incident_school', y_col='ssid', agg='count_unique',
                top_n=40, x_label_rotation=30, one_row_per_ssid=True,
            ),
        },
        {
            'name': f'{PREFIX} Incidents by Grade',
            'chart_type': 'bar',
            'dataset': incidents_ds,
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {'grade_label': 'Grade', 'incident_id': 'Incidents'},
            'config': base_chart_cfg(
                'bar', 'Incidents by Grade',
                x_col='grade_label', y_col='incident_id', agg='count',
                sort_col='__manual__', category_order=GRADE_ORDER,
            ),
        },
        {
            'name': f'{PREFIX} Major vs Minor',
            'chart_type': 'donut',
            'dataset': incidents_ds,
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {'severity': 'Severity', 'incident_id': 'Incidents'},
            'config': base_chart_cfg(
                'donut', 'Major vs Minor Incidents',
                x_col='severity', y_col='incident_id', agg='count',
                color_map=SEVERITY_COLORS,
            ),
        },
        {
            'name': f'{PREFIX} Top Violations',
            'chart_type': 'bar',
            'dataset': violations_ds,
            'drill_columns': _VIOLATION_COLUMNS,
            'column_defs': {'violation_value': 'Violation', 'incident_id': 'Incidents'},
            'config': base_chart_cfg(
                'bar', 'Top Incident Violations',
                x_col='violation_value', y_col='incident_id', agg='count',
                top_n=25, x_label_rotation=35,
            ),
        },
        {
            'name': f'{PREFIX} Top Ed Codes',
            'chart_type': 'bar',
            'dataset': ed_codes_ds,
            'drill_columns': _ED_CODE_COLUMNS,
            'column_defs': {'ed_code_value': 'Ed Code', 'incident_id': 'Incidents'},
            'config': base_chart_cfg(
                'bar', 'Top Education Codes',
                x_col='ed_code_value', y_col='incident_id', agg='count',
                top_n=25, x_label_rotation=35,
            ),
        },
        {
            'name': f'{PREFIX} Review Status',
            'chart_type': 'donut',
            'dataset': incidents_ds,
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {'review_status': 'Review Status', 'incident_id': 'Incidents'},
            'config': base_chart_cfg(
                'donut', 'Incident Review Status',
                x_col='review_status', y_col='incident_id', agg='count',
            ),
        },
        {
            'name': f'{PREFIX} Top Students by Incidents',
            'chart_type': 'bar',
            'dataset': summary_ds,
            'drill_columns': _STUDENT_SUMMARY_DRILL,
            'column_defs': {'student_label': 'Student (Incident School)', 'incident_count': 'Incidents'},
            'config': base_chart_cfg(
                'bar', 'Top Students by Incident Count',
                x_col='student_label', y_col='incident_count', agg='sum',
                top_n=25, x_label_rotation=35,
            ),
        },
        {
            'name': f'{PREFIX} Top Students by Suspension Days',
            'chart_type': 'bar',
            'dataset': summary_ds,
            'drill_columns': _STUDENT_SUMMARY_DRILL,
            'column_defs': {'student_label': 'Student (Incident School)', 'suspension_days': 'Suspension Days'},
            'config': base_chart_cfg(
                'bar', 'Top Students by Suspension Days',
                x_col='student_label', y_col='suspension_days', agg='sum',
                top_n=25, x_label_rotation=35,
            ),
        },
        {
            'name': f'{PREFIX} Incident Count Distribution',
            'chart_type': 'histogram',
            'dataset': summary_ds,
            'drill_columns': _STUDENT_SUMMARY_DRILL,
            'column_defs': {'incident_count': 'Incidents per Student'},
            'config': base_chart_cfg(
                'histogram', 'Distribution of Incidents per Student',
                x_col='incident_count', agg='count',
            ),
        },
        {
            'name': f'{PREFIX} Incidents by School Week',
            'chart_type': 'line',
            'dataset': incidents_ds,
            'drill_columns': _INCIDENT_DRILL,
            'drill_through_filter_map': {'x_col': 'school_week', 'series_col': 'school_year'},
            'column_defs': {'school_week': 'School Week', 'school_year': 'School Year', 'incident_id': 'Incidents'},
            'config': base_chart_cfg(
                'line', 'Incidents by School Week',
                x_col='school_week', y_col='incident_id', agg='count',
                series_col='school_year', sort_col='__manual__', sort_dir='asc',
                category_order=WEEK_ORDER, show_grid=False, show_points=True,
                data_labels=False, color_map=YEAR_COLORS,
                skip_page_filters=['school_year'],
            ),
        },
        {
            'name': f'{PREFIX} Incidents by Severity by Week',
            'chart_type': 'stacked_bar',
            'dataset': incidents_ds,
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {'school_week': 'School Week', 'severity': 'Severity', 'incident_id': 'Incidents'},
            'config': base_chart_cfg(
                'stacked_bar', 'Incidents by Severity and School Week',
                x_col='school_week', y_col='incident_id', agg='count',
                stack_col='severity', series_col='severity',
                sort_col='__manual__', sort_dir='asc', category_order=WEEK_ORDER,
                stack_order=['Major', 'Minor'], color_map=SEVERITY_COLORS,
                data_labels=False,
            ),
        },
        {
            'name': f'{PREFIX} Suspended Students by Incident School',
            'chart_type': 'bar',
            'dataset': incidents_ds,
            'source_filters': {'is_suspended': ['1']},
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {'incident_school': 'Incident School', 'ssid': 'Students'},
            'config': base_chart_cfg(
                'bar', 'Suspended Students by Incident School',
                x_col='incident_school', y_col='ssid', agg='count_unique',
                top_n=40, x_label_rotation=30, one_row_per_ssid=True,
            ),
        },
        {
            'name': f'{PREFIX} Suspension Days by Incident School',
            'chart_type': 'bar',
            'dataset': incidents_ds,
            'source_filters': {'is_suspended': ['1']},
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {'incident_school': 'Incident School', 'suspension_days': 'Suspension Days'},
            'config': base_chart_cfg(
                'bar', 'Suspension Days by Incident School',
                x_col='incident_school', y_col='suspension_days', agg='sum',
                top_n=40, x_label_rotation=30,
            ),
        },
        {
            'name': f'{PREFIX} Suspension Days by Week',
            'chart_type': 'line',
            'dataset': incidents_ds,
            'source_filters': {'is_suspended': ['1']},
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {'school_week': 'School Week', 'suspension_days': 'Suspension Days'},
            'config': base_chart_cfg(
                'line', 'Suspension Days by School Week',
                x_col='school_week', y_col='suspension_days', agg='sum',
                sort_col='__manual__', sort_dir='asc', category_order=WEEK_ORDER,
                show_grid=False, show_points=True, data_labels=False,
            ),
        },
        {
            'name': f'{PREFIX} Suspension Days Distribution',
            'chart_type': 'histogram',
            'dataset': incidents_ds,
            'source_filters': {'is_suspended': ['1']},
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {'suspension_days': 'Suspension Days per Incident'},
            'config': base_chart_cfg(
                'histogram', 'Suspension Days per Incident',
                x_col='suspension_days', agg='count',
            ),
        },
        {
            'name': f'{PREFIX} Incidents by Incident School and Location',
            'chart_type': 'summary_table',
            'dataset': incidents_ds,
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'incident_school': 'Incident School',
                'incident_location': 'Location',
                'incident_id': 'Incidents',
            },
            'config': summary_cfg(
                'Incidents by Incident School and Location',
                'incident_school', 'incident_location',
            ),
        },
        {
            'name': f'{PREFIX} Incidents by Incident School and Severity',
            'chart_type': 'summary_table',
            'dataset': incidents_ds,
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'incident_school': 'Incident School',
                'severity': 'Severity',
                'incident_id': 'Incidents',
            },
            'config': summary_cfg(
                'Incidents by Incident School and Severity',
                'incident_school', 'severity',
            ),
        },
    ]


PAGE_SPEC = {
    'title': 'Behavior',
    'slug': PAGE_SLUG,
    'folder': PAGE_FOLDER,
    'description': (
        'Student-focused discipline analysis by district and by the school where '
        'the incident occurred. Includes incident categories, student rankings, '
        'suspensions, and weekly trends on the dw_cal school calendar.'
    ),
    'page_filters': [
        {
            'id': 'school_year', 'label': 'School Year',
            'source_col': 'school_year', 'source_table': 'analytics_student_incidents',
            'source_distinct_sql': _filter_sql(_INCIDENTS_SQL, 'd.school_year'),
            'widget': 'select', 'default_val': '__sy_this__', 'hidden': False,
        },
        {
            'id': 'incident_school', 'label': 'Incident School',
            'source_col': 'incident_school', 'source_table': 'analytics_student_incidents',
            'source_distinct_sql': _filter_sql(_INCIDENTS_SQL, 'd.incident_school'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'incident_school_group', 'label': 'School Type',
            'source_col': 'incident_school_group', 'source_table': 'analytics_student_incidents',
            'source_distinct_sql': _filter_sql(_INCIDENTS_SQL, 'd.incident_school_group'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'grade', 'label': 'Grade',
            'source_col': 'grade_label', 'source_table': 'analytics_student_incidents',
            'source_distinct_sql': _filter_sql(_INCIDENTS_SQL, 'd.grade_label'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'severity', 'label': 'Severity',
            'source_col': 'severity', 'source_table': 'analytics_student_incidents',
            'source_distinct_sql': _filter_sql(_INCIDENTS_SQL, 'd.severity'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'review_status', 'label': 'Review Status',
            'source_col': 'review_status', 'source_table': 'analytics_student_incidents',
            'source_distinct_sql': _filter_sql(_INCIDENTS_SQL, 'd.review_status'),
            'widget': 'multi', 'default_val': '', 'hidden': True,
        },
    ],
    'tabs': ['Overview', 'By Student', 'Categories', 'Over Time', 'Suspensions', 'Detail'],
    'rows': [
        {'tab': 'Overview', 'label': 'Incidents over time', 'sections': [
            {'chart': f'{PREFIX} Incidents by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 7},
        ]},
        {'tab': 'Overview', 'label': 'Incidents by incident school', 'sections': [
            {'chart': f'{PREFIX} Incidents by Incident School', 'gs_x': 0, 'gs_w': 7, 'gs_h': 10},
            {'chart': f'{PREFIX} Students with Incidents by Incident School', 'gs_x': 7, 'gs_w': 5, 'gs_h': 10},
        ]},
        {'tab': 'Overview', 'label': 'Grade and severity', 'sections': [
            {'chart': f'{PREFIX} Incidents by Grade', 'gs_x': 0, 'gs_w': 8, 'gs_h': 7},
            {'chart': f'{PREFIX} Major vs Minor', 'gs_x': 8, 'gs_w': 4, 'gs_h': 7},
        ]},
        {'tab': 'By Student', 'label': 'Student rankings', 'sections': [
            {'chart': f'{PREFIX} Top Students by Incidents', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
        ]},
        {'tab': 'By Student', 'label': 'Suspension rankings and distribution', 'sections': [
            {'chart': f'{PREFIX} Top Students by Suspension Days', 'gs_x': 0, 'gs_w': 7, 'gs_h': 10},
            {'chart': f'{PREFIX} Incident Count Distribution', 'gs_x': 7, 'gs_w': 5, 'gs_h': 10},
        ]},
        {'tab': 'Categories', 'label': 'Violations and codes', 'sections': [
            {'chart': f'{PREFIX} Top Violations', 'gs_x': 0, 'gs_w': 6, 'gs_h': 10},
            {'chart': f'{PREFIX} Top Ed Codes', 'gs_x': 6, 'gs_w': 6, 'gs_h': 10},
        ]},
        {'tab': 'Categories', 'label': 'Review status', 'sections': [
            {'chart': f'{PREFIX} Review Status', 'gs_x': 0, 'gs_w': 12, 'gs_h': 6},
        ]},
        {'tab': 'Over Time', 'label': 'Incidents by school week', 'sections': [
            {'chart': f'{PREFIX} Incidents by School Week', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Over Time', 'label': 'Severity by school week', 'sections': [
            {'chart': f'{PREFIX} Incidents by Severity by Week', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Suspensions', 'label': 'Suspended students and days by school', 'sections': [
            {'chart': f'{PREFIX} Suspended Students by Incident School', 'gs_x': 0, 'gs_w': 6, 'gs_h': 10},
            {'chart': f'{PREFIX} Suspension Days by Incident School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 10},
        ]},
        {'tab': 'Suspensions', 'label': 'Suspensions over time', 'sections': [
            {'chart': f'{PREFIX} Suspension Days by Week', 'gs_x': 0, 'gs_w': 8, 'gs_h': 8},
            {'chart': f'{PREFIX} Suspension Days Distribution', 'gs_x': 8, 'gs_w': 4, 'gs_h': 8},
        ]},
        {'tab': 'Detail', 'label': 'Incidents by school and location', 'sections': [
            {'chart': f'{PREFIX} Incidents by Incident School and Location', 'gs_x': 0, 'gs_w': 12, 'gs_h': 14},
        ]},
        {'tab': 'Detail', 'label': 'Incidents by school and severity', 'sections': [
            {'chart': f'{PREFIX} Incidents by Incident School and Severity', 'gs_x': 0, 'gs_w': 12, 'gs_h': 12},
        ]},
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser(description='Create the Students -> Behavior page.')
    parser.add_argument('--owner', default='', help='Owner username or email.')
    args = parser.parse_args()

    project_dir = bootstrap_django()
    print(f'Django project: {project_dir}')
    owner = resolve_owner(args.owner)
    print(f'Using owner: {owner}')

    enable_dataset_tables([
        'current_student', 'school', 'analytics_student_incidents',
    ])

    datasets = {spec['name']: upsert_dataset(spec, owner, default_folder=DATASET_FOLDER)
                for spec in DATASET_SPECS}
    charts = {spec['name']: upsert_chart(
        spec, datasets, owner,
        chart_root=CHART_ROOT_FOLDER, chart_folder=CHART_FOLDER,
        allowed_prefix=PREFIX,
    ) for spec in _chart_specs()}
    page = upsert_page(PAGE_SPEC, charts, owner)
    attach_page_to_students_menu(page, 2)
    print('Behavior page ready.')


if __name__ == '__main__':
    main()
