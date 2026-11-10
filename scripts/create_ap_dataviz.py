#!/usr/bin/env python
"""Create the AP Results DataViz datasets, charts, and sectioned page.

Read-only ClickHouse SQL over analytics_ap_results / analytics_ap_awards plus
current_student for school, grade, and demographics.

Run:
    docker compose exec django python /app/scripts/create_ap_dataviz.py
Optional:
    --owner you@example.com
    --qa
"""
import argparse
import os
import sys
from pathlib import Path

SOURCE_TABLE = 'analytics_ap_results'
AP_PREFIX = 'AP •'
CHART_FOLDER = 'ap'
DATASET_FOLDER = 'Assessments / AP Results'
DEFAULT_OWNER_NAME = ('Carlos', 'Silva')
DEFAULT_OWNER_EMAIL = 'carlos.silva@desertsands.us'
PERFORMANCE_COL_GROUP_SEP = ' · '

_STUDENT_SELECT = """
    r.ssid AS ssid,
    coalesce(cs.student_name, cc.student_name, '') AS student_name,
    coalesce(cs.school, cc.school, 'Non-DS School') AS school,
    coalesce(cc.school, 'Non-DS School') AS enrolled_school,
    coalesce(cs.grade, '') AS grade,
    multiIf(
        upper(trim(ifNull(cs.grade, ''))) IN ('TK'), 'TK',
        upper(trim(ifNull(cs.grade, ''))) IN ('KN', 'K', 'KG'), 'KN',
        match(trim(ifNull(cs.grade, '')), '^[0-9]+$'),
        lpad(toString(toInt32OrZero(trim(cs.grade))), 2, '0'),
        if(trim(ifNull(cs.grade, '')) = '', 'Unknown', trim(cs.grade))
    ) AS grade_label,
    transform(
        multiIf(
            upper(trim(ifNull(cs.grade, ''))) IN ('TK'), 'TK',
            upper(trim(ifNull(cs.grade, ''))) IN ('KN', 'K', 'KG'), 'KN',
            match(trim(ifNull(cs.grade, '')), '^[0-9]+$'),
            lpad(toString(toInt32OrZero(trim(cs.grade))), 2, '0'),
            if(trim(ifNull(cs.grade, '')) = '', 'Unknown', trim(cs.grade))
        ),
        ['KN','01','02','03','04','05','06','07','08','09','10','11','12'],
        ['Kindergarten','1st','2nd','3rd','4th','5th','6th','7th','8th','9th','10th','11th','12th'],
        if(trim(ifNull(cs.grade, '')) = '', 'Unknown', trim(cs.grade))
    ) AS grade_display,
    multiIf(
        upper(trim(ifNull(cs.grade, ''))) IN ('09','10','11','12'), 'High',
        upper(trim(ifNull(cs.grade, ''))) IN ('06','07','08'), 'Middle',
        'Other'
    ) AS level_band,
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

_AP_JOINS = """
FROM analytics_ap_results AS r
LEFT JOIN current_student AS cs
    ON cs.ssid = r.ssid AND cs.school_year = r.admin_year
LEFT JOIN current_student AS cc
    ON cc.ssid = r.ssid AND cc.school_year = '{{This School Year}}'
LEFT JOIN (
    SELECT state_id FROM school
    WHERE state_id IS NOT NULL AND trim(state_id) != ''
    GROUP BY state_id
) AS dsch ON dsch.state_id = coalesce(cs.school_state_id, cc.school_state_id)
"""

_AP_WHERE = """
r.admin_year IS NOT NULL AND trim(r.admin_year) != ''
AND r.exam_name IS NOT NULL AND trim(r.exam_name) != ''
"""

_EXAM_GROUP_SQL = """
multiIf(
    r.exam_name IN ('Seminar', 'Research'), 'AP Capstone Diploma Program',
    r.exam_name IN ('2-D Art and Design', '3-D Art and Design', 'Drawing', 'Music Theory',
                    'Music Aural Subscore', 'Music Non-Aural Subscore'), 'Arts',
    r.exam_name IN ('English Language and Composition', 'English Literature and Composition'), 'English',
    r.exam_name IN ('African American Studies', 'European History', 'Human Geography',
                    'Macroeconomics', 'Microeconomics', 'Psychology',
                    'United States Government and Politics', 'United States History',
                    'World History: Modern'), 'History and Social Sciences',
    r.exam_name IN ('Calculus AB', 'Calculus BC', 'Calculus BC: AB Subscore',
                    'Computer Science A', 'Computer Science Principles', 'Precalculus',
                    'Statistics'), 'Math & Computer Science',
    r.exam_name IN ('Biology', 'Chemistry', 'Environmental Science', 'Physics 1', 'Physics 2',
                    'Physics C: Mechanics', 'Physics C: Electricity and Magnetism'), 'Sciences',
    r.exam_name IN ('Chinese Language and Culture', 'French Language and Culture',
                    'Japanese Language and Culture', 'Spanish Language and Culture',
                    'Spanish Literature and Culture'), 'World Languages and Cultures',
    'Other'
)"""

_EXAM_SELECT = f"""
SELECT
    r.admin_year AS test_year,
    r.exam_name AS exam_name,
    ({_EXAM_GROUP_SQL}) AS exam_group,
    concat(({_EXAM_GROUP_SQL}), ' — ', r.exam_name) AS subject_exam,
    r.exam_grade AS exam_grade,
    if(trim(ifNull(r.exam_grade, '')) = '',
       '',
       concat(substring(r.admin_year, 3, 2), '-', substring(r.admin_year, 8, 2), ' · ', r.exam_grade)
    ) AS year_score,
    concat(
        coalesce(cs.school, cc.school, 'Non-DS School'),
        ' · ',
        substring(r.admin_year, 3, 2), '-', substring(r.admin_year, 8, 2)
    ) AS school_year_label,
    toInt32OrZero(r.exam_grade) AS score_num,
    if(trim(ifNull(r.exam_grade, '')) IN ('1','2','3','4','5'), 1, 0) AS score_valid,
    if(toInt32OrZero(r.exam_grade) >= 3, 1, 0) AS passed,
    if(toInt32OrZero(r.exam_grade) >= 3, 100, 0) AS pass_pct,
    r.exam_id AS exam_id,
    1 AS exam_count,
{_STUDENT_SELECT}
"""

_AWARD_SELECT = f"""
SELECT
    r.award_year AS test_year,
    r.award_description AS award_description,
{_STUDENT_SELECT}
FROM analytics_ap_awards AS r
LEFT JOIN current_student AS cs
    ON cs.ssid = r.ssid AND cs.school_year = r.award_year
LEFT JOIN current_student AS cc
    ON cc.ssid = r.ssid AND cc.school_year = '{{This School Year}}'
LEFT JOIN (
    SELECT state_id FROM school
    WHERE state_id IS NOT NULL AND trim(state_id) != ''
    GROUP BY state_id
) AS dsch ON dsch.state_id = coalesce(cs.school_state_id, cc.school_state_id)
WHERE r.award_year IS NOT NULL AND trim(r.award_year) != ''
  AND r.award_description IS NOT NULL AND trim(r.award_description) != ''
"""

_STUDENT_SELECT_COLS = [
    'ssid', 'student_name', 'school', 'enrolled_school', 'grade', 'grade_label',
    'grade_display', 'level_band', 'gender', 'race', 'ell', 'sed', 'sped',
    'gate', 'foster', 'status', 'district_school',
]

_EXAM_DRILL = _STUDENT_SELECT_COLS + ['test_year', 'exam_name', 'exam_group', 'subject_exam', 'exam_grade', 'year_score', 'school_year_label', 'score_num', 'score_valid', 'passed', 'pass_pct', 'exam_id', 'exam_count']
_EXAM_PROGRAM_DRILL = _EXAM_DRILL + ['migrant', 'program']
_STUDENT_DRILL = _STUDENT_SELECT_COLS + [
    'test_year', 'exam_count', 'best_score', 'scored_3_plus', 'scored_3_plus_pct',
]
_AWARD_DRILL = _STUDENT_SELECT_COLS + ['test_year', 'award_description']

PL_COLORS = {
    '1': '#e15759',
    '2': '#f28e2b',
    '3': '#edc948',
    '4': '#59a14f',
    '5': '#4e79a7',
}
AWARD_ORDER = [
    'AP Capstone Diploma',
    'AP Seminar and Research Certificate',
    'AP Scholar with Distinction',
    'AP Scholar with Honor',
    'AP Scholar',
]
AWARD_COLORS = {
    'AP Capstone Diploma': '#4e79a7',
    'AP Seminar and Research Certificate': '#76b7b2',
    'AP Scholar with Distinction': '#59a14f',
    'AP Scholar with Honor': '#edc948',
    'AP Scholar': '#f28e2b',
}
GRADE_ORDER = [
    '9th', '10th', '11th', '12th', 'Kindergarten', '1st', '2nd', '3rd',
    '4th', '5th', '6th', '7th', '8th',
]
PROGRAM_ORDER = ['ELL', 'GATE', 'SED', 'SPED', 'Migrant', 'Foster']
RACE_ORDER = [
    'Hispanic', 'White', 'Asian', 'Black or African American', 'Filipino',
    'Multiple', 'American Indian or Alaska Native',
    'Hawaiian/Pacific Islander', 'Missing',
]


def _year_alias(year: str) -> str:
    y = str(year or '').strip()
    return f'{y[2:4]}-{y[7:9]}' if len(y) >= 9 else y


def _exam_results_sql() -> str:
    return f"""{_EXAM_SELECT}{_AP_JOINS}WHERE {_AP_WHERE}"""


def _program_results_sql(base_sql: str) -> str:
    """Long-format program membership rows on top of AP exam results."""
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


def _student_results_sql() -> str:
    return f"""
SELECT
    ssid,
    test_year,
    any(student_name) AS student_name,
    any(school) AS school,
    any(enrolled_school) AS enrolled_school,
    any(grade) AS grade,
    any(grade_label) AS grade_label,
    any(grade_display) AS grade_display,
    any(level_band) AS level_band,
    any(gender) AS gender,
    any(race) AS race,
    any(ell) AS ell,
    any(sed) AS sed,
    any(sped) AS sped,
    any(gate) AS gate,
    any(foster) AS foster,
    any(status) AS status,
    any(district_school) AS district_school,
    count() AS exam_count,
    max(score_num) AS best_score,
    maxIf(1, score_num >= 3) AS scored_3_plus,
    if(maxIf(1, score_num >= 3) > 0, 100, 0) AS scored_3_plus_pct
FROM (
    {_exam_results_sql()}
)
GROUP BY ssid, test_year
"""


def _awards_sql() -> str:
    return _AWARD_SELECT


def _page_filter_test_year_sql() -> str:
    return f"""
SELECT DISTINCT r.admin_year AS v
{_AP_JOINS}
WHERE {_AP_WHERE}
ORDER BY v
"""


def _page_filter_school_sql(expr: str, district_only: bool = False) -> str:
    district_clause = "AND dsch.state_id != ''" if district_only else ''
    return f"""
SELECT DISTINCT ({expr}) AS v
{_AP_JOINS}
WHERE {_AP_WHERE}
  {district_clause}
HAVING trim(v) != ''
ORDER BY v
"""


def _page_filter_demographic_sql(col: str) -> str:
    return f"""
SELECT DISTINCT coalesce(cs.{col}, cc.{col}, '') AS v
{_AP_JOINS}
WHERE {_AP_WHERE}
  AND trim(coalesce(cs.{col}, cc.{col}, '')) != ''
ORDER BY v
"""


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
        'skip_page_filters': ['test_year', 'school_year'],
        'one_row_per_ssid': False,
        'multi_year_trend': True,
    }
    base.update(overrides)
    return base


DATASET_SPECS = [
    {
        'name': f'{AP_PREFIX} Exam Results',
        'description': 'One row per AP exam result with student, school, grade, exam, and score.',
        'columns': _EXAM_DRILL,
        'sql': _exam_results_sql(),
    },
    {
        'name': f'{AP_PREFIX} Student Program Results',
        'description': (
            'One row per AP exam per student program membership '
            '(ELL, GATE, SED, SPED, Migrant, Foster).'
        ),
        'columns': _EXAM_PROGRAM_DRILL,
        'sql': _program_results_sql(_exam_results_sql()),
    },
    {
        'name': f'{AP_PREFIX} Student Results',
        'description': 'One row per AP student/year with exam count and 3+ performance flags.',
        'columns': _STUDENT_DRILL,
        'sql': _student_results_sql(),
    },
    {
        'name': f'{AP_PREFIX} Scholar Awards',
        'description': 'One row per AP Scholar award by student and award year.',
        'columns': _AWARD_DRILL,
        'sql': _awards_sql(),
    },
]


def _chart_specs() -> list[dict]:
    exam_ds = f'{AP_PREFIX} Exam Results'
    student_ds = f'{AP_PREFIX} Student Results'
    award_ds = f'{AP_PREFIX} Scholar Awards'
    program_ds = f'{AP_PREFIX} Student Program Results'
    charts = [
        {
            'name': f'{AP_PREFIX} Students Who Took AP by Year',
            'chart_type': 'bar',
            'dataset': student_ds,
            'drill_columns': _STUDENT_DRILL,
            'drill_through_filter_map': {'x_col': 'test_year', 'series_col': ''},
            'column_defs': {'test_year': 'Test Year', 'ssid': 'Students'},
            'config': _base_cfg(
                'AP: Students Who Took an AP Exam',
                x_col='test_year', y_col='ssid',
                sort_col='test_year', sort_dir='asc',
                **_multi_year(),
            ),
        },
        {
            'name': f'{AP_PREFIX} Students by School',
            'chart_type': 'bar',
            'dataset': student_ds,
            'source_filters': {'district_school': [1]},
            'drill_columns': _STUDENT_DRILL,
            'drill_through_filter_map': {'x_col': 'school,test_year', 'series_col': ''},
            'column_defs': {'school': 'School', 'test_year': 'Test Year', 'ssid': 'Students'},
            'config': _base_cfg(
                'AP: Students Who Took an AP Exam by School',
                x_col='school,test_year', y_col='ssid',
                sort_col='__manual__', category_order=[],
                group_gap=4, group_gap_size=1, bar_category_gap='4',
                center_group_labels=True, force_show_all_labels=True,
                show_axis_labels=False, show_axis_titles=False,
                x_label_rotation=0, x_label_font_size=14,
                **_multi_year(),
            ),
        },
        {
            'name': f'{AP_PREFIX} Exams by School',
            'chart_type': 'bar',
            'dataset': exam_ds,
            'source_filters': {'district_school': [1]},
            'drill_columns': _EXAM_DRILL,
            'drill_through_filter_map': {'x_col': 'school,test_year', 'series_col': ''},
            'column_defs': {'school': 'School', 'test_year': 'Test Year', 'exam_count': 'AP Exams'},
            'config': _base_cfg(
                'AP: Number of AP Exams by School',
                x_col='school,test_year', y_col='exam_count', agg='sum',
                sort_col='__manual__', category_order=[],
                group_gap=4, group_gap_size=1, bar_category_gap='4',
                center_group_labels=True, force_show_all_labels=True,
                show_axis_labels=False, show_axis_titles=False,
                x_label_rotation=0, x_label_font_size=14,
                **_multi_year(),
            ),
        },
        {
            'name': f'{AP_PREFIX} Students Scoring 3+ by School',
            'chart_type': 'bar',
            'dataset': student_ds,
            'source_filters': {'district_school': [1], 'scored_3_plus': [1]},
            'drill_columns': _STUDENT_DRILL,
            'drill_through_filter_map': {'x_col': 'school,test_year', 'series_col': ''},
            'column_defs': {'school': 'School', 'test_year': 'Test Year', 'ssid': 'Students 3+'},
            'config': _base_cfg(
                'AP: Students Scoring 3+ by School',
                x_col='school,test_year', y_col='ssid', agg='count_unique',
                sort_col='__manual__', category_order=[],
                group_gap=4, group_gap_size=1, bar_category_gap='4',
                center_group_labels=True, force_show_all_labels=True,
                show_axis_labels=False, show_axis_titles=False,
                x_label_rotation=0, x_label_font_size=14,
                **_multi_year(),
            ),
        },
        {
            'name': f'{AP_PREFIX} Pass Rate by School',
            'chart_type': 'bar',
            'dataset': exam_ds,
            'source_filters': {'district_school': [1], 'score_valid': [1]},
            'drill_columns': _EXAM_DRILL,
            'drill_through_filter_map': {'x_col': 'school,test_year', 'series_col': ''},
            'column_defs': {'school': 'School', 'test_year': 'Test Year', 'pass_pct': 'Pass Rate %'},
            'config': _base_cfg(
                'AP: Pass Rate by School (assessments scoring 3+)',
                x_col='school,test_year', y_col='pass_pct', agg='avg',
                agg_decimals=1, y_axis_max=100, y_axis_min=0,
                sort_col='__manual__', category_order=[],
                group_gap=4, group_gap_size=1, bar_category_gap='4',
                center_group_labels=True, force_show_all_labels=True,
                show_axis_labels=False, show_axis_titles=False,
                x_label_rotation=0, x_label_font_size=14,
                **_multi_year(),
            ),
        },
        {
            'name': f'{AP_PREFIX} Pass Rate by Exam',
            'chart_type': 'bar',
            'dataset': exam_ds,
            'split_exams_by_count': True,
            'source_filters': {'district_school': [1], 'score_valid': [1]},
            'drill_columns': _EXAM_DRILL,
            'drill_through_filter_map': {'x_col': 'exam_name,test_year', 'series_col': ''},
            'column_defs': {'exam_name': 'AP Exam', 'test_year': 'Test Year', 'pass_pct': 'Pass Rate %'},
            'config': _base_cfg(
                'AP: Pass Rate by Exam and Year (assessments scoring 3+)',
                x_col='exam_name,test_year', y_col='pass_pct', agg='avg',
                agg_decimals=1, y_axis_max=100, y_axis_min=0,
                sort_col='__manual__', category_order=[],
                group_gap=4, group_gap_size=1, bar_category_gap='4',
                center_group_labels=True, force_show_all_labels=True,
                show_axis_labels=False, show_axis_titles=False,
                x_label_rotation=0, x_label_font_size=14,
                **_multi_year(),
            ),
        },
        {
            'name': f'{AP_PREFIX} Pass Rate by Race',
            'chart_type': 'bar',
            'dataset': exam_ds,
            'source_filters': {'score_valid': [1]},
            'drill_columns': _EXAM_DRILL,
            'drill_through_filter_map': {'x_col': 'race', 'series_col': ''},
            'column_defs': {'race': 'Race', 'pass_pct': 'Pass Rate %'},
            'config': _base_cfg(
                'AP: Pass Rate by Race (assessments scoring 3+)',
                x_col='race', y_col='pass_pct', agg='avg',
                agg_decimals=1, y_axis_max=100, y_axis_min=0,
                sort_col='__manual__', category_order=RACE_ORDER, x_label_rotation=25,
            ),
        },
        {
            'name': f'{AP_PREFIX} Pass Rate by Student Program',
            'chart_type': 'bar',
            'dataset': program_ds,
            'source_filters': {'score_valid': [1]},
            'drill_columns': _EXAM_PROGRAM_DRILL,
            'drill_through_filter_map': {'x_col': 'program', 'series_col': ''},
            'column_defs': {'program': 'Student Program', 'pass_pct': 'Pass Rate %'},
            'config': _base_cfg(
                'AP: Pass Rate by Student Program (assessments scoring 3+)',
                x_col='program', y_col='pass_pct', agg='avg',
                agg_decimals=1, y_axis_max=100, y_axis_min=0,
                sort_col='__manual__', category_order=PROGRAM_ORDER,
            ),
        },
        {
            'name': f'{AP_PREFIX} AP Scholar Trends',
            'chart_type': 'stacked_bar',
            'dataset': award_ds,
            'drill_columns': _AWARD_DRILL,
            'drill_through_filter_map': {'x_col': 'test_year', 'series_col': 'award_description'},
            'column_defs': {
                'test_year': 'Award Year', 'award_description': 'AP Scholar Award',
                'ssid': 'Students',
            },
            'config': _stacked_cfg(
                'AP Scholar Trends over the Years',
                x_col='test_year', y_col='ssid',
                stack_col='award_description', series_col='award_description',
                stack_order=list(AWARD_ORDER), color_map=AWARD_COLORS,
                sort_col='test_year', sort_dir='asc',
                show_percent=False, y_axis_max=None,
                **_multi_year(),
            ),
        },
        {
            'name': f'{AP_PREFIX} Performance by Subject',
            'chart_type': 'summary_table',
            'dataset': exam_ds,
            'drill_columns': _EXAM_DRILL,
            'column_defs': {'subject_exam': 'AP Subject / Exam', 'year_score': 'Year · Score', 'ssid': 'Students'},
            'config': {
                'chart_type': 'summary_table',
                'chart_title': 'AP Performance by Subject',
                'st_row_col': 'subject_exam', 'st_col_col': 'year_score',
                'st_col_mode': 'values', 'st_agg_func': 'count', 'st_value_col': '',
                # Two-level table header: school year on top, scores below.
                'st_col_group_sep': PERFORMANCE_COL_GROUP_SEP,
            },
        },
        {
            'name': f'{AP_PREFIX} Scores by School Table',
            'chart_type': 'summary_table',
            'dataset': exam_ds,
            'source_filters': {'district_school': [1]},
            'drill_columns': _EXAM_DRILL,
            'column_defs': {'school_year_label': 'School / Year', 'exam_grade': 'Score', 'ssid': 'Students'},
            'config': {
                'chart_type': 'summary_table',
                'chart_title': 'AP Scores by School and Year',
                'st_row_col': 'school_year_label', 'st_col_col': 'exam_grade',
                'st_col_mode': 'values', 'st_agg_func': 'count', 'st_value_col': '',
            },
        },
        {
            'name': f'{AP_PREFIX} Course & Exam History by School',
            'chart_type': 'summary_table',
            'dataset': exam_ds,
            'source_filters': {'district_school': [1]},
            'drill_columns': _EXAM_DRILL,
            'column_defs': {'school_year_label': 'School / Year', 'exam_name': 'AP Exam', 'ssid': 'Exams'},
            'config': {
                'chart_type': 'summary_table',
                'chart_title': 'AP Course and Exam History by School and Year',
                'st_row_col': 'school_year_label', 'st_col_col': 'exam_name',
                'st_col_mode': 'values', 'st_agg_func': 'count', 'st_value_col': '',
            },
        },
    ]
    return charts


PAGE_SPEC = {
    'title': 'AP Results',
    'slug': 'ap-results',
    'folder': CHART_FOLDER,
    'description': (
        'Advanced Placement participation, exam counts, 3+ performance, AP Scholar '
        'awards, assessment-based pass rates by school and exam, subject performance, and school-level exam history. All charts and tables use all available AP years; '
        'year is shown directly in the performance and detail tables. School is the enrollment school in the AP year. '
        'Enrolled School (current) filters by the current enrollment school. '
        'The Demographics tab compares pass rates by race and by student program '
        'membership (ELL, GATE, SED, SPED, Migrant, Foster). '
        'Click a chart or table cell to drill into AP exam records.'
    ),
    'page_filters': [
        {
            'id': 'school', 'label': 'School (AP year)', 'source_col': 'school',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_school_sql(
                "coalesce(cs.school, cc.school, 'Non-DS School')", district_only=True
            ),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'enrolled_school', 'label': 'Enrolled School (current)',
            'source_col': 'enrolled_school', 'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_school_sql(
                "coalesce(cc.school, 'Non-DS School')", district_only=True
            ),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'grade', 'label': 'Grade (AP year)', 'source_col': 'grade_label',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': f"""
SELECT DISTINCT cs.grade AS v
{_AP_JOINS}
WHERE {_AP_WHERE}
  AND trim(coalesce(cs.grade, '')) != ''
ORDER BY v
""",
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
    'tabs': ['Overview', 'School Participation', 'Performance', 'AP Scholar', 'Demographics', 'Detail Tables'],
    'rows': [
        {'tab': 'Overview', 'label': 'Participation', 'sections': [
            {'chart': f'{AP_PREFIX} Students Who Took AP by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 7},
        ]},
        {'tab': 'School Participation', 'label': 'Students and exams by school', 'sections': [
            {'chart': f'{AP_PREFIX} Students by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            {'chart': f'{AP_PREFIX} Exams by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'Performance', 'label': 'Students scoring 3+', 'sections': [
            {'chart': f'{AP_PREFIX} Students Scoring 3+ by School', 'gs_x': 0, 'gs_w': 6, 'gs_h': 9},
            {'chart': f'{AP_PREFIX} Pass Rate by School', 'gs_x': 6, 'gs_w': 6, 'gs_h': 9},
        ]},
        {'tab': 'Performance', 'label': 'Pass rate by exam — Part 1', 'sections': [
            {'chart': f'{AP_PREFIX} Pass Rate by Exam (Part 1)', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'Performance', 'label': 'Pass rate by exam — Part 2', 'sections': [
            {'chart': f'{AP_PREFIX} Pass Rate by Exam (Part 2)', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'Performance', 'label': 'AP performance by subject', 'sections': [
            {'chart': f'{AP_PREFIX} Performance by Subject', 'gs_x': 0, 'gs_w': 12, 'gs_h': 12},
        ]},
        {'tab': 'AP Scholar', 'label': 'AP Scholar awards', 'sections': [
            {'chart': f'{AP_PREFIX} AP Scholar Trends', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Demographics', 'label': 'Race and student programs', 'sections': [
            {'chart': f'{AP_PREFIX} Pass Rate by Race', 'gs_x': 0, 'gs_w': 6, 'gs_h': 8},
            {'chart': f'{AP_PREFIX} Pass Rate by Student Program', 'gs_x': 6, 'gs_w': 6, 'gs_h': 8},
        ]},
        {'tab': 'Detail Tables', 'label': 'AP scores by school', 'sections': [
            {'chart': f'{AP_PREFIX} Scores by School Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
        ]},
        {'tab': 'Detail Tables', 'label': 'Course and exam history', 'sections': [
            {'chart': f'{AP_PREFIX} Course & Exam History by School', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
        ]},
    ],
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


def _recent_years(limit: int = 5) -> list[str]:
    from dataviz.utils.data_loader import run_raw_sql

    res = run_raw_sql(f"""
        SELECT DISTINCT r.admin_year AS y
        {_AP_JOINS}
        WHERE {_AP_WHERE}
        ORDER BY y DESC LIMIT {int(limit)}
    """)
    years = [str(r['y']).strip() for r in res.get('rows') or [] if r.get('y')]
    return list(reversed(years))


def _school_year_category_order(dataset_name: str, years: list[str]) -> list[str]:
    from dataviz.utils.data_loader import run_raw_sql

    ds_sql = next(s['sql'] for s in DATASET_SPECS if s['name'] == dataset_name)
    years_sql = ', '.join("'" + str(y).replace("'", "''") + "'" for y in years)
    res = run_raw_sql(f"""
        SELECT DISTINCT school FROM ({ds_sql})
        WHERE test_year IN ({years_sql}) ORDER BY school
    """)
    if res.get('error'):
        return []
    schools = [
        str(r.get('school')).strip() for r in res.get('rows') or []
        if str(r.get('school') or '').strip()
    ]
    return [f'{school} · {year}' for school in schools for year in years]


def _exam_chunks(spec: dict, years: list[str]) -> list[dict]:
    """Split an exam chart into two equal alphabetical parts."""
    from dataviz.utils.data_loader import run_raw_sql

    ds_sql = next(s['sql'] for s in DATASET_SPECS if s['name'] == spec['dataset'])
    res = run_raw_sql(f"""
        SELECT DISTINCT exam_name FROM ({ds_sql})
        WHERE score_valid = 1 AND district_school = 1
        ORDER BY exam_name
    """)
    if res.get('error'):
        return [spec]
    exams = [
        str(r.get('exam_name')).strip() for r in res.get('rows') or []
        if str(r.get('exam_name') or '').strip()
    ]
    if not exams:
        return [spec]
    size = max(1, (len(exams) + 1) // 2)
    chunks = [exams[i:i + size] for i in range(0, len(exams), size)]
    out = []
    for idx, chunk in enumerate(chunks, start=1):
        item = dict(spec)
        item['name'] = f"{spec['name']} (Part {idx})"
        sf = dict(spec.get('source_filters') or {})
        sf['exam_name'] = chunk
        item['source_filters'] = sf
        item.pop('split_exams_by_count', None)
        out.append(item)
    return out


def _prepare_charts(years: list[str]) -> list[dict]:
    out = []
    alias_map = {y: _year_alias(y) for y in years}
    specs = []
    for spec in _chart_specs():
        if spec.get('split_exams_by_count'):
            specs.extend(_exam_chunks(spec, years))
        else:
            specs.append(spec)
    for spec in specs:
        item = dict(spec)
        cfg = dict(item.get('config') or {})
        chart_years = years[-3:] if 'Pass Rate by Exam' in item.get('name', '') else years
        cfg['x_value_map'] = {y: _year_alias(y) for y in chart_years}
        skip = set(cfg.get('skip_page_filters') or [])
        if 'test_year' in skip and chart_years:
            sf = dict(item.get('source_filters') or {})
            sf['test_year'] = chart_years
            item['source_filters'] = sf
        if str(cfg.get('x_col') or '').replace(' ', '') == 'school,test_year':
            cfg['category_order'] = _school_year_category_order(item['dataset'], chart_years)
            cfg['sort_col'] = '__manual__'
        elif str(cfg.get('x_col') or '').replace(' ', '') == 'exam_name,test_year':
            from dataviz.utils.data_loader import run_raw_sql

            ds_sql = next(s['sql'] for s in DATASET_SPECS if s['name'] == item['dataset'])
            sf_exams = (item.get('source_filters') or {}).get('exam_name')
            if sf_exams:
                exams = [str(x) for x in sf_exams]
            else:
                res = run_raw_sql(f"SELECT DISTINCT exam_name FROM ({ds_sql}) ORDER BY exam_name")
                exams = [
                    str(r.get('exam_name')).strip() for r in res.get('rows') or []
                    if str(r.get('exam_name') or '').strip()
                ]
            cfg['category_order'] = [f'{exam} · {year}' for exam in exams for year in chart_years]
            cfg['sort_col'] = '__manual__'
        item['config'] = cfg
        out.append(item)
    return out


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
        raise SystemExit('No users exist to own the AP page.')
    return user


def enable_dataset_tables():
    from landing.models import ProjectConfig

    cfg = ProjectConfig.get()
    enabled = list(cfg.enabled_dataset_tables or [])
    if not enabled:
        return
    added = []
    for table in (SOURCE_TABLE, 'current_student', 'school', 'analytics_ap_awards'):
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

    folder_name = f'{CHART_FOLDER} / {spec["name"]}'
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
        site.menu_order = 6
        site.show_in_menu = True
        site.save(update_fields=['parent_page', 'menu_order', 'show_in_menu'])
    print('  menu: Assessments -> AP Results (order 6)')


def qa_dataviz() -> int:
    from dataviz.utils.data_loader import run_raw_sql

    failures = []

    def fail(msg):
        failures.append(msg)
        print(f'  FAIL {msg}')

    print('=== QA: dataset counts ===')
    ds_map = {s['name']: s['sql'] for s in DATASET_SPECS}
    for name, sql in ds_map.items():
        res = run_raw_sql(f"""
            SELECT test_year, count() AS n_rows, uniqExact(ssid) AS n_stu
            FROM ({sql}) GROUP BY test_year ORDER BY test_year
        """)
        if res.get('error'):
            fail(f'{name}: {res["error"]}')
        else:
            for r in res.get('rows') or []:
                print(f'  ok  {name} {r["test_year"]}: {r["n_rows"]} rows / {r["n_stu"]} students')

    print('=== QA: chart references and drill maps ===')
    columns = {s['name']: set(s['columns']) for s in DATASET_SPECS}
    years = _recent_years()
    for spec in _prepare_charts(years):
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

    print("=== QA: AP Performance by Subject grouped header ===")
    perf = next(
        (s for s in _prepare_charts(years)
         if s['name'] == f'{AP_PREFIX} Performance by Subject'),
        None,
    )
    if not perf:
        fail('AP Performance by Subject chart missing')
    else:
        from dataviz.utils.chart_builder.build import build_option

        perf_sql = next(s['sql'] for s in DATASET_SPECS if s['name'] == perf['dataset'])
        perf_res = run_raw_sql(perf_sql)
        if perf_res.get('error'):
            fail(f'AP Performance by Subject data: {perf_res["error"]}')
        else:
            option = build_option(
                perf_res.get('rows') or [], perf.get('column_defs') or {},
                perf['config'],
            )
            groups = option.get('col_header_groups') or []
            if len(groups) < 2 or not option.get('col_header_raw'):
                fail('AP Performance by Subject lost its year/score grouped header')
            else:
                print(
                    f'  ok  grouped header: {len(groups)} year groups, '
                    f'{len(option.get("col_headers") or [])} score columns'
                )

    if failures:
        print(f'\nQA FAILED ({len(failures)} issue(s)).')
        return 1
    print('\nQA PASSED.')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Create the AP Results DataViz page.')
    parser.add_argument('--owner', default='')
    parser.add_argument('--qa', action='store_true')
    args = parser.parse_args()

    bootstrap_django()
    if args.qa:
        raise SystemExit(qa_dataviz())

    owner = resolve_owner(args.owner)
    if not owner:
        raise SystemExit('No users exist to own the AP page.')
    print(f'Using owner: {owner}')

    enable_dataset_tables()
    years = _recent_years()
    print(f'AP years: {years}')

    datasets = {spec['name']: upsert_dataset(spec, owner) for spec in DATASET_SPECS}
    charts = {spec['name']: upsert_chart(spec, datasets, owner) for spec in _prepare_charts(years)}
    page = upsert_page(charts, owner)
    attach_to_menu(page)
    print('AP Results page ready.')


if __name__ == '__main__':
    main()
