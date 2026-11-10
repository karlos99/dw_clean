#!/usr/bin/env python
"""Create the CTE DataViz datasets, charts, and sectioned page.

Read-only PostgreSQL SQL over ``coursecatalog_cte_details`` (student pathway
status), ``coursecatalog_cte_pathways`` (course -> pathway / industry
taxonomy), ``schedule_list`` (CTE course enrollment) and ``current_student``
(school, grade, demographics).

The ``coursecatalog_cte_*`` tables are not mirrored into ClickHouse, so the
executor routes this dataset SQL to PostgreSQL. All SQL below is therefore
PostgreSQL syntax.

Run:
    docker compose exec django python /app/scripts/create_cte_dataviz.py
Optional:
    --owner you@example.com
    --qa
"""
import argparse
import os
import sys
from pathlib import Path

PREFIX = 'CTE •'
CHART_FOLDER = 'cte'
CHART_ROOT_FOLDER = 'College_and_Career_Readiness'
DATASET_FOLDER = 'College & Career Readiness / CTE'
PAGE_SLUG = 'cte-pathways'
PAGE_TITLE = 'CTE Pathways'
MENU_SLUG = 'college-career-readiness'
MENU_NAME = 'College & Career Readiness'
MENU_ORDER = 56
THIS_YEAR = '{{This School Year}}'

DEFAULT_OWNER_NAME = ('Carlos', 'Silva')
DEFAULT_OWNER_EMAIL = 'carlos.silva@desertsands.us'

COMPLETION_ORDER = ['Completed', 'On Track', 'Participation', 'Other']
COMPLETION_COLORS = {
    'Completed': '#59a14f',
    'On Track': '#4e79a7',
    'Participation': '#f28e2b',
    'Other': '#bab0ac',
}
PROGRAM_ORDER = ['ELL', 'GATE', 'SED', 'SPED', 'Migrant', 'Foster']
PROGRAM_COLORS = {
    'ELL': '#4e79a7',
    'GATE': '#59a14f',
    'SED': '#f28e2b',
    'SPED': '#e15759',
    'Migrant': '#76b7b2',
    'Foster': '#edc948',
}
RACE_ORDER = [
    'Hispanic', 'White', 'Asian', 'Black or African American', 'Filipino',
    'Multiple', 'American Indian or Alaska Native',
    'Hawaiian/Pacific Islander', 'Missing',
]
RACE_COLORS = {
    'Hispanic': '#4e79a7',
    'White': '#59a14f',
    'Asian': '#f28e2b',
    'Black or African American': '#e15759',
    'Filipino': '#76b7b2',
    'Multiple': '#edc948',
    'American Indian or Alaska Native': '#b07aa1',
    'Hawaiian/Pacific Islander': '#ff9da7',
    'Missing': '#bab0ac',
}
COURSE_LEVEL_ORDER = ['Introduction', 'Concentration', 'Capstone']
COURSE_LEVEL_COLORS = {
    'Introduction': '#4e79a7',
    'Concentration': '#f28e2b',
    'Capstone': '#59a14f',
}
GRADE_ORDER = ['9th', '10th', '11th', '12th']
GRADE_COLORS = {
    '9th': '#4e79a7',
    '10th': '#f28e2b',
    '11th': '#59a14f',
    '12th': '#e15759',
}


# ── SQL helpers (PostgreSQL syntax) ──────────────────────────────────────────

def _grade_label_sql(g: str) -> str:
    return f"""CASE
        WHEN btrim({g}) = '' THEN 'Unknown'
        WHEN upper(btrim({g})) = 'TK' THEN 'TK'
        WHEN upper(btrim({g})) IN ('KN', 'K', 'KG') THEN 'KN'
        WHEN btrim({g}) ~ '^[0-9]+$' THEN lpad(btrim({g}), 2, '0')
        ELSE btrim({g})
    END"""


def _grade_display_sql(g: str) -> str:
    return f"""CASE
        WHEN btrim({g}) = '' THEN 'Unknown'
        WHEN upper(btrim({g})) = 'TK' THEN 'TK'
        WHEN upper(btrim({g})) IN ('KN', 'K', 'KG') THEN 'Kindergarten'
        WHEN btrim({g}) ~ '^[0-9]+$' THEN
            CASE btrim({g})::int
                WHEN 1 THEN '1st'
                WHEN 2 THEN '2nd'
                WHEN 3 THEN '3rd'
                ELSE btrim({g})::int::text || 'th'
            END
        ELSE btrim({g})
    END"""


def _level_band_sql(g: str) -> str:
    return f"""CASE
        WHEN btrim({g}) ~ '^[0-9]+$' AND btrim({g})::int BETWEEN 9 AND 12 THEN 'High'
        WHEN btrim({g}) ~ '^[0-9]+$' AND btrim({g})::int BETWEEN 6 AND 8 THEN 'Middle'
        ELSE 'Other'
    END"""


def _completion_bucket_sql(col: str = 'd.pathway_status') -> str:
    return f"""CASE
        WHEN lower(btrim(coalesce({col}, ''))) = 'cte completer' THEN 'Completed'
        WHEN lower(btrim(coalesce({col}, ''))) IN ('in progress', 'needs capstone', 'needs concentration') THEN 'On Track'
        WHEN lower(btrim(coalesce({col}, ''))) = 'participation' THEN 'Participation'
        ELSE 'Other'
    END"""


def _student_demographics_sql(alias: str = 'cs') -> str:
    return f"""
    coalesce({alias}.gender, '') AS gender,
    coalesce({alias}.race, '') AS race,
    coalesce({alias}.ell, '') AS ell,
    coalesce({alias}.sed, '') AS sed,
    coalesce({alias}.sped, '') AS sped,
    coalesce({alias}.gate, '') AS gate,
    coalesce({alias}.foster, '') AS foster,
    coalesce({alias}.status, '') AS status,"""


def _pathway_sql() -> str:
    g = "coalesce(cs.grade, d.grade, '')"
    year_expr = (
        "CASE WHEN d.school_year ~ '^[0-9]{4}$' "
        "THEN d.school_year || '-' || lpad(((d.school_year)::int + 1)::text, 4, '0') "
        "ELSE d.school_year END"
    )
    return f"""
WITH cs_all AS MATERIALIZED (
    SELECT
        ssid,
        substring(school_year, 1, 4) AS year4,
        student_name,
        school,
        grade,
        gender,
        race,
        ell,
        sed,
        sped,
        gate,
        foster,
        status
    FROM current_student
    WHERE ssid IS NOT NULL AND btrim(ssid) <> ''
),
cc_cur AS MATERIALIZED (
    SELECT ssid, school
    FROM current_student
    WHERE school_year = '{THIS_YEAR}'
      AND ssid IS NOT NULL AND btrim(ssid) <> ''
)
SELECT
    nullif(btrim(d.state_student_id), '') AS ssid,
    coalesce(cs.student_name, '') AS student_name,
    coalesce(cs.school, d.school, 'Non-DS School') AS school,
    coalesce(cc.school, 'Non-DS School') AS enrolled_school,
    {g} AS grade,
    {_grade_label_sql(g)} AS grade_label,
    {_grade_display_sql(g)} AS grade_display,
    {_level_band_sql(g)} AS level_band,
{_student_demographics_sql('cs')}
    1 AS district_school,
    {year_expr} AS school_year,
    {year_expr} AS school_year_label,
    coalesce(d.cte_pathway, '') AS cte_pathway,
    coalesce(d.pathway_code, '') AS pathway_code,
    coalesce(d.pathway_code_name, '') AS pathway_code_name,
    coalesce(d.industry_sector, '') AS industry_sector,
    coalesce(d.pathway_status, '') AS pathway_status,
    {_completion_bucket_sql('d.pathway_status')} AS completion_bucket,
    CASE WHEN lower(btrim(coalesce(d.pathway_status, ''))) = 'cte completer' THEN 1 ELSE 0 END AS is_completer,
    CASE WHEN lower(btrim(coalesce(d.pathway_status, ''))) IN ('in progress', 'needs capstone', 'needs concentration') THEN 1 ELSE 0 END AS is_on_track,
    CASE WHEN d.school_year = substring('{THIS_YEAR}', 1, 4) THEN 1 ELSE 0 END AS is_current_year,
    CASE WHEN d.school_year = substring('{THIS_YEAR}', 1, 4) THEN 'Current' ELSE 'Prior' END AS year_scope,
    {year_expr} || ' · ' || {_grade_display_sql(g)} AS year_grade,
    {year_expr} || ' · ' || coalesce(cs.race, '') AS year_race,
    {year_expr} || ' · ' || {_completion_bucket_sql('d.pathway_status')} AS year_completion,
    {year_expr} || ' · ' || coalesce(d.industry_sector, '') AS year_industry
FROM coursecatalog_cte_details d
LEFT JOIN cs_all cs
    ON cs.ssid = nullif(btrim(d.state_student_id), '')
   AND cs.year4 = d.school_year
LEFT JOIN cc_cur cc
    ON cc.ssid = nullif(btrim(d.state_student_id), '')
WHERE d.school_year IS NOT NULL AND btrim(d.school_year) <> ''
  AND d.state_student_id IS NOT NULL AND btrim(d.state_student_id) <> ''
"""


def _course_sql() -> str:
    g = "coalesce(cs.grade, '')"
    return f"""
WITH cte_courses AS MATERIALIZED (
    SELECT
        nullif(btrim(s.ssid), '') AS ssid,
        min(s.stu_annual_key) AS stu_annual_key,
        s.school_year,
        nullif(btrim(s.course_code), '') AS course_code,
        min(coalesce(
            nullif(btrim(s.course_name), ''),
            nullif(btrim(s.course_code), ''),
            'Unknown course'
        )) AS course_name,
        coalesce(cte.cte_pathway, '') AS cte_pathway,
        coalesce(cte.career_cluster, '') AS industry_sector,
        coalesce(cte.cte_course_level, '') AS cte_course_level,
        min(coalesce(s.school_name, '')) AS sched_school,
        1 AS course_enrollment
    FROM schedule_list s
    JOIN coursecatalog_cte_pathways cte
        ON cte.course_id IS NOT NULL AND btrim(cte.course_id) <> ''
       AND cte.course_id = nullif(btrim(s.course_code), '')
    WHERE s.schedule_status IN ('Active', 'Completed')
      AND s.ssid IS NOT NULL AND btrim(s.ssid) <> ''
      AND s.school_year IS NOT NULL AND btrim(s.school_year) <> ''
      AND nullif(btrim(s.course_code), '') IN (
          SELECT c2.course_id
          FROM coursecatalog_cte_pathways c2
          WHERE c2.course_id IS NOT NULL AND btrim(c2.course_id) <> ''
      )
    GROUP BY
        nullif(btrim(s.ssid), ''),
        s.school_year,
        nullif(btrim(s.course_code), ''),
        coalesce(cte.cte_pathway, ''),
        coalesce(cte.career_cluster, ''),
        coalesce(cte.cte_course_level, '')
),
cc_cur AS MATERIALIZED (
    SELECT ssid, school
    FROM current_student
    WHERE school_year = '{THIS_YEAR}'
      AND ssid IS NOT NULL AND btrim(ssid) <> ''
)
SELECT
    c.ssid,
    coalesce(cs.student_name, '') AS student_name,
    coalesce(cs.school, c.sched_school, 'Non-DS School') AS school,
    coalesce(cc.school, 'Non-DS School') AS enrolled_school,
    {g} AS grade,
    {_grade_label_sql(g)} AS grade_label,
    {_grade_display_sql(g)} AS grade_display,
    {_level_band_sql(g)} AS level_band,
{_student_demographics_sql('cs')}
    1 AS district_school,
    c.school_year,
    c.school_year AS school_year_label,
    c.course_code,
    c.course_name,
    c.cte_pathway,
    c.industry_sector,
    c.cte_course_level,
    c.course_enrollment,
    c.school_year || ' · ' || coalesce(c.cte_course_level, '') AS year_course_level
FROM cte_courses c
LEFT JOIN current_student cs
    ON cs.stu_annual_key = c.stu_annual_key
   AND cs.school_year = c.school_year
LEFT JOIN cc_cur cc
    ON cc.ssid = c.ssid
"""


def _program_sql() -> str:
    return f"""
WITH pathway_base AS MATERIALIZED (
{_pathway_sql().strip().rstrip(';')}
),
migrant AS MATERIALIZED (
    SELECT
        nullif(btrim(ssid), '') AS ssid,
        substring(school_year, 1, 4) AS year4,
        max(CASE WHEN upper(btrim(coalesce(migrant, ''))) = 'Y' THEN 1 ELSE 0 END) AS is_migrant
    FROM student_enroll
    WHERE ssid IS NOT NULL AND btrim(ssid) <> ''
      AND school_year IS NOT NULL AND btrim(school_year) <> ''
    GROUP BY 1, 2
)
SELECT d.*, v.program, d.school_year || ' · ' || v.program AS year_program
FROM (
    SELECT p.*, coalesce(m.is_migrant, 0) AS migrant
    FROM pathway_base p
    LEFT JOIN migrant m
        ON m.ssid = p.ssid
       AND m.year4 = substring(p.school_year, 1, 4)
) d
CROSS JOIN LATERAL (
    VALUES
        ('ELL', CASE WHEN upper(btrim(coalesce(d.ell, ''))) = 'EL' THEN 1 ELSE 0 END),
        ('GATE', CASE WHEN upper(btrim(coalesce(d.gate, ''))) = 'Y' THEN 1 ELSE 0 END),
        ('SED', CASE WHEN upper(btrim(coalesce(d.sed, ''))) = 'Y' THEN 1 ELSE 0 END),
        ('SPED', CASE WHEN upper(btrim(coalesce(d.sped, ''))) = 'Y' THEN 1 ELSE 0 END),
        ('Migrant', CASE WHEN d.migrant = 1 THEN 1 ELSE 0 END),
        ('Foster', CASE WHEN upper(btrim(coalesce(d.foster, ''))) = 'Y' THEN 1 ELSE 0 END)
) AS v(program, member)
WHERE v.member = 1
"""


_STUDENT_COLS = [
    'ssid', 'student_name', 'school', 'enrolled_school', 'grade', 'grade_label',
    'grade_display', 'level_band', 'gender', 'race', 'ell', 'sed', 'sped',
    'gate', 'foster', 'status', 'district_school',
]
_PATHWAY_COLUMNS = _STUDENT_COLS + [
    'school_year', 'school_year_label', 'cte_pathway', 'pathway_code',
    'pathway_code_name', 'industry_sector', 'pathway_status',
    'completion_bucket', 'is_completer', 'is_on_track', 'is_current_year',
    'year_scope', 'year_grade', 'year_race', 'year_completion', 'year_industry',
]
_PATHWAY_PROGRAM_COLUMNS = _PATHWAY_COLUMNS + ['migrant', 'program', 'year_program']
_COURSE_COLUMNS = _STUDENT_COLS + [
    'school_year', 'school_year_label', 'course_code', 'course_name',
    'cte_pathway', 'industry_sector', 'cte_course_level', 'course_enrollment',
    'year_course_level',
]

DATASET_SPECS = [
    {
        'name': f'{PREFIX} Student Pathways',
        'description': (
            'One row per CTE student/pathway/school year from coursecatalog_cte_details '
            'with pathway status, industry sector, school, grade, and demographics.'
        ),
        'columns': _PATHWAY_COLUMNS,
        'sql': _pathway_sql(),
        'base_table': 'coursecatalog_cte_details',
    },
    {
        'name': f'{PREFIX} Course Enrollment',
        'description': (
            'One row per CTE course enrollment (Active/Completed) with the course, '
            'pathway, industry sector, course level, school, grade, and demographics.'
        ),
        'columns': _COURSE_COLUMNS,
        'sql': _course_sql(),
        'base_table': 'schedule_list',
    },
    {
        'name': f'{PREFIX} Student Program Pathways',
        'description': (
            'One row per CTE student/pathway/program membership (ELL, GATE, SED, '
            'SPED, Migrant, Foster) for participation by student program.'
        ),
        'columns': _PATHWAY_PROGRAM_COLUMNS,
        'sql': _program_sql(),
        'base_table': 'coursecatalog_cte_details',
    },
]


def _year_alias(year: str) -> str:
    y = str(year or '').strip()
    return f'{y[2:4]}-{y[7:9]}' if len(y) >= 9 else y


def _detail_year_label(year: str) -> str:
    y = str(year or '').strip()
    if len(y) == 4 and y.isdigit():
        return f'{y}-{int(y) + 1}'
    return y


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
    cfg.update({
        'show_percent': True,
        'percent_decimals': 0,
        'y_axis_max': 100,
    })
    cfg.update(overrides)
    return cfg


def _multi_year(**overrides):
    base = {
        'skip_page_filters': ['school_year'],
        'one_row_per_ssid': False,
        'multi_year_trend': True,
    }
    base.update(overrides)
    return base


def _summary_cfg(title, row_col, col_col, **overrides):
    cfg = {
        'chart_type': 'summary_table',
        'chart_title': title,
        'st_row_col': row_col,
        'st_col_col': col_col,
        'st_col_mode': 'values',
        'st_agg_func': 'count',
        'st_value_col': '',
    }
    cfg.update(overrides)
    return cfg


def _chart_specs() -> list[dict]:
    pathway_ds = f'{PREFIX} Student Pathways'
    course_ds = f'{PREFIX} Course Enrollment'
    program_ds = f'{PREFIX} Student Program Pathways'
    charts = [
        # ── Overview ────────────────────────────────────────────────────
        {
            'name': f'{PREFIX} Students Taking CTE Courses by Year',
            'chart_type': 'bar',
            'dataset': course_ds,
            'drill_columns': _COURSE_COLUMNS,
            'drill_through_filter_map': {'x_col': 'school_year', 'series_col': ''},
            'column_defs': {'school_year': 'School Year', 'ssid': 'Students'},
            'config': _base_cfg(
                'CTE: Students Taking CTE Courses by Year',
                x_col='school_year', y_col='ssid', agg='count_unique',
                sort_col='school_year', sort_dir='asc',
                **_multi_year(),
            ),
        },
        {
            'name': f'{PREFIX} Course Enrollments by Year',
            'chart_type': 'bar',
            'dataset': course_ds,
            'drill_columns': _COURSE_COLUMNS,
            'drill_through_filter_map': {'x_col': 'school_year', 'series_col': ''},
            'column_defs': {'school_year': 'School Year', 'course_enrollment': 'Course Enrollments'},
            'config': _base_cfg(
                'CTE: Course Enrollments by Year',
                x_col='school_year', y_col='course_enrollment', agg='sum',
                sort_col='school_year', sort_dir='asc',
                **_multi_year(),
            ),
        },
        {
            'name': f'{PREFIX} Students by School',
            'chart_type': 'bar',
            'dataset': course_ds,
            'drill_columns': _COURSE_COLUMNS,
            'drill_through_filter_map': {'x_col': 'school,school_year', 'series_col': ''},
            'column_defs': {'school': 'School', 'school_year': 'School Year', 'ssid': 'Students'},
            'config': _base_cfg(
                'CTE: Students Taking CTE Courses by School',
                x_col='school,school_year', y_col='ssid', agg='count_unique',
                sort_col='__manual__', category_order=[],
                group_gap=4, group_gap_size=1, bar_category_gap='4',
                center_group_labels=True, force_show_all_labels=True,
                show_axis_labels=False, show_axis_titles=False,
                x_label_rotation=0, x_label_font_size=14,
                **_multi_year(),
            ),
        },
        {
            'name': f'{PREFIX} Students by Grade and Year',
            'chart_type': 'stacked_bar',
            'dataset': course_ds,
            'drill_columns': _COURSE_COLUMNS,
            'drill_through_filter_map': {'x_col': 'school_year', 'series_col': 'grade_display'},
            'column_defs': {'school_year': 'School Year', 'grade_display': 'Grade', 'ssid': 'Students'},
            'config': _stacked_cfg(
                'CTE: Students by Grade and Year',
                x_col='school_year', y_col='ssid', agg='count_unique',
                stack_col='grade_display', series_col='grade_display',
                stack_order=list(GRADE_ORDER), color_map=GRADE_COLORS,
                sort_col='school_year', sort_dir='asc',
                show_percent=False, y_axis_max=None,
                **_multi_year(),
            ),
        },
        {
            'name': f'{PREFIX} Pathway Participation by Industry over the Years',
            'chart_type': 'stacked_bar',
            'dataset': pathway_ds,
            'drill_columns': _PATHWAY_COLUMNS,
            'drill_through_filter_map': {'x_col': 'school_year', 'series_col': 'industry_sector'},
            'column_defs': {'school_year': 'School Year', 'industry_sector': 'Industry Sector', 'ssid': 'Students'},
            'config': _stacked_cfg(
                'CTE: Pathway Participation by Industry over the Years',
                x_col='school_year', y_col='ssid', agg='count_unique',
                stack_col='industry_sector', series_col='industry_sector',
                sort_col='school_year', sort_dir='asc', legend_pos='right',
                show_percent=False, y_axis_max=None,
                **_multi_year(),
            ),
        },

        # ── Pathways & Industry ─────────────────────────────────────────
        {
            'name': f'{PREFIX} Students by Career Pathway',
            'chart_type': 'stacked_bar',
            'dataset': pathway_ds,
            'drill_columns': _PATHWAY_COLUMNS,
            'drill_through_filter_map': {'x_col': 'cte_pathway', 'series_col': 'school_year'},
            'column_defs': {'cte_pathway': 'Career Pathway', 'school_year': 'School Year', 'ssid': 'Students'},
            'config': _stacked_cfg(
                'CTE: Pathway Participation by Year',
                x_col='cte_pathway', y_col='ssid', agg='count_unique',
                stack_col='school_year', series_col='school_year',
                bar_orientation='horizontal', top_n=30,
                x_label_rotation=0, legend_pos='bottom',
                show_percent=False, y_axis_max=None,
                **_multi_year(),
            ),
        },
        {
            'name': f'{PREFIX} Pathway Participation by Industry, Year, and Grade',
            'chart_type': 'summary_table',
            'dataset': pathway_ds,
            'drill_columns': _PATHWAY_COLUMNS,
            'column_defs': {'industry_sector': 'Industry Sector', 'year_grade': 'Year · Grade'},
            'config': _summary_cfg(
                'CTE: Pathway Participation by Industry, Year, and Grade',
                'industry_sector', 'year_grade',
                st_col_group_sep=' · ', sort_col='__alpha__',
            ),
        },
        {
            'name': f'{PREFIX} Pathway Participation by Industry Sector',
            'chart_type': 'bar',
            'dataset': pathway_ds,
            'drill_columns': _PATHWAY_COLUMNS,
            'drill_through_filter_map': {'x_col': 'industry_sector', 'series_col': ''},
            'column_defs': {'industry_sector': 'Industry Sector', 'ssid': 'Students'},
            'config': _base_cfg(
                'CTE: Pathway Participation by Industry Sector',
                x_col='industry_sector', y_col='ssid', agg='count_unique',
                bar_orientation='horizontal', x_label_rotation=0,
            ),
        },
        {
            'name': f'{PREFIX} Course Level Progression by Year',
            'chart_type': 'stacked_bar',
            'dataset': course_ds,
            'drill_columns': _COURSE_COLUMNS,
            'drill_through_filter_map': {'x_col': 'school_year', 'series_col': 'cte_course_level'},
            'column_defs': {'school_year': 'School Year', 'cte_course_level': 'Course Level', 'ssid': 'Students'},
            'config': _stacked_cfg(
                'CTE: Course Level Progression by Year',
                x_col='school_year', y_col='ssid', agg='count_unique',
                stack_col='cte_course_level', series_col='cte_course_level',
                stack_order=list(COURSE_LEVEL_ORDER), color_map=COURSE_LEVEL_COLORS,
                sort_col='school_year', sort_dir='asc',
                show_percent=False, y_axis_max=None,
                **_multi_year(),
            ),
        },

        # ── Courses ─────────────────────────────────────────────────────
        {
            'name': f'{PREFIX} Enrollment by Course Level',
            'chart_type': 'bar',
            'dataset': course_ds,
            'drill_columns': _COURSE_COLUMNS,
            'drill_through_filter_map': {'x_col': 'cte_course_level', 'series_col': ''},
            'column_defs': {'cte_course_level': 'Course Level', 'course_enrollment': 'Course Enrollments'},
            'config': _base_cfg(
                'CTE: Course Enrollments by Course Level',
                x_col='cte_course_level', y_col='course_enrollment', agg='sum',
                sort_col='__manual__', category_order=list(COURSE_LEVEL_ORDER),
            ),
        },
        {
            'name': f'{PREFIX} Top CTE Courses by Enrollment',
            'chart_type': 'bar',
            'dataset': course_ds,
            'drill_columns': _COURSE_COLUMNS,
            'drill_through_filter_map': {'x_col': 'course_name', 'series_col': ''},
            'column_defs': {'course_name': 'CTE Course', 'course_enrollment': 'Course Enrollments'},
            'config': _base_cfg(
                'CTE: Top CTE Courses by Enrollment',
                x_col='course_name', y_col='course_enrollment', agg='sum',
                bar_orientation='horizontal', top_n=25,
            ),
        },
        {
            'name': f'{PREFIX} Course Enrollment by Course and Year',
            'chart_type': 'summary_table',
            'dataset': course_ds,
            'drill_columns': _COURSE_COLUMNS,
            'column_defs': {'course_name': 'CTE Course', 'school_year': 'School Year'},
            'config': _summary_cfg(
                'CTE: Course Enrollment by Course and Year',
                'course_name', 'school_year', sort_col='__alpha__',
            ),
        },
        {
            'name': f'{PREFIX} Course Enrollment by Pathway, Year, and Level',
            'chart_type': 'summary_table',
            'dataset': course_ds,
            'drill_columns': _COURSE_COLUMNS,
            'column_defs': {'cte_pathway': 'Career Pathway', 'year_course_level': 'Year · Course Level'},
            'config': _summary_cfg(
                'CTE: Course Enrollment by Pathway, Year, and Level',
                'cte_pathway', 'year_course_level',
                st_col_group_sep=' · ', sort_col='__alpha__',
            ),
        },

        # ── Completion ──────────────────────────────────────────────────
        {
            'name': f'{PREFIX} Pathway Completion Status by Year',
            'chart_type': 'stacked_bar',
            'dataset': pathway_ds,
            'drill_columns': _PATHWAY_COLUMNS,
            'drill_through_filter_map': {'x_col': 'school_year', 'series_col': 'completion_bucket'},
            'column_defs': {'school_year': 'School Year', 'completion_bucket': 'Status', 'ssid': 'Students'},
            'config': _stacked_cfg(
                'CTE: Pathway Completion Status by Year',
                x_col='school_year', y_col='ssid', agg='count_unique',
                stack_col='completion_bucket', series_col='completion_bucket',
                stack_order=list(COMPLETION_ORDER), color_map=COMPLETION_COLORS,
                sort_col='school_year', sort_dir='asc',
                show_percent=False, y_axis_max=None,
                **_multi_year(),
            ),
        },
        {
            'name': f'{PREFIX} Current On-Track Students by Pathway',
            'chart_type': 'bar',
            'dataset': pathway_ds,
            'source_filters': {'year_scope': ['Current'], 'is_on_track': [1]},
            'drill_columns': _PATHWAY_COLUMNS,
            'drill_through_filter_map': {'x_col': 'cte_pathway', 'series_col': ''},
            'column_defs': {'cte_pathway': 'Career Pathway', 'ssid': 'On-Track Students'},
            'config': _base_cfg(
                'CTE: Current On-Track Students by Pathway',
                x_col='cte_pathway', y_col='ssid', agg='count_unique',
                bar_orientation='horizontal', top_n=30,
            ),
        },
        {
            'name': f'{PREFIX} Current On-Track by Pathway and Industry',
            'chart_type': 'summary_table',
            'dataset': pathway_ds,
            'source_filters': {'year_scope': ['Current'], 'is_on_track': [1]},
            'drill_columns': _PATHWAY_COLUMNS,
            'column_defs': {'cte_pathway': 'Career Pathway', 'industry_sector': 'Industry Sector'},
            'config': _summary_cfg(
                'CTE: Current On-Track Students by Pathway and Industry',
                'cte_pathway', 'industry_sector', sort_col='__alpha__',
            ),
        },
        {
            'name': f'{PREFIX} Past CTE Completers by Pathway and Year',
            'chart_type': 'summary_table',
            'dataset': pathway_ds,
            'source_filters': {'is_completer': [1], 'year_scope': ['Prior']},
            'drill_columns': _PATHWAY_COLUMNS,
            'column_defs': {'cte_pathway': 'Career Pathway', 'school_year': 'School Year'},
            'config': _summary_cfg(
                'CTE: Past CTE Completers by Pathway and Year',
                'cte_pathway', 'school_year', sort_col='__alpha__',
            ),
        },
        {
            'name': f'{PREFIX} Past CTE Completers by Industry and Year',
            'chart_type': 'stacked_bar',
            'dataset': pathway_ds,
            'source_filters': {'is_completer': [1], 'year_scope': ['Prior']},
            'drill_columns': _PATHWAY_COLUMNS,
            'drill_through_filter_map': {'x_col': 'school_year', 'series_col': 'industry_sector'},
            'column_defs': {'school_year': 'School Year', 'industry_sector': 'Industry Sector', 'ssid': 'Completers'},
            'config': _stacked_cfg(
                'CTE: Past CTE Completers by Industry and Year',
                x_col='school_year', y_col='ssid', agg='count_unique',
                stack_col='industry_sector', series_col='industry_sector',
                sort_col='school_year', sort_dir='asc', legend_pos='right',
                show_percent=False, y_axis_max=None,
                **_multi_year(),
            ),
        },

        # ── Demographics: race ──────────────────────────────────────────
        {
            'name': f'{PREFIX} Students by Race',
            'chart_type': 'bar',
            'dataset': pathway_ds,
            'drill_columns': _PATHWAY_COLUMNS,
            'drill_through_filter_map': {'x_col': 'race', 'series_col': ''},
            'column_defs': {'race': 'Race', 'ssid': 'Students'},
            'config': _base_cfg(
                'CTE: Students by Race',
                x_col='race', y_col='ssid', agg='count_unique',
                bar_orientation='horizontal',
                sort_col='__manual__', category_order=list(RACE_ORDER),
                x_label_rotation=0,
            ),
        },
        {
            'name': f'{PREFIX} Students by Race and Year',
            'chart_type': 'stacked_bar',
            'dataset': pathway_ds,
            'drill_columns': _PATHWAY_COLUMNS,
            'drill_through_filter_map': {'x_col': 'school_year', 'series_col': 'race'},
            'column_defs': {'school_year': 'School Year', 'race': 'Race', 'ssid': 'Students'},
            'config': _stacked_cfg(
                'CTE: Students by Race and Year',
                x_col='school_year', y_col='ssid', agg='count_unique',
                stack_col='race', series_col='race',
                stack_order=list(RACE_ORDER), color_map=RACE_COLORS,
                sort_col='school_year', sort_dir='asc', legend_pos='right',
                show_percent=False, y_axis_max=None,
                **_multi_year(),
            ),
        },
        {
            'name': f'{PREFIX} Pathway Participation by Race',
            'chart_type': 'summary_table',
            'dataset': pathway_ds,
            'drill_columns': _PATHWAY_COLUMNS,
            'column_defs': {'cte_pathway': 'Career Pathway', 'year_race': 'Year · Race'},
            'config': _summary_cfg(
                'CTE: Pathway Participation by Race',
                'cte_pathway', 'year_race',
                st_col_group_sep=' · ', sort_col='__alpha__',
            ),
        },

        # ── Demographics: student programs ──────────────────────────────
        {
            'name': f'{PREFIX} Students by Student Program',
            'chart_type': 'bar',
            'dataset': program_ds,
            'drill_columns': _PATHWAY_PROGRAM_COLUMNS,
            'drill_through_filter_map': {'x_col': 'program', 'series_col': ''},
            'column_defs': {'program': 'Student Program', 'ssid': 'Students'},
            'config': _base_cfg(
                'CTE: Students by Student Program',
                x_col='program', y_col='ssid', agg='count_unique',
                bar_orientation='horizontal',
                sort_col='__manual__', category_order=list(PROGRAM_ORDER),
            ),
        },
        {
            'name': f'{PREFIX} Students by Student Program and Year',
            'chart_type': 'stacked_bar',
            'dataset': program_ds,
            'drill_columns': _PATHWAY_PROGRAM_COLUMNS,
            'drill_through_filter_map': {'x_col': 'school_year', 'series_col': 'program'},
            'column_defs': {'school_year': 'School Year', 'program': 'Student Program', 'ssid': 'Students'},
            'config': _stacked_cfg(
                'CTE: Students by Student Program and Year',
                x_col='school_year', y_col='ssid', agg='count_unique',
                stack_col='program', series_col='program',
                stack_order=list(PROGRAM_ORDER), color_map=PROGRAM_COLORS,
                sort_col='school_year', sort_dir='asc', legend_pos='right',
                show_percent=False, y_axis_max=None,
                **_multi_year(),
            ),
        },
        {
            'name': f'{PREFIX} Pathway Participation by Student Program',
            'chart_type': 'summary_table',
            'dataset': program_ds,
            'drill_columns': _PATHWAY_PROGRAM_COLUMNS,
            'column_defs': {'cte_pathway': 'Career Pathway', 'year_program': 'Year · Student Program'},
            'config': _summary_cfg(
                'CTE: Pathway Participation by Student Program',
                'cte_pathway', 'year_program',
                st_col_group_sep=' · ', sort_col='__alpha__',
            ),
        },
        {
            'name': f'{PREFIX} Industry Participation by Student Program',
            'chart_type': 'stacked_bar',
            'dataset': program_ds,
            'drill_columns': _PATHWAY_PROGRAM_COLUMNS,
            'drill_through_filter_map': {'x_col': 'industry_sector', 'series_col': 'program'},
            'column_defs': {'industry_sector': 'Industry Sector', 'program': 'Student Program', 'ssid': 'Students'},
            'config': _stacked_cfg(
                'CTE: Industry Participation by Student Program',
                x_col='industry_sector', y_col='ssid', agg='count_unique',
                stack_col='program', series_col='program',
                bar_orientation='horizontal',
                stack_order=list(PROGRAM_ORDER), color_map=PROGRAM_COLORS,
                sort_col='industry_sector', legend_pos='right',
                show_percent=False, y_axis_max=None,
            ),
        },

        # ── Detail tables ───────────────────────────────────────────────
        {
            'name': f'{PREFIX} Student Pathway Status by School',
            'chart_type': 'summary_table',
            'dataset': pathway_ds,
            'drill_columns': _PATHWAY_COLUMNS,
            'column_defs': {'school': 'School', 'year_completion': 'Year · Status'},
            'config': _summary_cfg(
                'CTE: Student Pathway Status by School',
                'school', 'year_completion',
                st_col_group_sep=' · ', sort_col='__alpha__',
            ),
        },
        {
            'name': f'{PREFIX} Course Enrollment by School',
            'chart_type': 'summary_table',
            'dataset': course_ds,
            'drill_columns': _COURSE_COLUMNS,
            'column_defs': {'school': 'School', 'year_course_level': 'Year · Course Level'},
            'config': _summary_cfg(
                'CTE: Course Enrollment by School',
                'school', 'year_course_level',
                st_col_group_sep=' · ', sort_col='__alpha__',
            ),
        },
    ]
    return charts


PAGE_SPEC = {
    'title': PAGE_TITLE,
    'slug': PAGE_SLUG,
    'folder': 'College & Career Readiness',
    'description': (
        'CTE participation and course enrollment by school, grade, pathway, industry '
        'sector, and course level. Charts and tables use the most recent three years; '
        'year-grouped tables show the year header with the measurement beneath it. '
        'Tracks pathway completion status over time, current on-track students, and '
        'past CTE completers. The Demographics tab compares participation by race and '
        'by student program membership (ELL, GATE, SED, SPED, Migrant, Foster). '
        'Click a chart or table cell to drill into CTE records.'
    ),
    'page_filters': [
        {
            'id': 'school', 'label': 'School (CTE year)', 'source_col': 'school',
            'source_table': 'coursecatalog_cte_details',
            'source_distinct_sql': """
SELECT DISTINCT coalesce(cs.school, d.school, 'Non-DS School') AS v
FROM coursecatalog_cte_details d
LEFT JOIN current_student cs
    ON cs.ssid = nullif(btrim(d.state_student_id), '')
   AND substring(cs.school_year, 1, 4) = d.school_year
WHERE d.school_year IS NOT NULL AND btrim(d.school_year) <> ''
  AND trim(coalesce(cs.school, d.school, '')) <> ''
ORDER BY v
""",
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'enrolled_school', 'label': 'Enrolled School (current)',
            'source_col': 'enrolled_school', 'source_table': 'coursecatalog_cte_details',
            'source_distinct_sql': f"""
SELECT DISTINCT coalesce(cc.school, 'Non-DS School') AS v
FROM coursecatalog_cte_details d
LEFT JOIN current_student cc
    ON cc.ssid = nullif(btrim(d.state_student_id), '')
   AND cc.school_year = '{THIS_YEAR}'
WHERE d.school_year IS NOT NULL AND btrim(d.school_year) <> ''
  AND trim(coalesce(cc.school, '')) <> ''
ORDER BY v
""",
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'grade', 'label': 'Grade (CTE year)', 'source_col': 'grade_label',
            'source_table': 'coursecatalog_cte_details',
            'source_distinct_sql': """
SELECT DISTINCT coalesce(cs.grade, d.grade, '') AS v
FROM coursecatalog_cte_details d
LEFT JOIN current_student cs
    ON cs.ssid = nullif(btrim(d.state_student_id), '')
   AND substring(cs.school_year, 1, 4) = d.school_year
WHERE d.school_year IS NOT NULL AND btrim(d.school_year) <> ''
  AND trim(coalesce(cs.grade, d.grade, '')) <> ''
ORDER BY v
""",
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        *[
            {
                'id': col, 'label': label, 'source_col': col,
                'source_table': 'coursecatalog_cte_details',
                'source_distinct_sql': f"""
SELECT DISTINCT coalesce(cs.{col}, '') AS v
FROM coursecatalog_cte_details d
LEFT JOIN current_student cs
    ON cs.ssid = nullif(btrim(d.state_student_id), '')
   AND substring(cs.school_year, 1, 4) = d.school_year
WHERE d.school_year IS NOT NULL AND btrim(d.school_year) <> ''
  AND trim(coalesce(cs.{col}, '')) <> ''
ORDER BY v
""",
                'widget': 'multi', 'default_val': '', 'hidden': False,
            }
            for col, label in (
                ('gender', 'Gender'),
                ('race', 'Race'),
                ('ell', 'ELL'),
                ('sed', 'SED'),
                ('sped', 'SPED'),
            )
        ],
    ],
    'tabs': [
        'Overview', 'Pathways & Industry', 'Courses', 'Completion',
        'Demographics', 'Detail Tables',
    ],
    'rows': [
        {'tab': 'Overview', 'label': 'Participation', 'sections': [
            {'chart': f'{PREFIX} Students Taking CTE Courses by Year', 'gs_x': 0, 'gs_w': 6, 'gs_h': 8},
            {'chart': f'{PREFIX} Course Enrollments by Year', 'gs_x': 6, 'gs_w': 6, 'gs_h': 8},
        ]},
        {'tab': 'Overview', 'label': 'Students by school', 'sections': [
            {'chart': f'{PREFIX} Students by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
        ]},
        {'tab': 'Overview', 'label': 'Students by grade and year', 'sections': [
            {'chart': f'{PREFIX} Students by Grade and Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Overview', 'label': 'Industry participation over the years', 'sections': [
            {'chart': f'{PREFIX} Pathway Participation by Industry over the Years', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
        ]},
        {'tab': 'Pathways & Industry', 'label': 'Career pathways by year', 'sections': [
            {'chart': f'{PREFIX} Students by Career Pathway', 'gs_x': 0, 'gs_w': 12, 'gs_h': 14},
        ]},
        {'tab': 'Pathways & Industry', 'label': 'Industry sector totals', 'sections': [
            {'chart': f'{PREFIX} Pathway Participation by Industry Sector', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'Pathways & Industry', 'label': 'Participation by industry, year, and grade', 'sections': [
            {'chart': f'{PREFIX} Pathway Participation by Industry, Year, and Grade', 'gs_x': 0, 'gs_w': 12, 'gs_h': 16},
        ]},
        {'tab': 'Pathways & Industry', 'label': 'Course level progression', 'sections': [
            {'chart': f'{PREFIX} Course Level Progression by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Courses', 'label': 'Course level', 'sections': [
            {'chart': f'{PREFIX} Enrollment by Course Level', 'gs_x': 0, 'gs_w': 12, 'gs_h': 7},
        ]},
        {'tab': 'Courses', 'label': 'Top courses', 'sections': [
            {'chart': f'{PREFIX} Top CTE Courses by Enrollment', 'gs_x': 0, 'gs_w': 12, 'gs_h': 14},
        ]},
        {'tab': 'Courses', 'label': 'Course enrollment by course and year', 'sections': [
            {'chart': f'{PREFIX} Course Enrollment by Course and Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 24},
        ]},
        {'tab': 'Courses', 'label': 'Course enrollment by pathway, year, and level', 'sections': [
            {'chart': f'{PREFIX} Course Enrollment by Pathway, Year, and Level', 'gs_x': 0, 'gs_w': 12, 'gs_h': 18},
        ]},
        {'tab': 'Completion', 'label': 'Pathway completion status', 'sections': [
            {'chart': f'{PREFIX} Pathway Completion Status by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Completion', 'label': 'Current on-track students by pathway', 'sections': [
            {'chart': f'{PREFIX} Current On-Track Students by Pathway', 'gs_x': 0, 'gs_w': 12, 'gs_h': 14},
        ]},
        {'tab': 'Completion', 'label': 'Current on-track by pathway and industry', 'sections': [
            {'chart': f'{PREFIX} Current On-Track by Pathway and Industry', 'gs_x': 0, 'gs_w': 12, 'gs_h': 14},
        ]},
        {'tab': 'Completion', 'label': 'Past completers by pathway and year', 'sections': [
            {'chart': f'{PREFIX} Past CTE Completers by Pathway and Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 16},
        ]},
        {'tab': 'Completion', 'label': 'Past completers by industry and year', 'sections': [
            {'chart': f'{PREFIX} Past CTE Completers by Industry and Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'Demographics', 'label': 'Students by race', 'sections': [
            {'chart': f'{PREFIX} Students by Race', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'Demographics', 'label': 'Students by race and year', 'sections': [
            {'chart': f'{PREFIX} Students by Race and Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'Demographics', 'label': 'Pathway participation by race', 'sections': [
            {'chart': f'{PREFIX} Pathway Participation by Race', 'gs_x': 0, 'gs_w': 12, 'gs_h': 18},
        ]},
        {'tab': 'Demographics', 'label': 'Students by student program', 'sections': [
            {'chart': f'{PREFIX} Students by Student Program', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Demographics', 'label': 'Students by student program and year', 'sections': [
            {'chart': f'{PREFIX} Students by Student Program and Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'Demographics', 'label': 'Pathway participation by student program', 'sections': [
            {'chart': f'{PREFIX} Pathway Participation by Student Program', 'gs_x': 0, 'gs_w': 12, 'gs_h': 18},
        ]},
        {'tab': 'Demographics', 'label': 'Industry participation by student program', 'sections': [
            {'chart': f'{PREFIX} Industry Participation by Student Program', 'gs_x': 0, 'gs_w': 12, 'gs_h': 14},
        ]},
        {'tab': 'Detail Tables', 'label': 'Pathway status by school', 'sections': [
            {'chart': f'{PREFIX} Student Pathway Status by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 14},
        ]},
        {'tab': 'Detail Tables', 'label': 'Course enrollment by school', 'sections': [
            {'chart': f'{PREFIX} Course Enrollment by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 14},
        ]},
    ],
}


# ── Django / data helpers ────────────────────────────────────────────────────

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
        raise SystemExit('No users exist to own the CTE page.')
    return user


def enable_dataset_tables():
    from landing.models import ProjectConfig

    cfg = ProjectConfig.get()
    enabled = list(cfg.enabled_dataset_tables or [])
    if not enabled:
        return
    added = []
    for table in (
        'coursecatalog_cte_details', 'coursecatalog_cte_pathways',
        'schedule_list', 'courses_catalog', 'current_student', 'student_enroll',
    ):
        if table not in enabled:
            enabled.append(table)
            added.append(table)
    if added:
        cfg.enabled_dataset_tables = enabled
        cfg.save()
        print(f'Enabled dataset tables: {", ".join(added)}')


def _course_years() -> list[str]:
    from dataviz.utils.data_loader import run_raw_sql

    res = run_raw_sql("""
        SELECT DISTINCT s.school_year AS y
        FROM schedule_list s
        JOIN coursecatalog_cte_pathways cte
            ON cte.course_id IS NOT NULL AND btrim(cte.course_id) <> ''
           AND cte.course_id = nullif(btrim(s.course_code), '')
        WHERE s.schedule_status IN ('Active', 'Completed')
          AND s.school_year IS NOT NULL AND btrim(s.school_year) <> ''
          AND nullif(btrim(s.course_code), '') IN (
              SELECT c2.course_id
              FROM coursecatalog_cte_pathways c2
              WHERE c2.course_id IS NOT NULL AND btrim(c2.course_id) <> ''
          )
        ORDER BY y
    """)
    if res.get('error'):
        print(f'Could not load course years: {res["error"]}')
        return []
    return [str(r['y']).strip() for r in res.get('rows') or [] if r.get('y')]


def _detail_years() -> list[str]:
    from dataviz.utils.data_loader import run_raw_sql

    res = run_raw_sql("""
        SELECT DISTINCT school_year AS y
        FROM coursecatalog_cte_details
        WHERE school_year IS NOT NULL AND btrim(school_year) <> ''
        ORDER BY y
    """)
    if res.get('error'):
        print(f'Could not load CTE detail years: {res["error"]}')
        return []
    return [
        _detail_year_label(r['y']) for r in res.get('rows') or [] if r.get('y')
    ]


def _course_schools() -> list[str]:
    from dataviz.utils.data_loader import run_raw_sql

    res = run_raw_sql("""
        SELECT DISTINCT coalesce(cs.school, s.school_name, 'Non-DS School') AS school
        FROM schedule_list s
        JOIN coursecatalog_cte_pathways cte
            ON cte.course_id IS NOT NULL AND btrim(cte.course_id) <> ''
           AND cte.course_id = nullif(btrim(s.course_code), '')
        LEFT JOIN current_student cs
            ON cs.stu_annual_key = s.stu_annual_key
           AND cs.school_year = s.school_year
        WHERE s.schedule_status IN ('Active', 'Completed')
          AND nullif(btrim(s.course_code), '') IN (
              SELECT c2.course_id
              FROM coursecatalog_cte_pathways c2
              WHERE c2.course_id IS NOT NULL AND btrim(c2.course_id) <> ''
          )
        ORDER BY school
    """)
    if res.get('error'):
        return []
    return [
        str(r.get('school')).strip() for r in res.get('rows') or []
        if str(r.get('school') or '').strip()
    ]


def _school_year_category_order(years: list[str]) -> list[str]:
    return [f'{school} · {year}' for school in _course_schools() for year in years]


def _prepare_charts(course_years: list[str], detail_years: list[str]) -> list[dict]:
    pathway_ds = f'{PREFIX} Student Pathways'
    program_ds = f'{PREFIX} Student Program Pathways'
    course_years = list(course_years)[-3:]
    detail_years = list(detail_years)[-3:]
    all_years = sorted({*course_years, *detail_years})
    alias_map = {y: _year_alias(y) for y in all_years}
    out = []
    for spec in _chart_specs():
        item = dict(spec)
        cfg = dict(item.get('config') or {})
        x_col = str(cfg.get('x_col') or '')
        st_row = str(cfg.get('st_row_col') or '')
        st_col = str(cfg.get('st_col_col') or '')
        dataset = item.get('dataset')
        chart_years = detail_years if dataset in (pathway_ds, program_ds) else course_years

        # Every chart/table uses only the most recent three years of its dataset.
        sf = dict(item.get('source_filters') or {})
        sf['school_year'] = list(chart_years)
        item['source_filters'] = sf

        if x_col == 'school_year':
            cfg['x_value_map'] = alias_map
            cfg['category_order'] = list(chart_years)
            cfg['sort_col'] = '__manual__'
        elif x_col == 'school,school_year':
            cfg['x_value_map'] = alias_map
            cfg['category_order'] = _school_year_category_order(chart_years)
            cfg['sort_col'] = '__manual__'
        elif x_col == 'grade_display':
            cfg['category_order'] = list(GRADE_ORDER)
            cfg['sort_col'] = '__manual__'

        if str(cfg.get('stack_col') or '') == 'school_year':
            cfg['stack_order'] = list(chart_years)

        if st_row == 'school_year':
            cfg['category_order'] = list(chart_years)
            cfg['sort_col'] = '__manual__'
        if st_col == 'school_year':
            cfg['st_col_order'] = list(chart_years)

        composite_orders = {
            'year_grade': GRADE_ORDER,
            'year_race': RACE_ORDER,
            'year_program': PROGRAM_ORDER,
            'year_completion': COMPLETION_ORDER,
            'year_course_level': COURSE_LEVEL_ORDER,
        }
        if st_col in composite_orders:
            cfg['st_col_order'] = [
                f'{year} · {value}'
                for year in chart_years
                for value in composite_orders[st_col]
            ]

        item['config'] = cfg
        out.append(item)
    return out


def upsert_dataset(spec, owner):
    from dataviz.models import Dataset, DataVizDatasetFolder

    mappings = {c: c for c in spec['columns']}
    dataset = Dataset.objects.filter(name=spec['name']).order_by('id').first()
    if dataset is None:
        dataset = Dataset(name=spec['name'], owner=owner)
    else:
        dataset.owner = owner
    dataset.source_type = Dataset.SOURCE_SQL
    dataset.source_sql = spec['sql']
    dataset.base_table = spec.get('base_table', 'coursecatalog_cte_details')
    dataset.distinct = False
    dataset.columns = spec['columns']
    dataset.column_mappings = mappings
    dataset.joins = []
    dataset.filters = []
    dataset.status_filter = []
    dataset.order_by = []
    dataset.description = spec['description']
    dataset.folder = DATASET_FOLDER
    dataset.save()
    parts = DATASET_FOLDER.split(' / ')
    for i in range(1, len(parts) + 1):
        DataVizDatasetFolder.objects.get_or_create(path=' / '.join(parts[:i]))
    print(f'  dataset ready: {dataset.name} (#{dataset.pk}) folder={dataset.folder}')
    return dataset


def upsert_chart(spec, datasets, owner):
    from dataviz.models import SavedChart

    raw_name = spec['name']
    folder_name = f'{CHART_ROOT_FOLDER} / {CHART_FOLDER} / {raw_name}'
    dataset = datasets[spec['dataset']]
    chart = SavedChart.objects.filter(name=folder_name).order_by('id').first()
    if chart is None:
        chart = SavedChart.objects.filter(name=raw_name).order_by('id').first()
    if chart is None:
        chart = SavedChart(name=folder_name)
    else:
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


def cleanup_stale_charts(keep_names):
    """Remove CTE charts that are no longer part of this page definition."""
    from dataviz.models import SavedChart, DataVizPageSection

    prefix = f'{CHART_ROOT_FOLDER} / {CHART_FOLDER} / {PREFIX}'
    stale = SavedChart.objects.filter(name__startswith=prefix).exclude(name__in=keep_names)
    attached = set(
        DataVizPageSection.objects.filter(chart__in=stale)
        .values_list('chart_id', flat=True)
    )
    stale = stale.exclude(pk__in=attached)
    count = stale.count()
    if count:
        stale.delete()
    print(f'  stale CTE charts removed: {count}')


def upsert_page(charts, owner):
    from django.db import transaction
    from dataviz.models import (
        DataVizPage, DataVizPageRow, DataVizPageSection, DataVizPageTab,
    )
    from dataviz.utils.page_publish import publish_page

    page, _ = DataVizPage.objects.update_or_create(
        slug=PAGE_SPEC['slug'],
        defaults={
            'title': PAGE_SPEC['title'],
            'description': PAGE_SPEC['description'],
            'folder': PAGE_SPEC['folder'],
            'container_width': DataVizPage.CONTAINER_WIDE,
            'show_header': True,
            'owner': owner,
            'page_filters': PAGE_SPEC['page_filters'],
            'status': DataVizPage.STATUS_ACTIVE,
        },
    )
    with transaction.atomic():
        page.rows.all().delete()
        page.tabs.all().delete()
        tab_map = {}
        for idx, name in enumerate(PAGE_SPEC['tabs']):
            tab_map[name] = DataVizPageTab.objects.create(page=page, name=name, position=idx)
        for r_idx, row_spec in enumerate(PAGE_SPEC['rows']):
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


def attach_to_menu(page):
    from users.models_pages import Page

    category, _ = Page.objects.update_or_create(
        slug=MENU_SLUG,
        defaults={
            'name': MENU_NAME,
            'app_reference': 'college-career-readiness',
            'description': 'College and career readiness dashboards',
            'url_path': '', 'status': 'active', 'is_menu_category': True,
            'show_in_menu': True, 'menu_order': MENU_ORDER,
            'required_roles': [], 'is_public': False,
        },
    )
    # Nest the existing CCGI category under College & Career Readiness.
    for child in Page.objects.filter(slug='ccgi'):
        child.parent_page = category
        child.menu_order = 1
        child.save(update_fields=['parent_page', 'menu_order', 'updated_at'])
    if page.linked_site_page_id:
        site = Page.objects.get(pk=page.linked_site_page_id)
        site.parent_page = category
        site.menu_order = 0
        site.show_in_menu = True
        site.save(update_fields=['parent_page', 'menu_order', 'show_in_menu', 'updated_at'])
    print('  menu: College & Career Readiness -> CTE Pathways, CCGI')


def qa_dataviz() -> int:
    from dataviz.utils.data_loader import run_raw_sql

    failures = []

    def fail(msg):
        failures.append(msg)
        print(f'  FAIL {msg}')

    print('=== QA: dataset counts ===')
    for spec in DATASET_SPECS:
        res = run_raw_sql(f"""
            SELECT school_year, count(*) AS n_rows, count(DISTINCT ssid) AS n_stu
            FROM ({spec['sql']}) q GROUP BY school_year ORDER BY school_year
        """)
        if res.get('error'):
            fail(f'{spec["name"]}: {res["error"]}')
        else:
            for r in res.get('rows') or []:
                print(f'  ok  {spec["name"]} {r["school_year"]}: {r["n_rows"]} rows / {r["n_stu"]} students')

    print('=== QA: chart columns and drill maps ===')
    columns = {s['name']: set(s['columns']) for s in DATASET_SPECS}
    course_years = _course_years()
    detail_years = _detail_years()
    for spec in _prepare_charts(course_years, detail_years):
        name = spec['name']
        cols = columns[spec['dataset']]
        refs = set(spec.get('drill_columns') or [])
        for key in ('x_col', 'y_col', 'stack_col', 'series_col', 'group_col',
                    'st_row_col', 'st_col_col', 'st_value_col'):
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

    print('=== QA: page filters ===')
    for f in PAGE_SPEC['page_filters']:
        res = run_raw_sql(f['source_distinct_sql'])
        if res.get('error'):
            fail(f'filter {f["id"]}: {res["error"]}')
        else:
            vals = [r.get('v') for r in res.get('rows') or []]
            print(f'  ok  filter {f["id"]}: {len(vals)} value(s) {vals[:6]}')

    print('=== QA: completion status ===')
    res = run_raw_sql(f"""
        SELECT school_year, completion_bucket, count(DISTINCT ssid) AS n_stu
        FROM ({_pathway_sql()}) q
        GROUP BY school_year, completion_bucket ORDER BY school_year, completion_bucket
    """)
    if res.get('error'):
        fail(f'completion: {res["error"]}')
    else:
        for r in res.get('rows') or []:
            print(f'  ok  {r["school_year"]} {r["completion_bucket"]}: {r["n_stu"]} students')

    print('=== QA: student programs ===')
    res = run_raw_sql(f"""
        SELECT program, count(DISTINCT ssid) AS n_stu
        FROM ({_program_sql()}) q GROUP BY program ORDER BY n_stu DESC
    """)
    if res.get('error'):
        fail(f'programs: {res["error"]}')
    else:
        for r in res.get('rows') or []:
            print(f'  ok  program {r["program"]}: {r["n_stu"]} students')

    if failures:
        print(f'\nQA FAILED ({len(failures)} issue(s)).')
        return 1
    print('\nQA PASSED.')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Create the CTE DataViz page.')
    parser.add_argument('--owner', default='')
    parser.add_argument('--qa', action='store_true')
    args = parser.parse_args()

    bootstrap_django()
    if args.qa:
        raise SystemExit(qa_dataviz())

    owner = resolve_owner(args.owner)
    print(f'Using owner: {owner}')

    enable_dataset_tables()
    course_years = _course_years()
    detail_years = _detail_years()
    print(f'CTE course years: {course_years}')
    print(f'CTE detail years: {detail_years}')

    datasets = {spec['name']: upsert_dataset(spec, owner) for spec in DATASET_SPECS}
    charts = {
        spec['name']: upsert_chart(spec, datasets, owner)
        for spec in _prepare_charts(course_years, detail_years)
    }
    page = upsert_page(charts, owner)
    cleanup_stale_charts(charts.keys())
    attach_to_menu(page)
    print('CTE Pathways page ready.')


if __name__ == '__main__':
    main()
