#!/usr/bin/env python
"""Create the Students -> Behavior DataViz page.

This is a student-focused discipline page. School analysis uses the **incident
school** from the incident JSON (``incident_school``), not the student's
enrollment school. The existing Discipline Incidents page is left unchanged.

Requires ``analytics_student_incidents`` with ``incident_school``,
``incident_location``, and expulsion flags (``is_expulsion_recommended``,
``is_expelled``). Rebuild after schema changes::

    docker compose exec django python manage.py sync_to_clickhouse --tables analytics_student_incidents

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
SEVERITY_STACK = {
    'stack_col': 'severity',
    'series_col': 'severity',
    'stack_order': ['Major', 'Minor'],
    'color_map': SEVERITY_COLORS,
    'data_labels': False,
}


def _incidents_stacked(title: str, x_col: str, **overrides) -> dict:
    """Stacked major/minor bars for incident counts."""
    return base_chart_cfg(
        'stacked_bar', title,
        x_col=x_col, y_col='incident_id', agg='count',
        **SEVERITY_STACK,
        **overrides,
    )


def _students_stacked(title: str, x_col: str, **overrides) -> dict:
    """Stacked major/minor bars for distinct students (incident rows)."""
    return base_chart_cfg(
        'stacked_bar', title,
        x_col=x_col, y_col='ssid', agg='count_unique',
        one_row_per_ssid=True,
        **SEVERITY_STACK,
        **overrides,
    )


def _category_rank_bar(title: str, x_col: str, **overrides) -> dict:
    """Single-severity category bar (violations, ed codes, review status)."""
    opts = {
        'top_n': 30,
        'x_label_rotation': 35,
        'data_labels': True,
    }
    opts.update(overrides)
    return base_chart_cfg(
        'bar', title,
        x_col=x_col, y_col='incident_id', agg='count',
        **opts,
    )


YEAR_COLORS = {
    '2023-2024': '#4e79a7',
    '2024-2025': '#f28e2b',
    '2025-2026': '#59a14f',
    '2026-2027': '#e15759',
}

OVER_TIME_LINE_CHARTS = frozenset({
    f'{PREFIX} Major Incidents by School Week',
    f'{PREFIX} Minor Incidents by School Week',
    # Suspension records in analytics_student_incidents are major incidents only.
    f'{PREFIX} Suspensions by School Week',
})


def _recent_school_years(limit: int = 3) -> list[str]:
    """Resolve the current school year and the prior years from project config."""
    from dataexploration.constants import SY_LAST_YEAR, SY_THIS_YEAR, SY_TWO_PRIOR
    from dataexploration.school_year_utils import resolve_school_year

    aliases = (SY_TWO_PRIOR, SY_LAST_YEAR, SY_THIS_YEAR)[-limit:]
    years = []
    for alias in aliases:
        resolved = resolve_school_year(alias)
        if resolved and resolved not in years and not resolved.startswith('__sy_'):
            years.append(resolved)
    return years


def _week_trend_line(title: str, y_col: str, *, agg: str = 'count', **overrides) -> dict:
    """School-week line chart with one series per school year (last 3 applied at upsert)."""
    return base_chart_cfg(
        'line', title,
        x_col='school_week', y_col=y_col, agg=agg,
        stack_col='school_year', series_col='school_year',
        sort_col='__manual__', sort_dir='asc',
        category_order=WEEK_ORDER,
        show_grid=False, show_points=True, data_labels=False,
        color_map=YEAR_COLORS, legend_pos='bottom',
        sparse_series=True, zero_as_gap=True, connect_nulls=True,
        truncate_current_year_at_last_data=True,
        skip_page_filters=['school_year'],
        **overrides,
    )


def _week_trend_line_by_severity(
    title: str,
    severity: str,
    *,
    y_col: str = 'incident_id',
    agg: str = 'count',
    **overrides,
) -> dict:
    return _week_trend_line(title, y_col, agg=agg, **overrides)

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
    i.week_label AS week_label,
    i.is_expulsion_recommended AS is_expulsion_recommended,
    i.is_expelled AS is_expelled
FROM analytics_student_incidents AS i
"""

_INCIDENT_COLUMNS = [
    'ssid', 'stu_annual_key', 'student_id', 'student_name', 'grade',
    'grade_label', 'enrolled_school', 'enrolled_school_state_id',
    'incident_school', 'incident_location', 'status',
    'school_year', 'severity', 'incident_id', 'incident_date', 'violation',
    'ed_code', 'review_status', 'description', 'suspension_days',
    'is_suspended', 'school_week', 'week_start_date', 'week_end_date',
    'week_label', 'is_expulsion_recommended', 'is_expelled',
]
_INCIDENT_DRILL = _INCIDENT_COLUMNS

# One row per student with incident totals (optional drill / exports).
_STUDENT_SUMMARY_SQL = f"""
SELECT
    ssid AS ssid,
    student_name AS student_name,
    student_name AS student_label,
    grade AS grade,
    grade_label AS grade_label,
    incident_school AS incident_school,
    school_year AS school_year,
    count() AS incident_count,
    sum(suspension_days) AS suspension_days,
    countIf(severity = 'Major') AS major_incidents,
    countIf(severity = 'Minor') AS minor_incidents,
    countIf(is_suspended = 1) AS suspension_events,
    countIf(is_expulsion_recommended = 1) AS expulsion_recommended_events,
    countIf(is_expelled = 1) AS expulsion_events
FROM ({_INCIDENTS_SQL}) AS i
GROUP BY
    ssid, student_name, grade, grade_label, incident_school, school_year
"""

_STUDENT_SUMMARY_COLUMNS = [
    'ssid', 'student_name', 'student_label', 'grade', 'grade_label',
    'incident_school', 'school_year', 'incident_count',
    'suspension_days', 'major_incidents', 'minor_incidents',
    'suspension_events', 'expulsion_recommended_events', 'expulsion_events',
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
    school_year AS school_year,
    severity AS severity,
    incident_id AS incident_id,
    violation_value AS violation_value
FROM (
    SELECT
        i.ssid, i.student_name, i.grade, i.grade_label, i.incident_school,
        i.school_year, i.severity, i.incident_id,
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
    'school_year', 'severity', 'incident_id',
    'violation_value',
]

_ED_CODES_SQL = f"""
SELECT
    ssid AS ssid,
    student_name AS student_name,
    grade AS grade,
    grade_label AS grade_label,
    incident_school AS incident_school,
    school_year AS school_year,
    severity AS severity,
    incident_id AS incident_id,
    ed_code_value AS ed_code_value
FROM (
    SELECT
        i.ssid, i.student_name, i.grade, i.grade_label, i.incident_school,
        i.school_year, i.severity, i.incident_id,
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
    'school_year', 'severity', 'incident_id',
    'ed_code_value',
]
# Source data: ed_code is populated on major incidents only (no minor ed-code rows).


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
    violations_ds = f'{PREFIX} Violations'
    ed_codes_ds = f'{PREFIX} Ed Codes'

    return [
        {
            'name': f'{PREFIX} Incidents by Year',
            'chart_type': 'stacked_bar',
            'dataset': incidents_ds,
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'school_year': 'School Year', 'severity': 'Severity',
                'incident_id': 'Incidents',
            },
            'config': _incidents_stacked(
                'Incidents by School Year', 'school_year',
                sort_col='school_year', sort_dir='asc',
                skip_page_filters=['school_year'],
            ),
        },
        {
            'name': f'{PREFIX} Incidents by Incident School',
            'chart_type': 'stacked_bar',
            'dataset': incidents_ds,
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'incident_school': 'Incident School', 'severity': 'Severity',
                'incident_id': 'Incidents',
            },
            'config': _incidents_stacked(
                'Incidents by Incident School', 'incident_school',
                top_n=40, x_label_rotation=30,
            ),
        },
        {
            'name': f'{PREFIX} Students with Incidents by Incident School',
            'chart_type': 'stacked_bar',
            'dataset': incidents_ds,
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'incident_school': 'Incident School', 'severity': 'Severity',
                'ssid': 'Students',
            },
            'config': _students_stacked(
                'Students with Incidents by Incident School', 'incident_school',
                top_n=40, x_label_rotation=30,
            ),
        },
        {
            'name': f'{PREFIX} Incidents by Grade',
            'chart_type': 'stacked_bar',
            'dataset': incidents_ds,
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'grade_label': 'Grade', 'severity': 'Severity',
                'incident_id': 'Incidents',
            },
            'config': _incidents_stacked(
                'Incidents by Grade', 'grade_label',
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
            'name': f'{PREFIX} Top Major Violations',
            'chart_type': 'bar',
            'dataset': violations_ds,
            'source_filters': {'severity': ['Major']},
            'drill_columns': _VIOLATION_COLUMNS,
            'column_defs': {
                'violation_value': 'Violation', 'incident_id': 'Major Incidents',
            },
            'config': _category_rank_bar('Top Major Violations', 'violation_value'),
        },
        {
            'name': f'{PREFIX} Top Minor Violations',
            'chart_type': 'bar',
            'dataset': violations_ds,
            'source_filters': {'severity': ['Minor']},
            'drill_columns': _VIOLATION_COLUMNS,
            'column_defs': {
                'violation_value': 'Violation', 'incident_id': 'Minor Incidents',
            },
            'config': _category_rank_bar('Top Minor Violations', 'violation_value'),
        },
        {
            'name': f'{PREFIX} Top Major Ed Codes',
            'chart_type': 'bar',
            'dataset': ed_codes_ds,
            'source_filters': {'severity': ['Major']},
            'drill_columns': _ED_CODE_COLUMNS,
            'column_defs': {
                'ed_code_value': 'Ed Code', 'incident_id': 'Major Incidents',
            },
            'config': _category_rank_bar('Top Major Education Codes', 'ed_code_value'),
        },
        {
            'name': f'{PREFIX} Minor Incidents by Incident School',
            'chart_type': 'bar',
            'dataset': incidents_ds,
            'source_filters': {'severity': ['Minor']},
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'incident_school': 'Incident School',
                'incident_id': 'Minor Incidents',
            },
            'config': _category_rank_bar(
                'Minor Incidents by Incident School', 'incident_school',
                top_n=40, x_label_rotation=30,
            ),
        },
        {
            'name': f'{PREFIX} Major Incidents by Review Status',
            'chart_type': 'bar',
            'dataset': incidents_ds,
            'source_filters': {'severity': ['Major']},
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'review_status': 'Review Status', 'incident_id': 'Major Incidents',
            },
            'config': _category_rank_bar(
                'Major Incidents by Review Status', 'review_status',
                top_n=20, x_label_rotation=25,
            ),
        },
        {
            'name': f'{PREFIX} Minor Incidents by Review Status',
            'chart_type': 'bar',
            'dataset': incidents_ds,
            'source_filters': {'severity': ['Minor']},
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'review_status': 'Review Status', 'incident_id': 'Minor Incidents',
            },
            'config': _category_rank_bar(
                'Minor Incidents by Review Status', 'review_status',
                top_n=20, x_label_rotation=25,
            ),
        },
        {
            'name': f'{PREFIX} Major Incidents by School Week',
            'chart_type': 'line',
            'dataset': incidents_ds,
            'source_filters': {'severity': ['Major']},
            'drill_columns': _INCIDENT_DRILL,
            'drill_through_filter_map': {
                'x_col': 'school_week', 'series_col': 'school_year',
            },
            'column_defs': {
                'school_week': 'School Week', 'school_year': 'School Year',
                'severity': 'Severity', 'incident_id': 'Major Incidents',
            },
            'config': _week_trend_line_by_severity(
                'Major Incidents by School Week (last 3 years)', 'Major',
            ),
        },
        {
            'name': f'{PREFIX} Minor Incidents by School Week',
            'chart_type': 'line',
            'dataset': incidents_ds,
            'source_filters': {'severity': ['Minor']},
            'drill_columns': _INCIDENT_DRILL,
            'drill_through_filter_map': {
                'x_col': 'school_week', 'series_col': 'school_year',
            },
            'column_defs': {
                'school_week': 'School Week', 'school_year': 'School Year',
                'severity': 'Severity', 'incident_id': 'Minor Incidents',
            },
            'config': _week_trend_line_by_severity(
                'Minor Incidents by School Week (last 3 years)', 'Minor',
            ),
        },
        {
            'name': f'{PREFIX} Suspended Students by Incident School',
            'chart_type': 'stacked_bar',
            'dataset': incidents_ds,
            'source_filters': {'is_suspended': ['1']},
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'incident_school': 'Incident School', 'severity': 'Severity',
                'ssid': 'Students',
            },
            'config': _students_stacked(
                'Suspended Students by Incident School', 'incident_school',
                top_n=40, x_label_rotation=30,
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
            'name': f'{PREFIX} Suspensions by School Year',
            'chart_type': 'stacked_bar',
            'dataset': incidents_ds,
            'source_filters': {'is_suspended': ['1']},
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'school_year': 'School Year', 'severity': 'Severity',
                'incident_id': 'Suspensions',
            },
            'config': _incidents_stacked(
                'Suspension Count by School Year', 'school_year',
                sort_col='school_year', sort_dir='asc',
                skip_page_filters=['school_year'],
            ),
        },
        {
            'name': f'{PREFIX} Suspensions by Incident School',
            'chart_type': 'stacked_bar',
            'dataset': incidents_ds,
            'source_filters': {'is_suspended': ['1']},
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'incident_school': 'Incident School', 'severity': 'Severity',
                'incident_id': 'Suspensions',
            },
            'config': _incidents_stacked(
                'Suspension Count by Incident School', 'incident_school',
                top_n=40, x_label_rotation=30,
            ),
        },
        {
            'name': f'{PREFIX} Suspensions by School Week',
            'chart_type': 'line',
            'dataset': incidents_ds,
            'source_filters': {'is_suspended': ['1']},
            'drill_columns': _INCIDENT_DRILL,
            'drill_through_filter_map': {
                'x_col': 'school_week', 'series_col': 'school_year',
            },
            'column_defs': {
                'school_week': 'School Week', 'school_year': 'School Year',
                'incident_id': 'Suspensions',
            },
            'config': _week_trend_line(
                'Suspensions by School Week (last 3 years)', 'incident_id',
            ),
        },
        {
            'name': f'{PREFIX} Suspension Days by Week',
            'chart_type': 'line',
            'dataset': incidents_ds,
            'source_filters': {'is_suspended': ['1']},
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'school_week': 'School Week', 'suspension_days': 'Suspension Days',
            },
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
        {
            'name': f'{PREFIX} Students Recommended for Expulsion by Year',
            'chart_type': 'stacked_bar',
            'dataset': incidents_ds,
            'source_filters': {'is_expulsion_recommended': ['1']},
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'school_year': 'School Year', 'severity': 'Severity',
                'ssid': 'Students',
            },
            'config': _students_stacked(
                'Students Recommended for Expulsion by School Year', 'school_year',
                sort_col='school_year', sort_dir='asc',
                skip_page_filters=['school_year'],
            ),
        },
        {
            'name': f'{PREFIX} Students Recommended for Expulsion by School',
            'chart_type': 'stacked_bar',
            'dataset': incidents_ds,
            'source_filters': {'is_expulsion_recommended': ['1']},
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'incident_school': 'Incident School', 'severity': 'Severity',
                'ssid': 'Students',
            },
            'config': _students_stacked(
                'Students Recommended for Expulsion by Incident School',
                'incident_school',
                top_n=40, x_label_rotation=30,
            ),
        },
        {
            'name': f'{PREFIX} Expulsions by School Year',
            'chart_type': 'stacked_bar',
            'dataset': incidents_ds,
            'source_filters': {'is_expelled': ['1']},
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'school_year': 'School Year', 'severity': 'Severity',
                'ssid': 'Students Expelled',
            },
            'config': _students_stacked(
                'Students Expelled by School Year', 'school_year',
                sort_col='school_year', sort_dir='asc',
                skip_page_filters=['school_year'],
            ),
        },
        {
            'name': f'{PREFIX} Expulsions by Incident School',
            'chart_type': 'stacked_bar',
            'dataset': incidents_ds,
            'source_filters': {'is_expelled': ['1']},
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'incident_school': 'Incident School', 'severity': 'Severity',
                'ssid': 'Students Expelled',
            },
            'config': _students_stacked(
                'Students Expelled by Incident School', 'incident_school',
                top_n=40, x_label_rotation=30,
            ),
        },
        {
            'name': f'{PREFIX} Expulsion Events by School Year',
            'chart_type': 'stacked_bar',
            'dataset': incidents_ds,
            'source_filters': {'is_expelled': ['1']},
            'drill_columns': _INCIDENT_DRILL,
            'column_defs': {
                'school_year': 'School Year', 'severity': 'Severity',
                'incident_id': 'Expulsion Events',
            },
            'config': _incidents_stacked(
                'Expulsion Events by School Year', 'school_year',
                sort_col='school_year', sort_dir='asc',
                skip_page_filters=['school_year'],
            ),
        },
    ]


PAGE_SPEC = {
    'title': 'Behavior',
    'slug': PAGE_SLUG,
    'folder': PAGE_FOLDER,
    'show_chart_titles': False,
    'description': (
        'Student-focused discipline analysis by district and by the school where '
        'the incident occurred. Includes incident categories, suspensions, '
        'expulsions, and weekly trends on the dw_cal school calendar.'
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
            'id': 'violation', 'label': 'Violation Type',
            'source_col': 'violation_value', 'source_table': 'analytics_student_incidents',
            'source_distinct_sql': _filter_sql(_VIOLATIONS_SQL, 'd.violation_value'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'ed_code', 'label': 'Education Code',
            'source_col': 'ed_code_value', 'source_table': 'analytics_student_incidents',
            'source_distinct_sql': _filter_sql(_ED_CODES_SQL, 'd.ed_code_value'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'review_status', 'label': 'Review Status',
            'source_col': 'review_status', 'source_table': 'analytics_student_incidents',
            'source_distinct_sql': _filter_sql(_INCIDENTS_SQL, 'd.review_status'),
            'widget': 'multi', 'default_val': '', 'hidden': True,
        },
    ],
    'tabs': [
        'Overview', 'Categories', 'Over Time',
        'Suspensions', 'Expulsions', 'Detail',
    ],
    'rows': [
        {'tab': 'Overview', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Incidents by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 7},
        ]},
        {'tab': 'Overview', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Incidents by Incident School', 'gs_x': 0, 'gs_w': 7, 'gs_h': 10},
            {'chart': f'{PREFIX} Students with Incidents by Incident School', 'gs_x': 7, 'gs_w': 5, 'gs_h': 10},
        ]},
        {'tab': 'Overview', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Incidents by Grade', 'gs_x': 0, 'gs_w': 8, 'gs_h': 7},
            {'chart': f'{PREFIX} Major vs Minor', 'gs_x': 8, 'gs_w': 4, 'gs_h': 7},
        ]},
        {'tab': 'Categories', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Top Major Violations', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
        ]},
        {'tab': 'Categories', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Top Minor Violations', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
        ]},
        {'tab': 'Categories', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Top Major Ed Codes', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
        ]},
        {'tab': 'Categories', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Minor Incidents by Incident School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
        ]},
        {'tab': 'Categories', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Major Incidents by Review Status', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Categories', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Minor Incidents by Review Status', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Over Time', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Major Incidents by School Week', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Over Time', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Minor Incidents by School Week', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Over Time', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Suspensions by School Week', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Suspensions', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Suspended Students by Incident School', 'gs_x': 0, 'gs_w': 6, 'gs_h': 10},
            {'chart': f'{PREFIX} Suspensions by Incident School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 10},
        ]},
        {'tab': 'Suspensions', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Suspension Days by Incident School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Suspensions', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Suspensions by School Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Suspensions', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Suspension Days by Week', 'gs_x': 0, 'gs_w': 7, 'gs_h': 8},
            {'chart': f'{PREFIX} Suspension Days Distribution', 'gs_x': 7, 'gs_w': 5, 'gs_h': 8},
        ]},
        {'tab': 'Expulsions', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Students Recommended for Expulsion by Year', 'gs_x': 0, 'gs_w': 6, 'gs_h': 9},
            {'chart': f'{PREFIX} Students Recommended for Expulsion by School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 9},
        ]},
        {'tab': 'Expulsions', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Expulsions by School Year', 'gs_x': 0, 'gs_w': 6, 'gs_h': 9},
            {'chart': f'{PREFIX} Expulsions by Incident School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 9},
        ]},
        {'tab': 'Expulsions', 'label': '', 'sections': [
            {'chart': f'{PREFIX} Expulsion Events by School Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 7},
        ]},
        {'tab': 'Detail', 'label': '', 'sections': [
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

    recent_years = _recent_school_years(3)
    if recent_years:
        print(f'Over-time charts limited to school years: {", ".join(recent_years)}')

    chart_specs = []
    for spec in _chart_specs():
        item = dict(spec)
        if item['name'] in OVER_TIME_LINE_CHARTS and recent_years:
            merged = dict(item.get('source_filters') or {})
            merged['school_year'] = recent_years
            item['source_filters'] = merged
        chart_specs.append(item)

    datasets = {spec['name']: upsert_dataset(spec, owner, default_folder=DATASET_FOLDER)
                for spec in DATASET_SPECS}
    charts = {spec['name']: upsert_chart(
        spec, datasets, owner,
        chart_root=CHART_ROOT_FOLDER, chart_folder=CHART_FOLDER,
        allowed_prefix=PREFIX,
    ) for spec in chart_specs}
    page = upsert_page(PAGE_SPEC, charts, owner)
    attach_page_to_students_menu(page, 2)
    print('Behavior page ready.')


if __name__ == '__main__':
    main()
