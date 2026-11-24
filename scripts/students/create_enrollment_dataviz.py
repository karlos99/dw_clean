#!/usr/bin/env python
"""Create the Students -> Enrollment DataViz page.

Datasets are read-only ClickHouse SQL over the existing synced student tables:

* ``current_student`` — SIS-aligned roster counts (Active / Inactive by school year).
* ``enroll_history`` — supplemental days attended; drives weekly drops/changes via
  ``analytics_student_enrollment_*`` (rebuilt in ``sync_to_clickhouse``).

Run inside the Django container (after ``sync_to_clickhouse`` has built enrollment analytics):

    docker compose exec django python /app/scripts/students/create_enrollment_dataviz.py
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
    PROGRAM_ORDER,
    SCHOOL_TYPE_ORDER,
    attach_page_to_students_menu,
    base_chart_cfg,
    bootstrap_django,
    current_school_year,
    enable_dataset_tables,
    recent_school_years,
    resolve_owner,
    summary_cfg,
    upsert_chart,
    upsert_dataset,
    upsert_page,
)

PREFIX = 'Students • Enrollment •'
PAGE_SLUG = 'students-enrollment'
PAGE_FOLDER = 'Students / Enrollment'
CHART_FOLDER = 'enrollment'
DATASET_FOLDER = 'Students / Enrollment'

GRADE_SQL = """
multiIf(
    upper(trimBoth(ifNull({col}, ''))) IN ('TK'), 'TK',
    upper(trimBoth(ifNull({col}, ''))) IN ('KN', 'K', 'KG'), 'KN',
    upper(trimBoth(ifNull({col}, ''))) IN ('US', '250'), 'US.',
    match(trimBoth(ifNull({col}, '')), '^[0-9]+$'),
    lpad(toString(toInt32OrZero(trimBoth({col}))), 2, '0'),
    if(trimBoth(ifNull({col}, '')) = '', 'Unknown', trimBoth({col}))
)"""

MONTH = "coalesce(nullif(trim(sch.school_group), ''), 'Unknown')"

PG_GRADE_LABEL = """
CASE
    WHEN upper(trim(coalesce({col}, ''))) IN ('TK') THEN 'TK'
    WHEN upper(trim(coalesce({col}, ''))) IN ('KN', 'K', 'KG') THEN 'KN'
    WHEN upper(trim(coalesce({col}, ''))) IN ('US', '250') THEN 'US.'
    WHEN trim(coalesce({col}, '')) ~ '^[0-9]+$' THEN lpad(trim({col})::text, 2, '0')
    WHEN trim(coalesce({col}, '')) = '' THEN 'Unknown'
    ELSE trim({col})
END"""


def _roster_students_sql(
    *,
    school_year_only: str | None = None,
    statuses: tuple[str, ...] = ('Active', 'Inactive'),
    postgres: bool = False,
) -> str:
    """SIS roster from ``current_student`` (Data Explorer parity)."""
    sy_only = (school_year_only or '').replace("'", "''")
    grade_expr = (
        PG_GRADE_LABEL.format(col='cs.grade')
        if postgres
        else GRADE_SQL.format(col='cs.grade')
    )
    status_literals = ', '.join(
        "'" + s.replace("'", "''") + "'" for s in statuses
    )
    return f"""
SELECT
    cs.ssid AS ssid,
    cs.stu_annual_key AS stu_annual_key,
    cs.student_name AS student_name,
    cs.grade AS grade,
    ({grade_expr}) AS grade_label,
    cs.school AS school,
    cs.school_state_id AS school_state_id,
    coalesce(nullif(trim(sch.school_group), ''), 'Unknown') AS school_group,
    cs.status AS status,
    cs.school_year AS school_year,
    coalesce(cs.ell, '') AS ell,
    coalesce(cs.ltel, '') AS ltel,
    coalesce(cs.sed, '') AS sed,
    coalesce(cs.sped, '') AS sped,
    coalesce(cs.foster, '') AS foster,
    coalesce(cs.homeless, '') AS homeless,
    coalesce(se.migrant, '') AS migrant,
    coalesce(cs.gate, '') AS gate,
    coalesce(cs.in_504, '') AS in_504,
    coalesce(cs.dli, '') AS dli,
    coalesce(cs.gender, '') AS gender,
    coalesce(cs.race, '') AS race,
    '' AS days_attended
FROM current_student AS cs
LEFT JOIN (
    SELECT state_id, max(school_group) AS school_group
    FROM school
    WHERE state_id IS NOT NULL AND trim(state_id) != ''
    GROUP BY state_id
) AS sch ON sch.state_id = cs.school_state_id
LEFT JOIN (
    SELECT ssid, school_year, max(migrant) AS migrant
    FROM student_enroll
    WHERE ssid IS NOT NULL AND trim(ssid) != ''
      AND school_year IS NOT NULL AND trim(school_year) != ''
    GROUP BY ssid, school_year
) AS se ON se.ssid = cs.ssid AND se.school_year = cs.school_year
WHERE cs.ssid IS NOT NULL AND trim(cs.ssid) != ''
  AND cs.school_year IS NOT NULL AND trim(cs.school_year) != ''
  AND cs.status IN ({status_literals})
  {f"AND trim(cs.school_year) = '{sy_only}'" if sy_only else ''}
"""


def _active_students_sql(current_sy: str, *, postgres: bool = False) -> str:
    return _roster_students_sql(
        school_year_only=current_sy,
        statuses=('Active',),
        postgres=postgres,
    )


def _inactive_students_sql(current_sy: str, *, postgres: bool = False) -> str:
    return _roster_students_sql(
        school_year_only=current_sy,
        statuses=('Inactive',),
        postgres=postgres,
    )

_STUDENT_COLUMNS = [
    'ssid', 'stu_annual_key', 'student_name', 'grade', 'grade_label',
    'school', 'school_state_id', 'school_group', 'status', 'school_year',
    'ell', 'ltel', 'sed', 'sped', 'foster', 'homeless', 'migrant', 'gate',
    'in_504', 'dli', 'gender', 'race',
]

_STUDENT_DRILL = _STUDENT_COLUMNS

# Year-by-year snapshot: for past years use week 41, for the current year use
# the latest week with data. This keeps historical bars comparable and stops
# the current year at the data snapshot.
def _week41_students_sql(this_sy: str) -> str:
    """ClickHouse week-41 (or latest-week) student snapshot per school year."""
    csy = (this_sy or '').replace("'", "''")
    current_predicate = (
        f"school_year = '{csy}'" if csy else "0"
    )
    return f"""
SELECT
    w.ssid AS ssid,
    w.stu_annual_key AS stu_annual_key,
    w.student_name AS student_name,
    w.grade AS grade,
    w.grade_label AS grade_label,
    w.school AS school,
    w.school_state_id AS school_state_id,
    coalesce(nullif(trim(w.school_group), ''), 'Unknown') AS school_group,
    w.status AS status,
    w.school_year AS school_year,
    w.school_week AS school_week,
    w.week_start_date AS week_start_date,
    w.week_end_date AS week_end_date,
    w.week_label AS week_label,
    w.ell AS ell,
    w.ltel AS ltel,
    w.sed AS sed,
    w.sped AS sped,
    w.foster AS foster,
    w.homeless AS homeless,
    w.migrant AS migrant,
    w.gate AS gate,
    w.in_504 AS in_504,
    w.dli AS dli,
    coalesce(cs.race, '') AS race,
    coalesce(cs.gender, '') AS gender
FROM analytics_student_enrollment_weekly AS w
INNER JOIN (
    SELECT
        school_year,
        multiIf(
            {current_predicate},
            max(school_week),
            if(countIf(school_week = 41) > 0, 41, max(school_week))
        ) AS target_week
    FROM analytics_student_enrollment_weekly
    GROUP BY school_year
) AS t
    ON t.school_year = w.school_year AND t.target_week = w.school_week
LEFT JOIN current_student AS cs
    ON cs.ssid = w.ssid AND cs.school_year = w.school_year
"""

_WEEK41_COLUMNS = [
    'ssid', 'stu_annual_key', 'student_name', 'grade', 'grade_label', 'school',
    'school_state_id', 'school_group', 'status', 'school_year', 'school_week',
    'week_start_date', 'week_end_date', 'week_label', 'ell', 'ltel', 'sed',
    'sped', 'foster', 'homeless', 'migrant', 'gate', 'in_504', 'dli', 'race',
    'gender',
]


_PROGRAM_COLUMNS = _STUDENT_COLUMNS + ['program']


def _programs_sql(enrolled_sql: str) -> str:
    """Long format: one row per student per program flag (PostgreSQL)."""
    parts = []
    templates = [
        ('SED', 'sed'), ('SPED', 'sped'), ('Foster', 'foster'),
        ('Homeless', 'homeless'), ('Migrant', 'migrant'), ('504', 'in_504'),
        ('GATE', 'gate'), ('DLI', 'dli'), ('LTEL', 'ltel'),
    ]
    for label, col in templates:
        parts.append(f"""
        SELECT
            ssid, stu_annual_key, student_name, grade, grade_label, school,
            school_state_id, school_group, status, school_year, ell, ltel, sed,
            sped, foster, homeless, migrant, gate, in_504, dli, gender, race,
            '{label}' AS program
        FROM ({enrolled_sql}) AS base
        WHERE lower(trim(coalesce({col}, ''))) = 'y'
        """)
    return '\nUNION ALL\n'.join(parts)


def _active_programs_sql(active_sql: str) -> str:
    parts = []
    templates = [
        ('SED', 'sed'), ('SPED', 'sped'), ('Foster', 'foster'),
        ('Homeless', 'homeless'), ('Migrant', 'migrant'), ('504', 'in_504'),
        ('GATE', 'gate'), ('DLI', 'dli'), ('LTEL', 'ltel'),
    ]
    for label, col in templates:
        parts.append(f"""
        SELECT
            ssid, stu_annual_key, student_name, grade, grade_label, school,
            school_state_id, school_group, status, school_year, ell, ltel, sed,
            sped, foster, homeless, migrant, gate, in_504, dli, gender, race,
            '{label}' AS program
        FROM ({active_sql}) AS base
        WHERE lower(trim(coalesce({col}, ''))) = 'y'
        """)
    return '\nUNION ALL\n'.join(parts)

# Active enrollment by dw_cal instructional week (existing attendance weekly table).
_WEEKLY_ACTIVE_SQL = f"""
SELECT
    aw.ssid AS ssid,
    aw.stu_annual_key AS stu_annual_key,
    aw.student_name AS student_name,
    aw.grade AS grade,
    aw.grade_label AS grade_label,
    aw.school AS school,
    aw.school_state_id AS school_state_id,
    coalesce(nullif(trim(aw.school_group), ''), 'Unknown') AS school_group,
    aw.status AS status,
    aw.school_year AS school_year,
    aw.school_week AS school_week,
    aw.week_start_date AS week_start_date,
    aw.week_end_date AS week_end_date,
    aw.week_label AS week_label
FROM analytics_student_enrollment_weekly AS aw
WHERE aw.ssid IS NOT NULL AND trim(aw.ssid) != ''
"""

_WEEKLY_ACTIVE_COLUMNS = [
    'ssid', 'stu_annual_key', 'student_name', 'grade', 'grade_label', 'school',
    'school_state_id', 'school_group', 'status', 'school_year', 'school_week',
    'week_start_date', 'week_end_date', 'week_label',
]

_EVENTS_SQL = f"""
SELECT
    event_type AS event_type,
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
WHERE event_type IN ('New Enrollment', 'Exit')
"""

_EVENT_COLUMNS = [
    'event_type', 'event_date', 'school_year', 'school_week',
    'week_start_date', 'week_end_date', 'week_label', 'ssid', 'stu_annual_key',
    'student_name', 'grade', 'grade_label', 'school', 'school_state_id',
    'school_group', 'status', 'ell', 'ltel', 'sed', 'sped', 'foster',
    'homeless', 'migrant', 'gate', 'in_504', 'dli', 'fep_date',
]

_WEEKLY_CHANGES_SQL = """
SELECT
    school_year AS school_year,
    school_week AS school_week,
    any(week_label) AS week_label,
    any(week_start_date) AS week_start_date,
    any(week_end_date) AS week_end_date,
    school AS school,
    school_state_id AS school_state_id,
    school_group AS school_group,
    grade AS grade,
    grade_label AS grade_label,
    countIf(event_type = 'New Enrollment') AS new_enrollments,
    countIf(event_type = 'Exit') AS exits,
    countIf(event_type = 'New Enrollment') - countIf(event_type = 'Exit') AS net_change
FROM analytics_student_enrollment_events
WHERE event_type IN ('New Enrollment', 'Exit')
  AND school_week IS NOT NULL
GROUP BY
    school_year, school_week, school, school_state_id, school_group, grade,
    grade_label
"""

_WEEKLY_CHANGES_DISTRICT_SQL = """
WITH
events AS (
    SELECT
        school_year,
        school_week,
        countIf(event_type = 'New Enrollment') AS new_enrollments,
        countIf(event_type = 'Exit') AS exits,
        countIf(event_type = 'New Enrollment') - countIf(event_type = 'Exit') AS net_change
    FROM analytics_student_enrollment_events
    WHERE event_type IN ('New Enrollment', 'Exit')
      AND school_week IS NOT NULL
    GROUP BY school_year, school_week
),
week_grid AS (
    SELECT
        school_year,
        school_week,
        any(week_label) AS week_label,
        any(week_start_date) AS week_start_date,
        any(week_end_date) AS week_end_date
    FROM analytics_student_enrollment_weekly
    WHERE school_week IS NOT NULL
    GROUP BY school_year, school_week
),
combined AS (
    SELECT
        g.school_year AS school_year,
        g.school_week AS school_week,
        g.week_label AS week_label,
        g.week_start_date AS week_start_date,
        g.week_end_date AS week_end_date,
        coalesce(e.new_enrollments, 0) AS new_enrollments,
        coalesce(e.exits, 0) AS exits,
        coalesce(e.net_change, 0) AS net_change
    FROM week_grid AS g
    LEFT JOIN events AS e
        ON e.school_year = g.school_year AND e.school_week = g.school_week
),
roster AS (
    SELECT
        school_year,
        uniqExactIf(ssid, status = 'Active') AS active_roster
    FROM current_student
    WHERE ssid IS NOT NULL AND trim(ssid) != ''
      AND status IN ('Active', 'Inactive')
    GROUP BY school_year
)
SELECT
    c.school_year AS school_year,
    c.school_week AS school_week,
    c.week_label AS week_label,
    c.week_start_date AS week_start_date,
    c.week_end_date AS week_end_date,
    c.new_enrollments AS new_enrollments,
    c.exits AS exits,
    c.net_change AS net_change,
    r.active_roster - sum(c.net_change) OVER (
        PARTITION BY c.school_year
        ORDER BY c.school_week DESC
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ) AS active_headcount
FROM combined AS c
INNER JOIN roster AS r ON r.school_year = c.school_year
ORDER BY c.school_year, c.school_week
"""

_WEEKLY_CHANGE_DISTRICT_COLUMNS = [
    'school_year', 'school_week', 'week_label', 'week_start_date',
    'week_end_date', 'new_enrollments', 'exits', 'net_change', 'active_headcount',
]


def _roster_overview_sql(roster_sql: str) -> str:
    """Overview from SIS roster (current_student): totals, programs, ELL, race by year."""
    programs_sql = _programs_sql(roster_sql)
    snap = f"FROM ({roster_sql}) AS snap"
    return f"""
SELECT
    CASE
        WHEN category_group = 'Total' THEN 'Total'
        ELSE category_group || ' · ' || category
    END AS category_label,
    category_group AS category_group,
    category AS category,
    school_year AS school_year,
    student_count AS student_count
FROM (
    SELECT 'Total' AS category_group, 'Total' AS category, school_year,
           COUNT(DISTINCT ssid) AS student_count
    {snap}
    GROUP BY school_year

    UNION ALL

    SELECT 'Program' AS category_group, program AS category, school_year,
           COUNT(DISTINCT ssid) AS student_count
    FROM ({programs_sql}) AS p
    GROUP BY program, school_year

    UNION ALL

    SELECT 'ELL' AS category_group,
           coalesce(nullif(trim(ell), ''), 'Unknown') AS category,
           school_year,
           COUNT(DISTINCT ssid) AS student_count
    {snap}
    GROUP BY category, school_year

    UNION ALL

    SELECT 'Race' AS category_group,
           coalesce(nullif(trim(race), ''), 'Unknown') AS category,
           school_year,
           COUNT(DISTINCT ssid) AS student_count
    {snap}
    GROUP BY category, school_year
) AS u
"""

_OVERVIEW_COLUMNS = [
    'category_label', 'category_group', 'category', 'school_year',
    'student_count',
]

_WEEKLY_CHANGE_COLUMNS = [
    'school_year', 'school_week', 'week_label', 'week_start_date',
    'week_end_date', 'school', 'school_state_id', 'school_group', 'grade',
    'grade_label', 'new_enrollments', 'exits', 'net_change',
]

WEEK_ORDER = [str(w) for w in range(1, 43)]

YEAR_COLORS = {
    '2023-2024': '#4e79a7',
    '2024-2025': '#f28e2b',
    '2025-2026': '#59a14f',
    '2026-2027': '#e15759',
}

TYPE_COLORS = {
    'Elementary School': '#4e79a7',
    'Middle School': '#f28e2b',
    'High School': '#59a14f',
    'Special School': '#b07aa1',
    'Unknown': '#9c9c9c',
}

ELL_STATUS_ORDER = ELL_ORDER + ['Unknown']
ELL_COLORS = {
    'EL': '#e15759',
    'IFEP': '#f28e2b',
    'RFEP': '#59a14f',
    'EO': '#4e79a7',
    'TBD': '#edc948',
    'ADEL': '#b07aa1',
    'Unknown': '#9c9c9c',
}
RACE_ORDER = [
    'Hispanic', 'White', 'Asian', 'Multiple', 'Black or African American',
    'Filipino', 'American Indian or Alaska Native', 'Hawaiian/Pacific Islander',
    'Missing', 'Unknown',
]
OVERVIEW_ORDER = (
    ['Total']
    + [f'Program · {p}' for p in PROGRAM_ORDER]
    + [f'ELL · {e}' for e in ELL_STATUS_ORDER]
    + [f'Race · {r}' for r in RACE_ORDER]
)
CURRENT_YEAR_ROSTER_ORDER = [
    'Roster · Total', 'Roster · Active', 'Roster · Inactive',
]
SCHOOL_GROUP_CHARTS = [
    ('High Schools', ['High School']),
    ('Middle Schools', ['Middle School']),
    ('Elementary Schools', ['Elementary School']),
    ('Special & Other', ['Special School', 'Unknown']),
]


def _filter_sql(dataset_sql: str, expr: str, *, extra_where: str = '') -> str:
    where = f'WHERE {extra_where}' if extra_where else ''
    return f"""
SELECT DISTINCT ({expr}) AS v
FROM ({dataset_sql}) AS d
{where}
HAVING trim(toString(v)) != ''
ORDER BY v
"""


def _filter_sql_pg(dataset_sql: str, expr: str, *, extra_where: str = '') -> str:
    where = f'WHERE {extra_where}' if extra_where else ''
    return f"""
SELECT DISTINCT ({expr}) AS v
FROM ({dataset_sql}) AS d
{where}
HAVING trim(({expr})::text) != ''
ORDER BY v
"""


def _current_overview_sql(
    roster_cy_sql: str,
    active_sql: str,
    inactive_sql: str,
) -> str:
    """Current year SIS roster: total, active/inactive, and active-only mix."""
    programs_sql = _active_programs_sql(active_sql)
    snap_roster = f"FROM ({roster_cy_sql}) AS snap"
    snap_active = f"FROM ({active_sql}) AS snap"
    snap_inactive = f"FROM ({inactive_sql}) AS snap"
    return f"""
SELECT
    category_group || ' · ' || category AS category_label,
    category_group AS category_group,
    category AS category,
    student_count AS student_count,
    'Students' AS value_axis
FROM (
    SELECT 'Roster' AS category_group, 'Total' AS category,
           COUNT(DISTINCT ssid) AS student_count
    {snap_roster}

    UNION ALL

    SELECT 'Roster' AS category_group, 'Active' AS category,
           COUNT(DISTINCT ssid) AS student_count
    {snap_active}

    UNION ALL

    SELECT 'Roster' AS category_group, 'Inactive' AS category,
           COUNT(DISTINCT ssid) AS student_count
    {snap_inactive}

    UNION ALL

    SELECT 'Program' AS category_group, program AS category,
           COUNT(DISTINCT ssid) AS student_count
    FROM ({programs_sql}) AS p
    GROUP BY program

    UNION ALL

    SELECT 'ELL' AS category_group,
           coalesce(nullif(trim(ell), ''), 'Unknown') AS category,
           COUNT(DISTINCT ssid) AS student_count
    {snap_active}
    GROUP BY category

    UNION ALL

    SELECT 'Race' AS category_group,
           coalesce(nullif(trim(race), ''), 'Unknown') AS category,
           COUNT(DISTINCT ssid) AS student_count
    {snap_active}
    GROUP BY category
) AS u
"""


_CURRENT_OVERVIEW_COLUMNS = [
    'category_label', 'category_group', 'category', 'student_count', 'value_axis',
]


def _dataset_specs(
    roster_sql: str,
    active_sql: str,
    roster_cy_sql: str,
    active_sql_pg: str,
    inactive_sql_pg: str,
    *,
    this_sy: str,
) -> list[dict]:
    programs_sql = _programs_sql(roster_sql)
    active_programs_sql = _active_programs_sql(active_sql)
    week41_sql = _week41_students_sql(this_sy)
    overview_sql = _roster_overview_sql(roster_sql)
    return [
    {
        'name': f'{PREFIX} Enrolled Students',
        'sql': roster_sql,
        'base_table': 'current_student',
        'columns': _STUDENT_COLUMNS + ['days_attended'],
        'folder': DATASET_FOLDER,
        'description': (
            'One row per student per school year from current_student (SIS roster: '
            'Active and Inactive), matching Data Explorer / SIS.'
        ),
    },
    {
        'name': f'{PREFIX} Active Students',
        'sql': active_sql,
        'base_table': 'current_student',
        'columns': _STUDENT_COLUMNS,
        'folder': DATASET_FOLDER,
        'description': (
            'Active current_student rows for the current school year — used for '
            'live roster tracking (programs, ELL, race).'
        ),
    },
    {
        'name': f'{PREFIX} Year Snapshot',
        'sql': week41_sql,
        'base_table': 'analytics_student_enrollment_weekly',
        'columns': _WEEK41_COLUMNS,
        'folder': DATASET_FOLDER,
        'description': (
            'Enrollment snapshot per school year: week 41 for past years and '
            'the latest available week for the current year.'
        ),
    },
    {
        'name': f'{PREFIX} Current Year Active Overview',
        'sql': _current_overview_sql(
            roster_cy_sql, active_sql_pg, inactive_sql_pg,
        ),
        'base_table': 'current_student',
        'columns': _CURRENT_OVERVIEW_COLUMNS,
        'folder': DATASET_FOLDER,
        'description': (
            'Current school year SIS roster totals (Active / Inactive) and '
            'active-roster program / ELL / race mix.'
        ),
    },
    {
        'name': f'{PREFIX} Overview Breakdown',
        'sql': overview_sql,
        'base_table': 'current_student',
        'columns': _OVERVIEW_COLUMNS,
        'folder': DATASET_FOLDER,
        'description': (
            'SIS roster counts by school year: total, programs, ELL, and race.'
        ),
    },
    {
        'name': f'{PREFIX} Program Participation',
        'sql': programs_sql,
        'base_table': 'current_student',
        'columns': _PROGRAM_COLUMNS,
        'folder': DATASET_FOLDER,
        'description': (
            'Long format: one row per enrolled student per program (SED, SPED, '
            'Foster, Homeless, Migrant, 504, GATE, DLI, LTEL).'
        ),
    },
    {
        'name': f'{PREFIX} Active Program Participation',
        'sql': active_programs_sql,
        'base_table': 'current_student',
        'columns': _PROGRAM_COLUMNS,
        'folder': DATASET_FOLDER,
        'description': 'Active current-year students by program flag.',
    },
    {
        'name': f'{PREFIX} Weekly Active',
        'sql': _WEEKLY_ACTIVE_SQL,
        'base_table': 'analytics_student_enrollment_weekly',
        'columns': _WEEKLY_ACTIVE_COLUMNS,
        'folder': DATASET_FOLDER,
        'description': (
            'One row per enrolled student per dw_cal instructional school week, '
            'Week-level active counts from enroll_history segments (drops/changes source).'
        ),
    },
    {
        'name': f'{PREFIX} Events',
        'sql': _EVENTS_SQL,
        'base_table': 'analytics_student_enrollment_events',
        'columns': _EVENT_COLUMNS,
        'folder': DATASET_FOLDER,
        'description': (
            'New enrollment and Exit events mapped to dw_cal school weeks. '
            'Exits are only inactive students so end-of-year/snapshot dates are '
            'not counted as withdrawals.'
        ),
    },
    {
        'name': f'{PREFIX} Weekly Changes',
        'sql': _WEEKLY_CHANGES_SQL,
        'base_table': 'analytics_student_enrollment_events',
        'columns': _WEEKLY_CHANGE_COLUMNS,
        'folder': DATASET_FOLDER,
        'description': (
            'Weekly new enrollment, exit, and net change counts by school, '
            'school type, and grade, using dw_cal school weeks.'
        ),
    },
    {
        'name': f'{PREFIX} Weekly Changes District',
        'sql': _WEEKLY_CHANGES_DISTRICT_SQL,
        'base_table': 'analytics_student_enrollment_events',
        'columns': _WEEKLY_CHANGE_DISTRICT_COLUMNS,
        'folder': DATASET_FOLDER,
        'description': (
            'District weekly events from enroll_history plus active_headcount '
            'anchored to current_student (SIS) at the latest analytics week.'
        ),
    },
    ]


def _chart_specs() -> list[dict]:
    enrolled_ds = f'{PREFIX} Enrolled Students'
    active_ds = f'{PREFIX} Active Students'
    year_ds = f'{PREFIX} Year Snapshot'
    overview_ds = f'{PREFIX} Overview Breakdown'
    current_overview_ds = f'{PREFIX} Current Year Active Overview'
    programs_ds = f'{PREFIX} Program Participation'
    active_programs_ds = f'{PREFIX} Active Program Participation'
    changes_ds = f'{PREFIX} Weekly Changes District'
    years = recent_school_years(4)
    current_overview_order = (
        CURRENT_YEAR_ROSTER_ORDER
        + [f'Program · {p}' for p in PROGRAM_ORDER]
        + [f'ELL · {e}' for e in ELL_STATUS_ORDER]
        + [f'Race · {r}' for r in RACE_ORDER]
    )

    charts = [
        {
            'name': f'{PREFIX} Enrollment Overview Table',
            'chart_type': 'summary_table',
            'dataset': overview_ds,
            'drill_columns': _OVERVIEW_COLUMNS,
            'column_defs': {
                'category_label': 'Category', 'school_year': 'School Year',
                'student_count': 'Students',
            },
            'config': summary_cfg(
                'SIS Roster — Totals, Programs, ELL, and Race by Year',
                'category_label', 'school_year',
                st_agg_func='sum', st_value_col='student_count',
                sort_col='__manual__', category_order=OVERVIEW_ORDER,
                st_col_order=years, st_show_row_totals=False,
                st_show_col_totals=False,
                skip_page_filters=['school_year'],
                __compact_cells__=True,
            ),
        },
        {
            'name': f'{PREFIX} Enrollment by Year',
            'chart_type': 'bar',
            'dataset': enrolled_ds,
            'drill_columns': _STUDENT_DRILL,
            'column_defs': {
                'school_year': 'School Year', 'ssid': 'Total Students',
            },
            'config': base_chart_cfg(
                'bar', 'Enrollment by School Year (SIS Roster)',
                x_col='school_year', y_col='ssid', agg='count_unique',
                sort_col='__manual__', category_order=years,
                skip_page_filters=['school_year'],
                one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} Current Year Active Overview Table',
            'chart_type': 'summary_table',
            'dataset': current_overview_ds,
            'drill_columns': _CURRENT_OVERVIEW_COLUMNS,
            'column_defs': {
                'category_label': 'Category', 'student_count': 'Active Students',
            },
            'config': summary_cfg(
                'Current Year — SIS Roster and Active Mix',
                'category_label', 'value_axis',
                st_agg_func='sum', st_value_col='student_count',
                sort_col='__manual__', category_order=current_overview_order,
                st_show_row_totals=False, st_show_col_totals=False,
                skip_page_filters=['school_year'],
                __compact_cells__=True,
            ),
        },
        {
            'name': f'{PREFIX} Active Enrollment by School Type',
            'chart_type': 'donut',
            'dataset': active_ds,
            'drill_columns': _STUDENT_DRILL,
            'column_defs': {'school_group': 'School Type', 'ssid': 'Students'},
            'config': base_chart_cfg(
                'donut', 'Active Students by School Type',
                x_col='school_group', y_col='ssid', agg='count_unique',
                sort_col='__manual__', category_order=SCHOOL_TYPE_ORDER,
                color_map=TYPE_COLORS,
                one_row_per_stu_annual_key=True,
                skip_page_filters=['school_year'],
            ),
        },
        {
            'name': f'{PREFIX} Enrollment by School Type by Year',
            'chart_type': 'stacked_bar',
            'dataset': enrolled_ds,
            'drill_columns': _STUDENT_DRILL,
            'column_defs': {'school_year': 'School Year', 'school_group': 'School Type', 'ssid': 'Students'},
            'config': base_chart_cfg(
                'stacked_bar', 'Enrollment by School Type and Year',
                x_col='school_year', y_col='ssid', agg='count_unique',
                stack_col='school_group', series_col='school_group',
                sort_col='__manual__', category_order=years,
                stack_order=SCHOOL_TYPE_ORDER, color_map=TYPE_COLORS,
                skip_page_filters=['school_year'],
                one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} Active Enrollment by Grade',
            'chart_type': 'bar',
            'dataset': active_ds,
            'drill_columns': _STUDENT_DRILL,
            'column_defs': {'grade_label': 'Grade', 'ssid': 'Students'},
            'config': base_chart_cfg(
                'bar', 'Active Students by Grade',
                x_col='grade_label', y_col='ssid', agg='count_unique',
                sort_col='__manual__', category_order=GRADE_ORDER,
                one_row_per_stu_annual_key=True,
                skip_page_filters=['school_year'],
            ),
        },
        {
            'name': f'{PREFIX} Enrollment by Grade',
            'chart_type': 'bar',
            'dataset': enrolled_ds,
            'drill_columns': _STUDENT_DRILL,
            'column_defs': {'grade_label': 'Grade', 'ssid': 'Students'},
            'config': base_chart_cfg(
                'bar', 'Enrollment by Grade (Selected Year)',
                x_col='grade_label', y_col='ssid', agg='count_unique',
                sort_col='__manual__', category_order=GRADE_ORDER,
                one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} Enrollment by School Type',
            'chart_type': 'donut',
            'dataset': enrolled_ds,
            'drill_columns': _STUDENT_DRILL,
            'column_defs': {'school_group': 'School Type', 'ssid': 'Students'},
            'config': base_chart_cfg(
                'donut', 'Enrollment by School Type (Selected Year)',
                x_col='school_group', y_col='ssid', agg='count_unique',
                sort_col='__manual__', category_order=SCHOOL_TYPE_ORDER,
                color_map=TYPE_COLORS,
                one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} Enrollment by Grade and School Type',
            'chart_type': 'stacked_bar',
            'dataset': enrolled_ds,
            'drill_columns': _STUDENT_DRILL,
            'column_defs': {
                'grade_label': 'Grade', 'school_group': 'School Type', 'ssid': 'Students',
            },
            'config': base_chart_cfg(
                'stacked_bar', 'Enrollment by Grade and School Type',
                x_col='grade_label', y_col='ssid', agg='count_unique',
                stack_col='school_group', series_col='school_group',
                sort_col='__manual__', category_order=GRADE_ORDER,
                stack_order=SCHOOL_TYPE_ORDER, color_map=TYPE_COLORS,
                one_row_per_stu_annual_key=True,
            ),
        },
        {
            'name': f'{PREFIX} Program Participation',
            'chart_type': 'bar',
            'dataset': programs_ds,
            'drill_columns': _PROGRAM_COLUMNS,
            'column_defs': {'program': 'Program', 'ssid': 'Students'},
            'config': base_chart_cfg(
                'bar', 'Students by Program',
                x_col='program', y_col='ssid', agg='count_unique',
                sort_col='__manual__', category_order=PROGRAM_ORDER,
            ),
        },
        {
            'name': f'{PREFIX} Program Participation by School Type',
            'chart_type': 'stacked_bar',
            'dataset': programs_ds,
            'drill_columns': _PROGRAM_COLUMNS,
            'column_defs': {
                'program': 'Program', 'school_group': 'School Type', 'ssid': 'Students',
            },
            'config': base_chart_cfg(
                'stacked_bar', 'Students by Program and School Type',
                x_col='program', y_col='ssid', agg='count_unique',
                stack_col='school_group', series_col='school_group',
                sort_col='__manual__', category_order=PROGRAM_ORDER,
                stack_order=SCHOOL_TYPE_ORDER, color_map=TYPE_COLORS,
            ),
        },
        {
            'name': f'{PREFIX} Active Enrollment by ELL Status',
            'chart_type': 'donut',
            'dataset': active_ds,
            'drill_columns': _STUDENT_DRILL,
            'column_defs': {'ell': 'ELL Status', 'ssid': 'Students'},
            'config': base_chart_cfg(
                'donut', 'Active Students by ELL Status',
                x_col='ell', y_col='ssid', agg='count_unique',
                sort_col='__manual__', category_order=ELL_STATUS_ORDER,
                color_map=ELL_COLORS,
                skip_page_filters=['school_year'],
            ),
        },
        {
            'name': f'{PREFIX} Active Enrollment by Race',
            'chart_type': 'bar',
            'dataset': active_ds,
            'drill_columns': _STUDENT_DRILL,
            'column_defs': {'race': 'Race', 'ssid': 'Students'},
            'config': base_chart_cfg(
                'bar', 'Active Students by Race',
                x_col='race', y_col='ssid', agg='count_unique',
                sort_col='_value', sort_dir='desc', top_n=20,
                x_label_rotation=30,
                skip_page_filters=['school_year'],
            ),
        },
        {
            'name': f'{PREFIX} Active Program Participation',
            'chart_type': 'bar',
            'dataset': active_programs_ds,
            'drill_columns': _PROGRAM_COLUMNS,
            'column_defs': {'program': 'Program', 'ssid': 'Students'},
            'config': base_chart_cfg(
                'bar', 'Active Students by Program',
                x_col='program', y_col='ssid', agg='count_unique',
                sort_col='__manual__', category_order=PROGRAM_ORDER,
                skip_page_filters=['school_year'],
            ),
        },
        {
            'name': f'{PREFIX} Active Enrollment by School Week',
            'chart_type': 'line',
            'dataset': changes_ds,
            'drill_columns': _WEEKLY_CHANGE_DISTRICT_COLUMNS,
            'drill_through_filter_map': {
                'x_col': 'school_week', 'series_col': 'school_year',
            },
            'column_defs': {
                'school_week': 'School Week', 'school_year': 'School Year',
                'active_headcount': 'Active Students (SIS)',
            },
            'config': base_chart_cfg(
                'line', 'Active Enrollment by School Week (SIS roster + history deltas)',
                x_col='school_week', y_col='active_headcount', agg='sum',
                stack_col='school_year', series_col='school_year',
                sort_col='__manual__', category_order=WEEK_ORDER,
                stack_order=years, show_grid=False, show_points=True,
                data_labels=False, color_map=YEAR_COLORS,
                sparse_series=True, zero_as_gap=True, connect_nulls=True,
                truncate_current_year_at_last_data=True,
                skip_page_filters=['school_year'],
            ),
        },
        {
            'name': f'{PREFIX} New Enrollments, Exits, and Net Change by Week',
            'chart_type': 'line',
            'dataset': changes_ds,
            'drill_columns': _WEEKLY_CHANGE_DISTRICT_COLUMNS,
            'drill_through_filter_map': {'x_col': 'school_week'},
            'column_defs': {
                'school_week': 'School Week',
                'new_enrollments': 'New Enrollments',
                'exits': 'Exits',
                'net_change': 'Net Change',
                'active_headcount': 'Active Students (SIS)',
            },
            'config': base_chart_cfg(
                'line', 'Weekly Events and Active Headcount (SIS-anchored)',
                x_col='school_week', y_cols=[
                    'new_enrollments', 'exits', 'net_change', 'active_headcount',
                ],
                agg='sum', sort_col='__manual__',
                category_order=WEEK_ORDER, show_points=True, data_labels=False,
                show_grid=False, sparse_series=True, zero_as_gap=True,
                connect_nulls=True,
            ),
        },
        {
            'name': f'{PREFIX} Enrollment Detail Table',
            'chart_type': 'summary_table',
            'dataset': enrolled_ds,
            'drill_columns': _STUDENT_DRILL,
            'column_defs': {'school': 'School', 'grade_label': 'Grade', 'ssid': 'Students'},
            'config': summary_cfg(
                'Enrollment by School and Grade', 'school', 'grade_label',
            ),
        },
    ]

    # One school chart per type so all high schools stay together, then middle,
    # elementary, and special/other.
    for label, groups in SCHOOL_GROUP_CHARTS:
        charts.append({
            'name': f'{PREFIX} Enrollment by School - {label}',
            'chart_type': 'bar',
            'dataset': enrolled_ds,
            'source_filters': {'school_group': groups},
            'drill_columns': _STUDENT_DRILL,
            'column_defs': {'school': 'School', 'ssid': 'Students'},
            'config': base_chart_cfg(
                'bar', f'Enrollment by School — {label}',
                x_col='school', y_col='ssid', agg='count_unique',
                top_n=60, x_label_rotation=30,
                one_row_per_stu_annual_key=True,
            ),
        })

    return charts


def page_spec(roster_sql: str) -> dict:
    return {
    'title': 'Enrollment',
    'slug': PAGE_SLUG,
    'folder': PAGE_FOLDER,
    'description': (
        'Enrollment counts use current_student (SIS roster). enroll_history drives '
        'weekly drops, exits, and net change from enrollment segments.'
    ),
    'page_filters': [
        {
            'id': 'school_year', 'label': 'School Year',
            'source_col': 'school_year', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql_pg(roster_sql, 'd.school_year'),
            'widget': 'select', 'default_val': '__sy_this__', 'hidden': False,
        },
        {
            'id': 'school_group', 'label': 'School Type',
            'source_col': 'school_group', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql_pg(roster_sql, 'd.school_group'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'school', 'label': 'School',
            'source_col': 'school', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql_pg(roster_sql, 'd.school'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'grade', 'label': 'Grade',
            'source_col': 'grade_label', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql_pg(roster_sql, 'd.grade_label'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'sed', 'label': 'SED',
            'source_col': 'sed', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql_pg(roster_sql, 'd.sed'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'sped', 'label': 'SPED',
            'source_col': 'sped', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql_pg(roster_sql, 'd.sped'),
            'widget': 'multi', 'default_val': '', 'hidden': False,
        },
        {
            'id': 'foster', 'label': 'Foster',
            'source_col': 'foster', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql_pg(roster_sql, 'd.foster'),
            'widget': 'multi', 'default_val': '', 'hidden': True,
        },
        {
            'id': 'homeless', 'label': 'Homeless',
            'source_col': 'homeless', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql_pg(roster_sql, 'd.homeless'),
            'widget': 'multi', 'default_val': '', 'hidden': True,
        },
        {
            'id': 'migrant', 'label': 'Migrant',
            'source_col': 'migrant', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql_pg(roster_sql, 'd.migrant'),
            'widget': 'multi', 'default_val': '', 'hidden': True,
        },
        {
            'id': 'gate', 'label': 'GATE',
            'source_col': 'gate', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql_pg(roster_sql, 'd.gate'),
            'widget': 'multi', 'default_val': '', 'hidden': True,
        },
        {
            'id': 'in_504', 'label': '504',
            'source_col': 'in_504', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql_pg(roster_sql, 'd.in_504'),
            'widget': 'multi', 'default_val': '', 'hidden': True,
        },
        {
            'id': 'dli', 'label': 'DLI',
            'source_col': 'dli', 'source_table': 'current_student',
            'source_distinct_sql': _filter_sql_pg(roster_sql, 'd.dli'),
            'widget': 'multi', 'default_val': '', 'hidden': True,
        },
    ],
    'tabs': [
        'Overview', 'Current Year', 'By School Type', 'By School',
        'Programs', 'Weekly Changes', 'Detail',
    ],
    'rows': [
        {'tab': 'Overview', 'label': 'Enrollment totals, programs, ELL, and race', 'sections': [
            {'chart': f'{PREFIX} Enrollment Overview Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 22},
        ]},
        {'tab': 'Overview', 'label': 'Enrollment over time', 'sections': [
            {'chart': f'{PREFIX} Enrollment by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 7},
        ]},
        {'tab': 'Overview', 'label': 'Selected year enrollment mix', 'sections': [
            {'chart': f'{PREFIX} Enrollment by School Type', 'gs_x': 0, 'gs_w': 5, 'gs_h': 7},
            {'chart': f'{PREFIX} Enrollment by Grade', 'gs_x': 5, 'gs_w': 7, 'gs_h': 7},
        ]},
        {'tab': 'Current Year', 'label': 'Active student totals', 'sections': [
            {'chart': f'{PREFIX} Current Year Active Overview Table',
             'gs_x': 0, 'gs_w': 12, 'gs_h': 18},
        ]},
        {'tab': 'Current Year', 'label': 'Active enrollment mix', 'sections': [
            {'chart': f'{PREFIX} Active Enrollment by School Type', 'gs_x': 0, 'gs_w': 5, 'gs_h': 7},
            {'chart': f'{PREFIX} Active Enrollment by Grade', 'gs_x': 5, 'gs_w': 7, 'gs_h': 7},
        ]},
        {'tab': 'Current Year', 'label': 'Programs, ELL, and race (active roster)', 'sections': [
            {'chart': f'{PREFIX} Active Program Participation', 'gs_x': 0, 'gs_w': 12, 'gs_h': 7},
        ]},
        {'tab': 'Current Year', 'label': 'ELL and race', 'sections': [
            {'chart': f'{PREFIX} Active Enrollment by ELL Status', 'gs_x': 0, 'gs_w': 5, 'gs_h': 8},
            {'chart': f'{PREFIX} Active Enrollment by Race', 'gs_x': 5, 'gs_w': 7, 'gs_h': 8},
        ]},
        {'tab': 'By School Type', 'label': 'School type over time', 'sections': [
            {'chart': f'{PREFIX} Enrollment by School Type by Year', 'gs_x': 0, 'gs_w': 12, 'gs_h': 7},
        ]},
        {'tab': 'By School Type', 'label': 'Grade by school type', 'sections': [
            {'chart': f'{PREFIX} Enrollment by Grade and School Type', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'By School', 'label': 'High schools', 'sections': [
            {'chart': f'{PREFIX} Enrollment by School - High Schools', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'By School', 'label': 'Middle schools', 'sections': [
            {'chart': f'{PREFIX} Enrollment by School - Middle Schools', 'gs_x': 0, 'gs_w': 12, 'gs_h': 9},
        ]},
        {'tab': 'By School', 'label': 'Elementary schools', 'sections': [
            {'chart': f'{PREFIX} Enrollment by School - Elementary Schools', 'gs_x': 0, 'gs_w': 12, 'gs_h': 13},
        ]},
        {'tab': 'By School', 'label': 'Special and other schools', 'sections': [
            {'chart': f'{PREFIX} Enrollment by School - Special & Other', 'gs_x': 0, 'gs_w': 12, 'gs_h': 10},
        ]},
        {'tab': 'Programs', 'label': 'Program participation (roster, filtered year)', 'sections': [
            {'chart': f'{PREFIX} Program Participation', 'gs_x': 0, 'gs_w': 12, 'gs_h': 7},
        ]},
        {'tab': 'Programs', 'label': 'Programs by school type', 'sections': [
            {'chart': f'{PREFIX} Program Participation by School Type', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Weekly Changes', 'label': 'Total enrollment by week', 'sections': [
            {'chart': f'{PREFIX} Active Enrollment by School Week', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Weekly Changes', 'label': 'New enrollments, exits, and net change', 'sections': [
            {'chart': f'{PREFIX} New Enrollments, Exits, and Net Change by Week', 'gs_x': 0, 'gs_w': 12, 'gs_h': 8},
        ]},
        {'tab': 'Detail', 'label': 'Enrollment by school and grade', 'sections': [
            {'chart': f'{PREFIX} Enrollment Detail Table', 'gs_x': 0, 'gs_w': 12, 'gs_h': 16},
        ]},
    ],
    }


def remove_obsolete_charts() -> None:
    from dataviz.models import SavedChart

    keep = set()
    for spec in _chart_specs():
        keep.add(f'{CHART_ROOT_FOLDER} / {CHART_FOLDER} / {spec["name"]}')
    removed = 0
    for chart in SavedChart.objects.filter(
        name__startswith=f'{CHART_ROOT_FOLDER} / {CHART_FOLDER} / {PREFIX}'
    ):
        if chart.name not in keep:
            chart.delete()
            removed += 1
    if removed:
        print(f'  removed {removed} obsolete enrollment chart(s)')


def main() -> None:
    parser = argparse.ArgumentParser(description='Create the Students -> Enrollment page.')
    parser.add_argument('--owner', default='', help='Owner username or email.')
    args = parser.parse_args()

    project_dir = bootstrap_django()
    print(f'Django project: {project_dir}')
    owner = resolve_owner(args.owner)
    print(f'Using owner: {owner}')

    this_sy = current_school_year()
    roster_sql = _roster_students_sql()
    roster_cy_sql = _roster_students_sql(
        school_year_only=this_sy,
        postgres=True,
    )
    active_sql = _active_students_sql(this_sy)
    active_sql_pg = _active_students_sql(this_sy, postgres=True)
    inactive_sql_pg = _inactive_students_sql(this_sy, postgres=True)

    enable_dataset_tables([
        'enroll_history', 'current_student', 'school', 'student_enroll',
        'analytics_student_enrollment_weekly',
        'analytics_student_enrollment_events',
    ])

    dataset_specs = _dataset_specs(
        roster_sql,
        active_sql,
        roster_cy_sql,
        active_sql_pg,
        inactive_sql_pg,
        this_sy=this_sy,
    )
    datasets = {spec['name']: upsert_dataset(spec, owner, default_folder=DATASET_FOLDER)
                for spec in dataset_specs}
    charts = {spec['name']: upsert_chart(
        spec, datasets, owner,
        chart_root=CHART_ROOT_FOLDER, chart_folder=CHART_FOLDER,
        allowed_prefix=PREFIX,
    ) for spec in _chart_specs()}
    remove_obsolete_charts()
    page = upsert_page(page_spec(roster_sql), charts, owner)
    attach_page_to_students_menu(page, 0)
    print('Enrollment page ready.')


if __name__ == '__main__':
    main()
