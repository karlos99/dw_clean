#!/usr/bin/env python
"""Create the Official Assessment Results DataViz page.

Read-only SQL over the single ``official_data_db`` table. The page brings the
official CAASPP, CAST, CAA, and ELPAC results into one sectioned dashboard with
multi-year trends. Charts are intentionally non-drillable because the official
table is already aggregate data (no student rows to drill into).

Run:
    docker compose exec django python /app/scripts/create_official_assessment_dataviz.py
Optional:
    --owner you@example.com
    --qa
"""
import argparse
import os
import sys
from pathlib import Path

SOURCE_TABLE = 'official_data_db'
DATASET_FOLDER = 'Assessments / Official Data'
CHART_FOLDER = 'official_assessment_results'
CHART_ROOT_FOLDER = 'Assessment_pages'
DEFAULT_OWNER_NAME = ('Carlos', 'Silva')
DEFAULT_OWNER_EMAIL = 'carlos.silva@desertsands.us'

ASSESSMENT_ORDER = [
    'Smarter Balanced', 'CAA', 'CAST', 'CAA Science',
    'ELPAC Summative', 'ELPAC Interim',
    'Alternate ELPAC Summative', 'Alternate ELPAC Interim',
]
GRADE_ORDER = [
    'Kindergarten', '1st', '2nd', '3rd', '4th', '5th', '6th', '7th',
    '8th', '9th', '10th', '11th', '12th', 'Other/Ungraded',
]
# Smarter Balanced / CAST grade-by-year charts (Table B); excludes All Grades (13).
CAASPP_GRADE_ORDER = ['3rd', '4th', '5th', '6th', '7th', '8th', '11th']
CAST_GRADE_ORDER = ['5th', '8th', '11th', '12th']
ELPAC_GRADE_ORDER = [
    'Kindergarten', '1st', '2nd', '3rd', '4th', '5th', '6th', '7th',
    '8th', '9th', '10th', '11th', '12th',
]
DISTRICT_TOTAL_GRADE = 'All Grades'
DISTRICT_TOTAL_SCHOOL = 'District Total'
YEAR_PL_SEP = ' · '
# Mutually exclusive row sets so district grade 13 is never stacked with grades 3–11.
ROW_DISTRICT_ALL_GRADES = 'district_all_grades'
ROW_DISTRICT_BY_GRADE = 'district_by_grade'
ROW_SCHOOL_ALL_GRADES = 'school_all_grades'
ROW_SCHOOL_BY_GRADE = 'school_by_grade'
GROUP_ORDER = [
    'All Students', 'English Learners',
    'Students with Disabilities', 'Socioeconomically Disadvantaged',
    'Foster Youth', 'Migrant Education', 'Homeless',
    'Hispanic or Latino', 'White', 'Asian', 'Black or African American',
    'Filipino', 'American Indian or Alaska Native',
    'Native Hawaiian or Pacific Islander', 'Two or More Races',
]
PROGRAM_GROUP_ORDER = [
    'English Learners',
    'Students with Disabilities',
    'Socioeconomically Disadvantaged',
    'Foster Youth',
    'Migrant Education',
    'Homeless',
]
RACE_GROUP_ORDER = [
    'Hispanic or Latino',
    'White',
    'Asian',
    'Black or African American',
    'Filipino',
    'American Indian or Alaska Native',
    'Native Hawaiian or Pacific Islander',
    'Two or More Races',
]
STATUS_ORDER = ['Met Standard', 'Not Met Standard']
STATUS_COLORS = {'Met Standard': '#59a14f', 'Not Met Standard': '#e15759'}
LEVEL_ORDER = ['Level 4', 'Level 3', 'Level 2', 'Level 1']
DOMAIN_LEVEL_ORDER = ['Level 3', 'Level 2', 'Level 1']
LEVEL_COLORS = {
    'Level 1': '#e15759',
    'Level 2': '#f28e2b',
    'Level 3': '#59a14f',
    'Level 4': '#4e79a7',
}

# CAASPP Table A reporting groups (https://caaspp-elpac.ets.org/caaspp/) used on the
# public site for district summaries. Legacy mislabeled IDs (e.g. 28≠Hispanic) are excluded.
_CAASPP_REPORT_GROUP_IDS = (
    '1', '001',
    '128',
    '31', '031',
    '160',
    '240',
    '52', '052',
    '28', '028',
    '75', '075',
    '76', '076',
    '74', '074',
    '77', '077',
    '78', '078',
    '79', '079',
    '80', '080',
    '144',
)
_RACE_GROUP_IDS = (
    '75', '075', '76', '076', '74', '074', '77', '077',
    '78', '078', '79', '079', '80', '080', '144',
)
_GROUP_LABELS = {
    '1': 'All Students', '001': 'All Students',
    '128': 'Students with Disabilities',
    '31': 'Socioeconomically Disadvantaged', '031': 'Socioeconomically Disadvantaged',
    '160': 'English Learners',
    '240': 'Foster Youth',
    '52': 'Homeless', '052': 'Homeless',
    '28': 'Migrant Education', '028': 'Migrant Education',
    '75': 'American Indian or Alaska Native', '075': 'American Indian or Alaska Native',
    '76': 'Asian', '076': 'Asian',
    '74': 'Black or African American', '074': 'Black or African American',
    '77': 'Filipino', '077': 'Filipino',
    '78': 'Hispanic or Latino', '078': 'Hispanic or Latino',
    '79': 'Native Hawaiian or Pacific Islander', '079': 'Native Hawaiian or Pacific Islander',
    '80': 'White', '080': 'White',
    '144': 'Two or More Races',
}
_GROUP_ID_SQL = ', '.join(f"'{g}'" for g in _CAASPP_REPORT_GROUP_IDS)
_RACE_GROUP_ID_SQL = ', '.join(f"'{g}'" for g in _RACE_GROUP_IDS)

# ``official_data_db.student_group_name`` (CDE / build lookup) → CAASPP Table A labels.
_CDE_GROUP_NAME_TO_CANONICAL: dict[str, str] = {
    'migrant education': 'Migrant Education',
    'socioeconomically disadvantaged': 'Socioeconomically Disadvantaged',
    'homeless': 'Homeless',
    'foster youth': 'Foster Youth',
    'english learner excluding rfep': 'English Learners',
    'reported disabilities': 'Students with Disabilities',
    'american indian or alaska native': 'American Indian or Alaska Native',
    'asian': 'Asian',
    'black or african american': 'Black or African American',
    'filipino': 'Filipino',
    'hispanic or latino': 'Hispanic or Latino',
    'native hawaiian or pacific islander': 'Native Hawaiian or Pacific Islander',
    'white': 'White',
    'two or more races': 'Two or More Races',
    'all students': 'All Students',
}
_CDE_GROUP_NAME_SQL = ', '.join(
    f"'{name.replace(chr(39), chr(39) * 2)}'" for name in _CDE_GROUP_NAME_TO_CANONICAL
)
_REPORT_GROUP_NAME_SQL = ', '.join(
    f"'{label.replace(chr(39), chr(39) * 2)}'"
    for label in dict.fromkeys(_GROUP_LABELS.values())
)
_RACE_GROUP_NAME_SQL = ', '.join(
    f"'{label.replace(chr(39), chr(39) * 2)}'" for label in RACE_GROUP_ORDER
)
_PROGRAM_GROUP_NAME_SQL = ', '.join(
    f"'{label.replace(chr(39), chr(39) * 2)}'" for label in PROGRAM_GROUP_ORDER
)


def _sql_case_trim_id(mapping: dict[str, str], *, default_expr: str) -> str:
    lines = ['CASE TRIM(student_group_id)']
    for gid, label in mapping.items():
        safe_label = label.replace("'", "''")
        lines.append(f"    WHEN '{gid}' THEN '{safe_label}'")
    lines.append(f'    ELSE {default_expr}')
    lines.append('END')
    return '\n'.join(lines)


def _sql_case_lower_name(mapping: dict[str, str], column: str = 'student_group_name') -> str:
    lines = [f'CASE LOWER(BTRIM({column}))']
    for raw_name, label in mapping.items():
        safe_label = label.replace("'", "''")
        lines.append(f"    WHEN '{raw_name.replace(chr(39), chr(39) * 2)}' THEN '{safe_label}'")
    lines.append("    ELSE ''")
    lines.append('END')
    return '\n'.join(lines)


GROUP_CASE = _sql_case_trim_id(_GROUP_LABELS, default_expr="'Group ' || TRIM(student_group_id)")
_GROUP_NAME_CANONICAL = _sql_case_lower_name(_CDE_GROUP_NAME_TO_CANONICAL)

# Prefer Table A ids, then CDE ``student_group_name``, then raw name (prior-year files).
STUDENT_GROUP_SQL = f"""
COALESCE(
    NULLIF(({GROUP_CASE}), 'Group ' || TRIM(student_group_id)),
    NULLIF(({_GROUP_NAME_CANONICAL}), ''),
    NULLIF(BTRIM(student_group_name), ''),
    'Group ' || TRIM(student_group_id)
)
"""

GROUP_KNOWN_CASE = f"""
CASE WHEN ({STUDENT_GROUP_SQL}) IN ({_REPORT_GROUP_NAME_SQL}) THEN 1 ELSE 0 END
"""

OFFICIAL_GROUP_ROW_PREDICATE = f"""
(
    TRIM(student_group_id) IN ({_GROUP_ID_SQL})
    OR LOWER(BTRIM(student_group_name)) IN ({_CDE_GROUP_NAME_SQL})
    OR BTRIM(student_group_name) IN ({_REPORT_GROUP_NAME_SQL})
)
"""

GROUP_TYPE_CASE = f"""
CASE
    WHEN ({STUDENT_GROUP_SQL}) = 'All Students' THEN 'All Students'
    WHEN ({STUDENT_GROUP_SQL}) IN ({_RACE_GROUP_NAME_SQL}) THEN 'Race/Ethnicity'
    WHEN ({STUDENT_GROUP_SQL}) IN ({_PROGRAM_GROUP_NAME_SQL}) THEN 'Program'
    ELSE 'Other'
END
"""


def _num(col: str) -> str:
    """Safely cast a text metric; official files use '*' for masked values."""
    return (
        f"CASE WHEN BTRIM(COALESCE({col}, '')) ~ '^[0-9]+(\\.[0-9]+)?$' "
        f"THEN BTRIM({col})::numeric ELSE 0 END"
    )


def _students_with_scores_sql(prefix: str = '') -> str:
    p = f'{prefix}.' if prefix else ''
    return f"""CASE WHEN {_num(f'{p}total_students_tested_with_scores')} > 0
     THEN {_num(f'{p}total_students_tested_with_scores')}
     ELSE {_num(f'{p}total_students_tested')}
END"""


STUDENTS_WITH_SCORES_SQL = _students_with_scores_sql()
RAW_STUDENTS_WITH_SCORES_SQL = _students_with_scores_sql('raw0')

REPORT_ROW_TYPE_SQL = """
CASE
    WHEN COALESCE(NULLIF(BTRIM(school_code), ''), '0000000') = '0000000'
         AND LTRIM(BTRIM(grade), '0') = '13' THEN 'district_all_grades'
    WHEN COALESCE(NULLIF(BTRIM(school_code), ''), '0000000') = '0000000'
         THEN 'district_by_grade'
    WHEN LTRIM(BTRIM(grade), '0') = '13' THEN 'school_all_grades'
    ELSE 'school_by_grade'
END
"""

ASSESSMENT_CASE = """
CASE data_source
    WHEN 'sb' THEN 'Smarter Balanced'
    WHEN 'caa' THEN 'CAA'
    WHEN 'cast' THEN 'CAST'
    WHEN 'caas' THEN 'CAA Science'
    WHEN 'sa' THEN 'ELPAC Summative'
    WHEN 'ia' THEN 'ELPAC Interim'
    WHEN 'altsa' THEN 'Alternate ELPAC Summative'
    WHEN 'altia' THEN 'Alternate ELPAC Interim'
    ELSE data_source
END
"""

SUBJECT_CASE = """
CASE
    WHEN data_source = 'sb' AND test_id = '1' THEN 'ELA'
    WHEN data_source = 'sb' AND test_id = '2' THEN 'Math'
    WHEN data_source = 'caa' AND test_id = '3' THEN 'ELA'
    WHEN data_source = 'caa' AND test_id = '4' THEN 'Math'
    WHEN data_source IN ('cast', 'caas') THEN 'Science'
    WHEN data_source IN ('sa', 'ia', 'altsa', 'altia') THEN 'ELPAC'
    ELSE 'Other'
END
"""

TEST_YEAR_LABEL = "COALESCE(NULLIF(BTRIM(test_year), ''), 'Unknown')"

GRADE_LABEL_CASE = """
CASE
    WHEN UPPER(BTRIM(grade)) = 'KN' THEN 'Kindergarten'
    WHEN BTRIM(grade) ~ '^[0-9]+$' THEN
        CASE LTRIM(BTRIM(grade), '0')
            WHEN '1' THEN '1st'
            WHEN '2' THEN '2nd'
            WHEN '3' THEN '3rd'
            WHEN '4' THEN '4th'
            WHEN '5' THEN '5th'
            WHEN '6' THEN '6th'
            WHEN '7' THEN '7th'
            WHEN '8' THEN '8th'
            WHEN '9' THEN '9th'
            WHEN '10' THEN '10th'
            WHEN '11' THEN '11th'
            WHEN '12' THEN '12th'
            WHEN '13' THEN 'All Grades'
            WHEN '14' THEN 'Other/Ungraded'
            WHEN '99' THEN 'Other/Ungraded'
            ELSE 'Other/Ungraded'
        END
    ELSE COALESCE(NULLIF(BTRIM(grade), ''), 'Unknown')
END
"""

MET_COUNT_SQL = f"""
CASE data_source
    WHEN 'sb' THEN {_num('count_proficient_and_above')}
    WHEN 'cast' THEN {_num('count_proficient_and_above')}
    WHEN 'caa' THEN {_num('count_level_3')}
    WHEN 'caas' THEN {_num('count_level_3')}
    WHEN 'sa' THEN {_num('overall_perf_lvl3_count')} + {_num('overall_perf_lvl4_count')}
    WHEN 'altsa' THEN {_num('overall_perf_lvl3_count')}
    WHEN 'ia' THEN {_num('intermediate_el_perf_lvl_count')} + {_num('ifep_perf_lvl_count')}
    WHEN 'altia' THEN {_num('intermediate_el_perf_lvl_count')} + {_num('ifep_perf_lvl_count')}
    ELSE 0
END
"""

SUMMARY_SQL = f"""
WITH raw AS (
    SELECT
        data_source,
        test_year,
        test_id,
        COALESCE(NULLIF(BTRIM(school_code), ''), '0000000') AS school_code,
        COALESCE(NULLIF(BTRIM(school_name), ''), 'District Total') AS school_name,
        grade,
        TRIM(student_group_id) AS student_group_id,
        BTRIM(student_group_name) AS student_group_name,
        {_num('total_students_enrolled')} AS enrolled_count,
        {_num('total_students_tested_with_scores')} AS tested_with_scores,
        {_num('total_students_tested')} AS tested_any,
        {_num('mean_scale_score')} AS mean_scale_score,
        {MET_COUNT_SQL} AS met_count
    FROM official_data_db
    WHERE data_source IN ('sb', 'caa', 'cast', 'caas', 'sa', 'ia', 'altsa', 'altia')
      AND {OFFICIAL_GROUP_ROW_PREDICATE}
),
base AS (
    SELECT
        raw.*,
        {ASSESSMENT_CASE} AS assessment,
        {SUBJECT_CASE} AS subject,
        {TEST_YEAR_LABEL} AS test_year_label,
        {GRADE_LABEL_CASE} AS grade_label,
        {STUDENT_GROUP_SQL} AS student_group,
        {GROUP_KNOWN_CASE} AS group_known,
        CASE WHEN tested_with_scores > 0 THEN tested_with_scores ELSE tested_any END AS tested_count
        -- tested_count = official "students with scores" (CAASPP ViewReportSB)
    FROM raw
)
SELECT
    assessment,
    subject,
    test_year,
    test_year_label,
    assessment || ' · ' || test_year_label AS assessment_year,
    CASE WHEN school_code = '0000000' THEN 'District' ELSE 'School' END AS school_scope,
    school_name,
    grade_label,
    student_group_id,
    student_group,
    group_known,
    CASE WHEN group_known = 1 AND student_group <> 'All Students' THEN 1 ELSE 0 END AS is_subgroup,
    tested_count,
    enrolled_count,
    met_count,
    GREATEST(tested_count - met_count, 0) AS not_met_count,
    mean_scale_score
FROM base
"""

STATUS_SQL = f"""
WITH summary AS (
{SUMMARY_SQL}
)
SELECT
    assessment, subject, test_year, test_year_label, assessment_year,
    school_scope, school_name, grade_label, student_group_id, student_group,
    group_known, is_subgroup,
    'Met Standard' AS status,
    met_count AS student_count,
    tested_count
FROM summary
UNION ALL
SELECT
    assessment, subject, test_year, test_year_label, assessment_year,
    school_scope, school_name, grade_label, student_group_id, student_group,
    group_known, is_subgroup,
    'Not Met Standard' AS status,
    not_met_count AS student_count,
    tested_count
FROM summary
"""

LEVELS_SQL = f"""
WITH raw0 AS (
    SELECT *
    FROM official_data_db
    WHERE data_source IN ('sb', 'caa', 'cast', 'caas', 'sa', 'ia', 'altsa', 'altia')
      AND {OFFICIAL_GROUP_ROW_PREDICATE}
),
raw AS (
    SELECT
        raw0.*,
        {ASSESSMENT_CASE} AS assessment,
        {SUBJECT_CASE} AS subject,
        {TEST_YEAR_LABEL} AS test_year_label,
        {GRADE_LABEL_CASE} AS grade_label,
        CASE WHEN COALESCE(NULLIF(BTRIM(raw0.school_code), ''), '0000000') = '0000000'
             THEN 'District' ELSE 'School' END AS school_scope,
        COALESCE(NULLIF(BTRIM(raw0.school_name), ''), 'District Total') AS school_label,
        {STUDENT_GROUP_SQL} AS student_group,
        {GROUP_KNOWN_CASE} AS group_known,
        {GROUP_TYPE_CASE} AS group_type,
        {REPORT_ROW_TYPE_SQL} AS report_row_type,
        {RAW_STUDENTS_WITH_SCORES_SQL} AS students_with_scores
    FROM raw0
),
long_levels AS (
    SELECT r.assessment, r.subject, r.test_year, r.test_year_label, r.grade_label,
           r.school_scope, r.school_label AS school_name, r.student_group,
           r.group_known, r.group_type, r.report_row_type, r.students_with_scores,
           CASE WHEN r.group_known = 1 AND r.student_group <> 'All Students'
                THEN 1 ELSE 0 END AS is_subgroup,
           lvl.performance_level, lvl.level_rank, lvl.student_count, lvl.percent
    FROM raw r
    CROSS JOIN LATERAL (VALUES
        ('Level 4', 4, {_num('r.count_advanced')}, {_num('r.percentage_advanced')}),
        ('Level 3', 3, {_num('r.count_proficient')}, {_num('r.percentage_proficient')}),
        ('Level 2', 2, {_num('r.count_developing')}, {_num('r.percentage_developing')}),
        ('Level 1', 1, {_num('r.count_minimal')}, {_num('r.percentage_minimal')})
    ) AS lvl(performance_level, level_rank, student_count, percent)
    WHERE r.data_source IN ('sb', 'cast')
    UNION ALL
    SELECT r.assessment, r.subject, r.test_year, r.test_year_label, r.grade_label,
           r.school_scope, r.school_label AS school_name, r.student_group,
           r.group_known, r.group_type, r.report_row_type, r.students_with_scores,
           CASE WHEN r.group_known = 1 AND r.student_group <> 'All Students'
                THEN 1 ELSE 0 END AS is_subgroup,
           lvl.performance_level, lvl.level_rank, lvl.student_count, lvl.percent
    FROM raw r
    CROSS JOIN LATERAL (VALUES
        ('Level 3', 3, {_num('r.count_level_3')}, {_num('r.percentage_level_3')}),
        ('Level 2', 2, {_num('r.count_level_2')}, {_num('r.percentage_level_2')}),
        ('Level 1', 1, {_num('r.count_level_1')}, {_num('r.percentage_level_1')})
    ) AS lvl(performance_level, level_rank, student_count, percent)
    WHERE r.data_source IN ('caa', 'caas')
    UNION ALL
    SELECT r.assessment, r.subject, r.test_year, r.test_year_label, r.grade_label,
           r.school_scope, r.school_label AS school_name, r.student_group,
           r.group_known, r.group_type, r.report_row_type, r.students_with_scores,
           CASE WHEN r.group_known = 1 AND r.student_group <> 'All Students'
                THEN 1 ELSE 0 END AS is_subgroup,
           lvl.performance_level, lvl.level_rank, lvl.student_count, lvl.percent
    FROM raw r
    CROSS JOIN LATERAL (VALUES
        ('Level 4', 4, {_num('r.overall_perf_lvl4_count')}, {_num('r.overall_perf_lvl4_pcnt')}),
        ('Level 3', 3, {_num('r.overall_perf_lvl3_count')}, {_num('r.overall_perf_lvl3_pcnt')}),
        ('Level 2', 2, {_num('r.overall_perf_lvl2_count')}, {_num('r.overall_perf_lvl2_pcnt')}),
        ('Level 1', 1, {_num('r.overall_perf_lvl1_count')}, {_num('r.overall_perf_lvl1_pcnt')})
    ) AS lvl(performance_level, level_rank, student_count, percent)
    WHERE r.data_source = 'sa'
    UNION ALL
    SELECT r.assessment, r.subject, r.test_year, r.test_year_label, r.grade_label,
           r.school_scope, r.school_label AS school_name, r.student_group,
           r.group_known, r.group_type, r.report_row_type, r.students_with_scores,
           CASE WHEN r.group_known = 1 AND r.student_group <> 'All Students'
                THEN 1 ELSE 0 END AS is_subgroup,
           lvl.performance_level, lvl.level_rank, lvl.student_count, lvl.percent
    FROM raw r
    CROSS JOIN LATERAL (VALUES
        ('Level 3', 3, {_num('r.overall_perf_lvl3_count')}, {_num('r.overall_perf_lvl3_pcnt')}),
        ('Level 2', 2, {_num('r.overall_perf_lvl2_count')}, {_num('r.overall_perf_lvl2_pcnt')}),
        ('Level 1', 1, {_num('r.overall_perf_lvl1_count')}, {_num('r.overall_perf_lvl1_pcnt')})
    ) AS lvl(performance_level, level_rank, student_count, percent)
    WHERE r.data_source = 'altsa'
    UNION ALL
    SELECT r.assessment, r.subject, r.test_year, r.test_year_label, r.grade_label,
           r.school_scope, r.school_label AS school_name, r.student_group,
           r.group_known, r.group_type, r.report_row_type, r.students_with_scores,
           CASE WHEN r.group_known = 1 AND r.student_group <> 'All Students'
                THEN 1 ELSE 0 END AS is_subgroup,
           lvl.performance_level, lvl.level_rank, lvl.student_count, lvl.percent
    FROM raw r
    CROSS JOIN LATERAL (VALUES
        ('Level 3', 3, {_num('r.ifep_perf_lvl_count')}, {_num('r.ifep_perf_lvl_pcnt')}),
        ('Level 2', 2, {_num('r.intermediate_el_perf_lvl_count')}, {_num('r.intermediate_el_perf_lvl_pcnt')}),
        ('Level 1', 1, {_num('r.novice_el_perf_lvl_count')}, {_num('r.novice_el_perf_lvl_pcnt')})
    ) AS lvl(performance_level, level_rank, student_count, percent)
    WHERE r.data_source IN ('ia', 'altia')
)
SELECT
    assessment, subject, test_year, test_year_label,
    assessment || ' · ' || test_year_label AS assessment_year,
    school_scope, school_name,
    school_name || ' · ' || test_year_label AS school_year,
    subject || ' · ' || school_name AS school_subject,
    subject || ' · ' || school_name || ' · ' || test_year_label AS school_subject_year,
    test_year_label || '{YEAR_PL_SEP}' || performance_level AS year_pl,
    grade_label, student_group, group_known, group_type,
    report_row_type, students_with_scores,
    is_subgroup, performance_level, level_rank, student_count, percent
FROM long_levels
"""

DOMAINS_SQL = f"""
WITH raw AS (
    SELECT
        *,
        {ASSESSMENT_CASE} AS assessment,
        {SUBJECT_CASE} AS subject,
        {TEST_YEAR_LABEL} AS test_year_label,
        {GRADE_LABEL_CASE} AS grade_label,
        CASE WHEN COALESCE(NULLIF(BTRIM(school_code), ''), '0000000') = '0000000'
             THEN 'District' ELSE 'School' END AS school_scope,
        COALESCE(NULLIF(BTRIM(school_name), ''), '{DISTRICT_TOTAL_SCHOOL}') AS school_label
    FROM official_data_db
    WHERE data_source IN ('cast', 'sa', 'ia')
      AND TRIM(student_group_id) IN ('1', '001')
      AND LTRIM(BTRIM(grade), '0') = '13'
),
domains AS (
    SELECT r.assessment, r.subject, r.test_year, r.test_year_label, r.grade_label,
           r.school_scope, r.school_label AS school_name,
           'Science' AS domain_group, d.domain, d.performance_level,
           d.level_rank, d.student_count, d.percent
    FROM raw r
    CROSS JOIN LATERAL (VALUES
        ('Life Sciences', 'Level 3', 3, {_num('r.life_sciences_domain_count_above_proficient')}, {_num('r.life_sciences_domain_percent_above_proficient')}),
        ('Life Sciences', 'Level 2', 2, {_num('r.life_sciences_domain_count_at_or_near_proficient')}, {_num('r.life_sciences_domain_percent_at_or_near_proficient')}),
        ('Life Sciences', 'Level 1', 1, {_num('r.life_sciences_domain_count_below_proficient')}, {_num('r.life_sciences_domain_percent_below_proficient')}),
        ('Physical Sciences', 'Level 3', 3, {_num('r.physical_sciences_domain_count_above_proficient')}, {_num('r.physical_sciences_domain_percent_above_proficient')}),
        ('Physical Sciences', 'Level 2', 2, {_num('r.physical_sciences_domain_count_at_or_near_proficient')}, {_num('r.physical_sciences_domain_percent_at_or_near_proficient')}),
        ('Physical Sciences', 'Level 1', 1, {_num('r.physical_sciences_domain_count_below_proficient')}, {_num('r.physical_sciences_domain_percent_below_proficient')}),
        ('Earth & Space Sciences', 'Level 3', 3, {_num('r.earth_and_space_sciences_domain_count_above_proficient')}, {_num('r.earth_and_space_sciences_domain_percent_above_proficient')}),
        ('Earth & Space Sciences', 'Level 2', 2, {_num('r.earth_and_space_sciences_domain_count_at_or_near_proficient')}, {_num('r.earth_and_space_sciences_domain_percent_at_or_near_proficient')}),
        ('Earth & Space Sciences', 'Level 1', 1, {_num('r.earth_and_space_sciences_domain_count_below_proficient')}, {_num('r.earth_and_space_sciences_domain_percent_below_proficient')})
    ) AS d(domain, performance_level, level_rank, student_count, percent)
    WHERE r.data_source = 'cast'
    UNION ALL
    SELECT r.assessment, r.subject, r.test_year, r.test_year_label, r.grade_label,
           r.school_scope, r.school_label AS school_name,
           'ELPAC', d.domain, d.performance_level, d.level_rank, d.student_count, d.percent
    FROM raw r
    CROSS JOIN LATERAL (VALUES
        ('Listening', 'Level 3', 3, {_num('r.listening_domain_developed_count')}, {_num('r.listening_domain_developed_pcnt')}),
        ('Listening', 'Level 2', 2, {_num('r.listening_domain_moderate_count')}, {_num('r.listening_domain_moderate_pcnt')}),
        ('Listening', 'Level 1', 1, {_num('r.listening_domain_begin_count')}, {_num('r.listening_domain_begin_pcnt')}),
        ('Speaking', 'Level 3', 3, {_num('r.speaking_domain_developed_count')}, {_num('r.speaking_domain_developed_pcnt')}),
        ('Speaking', 'Level 2', 2, {_num('r.speaking_domain_moderate_count')}, {_num('r.speaking_domain_moderate_pcnt')}),
        ('Speaking', 'Level 1', 1, {_num('r.speaking_domain_begin_count')}, {_num('r.speaking_domain_begin_pcnt')}),
        ('Reading', 'Level 3', 3, {_num('r.reading_domain_developed_count')}, {_num('r.reading_domain_developed_pcnt')}),
        ('Reading', 'Level 2', 2, {_num('r.reading_domain_moderate_count')}, {_num('r.reading_domain_moderate_pcnt')}),
        ('Reading', 'Level 1', 1, {_num('r.reading_domain_begin_count')}, {_num('r.reading_domain_begin_pcnt')}),
        ('Writing', 'Level 3', 3, {_num('r.writing_domain_developed_count')}, {_num('r.writing_domain_developed_pcnt')}),
        ('Writing', 'Level 2', 2, {_num('r.writing_domain_moderate_count')}, {_num('r.writing_domain_moderate_pcnt')}),
        ('Writing', 'Level 1', 1, {_num('r.writing_domain_begin_count')}, {_num('r.writing_domain_begin_pcnt')})
    ) AS d(domain, performance_level, level_rank, student_count, percent)
    WHERE r.data_source = 'sa'
    UNION ALL
    SELECT r.assessment, r.subject, r.test_year, r.test_year_label, r.grade_label,
           r.school_scope, r.school_label AS school_name,
           'ELPAC', d.domain, d.performance_level, d.level_rank, d.student_count, d.percent
    FROM raw r
    CROSS JOIN LATERAL (VALUES
        ('Oral Language', 'Level 3', 3, {_num('r.oral_lang_well_developed_perf_lvl_count')}, {_num('r.oral_lang_well_developed_perf_lvl_pcnt')}),
        ('Oral Language', 'Level 2', 2, {_num('r.oral_lang_moderately_developed_perf_lvl_count')}, {_num('r.oral_lang_moderately_developed_perf_lvl_pcnt')}),
        ('Oral Language', 'Level 1', 1, {_num('r.oral_lang_minimally_developed_perf_lvl_count')}, {_num('r.oral_lang_minimally_developed_perf_lvl_pcnt')}),
        ('Written Language', 'Level 3', 3, {_num('r.writ_lang_well_developed_perf_lvl_count')}, {_num('r.writ_lang_well_developed_perf_lvl_pcnt')}),
        ('Written Language', 'Level 2', 2, {_num('r.writ_lang_moderately_developed_perf_lvl_count')}, {_num('r.writ_lang_moderately_developed_perf_lvl_pcnt')}),
        ('Written Language', 'Level 1', 1, {_num('r.writ_lang_minimally_developed_perf_lvl_count')}, {_num('r.writ_lang_minimally_developed_perf_lvl_pcnt')})
    ) AS d(domain, performance_level, level_rank, student_count, percent)
    WHERE r.data_source = 'ia'
)
SELECT * FROM domains
"""

SUMMARY_COLS = [
    'assessment', 'subject', 'test_year', 'test_year_label', 'assessment_year',
    'school_scope', 'school_name', 'grade_label', 'student_group_id',
    'student_group', 'group_known', 'is_subgroup', 'tested_count',
    'enrolled_count', 'met_count', 'not_met_count', 'mean_scale_score',
]
STATUS_COLS = [
    'assessment', 'subject', 'test_year', 'test_year_label', 'assessment_year',
    'school_scope', 'school_name', 'grade_label', 'student_group_id',
    'student_group', 'group_known', 'is_subgroup', 'status', 'student_count',
    'tested_count',
]
LEVELS_COLS = [
    'assessment', 'subject', 'test_year', 'test_year_label', 'assessment_year',
    'school_scope', 'school_name', 'school_year', 'school_subject', 'school_subject_year',
    'year_pl',
    'grade_label',
    'student_group', 'group_known', 'group_type', 'report_row_type',
    'students_with_scores', 'is_subgroup',
    'performance_level', 'level_rank', 'student_count', 'percent',
]
DOMAINS_COLS = [
    'assessment', 'subject', 'test_year', 'test_year_label', 'grade_label',
    'school_scope', 'school_name',
    'domain_group', 'domain', 'performance_level', 'level_rank',
    'student_count', 'percent',
]

DATASET_SPECS = [
    {
        'name': 'Official • Performance Levels',
        'description': (
            'Official performance levels by assessment, subject, year, school, '
            'grade, and student group. Includes district, school, and standard '
            'subgroup rows for multi-year and subgroup analysis.'
        ),
        'columns': LEVELS_COLS,
        'sql': LEVELS_SQL,
    },
    {
        'name': 'Official • Domains',
        'description': (
            'Official domain performance levels for CAST science and ELPAC domains '
            '(district or school All Grades rollups).'
        ),
        'columns': DOMAINS_COLS,
        'sql': DOMAINS_SQL,
    },
]


def _base_cfg(title, **overrides):
    cfg = {
        'chart_type': 'bar',
        'chart_title': title,
        'agg': 'sum',
        'x_col': '',
        'y_col': 'student_count',
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
        'disable_drill': True,
    }
    cfg.update(overrides)
    return cfg


def _stacked_cfg(title, **overrides):
    cfg = _base_cfg(
        title,
        chart_type='stacked_bar',
        bar_layout='stacked',
        label_position='inside',
        show_percent=True,
        percent_decimals=0,
        y_axis_max=100,
    )
    cfg.update(overrides)
    return cfg


def _composite_year_category_order(outer_values: list[str], year_order: list[str]) -> list[str]:
    """Categories like chart #475: ``Grade · Year`` for a two-level x-axis."""
    return [f'{outer} · {year}' for outer in outer_values for year in year_order]


# Saved chart #475 layout: composite x-axis, centered outer labels, stacked % bars.
_MULTI_YEAR_COMPARE_STYLE = {
    'chart_type': 'bar',
    'bar_layout': 'stacked',
    'compare_gap': 30,
    'bar_category_gap': '20',
    'center_group_labels': True,
    'compare_cat_font_size': 12,
    'compare_cat_rotation': 0,
    'x_label_rotation': 0,
    'x_label_font_size': 11,
    'force_show_all_labels': False,
    'show_axis_labels': True,
    'show_axis_titles': True,
    'show_toolbox': True,
    'tooltip_show_count': True,
    'tooltip_show_pct': True,
    'group_col': '',
}


def _chart_specs(year_order):
    levels = 'Official • Performance Levels'
    domains = 'Official • Domains'
    filter_rollup_all_grades = {
        'report_row_type': [ROW_DISTRICT_ALL_GRADES, ROW_SCHOOL_ALL_GRADES],
    }
    filter_by_grade = {
        'report_row_type': [ROW_DISTRICT_BY_GRADE, ROW_SCHOOL_BY_GRADE],
    }
    filter_school_rollup = {'report_row_type': [ROW_SCHOOL_ALL_GRADES]}
    _MULTI_YEAR_SKIP = ['test_year', 'grade']

    def level_chart(name, title, x_col, source_filters, dataset=None, **cfg):
        stack_order = cfg.pop('stack_order', LEVEL_ORDER)
        saved_chart_type = cfg.pop('saved_chart_type', None)
        if saved_chart_type is None:
            saved_chart_type = cfg.get('chart_type', 'stacked_bar')
        x_label = cfg.pop('x_label', None)
        col_defs = {
            'test_year_label': 'Year',
            'student_group': 'Student Group',
            'performance_level': 'Performance Level',
            'student_count': 'Students',
            'students_with_scores': 'Students With Scores',
            'grade_label': 'Grade',
            'subject': 'Subject',
            'assessment': 'Assessment',
            'domain': 'Domain',
        }
        if x_label and ',' not in x_col:
            col_defs[x_col] = x_label
        return {
            'name': name,
            'chart_type': saved_chart_type,
            'dataset': dataset or levels,
            'source_filters': source_filters,
            'drill_columns': [],
            'drill_through_filter_map': {},
            'column_defs': col_defs,
            'config': _stacked_cfg(
                title,
                x_col=x_col, y_col='student_count',
                stack_col='performance_level', series_col='performance_level',
                stack_order=stack_order, color_map=LEVEL_COLORS, **cfg,
            ),
        }

    def _merge_filters(*parts):
        out = {}
        for part in parts:
            out.update(part)
        return out

    def year_chart(name, title, assessment, subject=None):
        filters = _merge_filters(
            filter_rollup_all_grades,
            {
                'assessment': [assessment],
                'student_group': ['All Students'],
            },
        )
        if subject:
            filters['subject'] = [subject]
        return level_chart(
            name, title, 'test_year_label', filters,
            x_label='Test Year',
            sort_col='test_year_label', sort_dir='asc',
            skip_page_filters=_MULTI_YEAR_SKIP,
            multi_year_trend=True,
        )

    def year_group_chart(name, title, assessment, group_field='subject', group_order=None):
        assessments = assessment if isinstance(assessment, list) else [assessment]
        filters = _merge_filters(
            filter_rollup_all_grades,
            {
                'assessment': assessments,
                'student_group': ['All Students'],
            },
        )
        if group_order:
            outer_order = list(dict.fromkeys(
                p.split(' · ', 1)[0] for p in group_order
            ))
        elif group_field == 'subject':
            outer_order = ['ELA', 'Math']
        elif group_field == 'assessment':
            outer_order = [
                'ELPAC Summative', 'ELPAC Interim',
                'Alternate ELPAC Summative', 'Alternate ELPAC Interim',
            ]
        category_order = group_order or _composite_year_category_order(outer_order, year_order)
        x_col = f'{group_field},test_year_label'
        chart = level_chart(
            name, title, x_col, filters,
            sort_col='__manual__',
            category_order=category_order,
            group_order=year_order,
            skip_page_filters=_MULTI_YEAR_SKIP,
            multi_year_trend=True,
            **_MULTI_YEAR_COMPARE_STYLE,
        )
        return chart

    def group_chart(name, title, assessment, group_type, subject=None):
        filters = _merge_filters(
            filter_rollup_all_grades,
            {
                'assessment': [assessment],
                'group_type': ['All Students', group_type],
            },
        )
        if subject:
            filters['subject'] = [subject]
        if group_type == 'Race/Ethnicity':
            outer_groups = ['All Students', *RACE_GROUP_ORDER]
        else:
            outer_groups = ['All Students', *PROGRAM_GROUP_ORDER]
        return level_chart(
            name, title, 'student_group,test_year_label', filters,
            sort_col='__manual__',
            category_order=_composite_year_category_order(outer_groups, year_order),
            group_order=year_order,
            skip_page_filters=_MULTI_YEAR_SKIP,
            multi_year_trend=True,
            **_MULTI_YEAR_COMPARE_STYLE,
        )

    def grade_year_chart(name, title, assessment, subject=None, grade_order=None):
        grade_order = grade_order or CAASPP_GRADE_ORDER
        filters = _merge_filters(
            filter_by_grade,
            {
                'assessment': [assessment],
                'student_group': ['All Students'],
                'grade_label': grade_order,
            },
        )
        if subject:
            filters['subject'] = [subject]
        return level_chart(
            name, title, 'grade_label,test_year_label', filters,
            sort_col='__manual__',
            category_order=_composite_year_category_order(grade_order, year_order),
            group_order=year_order,
            skip_page_filters=_MULTI_YEAR_SKIP,
            multi_year_trend=True,
            **_MULTI_YEAR_COMPARE_STYLE,
        )

    def domain_year_chart(name, title, assessment, domain_order):
        return level_chart(
            name, title, 'domain,test_year_label',
            _merge_filters(
                {'grade_label': [DISTRICT_TOTAL_GRADE]},
                {'assessment': [assessment]},
            ),
            dataset=domains,
            sort_col='__manual__',
            category_order=_composite_year_category_order(domain_order, year_order),
            group_order=year_order,
            stack_order=DOMAIN_LEVEL_ORDER,
            skip_page_filters=_MULTI_YEAR_SKIP,
            multi_year_trend=True,
            **_MULTI_YEAR_COMPARE_STYLE,
        )

    def school_table(name, title, assessment, row_col='school_name', subject=None):
        filters = _merge_filters(
            filter_school_rollup,
            {
                'assessment': [assessment],
                'school_scope': ['School'],
                'student_group': ['All Students'],
            },
        )
        if subject:
            filters['subject'] = [subject]
        row_label = 'Subject · School' if row_col == 'school_subject' else 'School'
        year_pl_order = [
            f'{yr}{YEAR_PL_SEP}{pl}'
            for yr in year_order
            for pl in LEVEL_ORDER
        ]
        return {
            'name': name,
            'chart_type': 'summary_table',
            'dataset': levels,
            'source_filters': filters,
            'drill_columns': [],
            'drill_through_filter_map': {},
            'column_defs': {
                row_col: row_label,
                'year_pl': 'Year / Performance Level',
                'performance_level': 'Performance Level',
                'test_year_label': 'Year',
                'student_count': 'Students',
                'school_name': 'School',
            },
            'config': {
                'chart_type': 'summary_table',
                'chart_title': title,
                'st_row_col': row_col,
                'st_col_col': 'year_pl',
                'st_col_group_sep': YEAR_PL_SEP,
                'st_col_mode': 'values',
                'st_agg_func': 'sum',
                'st_value_col': 'student_count',
                'st_col_order': year_pl_order,
                'sort_col': '__alpha__',
                'disable_drill': True,
                'multi_year_trend': True,
                'district_total_school': DISTRICT_TOTAL_SCHOOL,
                'ignore_school_filter_at_district': True,
                'skip_page_filters': ['test_year', 'grade'],
            },
        }

    charts = []
    subject_year_order = [f'{subj} · {yr}' for subj in ('ELA', 'Math') for yr in year_order]
    elpac_year_order = [
        f'{assess} · {yr}'
        for assess in ('ELPAC Summative', 'ELPAC Interim', 'Alternate ELPAC Summative', 'Alternate ELPAC Interim')
        for yr in year_order
    ]

    # ── Smarter Balanced ───────────────────────────────────────────────────
    charts.extend([
        year_group_chart(
            'Official • Smarter Balanced ELA and Math PL by Year',
            'Smarter Balanced: ELA & Math PL by Year',
            'Smarter Balanced', 'subject', subject_year_order,
        ),
        group_chart(
            'Official • Smarter Balanced ELA PL by Race',
            'Smarter Balanced ELA: PL by Race', 'Smarter Balanced', 'Race/Ethnicity', 'ELA',
        ),
        group_chart(
            'Official • Smarter Balanced ELA PL by Program',
            'Smarter Balanced ELA: PL by Program', 'Smarter Balanced', 'Program', 'ELA',
        ),
        group_chart(
            'Official • Smarter Balanced Math PL by Race',
            'Smarter Balanced Math: PL by Race', 'Smarter Balanced', 'Race/Ethnicity', 'Math',
        ),
        group_chart(
            'Official • Smarter Balanced Math PL by Program',
            'Smarter Balanced Math: PL by Program', 'Smarter Balanced', 'Program', 'Math',
        ),
        grade_year_chart(
            'Official • Smarter Balanced ELA PL by Grade and Year',
            'Smarter Balanced ELA: PL by Grade and Year', 'Smarter Balanced', 'ELA',
        ),
        grade_year_chart(
            'Official • Smarter Balanced Math PL by Grade and Year',
            'Smarter Balanced Math: PL by Grade and Year', 'Smarter Balanced', 'Math',
        ),
        school_table(
            'Official • Smarter Balanced School Results Table',
            'Smarter Balanced: ELA and Math Results by School',
            'Smarter Balanced',
            row_col='school_subject',
        ),
    ])

    # ── CAA ────────────────────────────────────────────────────────────────
    charts.extend([
        year_group_chart(
            'Official • CAA ELA and Math PL by Year',
            'CAA: ELA & Math PL by Year', 'CAA', 'subject', subject_year_order,
        ),
        group_chart('Official • CAA ELA PL by Race', 'CAA ELA: PL by Race', 'CAA', 'Race/Ethnicity', 'ELA'),
        group_chart('Official • CAA ELA PL by Program', 'CAA ELA: PL by Program', 'CAA', 'Program', 'ELA'),
        group_chart('Official • CAA Math PL by Race', 'CAA Math: PL by Race', 'CAA', 'Race/Ethnicity', 'Math'),
        group_chart('Official • CAA Math PL by Program', 'CAA Math: PL by Program', 'CAA', 'Program', 'Math'),
        grade_year_chart('Official • CAA ELA PL by Grade and Year', 'CAA ELA: PL by Grade and Year', 'CAA', 'ELA'),
        grade_year_chart('Official • CAA Math PL by Grade and Year', 'CAA Math: PL by Grade and Year', 'CAA', 'Math'),
        school_table(
            'Official • CAA School Results Table',
            'CAA: ELA and Math Results by School',
            'CAA',
            row_col='school_subject',
        ),
    ])

    # ── CAST ───────────────────────────────────────────────────────────────
    charts.extend([
        year_chart('Official • CAST PL by Year', 'CAST: PL by Year', 'CAST'),
        group_chart('Official • CAST PL by Race', 'CAST: PL by Race', 'CAST', 'Race/Ethnicity'),
        group_chart('Official • CAST PL by Program', 'CAST: PL by Program', 'CAST', 'Program'),
        grade_year_chart(
            'Official • CAST PL by Grade and Year', 'CAST: PL by Grade and Year',
            'CAST', grade_order=CAST_GRADE_ORDER,
        ),
        domain_year_chart(
            'Official • CAST Science Domain PL by Year',
            'CAST: Science Domain PL by Year', 'CAST',
            ['Life Sciences', 'Physical Sciences', 'Earth & Space Sciences'],
        ),
        school_table(
            'Official • CAST School Results Table',
            'CAST: School Results by Year',
            'CAST',
        ),
    ])

    # ── CAA Science ────────────────────────────────────────────────────────
    charts.extend([
        year_chart('Official • CAA Science PL by Year', 'CAA Science: PL by Year', 'CAA Science'),
        group_chart('Official • CAA Science PL by Race', 'CAA Science: PL by Race', 'CAA Science', 'Race/Ethnicity'),
        group_chart('Official • CAA Science PL by Program', 'CAA Science: PL by Program', 'CAA Science', 'Program'),
        grade_year_chart('Official • CAA Science PL by Grade and Year', 'CAA Science: PL by Grade and Year', 'CAA Science'),
        school_table(
            'Official • CAA Science School Results Table',
            'CAA Science: School Results by Year',
            'CAA Science',
        ),
    ])

    # ── ELPAC ──────────────────────────────────────────────────────────────
    charts.extend([
        year_group_chart(
            'Official • ELPAC PL by Assessment and Year',
            'ELPAC: PL by Assessment and Year',
            ['ELPAC Summative', 'ELPAC Interim', 'Alternate ELPAC Summative', 'Alternate ELPAC Interim'],
            'assessment', elpac_year_order,
        ),
        group_chart('Official • ELPAC Summative PL by Race', 'ELPAC Summative: PL by Race', 'ELPAC Summative', 'Race/Ethnicity'),
        group_chart('Official • ELPAC Summative PL by Program', 'ELPAC Summative: PL by Program', 'ELPAC Summative', 'Program'),
        grade_year_chart(
            'Official • ELPAC Summative PL by Grade and Year',
            'ELPAC Summative: PL by Grade and Year', 'ELPAC Summative',
            grade_order=ELPAC_GRADE_ORDER,
        ),
        domain_year_chart(
            'Official • ELPAC Summative Domain PL by Year',
            'ELPAC Summative: Domain PL by Year', 'ELPAC Summative',
            ['Listening', 'Speaking', 'Reading', 'Writing'],
        ),
        school_table(
            'Official • ELPAC Summative School Results Table',
            'ELPAC Summative: School Results by Year',
            'ELPAC Summative',
        ),
    ])

    return charts


def _latest_year() -> str:
    from dataviz.utils.data_loader import run_raw_sql

    res = run_raw_sql("""
        SELECT MAX(test_year) AS y
        FROM official_data_db
        WHERE test_year IS NOT NULL AND BTRIM(test_year) <> ''
    """)
    rows = res.get('rows') or []
    return str(rows[0]['y']) if rows and rows[0].get('y') else ''


def _year_order() -> list[str]:
    from dataviz.utils.data_loader import run_raw_sql

    res = run_raw_sql(f"""
        SELECT DISTINCT test_year_label
        FROM ({LEVELS_SQL}) s
        WHERE test_year_label IS NOT NULL
        ORDER BY test_year_label
    """)
    return [str(r['test_year_label']) for r in (res.get('rows') or [])]


def _page_filter_school_sql() -> str:
    """Fast school list from official_data_db (avoid LEVELS_SQL on every dropdown)."""
    safe_district = DISTRICT_TOTAL_SCHOOL.replace("'", "''")
    return f"""
SELECT v FROM (
    SELECT DISTINCT '{safe_district}' AS v, 0 AS sort_key
    UNION ALL
    SELECT DISTINCT COALESCE(NULLIF(BTRIM(school_name), ''), 'Unknown') AS v, 1 AS sort_key
    FROM {SOURCE_TABLE}
    WHERE COALESCE(NULLIF(BTRIM(school_code), ''), '0000000') <> '0000000'
      AND BTRIM(COALESCE(school_name, '')) <> ''
) q
WHERE v IS NOT NULL AND BTRIM(v::text) <> ''
ORDER BY sort_key, v
"""


def _page_filters(_latest_year: str) -> list[dict]:
    return [
        {
            'id': 'school',
            'label': 'School',
            'source_col': 'school_name',
            'source_table': SOURCE_TABLE,
            'source_distinct_sql': _page_filter_school_sql(),
            'widget': 'select',
            'default_val': DISTRICT_TOTAL_SCHOOL,
            'hidden': False,
        },
    ]


def _page_spec(latest_year: str) -> dict:
    return {
        'title': 'Official Assessment Results',
        'slug': 'official-assessment-results',
        'folder': CHART_FOLDER,
        'menu_order': 10,
        'description': (
            'Official CAASPP, CAST, CAA, and ELPAC results from official_data_db, '
            'using CAASPP Table A student groups and grade 13 (All Grades) rollup rows '
            'so totals match caaspp-elpac.ets.org. Charts compare multiple test years '
            '(no test-year page filter). Use the School control for district totals '
            f'(default {DISTRICT_TOTAL_SCHOOL}) or a single school; school results tables '
            'list every school with test years across the column headers (year over '
            'performance level). Race/program charts '
            'need full “all student groups” research files for earlier years; 2024–2025 '
            'SB files in the current load only include All Students at the district level. '
            'Each assessment section includes a school results table (all schools at '
            'district view). Charts are not drillable (aggregate data only).'
        ),
        'page_filters': _page_filters(latest_year),
        'tabs': ['Smarter Balanced', 'CAA', 'CAST', 'CAA Science', 'ELPAC'],
        'rows': [
            # ── Smarter Balanced ──────────────────────────────────────────
            {'tab': 'Smarter Balanced', 'label': 'ELA and Math by year', 'sections': [
                {'chart': 'Official • Smarter Balanced ELA and Math PL by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
            ]},
            {'tab': 'Smarter Balanced', 'label': 'ELA by race and year', 'sections': [
                {'chart': 'Official • Smarter Balanced ELA PL by Race', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'Smarter Balanced', 'label': 'ELA by program and year', 'sections': [
                {'chart': 'Official • Smarter Balanced ELA PL by Program', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'Smarter Balanced', 'label': 'Math by race and year', 'sections': [
                {'chart': 'Official • Smarter Balanced Math PL by Race', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'Smarter Balanced', 'label': 'Math by program and year', 'sections': [
                {'chart': 'Official • Smarter Balanced Math PL by Program', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'Smarter Balanced', 'label': 'ELA by grade and year', 'sections': [
                {'chart': 'Official • Smarter Balanced ELA PL by Grade and Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'Smarter Balanced', 'label': 'Math by grade and year', 'sections': [
                {'chart': 'Official • Smarter Balanced Math PL by Grade and Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'Smarter Balanced', 'label': 'School results', 'sections': [
                {'chart': 'Official • Smarter Balanced School Results Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 12},
            ]},
            # ── CAA ───────────────────────────────────────────────────────
            {'tab': 'CAA', 'label': 'ELA and Math by year', 'sections': [
                {'chart': 'Official • CAA ELA and Math PL by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
            ]},
            {'tab': 'CAA', 'label': 'ELA by race and year', 'sections': [
                {'chart': 'Official • CAA ELA PL by Race', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'CAA', 'label': 'ELA by program and year', 'sections': [
                {'chart': 'Official • CAA ELA PL by Program', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'CAA', 'label': 'Math by race and year', 'sections': [
                {'chart': 'Official • CAA Math PL by Race', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'CAA', 'label': 'Math by program and year', 'sections': [
                {'chart': 'Official • CAA Math PL by Program', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'CAA', 'label': 'ELA by grade and year', 'sections': [
                {'chart': 'Official • CAA ELA PL by Grade and Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'CAA', 'label': 'Math by grade and year', 'sections': [
                {'chart': 'Official • CAA Math PL by Grade and Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'CAA', 'label': 'School results', 'sections': [
                {'chart': 'Official • CAA School Results Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 12},
            ]},
            # ── CAST ──────────────────────────────────────────────────────
            {'tab': 'CAST', 'label': 'Multi-year performance levels', 'sections': [
                {'chart': 'Official • CAST PL by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
            ]},
            {'tab': 'CAST', 'label': 'By race and year', 'sections': [
                {'chart': 'Official • CAST PL by Race', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'CAST', 'label': 'By program and year', 'sections': [
                {'chart': 'Official • CAST PL by Program', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'CAST', 'label': 'Grade and year', 'sections': [
                {'chart': 'Official • CAST PL by Grade and Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'CAST', 'label': 'Science domains and year', 'sections': [
                {'chart': 'Official • CAST Science Domain PL by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'CAST', 'label': 'School results', 'sections': [
                {'chart': 'Official • CAST School Results Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 12},
            ]},
            # ── CAA Science ───────────────────────────────────────────────
            {'tab': 'CAA Science', 'label': 'Multi-year performance levels', 'sections': [
                {'chart': 'Official • CAA Science PL by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
            ]},
            {'tab': 'CAA Science', 'label': 'By race and year', 'sections': [
                {'chart': 'Official • CAA Science PL by Race', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'CAA Science', 'label': 'By program and year', 'sections': [
                {'chart': 'Official • CAA Science PL by Program', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'CAA Science', 'label': 'Grade and year', 'sections': [
                {'chart': 'Official • CAA Science PL by Grade and Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'CAA Science', 'label': 'School results', 'sections': [
                {'chart': 'Official • CAA Science School Results Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 12},
            ]},
            # ── ELPAC ─────────────────────────────────────────────────────
            {'tab': 'ELPAC', 'label': 'Assessment and year comparison', 'sections': [
                {'chart': 'Official • ELPAC PL by Assessment and Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'ELPAC', 'label': 'Summative: by race and year', 'sections': [
                {'chart': 'Official • ELPAC Summative PL by Race', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'ELPAC', 'label': 'Summative: by program and year', 'sections': [
                {'chart': 'Official • ELPAC Summative PL by Program', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'ELPAC', 'label': 'Summative: grade and year', 'sections': [
                {'chart': 'Official • ELPAC Summative PL by Grade and Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'ELPAC', 'label': 'Summative: domains and year', 'sections': [
                {'chart': 'Official • ELPAC Summative Domain PL by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
            ]},
            {'tab': 'ELPAC', 'label': 'Summative school results', 'sections': [
                {'chart': 'Official • ELPAC Summative School Results Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 12},
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
        raise SystemExit('No users exist to own the page.')
    return user


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
    from dataviz.models import Dataset, DataVizDatasetFolder

    mappings = {c: c for c in spec['columns']}
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
    chart.drill_through_columns = []
    chart.drill_through_filter_map = {}
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


def qa_dataviz() -> int:
    from dataviz.utils.data_loader import run_raw_sql

    failures = []
    latest = _latest_year()
    order = _year_order()
    charts = _chart_specs(order)

    print('=== QA: dataset rows ===')
    for spec in DATASET_SPECS:
        res = run_raw_sql(f"SELECT COUNT(*) AS n FROM ({spec['sql']}) s")
        if res.get('error'):
            failures.append(f"{spec['name']}: {res['error']}")
            print(f"  FAIL {spec['name']}: {res['error']}")
        else:
            n = (res.get('rows') or [{}])[0].get('n')
            print(f"  ok  {spec['name']}: {n} rows")

    print('=== QA: chart columns ===')
    cols = {s['name']: set(s['columns']) for s in DATASET_SPECS}
    for spec in charts:
        missing = set(spec.get('drill_columns') or []) - cols.get(spec['dataset'], set())
        for key in ('x_col', 'y_col', 'stack_col', 'series_col', 'st_row_col', 'st_col_col', 'st_value_col'):
            val = spec['config'].get(key)
            if isinstance(val, str) and val.strip():
                missing.update(p.strip() for p in val.split(',') if p.strip() and p.strip() not in cols.get(spec['dataset'], set()))
        if missing:
            failures.append(f"{spec['name']}: missing columns {sorted(missing)}")
            print(f"  FAIL {spec['name']}: missing columns {sorted(missing)}")

    print('=== QA: CAASPP district rollups (students with scores) ===')
    expect_with_scores = {
        ('2026', 'All Students'): 12952.0,
    }
    res = run_raw_sql(f"""
        SELECT test_year,
               student_group,
               MAX(students_with_scores) AS with_scores,
               SUM(student_count) AS level_total,
               SUM(CASE WHEN performance_level IN ('Level 3', 'Level 4')
                        THEN student_count ELSE 0 END) AS met_or_above
        FROM ({LEVELS_SQL}) s
        WHERE assessment = 'Smarter Balanced'
          AND subject = 'ELA'
          AND report_row_type = '{ROW_DISTRICT_ALL_GRADES}'
          AND student_group IN ('All Students', 'Hispanic or Latino', 'English Learners')
        GROUP BY test_year, student_group
        ORDER BY test_year, student_group
    """)
    if res.get('error'):
        failures.append(f'CAASPP alignment query: {res["error"]}')
        print(f"  FAIL CAASPP alignment: {res['error']}")
    else:
        for row in res.get('rows') or []:
            ty = str(row.get('test_year') or '')
            grp = row.get('student_group')
            with_scores = float(row.get('with_scores') or 0)
            level_total = float(row.get('level_total') or 0)
            met = float(row.get('met_or_above') or 0)
            if abs(with_scores - level_total) > 0.5:
                failures.append(
                    f'{ty} {grp}: level counts sum to {level_total}, '
                    f'but students_with_scores is {with_scores}'
                )
                print(f"  FAIL {ty} {grp}: totals {level_total} vs with_scores {with_scores}")
            else:
                pct = round(met / with_scores * 100, 2) if with_scores else 0
                print(f"  ok  {ty} {grp}: {with_scores:.0f} with scores ({pct}% L3+L4)")
            exp = expect_with_scores.get((ty, grp))
            if exp is not None and abs(with_scores - exp) > 0.5:
                failures.append(
                    f'{ty} {grp}: expected {exp:.0f} with scores, got {with_scores}'
                )
                print(f"  FAIL {ty} {grp}: expected {exp:.0f}, got {with_scores}")

    print('=== QA: district subgroup years (data load) ===')
    res = run_raw_sql(f"""
        SELECT student_group, COUNT(DISTINCT test_year) AS years
        FROM ({LEVELS_SQL}) s
        WHERE assessment = 'Smarter Balanced'
          AND subject = 'ELA'
          AND report_row_type = '{ROW_DISTRICT_ALL_GRADES}'
          AND is_subgroup = 1
        GROUP BY student_group
        HAVING COUNT(DISTINCT test_year) < 2
        ORDER BY student_group
        LIMIT 15
    """)
    sparse = res.get('rows') or []
    if sparse:
        print(
            f"  WARN {len(sparse)} district subgroup(s) have <2 years in "
            f'{SOURCE_TABLE} (charts show only years present in the table)'
        )
        for row in sparse[:8]:
            print(f"       {row.get('student_group')}: {row.get('years')} year(s)")

    print('=== QA: multi-year subgroups (Smarter Balanced ELA, district rollups) ===')
    res = run_raw_sql(f"""
        SELECT student_group,
               array_agg(DISTINCT test_year ORDER BY test_year) AS years
        FROM ({LEVELS_SQL}) s
        WHERE assessment = 'Smarter Balanced'
          AND subject = 'ELA'
          AND report_row_type = '{ROW_DISTRICT_ALL_GRADES}'
          AND school_name = '{DISTRICT_TOTAL_SCHOOL}'
          AND is_subgroup = 1
        GROUP BY student_group
    """)
    subgroup_years = {}
    for r in res.get('rows') or []:
        raw = r.get('years')
        if isinstance(raw, str):
            yrs = [y.strip() for y in raw.strip('{}').split(',') if y.strip()]
        else:
            yrs = [str(y) for y in (raw or [])]
        subgroup_years[str(r['student_group'])] = yrs
    anchor_years = list(order)
    for probe in ('Hispanic or Latino', 'English Learners'):
        got = subgroup_years.get(probe, [])
        missing = [y for y in anchor_years if y not in got]
        if missing:
            failures.append(
                f'{probe}: missing years {missing} at district '
                f'(have {got or "none"}; All Students has {anchor_years})'
            )
            print(f"  FAIL {probe}: years {got or 'none'} (missing {missing})")
        else:
            print(f"  ok  {probe}: years {got}")

    print('=== QA: page filters ===')
    for filt in _page_filters(latest):
        res = run_raw_sql(filt['source_distinct_sql'])
        n = len(res.get('rows') or [])
        if res.get('error') or n == 0:
            failures.append(f"filter {filt['id']}: {res.get('error') or 'no values'}")
            print(f"  FAIL filter {filt['id']}: {res.get('error') or 'no values'}")
        else:
            print(f"  ok  filter {filt['id']}: {n} values")

    if failures:
        print(f'\nQA FAILED ({len(failures)} issue(s)).')
        return 1
    print('\nQA PASSED.')
    return 0


def cleanup_official_assets(owner, charts):
    from dataviz.models import Dataset, SavedChart

    prefix = f'{CHART_ROOT_FOLDER} / {CHART_FOLDER} / '
    keep_charts = {prefix + name for name in charts}
    deleted, _ = (
        SavedChart.objects
        .filter(owner=owner, name__startswith=prefix)
        .exclude(name__in=keep_charts)
        .delete()
    )
    if deleted:
        print(f'  removed {deleted} obsolete chart(s)')

    keep_datasets = {spec['name'] for spec in DATASET_SPECS}
    deleted, _ = (
        Dataset.objects
        .filter(owner=owner, name__startswith='Official • ')
        .exclude(name__in=keep_datasets)
        .delete()
    )
    if deleted:
        print(f'  removed {deleted} obsolete dataset(s)')


def main():
    parser = argparse.ArgumentParser(description='Create the Official Assessment Results page.')
    parser.add_argument('--owner', default='')
    parser.add_argument('--qa', action='store_true')
    args = parser.parse_args()

    bootstrap_django()
    if args.qa:
        raise SystemExit(qa_dataviz())

    owner = resolve_owner(args.owner)
    print(f'Using owner: {owner}')
    latest = _latest_year()
    order = _year_order()
    print(f'Latest official test year: {latest}; assessment-year categories: {len(order)}')

    enable_dataset_tables()
    datasets = {spec['name']: upsert_dataset(spec, owner) for spec in DATASET_SPECS}
    charts = {spec['name']: upsert_chart(spec, datasets, owner) for spec in _chart_specs(order)}
    cleanup_official_assets(owner, charts)
    page = upsert_page(_page_spec(latest), charts, owner)
    attach_to_menu(page, 10)
    print('Official Assessment Results page ready.')


if __name__ == '__main__':
    main()
