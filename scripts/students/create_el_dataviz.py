#!/usr/bin/env python
"""Create the Students -> English Learners DataViz page.

Uses ``current_student`` for ELL status snapshots, ``analytics_student_results``
for ELPAC Summative ELPI levels, and ``analytics_student_enrollment_events`` for
RFEP/reclassification events mapped to dw_cal school weeks.

Run inside the Django container:

    docker compose exec django python /app/scripts/students/create_el_dataviz.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    CHART_ROOT_FOLDER,
    ELL_ORDER,
    GRADE_ORDER,
    SCHOOL_TYPE_ORDER,
    attach_page_to_students_menu,
    base_chart_cfg,
    bootstrap_django,
    current_school_year,
    enable_dataset_tables,
    resolve_owner,
    summary_cfg,
    upsert_chart,
    upsert_dataset,
    upsert_page,
)

PREFIX = 'Students • EL •'
PAGE_SLUG = 'students-el'
PAGE_FOLDER = 'Students / EL'
CHART_FOLDER = 'el'
DATASET_FOLDER = 'Students / EL'

WEEK_ORDER = [str(w) for w in range(1, 43)]
ELL_STATUS_ORDER = ELL_ORDER + ['Unknown']

GRADE_SQL = """
multiIf(
    upper(trimBoth(ifNull({col}, ''))) IN ('TK'), 'TK',
    upper(trimBoth(ifNull({col}, ''))) IN ('KN', 'K', 'KG'), 'KN',
    upper(trimBoth(ifNull({col}, ''))) IN ('US', '250'), 'US.',
    match(trimBoth(ifNull({col}, '')), '^[0-9]+$'),
    lpad(toString(toInt32OrZero(trimBoth({col}))), 2, '0'),
    if(trimBoth(ifNull({col}, '')) = '', 'Unknown', trimBoth({col}))
)"""

GROUP_SQL = "coalesce(nullif(trim(sch.school_group), ''), 'Unknown')"

ELL_COLORS = {
    'EL': '#e15759',
    'IFEP': '#f28e2b',
    'RFEP': '#59a14f',
    'EO': '#4e79a7',
    'TBD': '#edc948',
    'ADEL': '#b07aa1',
    'Unknown': '#9c9c9c',
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

YEAR_COLORS = {
    '2023-2024': '#4e79a7',
    '2024-2025': '#f28e2b',
    '2025-2026': '#59a14f',
    '2026-2027': '#e15759',
}

# ELL status snapshot for Active students.
_STATUS_SQL = f"""
SELECT
    cs.ssid AS ssid,
    cs.stu_annual_key AS stu_annual_key,
    cs.student_name AS student_name,
    cs.grade AS grade,
    ({GRADE_SQL.format(col='cs.grade')}) AS grade_label,
    cs.school AS school,
    cs.school_state_id AS school_state_id,
    ({GROUP_SQL}) AS school_group,
    cs.status AS status,
    cs.school_year AS school_year,
    coalesce(nullif(trim(cs.ell), ''), 'Unknown') AS ell,
    coalesce(cs.ltel, '') AS ltel,
    coalesce(cs.sed, '') AS sed,
    coalesce(cs.sped, '') AS sped,
    coalesce(cs.foster, '') AS foster,
    coalesce(cs.homeless, '') AS homeless,
    coalesce(cs.gender, '') AS gender,
    coalesce(cs.race, '') AS race
FROM current_student AS cs
LEFT JOIN (
    SELECT state_id, max(school_group) AS school_group
    FROM school
    WHERE state_id IS NOT NULL AND trim(state_id) != ''
    GROUP BY state_id
) AS sch ON sch.state_id = cs.school_state_id
WHERE cs.status = 'Active'
  AND cs.ssid IS NOT NULL AND trim(cs.ssid) != ''
"""

_STATUS_COLUMNS = [
    'ssid', 'stu_annual_key', 'student_name', 'grade', 'grade_label', 'school',
    'school_state_id', 'school_group', 'status', 'school_year', 'ell', 'ltel',
    'sed', 'sped', 'foster', 'homeless', 'gender', 'race',
]
_STATUS_DRILL = _STATUS_COLUMNS

# ELPAC Summative ELPI results for students with an ELL status in the test year.
_ELPI_SQL = f"""
SELECT *
FROM (
    SELECT
        r.ssid AS ssid,
        coalesce(cs.stu_annual_key, '') AS stu_annual_key,
        coalesce(cs.student_name, '') AS student_name,
        coalesce(cs.grade, '') AS grade,
        ({GRADE_SQL.format(col='cs.grade')}) AS grade_label,
        coalesce(nullif(trim(cs.school), ''), 'Unknown') AS school,
        coalesce(cs.school_state_id, '') AS school_state_id,
        ({GROUP_SQL}) AS school_group,
        coalesce(cs.ell, '') AS ell,
        coalesce(cs.ltel, '') AS ltel,
        coalesce(r.school_year, '') AS school_year,
        coalesce(r.school_year, '') AS test_year,
        coalesce(r.subject, '') AS subject,
        coalesce(r.test_window, '') AS test_window,
        multiIf(
            upper(trim(ifNull(JSONExtractString(assumeNotNull(r.other_info), 'ELPI_level'), ''))) IN ('1', 'LEVEL 1'), 'Level 1',
            upper(trim(ifNull(JSONExtractString(assumeNotNull(r.other_info), 'ELPI_level'), ''))) IN ('2L', 'LEVEL 2L'), 'Level 2L',
            upper(trim(ifNull(JSONExtractString(assumeNotNull(r.other_info), 'ELPI_level'), ''))) IN ('2H', 'LEVEL 2H'), 'Level 2H',
            upper(trim(ifNull(JSONExtractString(assumeNotNull(r.other_info), 'ELPI_level'), ''))) IN ('2', 'LEVEL 2'), 'Level 2',
            upper(trim(ifNull(JSONExtractString(assumeNotNull(r.other_info), 'ELPI_level'), ''))) IN ('3L', 'LEVEL 3L'), 'Level 3L',
            upper(trim(ifNull(JSONExtractString(assumeNotNull(r.other_info), 'ELPI_level'), ''))) IN ('3H', 'LEVEL 3H'), 'Level 3H',
            upper(trim(ifNull(JSONExtractString(assumeNotNull(r.other_info), 'ELPI_level'), ''))) IN ('3', 'LEVEL 3'), 'Level 3',
            upper(trim(ifNull(JSONExtractString(assumeNotNull(r.other_info), 'ELPI_level'), ''))) IN ('4', 'LEVEL 4'), 'Level 4',
            ''
        ) AS elpi_level
    FROM analytics_student_results AS r
    LEFT JOIN current_student AS cs
        ON cs.ssid = r.ssid AND cs.school_year = r.school_year
    LEFT JOIN (
        SELECT state_id, max(school_group) AS school_group
        FROM school
        WHERE state_id IS NOT NULL AND trim(state_id) != ''
        GROUP BY state_id
    ) AS sch ON sch.state_id = cs.school_state_id
    WHERE lower(trim(r.test_name)) = 'elpac_summative'
      AND r.ssid IS NOT NULL AND trim(r.ssid) != ''
) AS e
WHERE elpi_level != ''
"""

_ELPI_COLUMNS = [
    'ssid', 'stu_annual_key', 'student_name', 'grade', 'grade_label', 'school',
    'school_state_id', 'school_group', 'ell', 'ltel', 'school_year',
    'test_year', 'subject', 'test_window', 'elpi_level',
]
_ELPI_DRILL = _ELPI_COLUMNS

# RFEP/reclassification events.
_RECLASS_SQL = f"""
SELECT
    event_date AS event_date,
    school_year AS school_year,
    school_week AS school_week,
    week_start_date AS week_start_date,
    week_end_date AS week_end_date,
    week_label AS week_label,
    ssid AS ssid,
    stu_annual_key AS stu_annual_key,
    student_name AS student_name,
    grade AS grade,
    grade_label AS grade_label,
    school AS school,
    school_state_id AS school_state_id,
    coalesce(nullif(trim(school_group), ''), 'Unknown') AS school_group,
    status AS status,
    ell AS ell,
    ltel AS ltel,
    sed AS sed,
    sped AS sped,
    foster AS foster,
    homeless AS homeless,
    migrant AS migrant,
    gate AS gate,
    in_504 AS in_504,
    dli AS dli,
    fep_date AS fep_date
FROM analytics_student_enrollment_events
WHERE event_type = 'Reclassified'
"""

_RECLASS_COLUMNS = [
    'event_date', 'school_year', 'school_week', 'week_start_date',
    'week_end_date', 'week_label', 'ssid', 'stu_annual_key', 'student_name',
    'grade', 'grade_label', 'school', 'school_state_id', 'school_group',
    'status', 'ell', 'ltel', 'sed', 'sped', 'foster', 'homeless', 'migrant',
    'gate', 'in_504', 'dli', 'fep_date',
]
_RECLASS_DRILL = _RECLASS_COLUMNS


def _filter_sql(dataset_sql: str, expr: str) -> str:
    return f"""
SELECT DISTINCT ({expr}) AS v
FROM ({dataset_sql}) AS d
HAVING trim(toString(v)) != ''
ORDER BY v
"""


DATASET_SPECS = [
    {
        'name': f'{PREFIX} Status',
        'sql': _STATUS_SQL,
        'base_table': 'current_student',
        'columns': _STATUS_COLUMNS,
        'folder': DATASET_FOLDER,
        'description': (
            'One row per Active student with the ELL status list '
            '(EL/IFEP/RFEP/EO/TBD/ADEL), LTEL flag, school type, school, and grade.'
        ),
    },
    {
        'name': f'{PREFIX} ELPI',
        'sql': _ELPI_SQL,
        'base_table': 'analytics_student_results',
        'columns': _ELPI_COLUMNS,
        'folder': DATASET_FOLDER,
        'description': (
            'One row per ELPAC Summative result with a normalized ELPI level and '
            'the student ELL status from the test-year roster.'
        ),
    },
    {
        'name': f'{PREFIX} Reclassification',
        'sql': _RECLASS_SQL,
        'base_table': 'analytics_student_enrollment_events',
        'columns': _RECLASS_COLUMNS,
        'folder': DATASET_FOLDER,
        'description': (
            'One row per RFEP/reclassification event (fep_date), with the grade '
            'and school from the enrollment year and the dw_cal school week.'
        ),
    },
]


def _chart_specs() -> list[dict]:
    status_ds = f'{PREFIX} Status'
    elpi_ds = f'{PREFIX} ELPI'
    reclass_ds = f'{PREFIX} Reclassification'
    this_year = current_school_year()

    return [
        {
            'name': f'{PREFIX} ELL Status Mix by Year',
            'chart_type': 'stacked_bar',
            'dataset': status_ds,
            'drill_columns': _STATUS_DRILL,
            'column_defs': {'school_year': 'School Year', 'ell': 'ELL Status', 'ssid': 'Students'},
            'config': base_chart_cfg(
                'stacked_bar', 'ELL Status Mix by School Year',
                x_col='school_year', y_col='ssid', agg='count_unique',
                stack_col='ell', series_col='ell',
                sort_col='school_year', sort_dir='asc',
                stack_order=ELL_STATUS_ORDER, color_map=ELL_COLORS,
                skip_page_filters=['school_year', 'ell'],
                one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} EL Students by Year',
            'chart_type': 'bar',
            'dataset': status_ds,
            'source_filters': {'ell': ['EL']},
            'drill_columns': _STATUS_DRILL,
            'column_defs': {'school_year': 'School Year', 'ssid': 'EL Students'},
            'config': base_chart_cfg(
                'bar', 'English Learner Students by School Year',
                x_col='school_year', y_col='ssid', agg='count_unique',
                sort_col='school_year', sort_dir='asc',
                skip_page_filters=['school_year'], one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} EL Students by School',
            'chart_type': 'bar',
            'dataset': status_ds,
            'source_filters': {'ell': ['EL']},
            'drill_columns': _STATUS_DRILL,
            'column_defs': {'school': 'School', 'ssid': 'EL Students'},
            'config': base_chart_cfg(
                'bar', 'English Learner Students by School',
                x_col='school', y_col='ssid', agg='count_unique',
                top_n=40, x_label_rotation=30, one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} EL Students by Grade',
            'chart_type': 'bar',
            'dataset': status_ds,
            'source_filters': {'ell': ['EL']},
            'drill_columns': _STATUS_DRILL,
            'column_defs': {'grade_label': 'Grade', 'ssid': 'EL Students'},
            'config': base_chart_cfg(
                'bar', 'English Learner Students by Grade',
                x_col='grade_label', y_col='ssid', agg='count_unique',
                sort_col='__manual__', category_order=GRADE_ORDER,
                one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} LTEL Students by Year',
            'chart_type': 'bar',
            'dataset': status_ds,
            'source_filters': {'ltel': ['Y']},
            'drill_columns': _STATUS_DRILL,
            'column_defs': {'school_year': 'School Year', 'ssid': 'LTEL Students'},
            'config': base_chart_cfg(
                'bar', 'Long-Term English Learner Students by Year',
                x_col='school_year', y_col='ssid', agg='count_unique',
                sort_col='school_year', sort_dir='asc',
                skip_page_filters=['school_year'], one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} ELPI Level Distribution by Year',
            'chart_type': 'stacked_bar',
            'dataset': elpi_ds,
            'drill_columns': _ELPI_DRILL,
            'column_defs': {'test_year': 'ELPAC Year', 'elpi_level': 'ELPI Level', 'ssid': 'Students'},
            'config': base_chart_cfg(
                'stacked_bar', 'ELPI Level Distribution by ELPAC Year',
                x_col='test_year', y_col='ssid', agg='count_unique',
                stack_col='elpi_level', series_col='elpi_level',
                sort_col='test_year', sort_dir='asc',
                stack_order=ELPI_STACK_ORDER, color_map=ELPI_COLORS,
                skip_page_filters=['school_year'], one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} ELPI by Grade',
            'chart_type': 'stacked_bar',
            'dataset': elpi_ds,
            'drill_columns': _ELPI_DRILL,
            'column_defs': {'grade_label': 'Grade', 'elpi_level': 'ELPI Level', 'ssid': 'Students'},
            'config': base_chart_cfg(
                'stacked_bar', 'ELPI Level by Grade',
                x_col='grade_label', y_col='ssid', agg='count_unique',
                stack_col='elpi_level', series_col='elpi_level',
                sort_col='__manual__', category_order=GRADE_ORDER,
                stack_order=ELPI_STACK_ORDER, color_map=ELPI_COLORS,
                skip_page_filters=['school_year'], one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} ELPI by School',
            'chart_type': 'stacked_bar',
            'dataset': elpi_ds,
            'drill_columns': _ELPI_DRILL,
            'column_defs': {'school': 'School', 'elpi_level': 'ELPI Level', 'ssid': 'Students'},
            'config': base_chart_cfg(
                'stacked_bar', 'ELPI Level by School',
                x_col='school', y_col='ssid', agg='count_unique',
                stack_col='elpi_level', series_col='elpi_level',
                sort_col='_value', sort_dir='desc', top_n=25,
                stack_order=ELPI_STACK_ORDER, color_map=ELPI_COLORS,
                skip_page_filters=['school_year'],
                x_label_rotation=30, one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} Reclassifications by Year',
            'chart_type': 'bar',
            'dataset': reclass_ds,
            'drill_columns': _RECLASS_DRILL,
            'column_defs': {'school_year': 'School Year', 'ssid': 'Reclassified Students'},
            'config': base_chart_cfg(
                'bar', 'RFEP / Reclassifications by School Year',
                x_col='school_year', y_col='ssid', agg='count_unique',
                sort_col='school_year', sort_dir='asc',
                skip_page_filters=['school_year'], one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} RFEP Students by Year',
            'chart_type': 'bar',
            'dataset': status_ds,
            'source_filters': {'ell': ['RFEP']},
            'drill_columns': _STATUS_DRILL,
            'column_defs': {'school_year': 'School Year', 'ssid': 'RFEP Students'},
            'config': base_chart_cfg(
                'bar', 'RFEP Students by School Year',
                x_col='school_year', y_col='ssid', agg='count_unique',
                sort_col='school_year', sort_dir='asc',
                skip_page_filters=['school_year'], one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} Reclassifications by School',
            'chart_type': 'bar',
            'dataset': reclass_ds,
            'drill_columns': _RECLASS_DRILL,
            'column_defs': {'school': 'School', 'ssid': 'Reclassified Students'},
            'config': base_chart_cfg(
                'bar', 'Reclassifications by School',
                x_col='school', y_col='ssid', agg='count_unique',
                top_n=40, x_label_rotation=30, one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} Reclassifications by Grade',
            'chart_type': 'bar',
            'dataset': reclass_ds,
            'drill_columns': _RECLASS_DRILL,
            'column_defs': {'grade_label': 'Grade', 'ssid': 'Reclassified Students'},
            'config': base_chart_cfg(
                'bar', 'Reclassifications by Grade',
                x_col='grade_label', y_col='ssid', agg='count_unique',
                sort_col='__manual__', category_order=GRADE_ORDER,
                one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} Current Year Reclassifications by Week',
            'chart_type': 'bar',
            'dataset': reclass_ds,
            'source_filters': {'school_year': [this_year]} if this_year else {},
            'drill_columns': _RECLASS_DRILL,
            'column_defs': {
                'school_week': 'School Week', 'ssid': 'Reclassified Students',
                'week_label': 'Week',
            },
            'config': base_chart_cfg(
                'bar', 'Current Year Reclassifications by School Week',
                x_col='school_week', y_col='ssid', agg='count_unique',
                sort_col='__manual__', sort_dir='asc', category_order=WEEK_ORDER,
                x_label_rotation=0, one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} Current Year Reclassifications by Week and School Type',
            'chart_type': 'stacked_bar',
            'dataset': reclass_ds,
            'source_filters': {'school_year': [this_year]} if this_year else {},
            'drill_columns': _RECLASS_DRILL,
            'column_defs': {
                'school_week': 'School Week', 'school_group': 'School Type',
                'ssid': 'Reclassified Students',
            },
            'config': base_chart_cfg(
                'stacked_bar', 'Current Year Reclassifications by School Week and Type',
                x_col='school_week', y_col='ssid', agg='count_unique',
                stack_col='school_group', series_col='school_group',
                sort_col='__manual__', sort_dir='asc', category_order=WEEK_ORDER,
                stack_order=SCHOOL_TYPE_ORDER, one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} Reclassification Detail Table',
            'chart_type': 'summary_table',
            'dataset': reclass_ds,
            'source_filters': {'school_year': [this_year]} if this_year else {},
            'drill_columns': _RECLASS_DRILL,
            'column_defs': {'school': 'School', 'school_week': 'School Week', 'ssid': 'Reclassified Students'},
            'config': summary_cfg(
                'Current Year Reclassifications by School and Week',
                'school', 'school_week',
            ),
        },
    ]


PAGE_SPEC = {
    'title': 'English Learners',
    'slug': PAGE_SLUG,
    'folder': PAGE_FOLDER,
    'description': (
        'English learner status, ELPAC Summative ELPI levels, and RFEP / '
        'reclassification by year and by dw_cal school week.'
    ),
    'page_filters': [
        {
            'id': 'school_year', 'label': 'School Year',
            'source_col': 'school_year', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql(_STATUS_SQL, 'd.school_year'),
            'widget': 'select', 'default_val': '__sy_this__', 'hidden': False,
        },
        {
            'id': 'school_group', 'label': 'School Type',
            'source_col': 'school_group', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql(_STATUS_SQL, 'd.school_group'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'school', 'label': 'School',
            'source_col': 'school', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql(_STATUS_SQL, 'd.school'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'grade', 'label': 'Grade',
            'source_col': 'grade_label', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql(_STATUS_SQL, 'd.grade_label'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'ell', 'label': 'ELL Status',
            'source_col': 'ell', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql(_STATUS_SQL, 'd.ell'),
            'widget': 'multi', 'default_val': 'EL,RFEP', 'hidden': False,
        },
        {
            'id': 'ltel', 'label': 'LTEL',
            'source_col': 'ltel', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql(_STATUS_SQL, 'd.ltel'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'test_year', 'label': 'ELPAC Year',
            'source_col': 'test_year', 'source_table': 'analytics_student_results',
            'source_distinct_sql': _filter_sql(_ELPI_SQL, 'd.test_year'),
            'widget': 'select', 'default_val': '', 'hidden': False,
        },
    ],
    'tabs': ['Overview', 'ELPI', 'Reclassification', 'Weekly Reclassification', 'Detail'],
    'rows': [
        {'tab': 'Overview', 'label': 'ELL status mix', 'sections': [
            {'chart': f'{PREFIX} ELL Status Mix by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Overview', 'label': 'English learner counts', 'sections': [
            {'chart': f'{PREFIX} EL Students by Year', 'gs_x': 0, 'gs_w': 4, 'gs_h': 7},
            {'chart': f'{PREFIX} EL Students by Grade', 'gs_x': 4, 'gs_w': 8, 'gs_h': 7},
        ]},
        {'tab': 'Overview', 'label': 'EL students and LTEL by school', 'sections': [
            {'chart': f'{PREFIX} EL Students by School', 'gs_x': 0, 'gs_w': 7, 'gs_h': 10},
            {'chart': f'{PREFIX} LTEL Students by Year', 'gs_x': 7, 'gs_w': 5, 'gs_h': 10},
        ]},
        {'tab': 'ELPI', 'label': 'ELPI level distribution', 'sections': [
            {'chart': f'{PREFIX} ELPI Level Distribution by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'ELPI', 'label': 'ELPI by grade and school', 'sections': [
            {'chart': f'{PREFIX} ELPI by Grade', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'ELPI', 'label': 'ELPI by school', 'sections': [
            {'chart': f'{PREFIX} ELPI by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
        ]},
        {'tab': 'Reclassification', 'label': 'RFEP by year', 'sections': [
            {'chart': f'{PREFIX} Reclassifications by Year', 'gs_x': 0, 'gs_w': 6, 'gs_h': 7},
            {'chart': f'{PREFIX} RFEP Students by Year', 'gs_x': 6, 'gs_w': 6, 'gs_h': 7},
        ]},
        {'tab': 'Reclassification', 'label': 'RFEP by school and grade', 'sections': [
            {'chart': f'{PREFIX} Reclassifications by School', 'gs_x': 0, 'gs_w': 8, 'gs_h': 10},
            {'chart': f'{PREFIX} Reclassifications by Grade', 'gs_x': 8, 'gs_w': 4, 'gs_h': 10},
        ]},
        {'tab': 'Weekly Reclassification', 'label': 'Current year by week', 'sections': [
            {'chart': f'{PREFIX} Current Year Reclassifications by Week', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Weekly Reclassification', 'label': 'Current year by week and school type', 'sections': [
            {'chart': f'{PREFIX} Current Year Reclassifications by Week and School Type', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Detail', 'label': 'Reclassifications by school and week', 'sections': [
            {'chart': f'{PREFIX} Reclassification Detail Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 14},
        ]},
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser(description='Create the Students -> English Learners page.')
    parser.add_argument('--owner', default='', help='Owner username or email.')
    args = parser.parse_args()

    project_dir = bootstrap_django()
    print(f'Django project: {project_dir}')
    owner = resolve_owner(args.owner)
    print(f'Using owner: {owner}')

    enable_dataset_tables([
        'current_student', 'school', 'student_enroll',
        'analytics_student_results',
        'analytics_student_enrollment_events',
    ])

    datasets = {spec['name']: upsert_dataset(spec, owner, default_folder=DATASET_FOLDER)
                for spec in DATASET_SPECS}
    charts = {spec['name']: upsert_chart(
        spec, datasets, owner,
        chart_root=CHART_ROOT_FOLDER, chart_folder=CHART_FOLDER,
        allowed_prefix=PREFIX,
    ) for spec in _chart_specs()}
    page = upsert_page(PAGE_SPEC, charts, owner)
    attach_page_to_students_menu(page, 1)
    print('English Learners page ready.')


if __name__ == '__main__':
    main()
