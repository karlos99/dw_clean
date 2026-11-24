#!/usr/bin/env python
"""Build the student event table used by the Students dashboards.

The table is intentionally small and page-oriented:

* ``New Enrollment`` — one event per student enrollment start (enter_date).
* ``Exit`` — one event per inactive student's leave_date. Active students are
  skipped because their leave_date is the data-snapshot / school-year end, not
  a withdrawal.
* ``Reclassified`` — one event per RFEP/fep_date. The school year is derived
  from the FEP date and the enrollment row for that year supplies the grade and
  school, so the same historical FEP date is not counted again in later years.

Events are mapped to ``dw_cal`` school weeks, which is the same week numbering
used by the Attendance page.

Run inside the Django container:

    docker compose exec django python /app/scripts/students/build_student_weekly_events.py
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta

TABLE = 'analytics_student_enrollment_events'
WEEKLY_TABLE = 'analytics_student_enrollment_weekly'

EVENT_COLUMNS = [
    'event_type',
    'event_date',
    'school_year',
    'school_week',
    'week_start_date',
    'week_end_date',
    'week_label',
    'ssid',
    'stu_annual_key',
    'student_name',
    'grade',
    'grade_label',
    'school',
    'school_state_id',
    'school_group',
    'status',
    'ell',
    'ltel',
    'sed',
    'sped',
    'foster',
    'homeless',
    'migrant',
    'gate',
    'in_504',
    'dli',
    'fep_date',
]

EVENT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    event_type String,
    event_date Nullable(String),
    school_year String,
    school_week Nullable(Int32),
    week_start_date Nullable(String),
    week_end_date Nullable(String),
    week_label Nullable(String),
    ssid String,
    stu_annual_key Nullable(String),
    student_name Nullable(String),
    grade Nullable(String),
    grade_label Nullable(String),
    school Nullable(String),
    school_state_id Nullable(String),
    school_group Nullable(String),
    status Nullable(String),
    ell Nullable(String),
    ltel Nullable(String),
    sed Nullable(String),
    sped Nullable(String),
    foster Nullable(String),
    homeless Nullable(String),
    migrant Nullable(String),
    gate Nullable(String),
    in_504 Nullable(String),
    dli Nullable(String),
    fep_date Nullable(String)
)
ENGINE = MergeTree
ORDER BY (school_year, event_type, ssid)
SETTINGS index_granularity = 8192
"""

WEEKLY_COLUMNS = [
    'ssid',
    'stu_annual_key',
    'student_name',
    'grade',
    'grade_label',
    'school',
    'school_state_id',
    'school_group',
    'status',
    'school_year',
    'school_week',
    'week_start_date',
    'week_end_date',
    'week_label',
    'ell',
    'ltel',
    'sed',
    'sped',
    'foster',
    'homeless',
    'migrant',
    'gate',
    'in_504',
    'dli',
]

WEEKLY_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    ssid String,
    stu_annual_key Nullable(String),
    student_name Nullable(String),
    grade Nullable(String),
    grade_label Nullable(String),
    school Nullable(String),
    school_state_id Nullable(String),
    school_group Nullable(String),
    status Nullable(String),
    school_year String,
    school_week Int32,
    week_start_date Nullable(String),
    week_end_date Nullable(String),
    week_label Nullable(String),
    ell Nullable(String),
    ltel Nullable(String),
    sed Nullable(String),
    sped Nullable(String),
    foster Nullable(String),
    homeless Nullable(String),
    migrant Nullable(String),
    gate Nullable(String),
    in_504 Nullable(String),
    dli Nullable(String)
)
ENGINE = MergeTree
ORDER BY (school_year, school_week, ssid)
SETTINGS index_granularity = 8192
"""


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    if not text or text.startswith('0000'):
        return None
    try:
        return datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError:
        return None


def _grade_label(raw) -> str:
    g = str(raw or '').strip()
    u = g.upper()
    if u == 'TK':
        return 'TK'
    if u in ('KN', 'K', 'KG'):
        return 'KN'
    if u in ('US', '250'):
        return 'US.'
    if g.isdigit():
        return str(int(g)).zfill(2)
    return g or 'Unknown'


def _school_year_start_year(sy: str) -> int | None:
    text = str(sy or '').strip()
    if len(text) < 4 or not text[:4].isdigit():
        return None
    return int(text[:4])


def _school_year_for_date(day: date) -> str:
    """August-July school-year label for a date (e.g. 2026-05-10 -> 2025-2026)."""
    start_year = day.year if day.month >= 7 else day.year - 1
    return f'{start_year}-{start_year + 1}'


def _format_us_date(iso_date: str) -> str:
    text = (iso_date or '').strip()[:10]
    if not text:
        return ''
    try:
        return datetime.strptime(text, '%Y-%m-%d').strftime('%m/%d/%Y')
    except ValueError:
        return text


def load_calendar_lookup() -> dict[tuple[str, str], dict]:
    """(school_year_label, YYYY-MM-DD) -> week metadata from PostgreSQL dw_cal."""
    from django.db import connection

    lookup: dict[tuple[str, str], dict] = {}
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT school_year_label, calendar_date, school_week_number,
                   week_start_monday
            FROM dw_cal
            WHERE calendar_date IS NOT NULL AND trim(calendar_date) != ''
            """
        )
        for sy_raw, cal_raw, week_raw, week_start_raw in cur.fetchall():
            sy = str(sy_raw or '').strip()
            day = str(cal_raw or '').strip()[:10]
            if not sy or not day:
                continue
            week_int = None
            if week_raw is not None and str(week_raw).strip().isdigit():
                week_int = int(str(week_raw).strip())
            week_start = str(week_start_raw or '').strip()[:10]
            week_end = ''
            if week_start:
                try:
                    week_end = (
                        datetime.strptime(week_start, '%Y-%m-%d')
                        + timedelta(days=6)
                    ).strftime('%Y-%m-%d')
                except ValueError:
                    week_end = week_start
            label = ''
            if week_int is not None and week_start:
                label = (
                    f"Week {week_int} ({_format_us_date(week_start)}"
                    f"–{_format_us_date(week_end)})"
                )
            lookup[(sy, day)] = {
                'school_week': week_int,
                'week_start_date': week_start or None,
                'week_end_date': week_end or None,
                'week_label': label or None,
            }
    return lookup


def _fetch_enrollment_rows() -> list[tuple]:
    """One enrollment row per student/school-year, enriched with roster fields."""
    from django.db import connection

    sql = """
        SELECT
            se.ssid,
            COALESCE(se.stu_annual_key, cs.stu_annual_key) AS stu_annual_key,
            cs.student_name,
            COALESCE(se.grade, cs.grade) AS grade,
            COALESCE(se.school_name, cs.school) AS school,
            COALESCE(se.state_school_code, cs.school_state_id) AS school_state_id,
            se.enter_date,
            se.leave_date,
            se.fep_date,
            COALESCE(cs.status, se.status) AS status,
            se.school_year,
            cs.ell,
            cs.ltel,
            cs.sed,
            cs.sped,
            cs.foster,
            cs.homeless,
            se.migrant,
            cs.gate,
            cs.in_504,
            cs.dli,
            sch.school_group
        FROM student_enroll se
        INNER JOIN current_student cs
            ON cs.ssid = se.ssid
           AND cs.school_year = se.school_year
        LEFT JOIN (
            SELECT state_id, max(school_group) AS school_group
            FROM school
            WHERE state_id IS NOT NULL AND trim(state_id) != ''
            GROUP BY state_id
        ) sch ON sch.state_id = se.state_school_code
        WHERE se.ssid IS NOT NULL AND trim(se.ssid) != ''
          AND se.school_year IS NOT NULL AND trim(se.school_year) != ''
        ORDER BY se.school_year, se.ssid, se.enter_date
    """
    with connection.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())


def _event_row(base: dict, event_type: str, event_date: date,
               school_year: str, lookup: dict) -> tuple:
    day = event_date.isoformat()
    meta = lookup.get((school_year, day), {})
    return (
        event_type,
        day,
        school_year,
        meta.get('school_week'),
        meta.get('week_start_date'),
        meta.get('week_end_date'),
        meta.get('week_label'),
        base['ssid'],
        base['stu_annual_key'],
        base['student_name'],
        base['grade'],
        _grade_label(base['grade']),
        base['school'],
        base['school_state_id'],
        (base['school_group'] or 'Unknown'),
        base['status'],
        base['ell'],
        base['ltel'],
        base['sed'],
        base['sped'],
        base['foster'],
        base['homeless'],
        base['migrant'],
        base['gate'],
        base['in_504'],
        base['dli'],
        base['fep_date'],
    )


def build_event_rows() -> list[tuple]:
    lookup = load_calendar_lookup()
    rows: list[tuple] = []
    seen: set[tuple] = set()

    for (
        ssid,
        stu_annual_key,
        student_name,
        grade,
        school,
        school_state_id,
        enter_date,
        leave_date,
        fep_date,
        status,
        school_year,
        ell,
        ltel,
        sed,
        sped,
        foster,
        homeless,
        migrant,
        gate,
        in_504,
        dli,
        school_group,
    ) in _fetch_enrollment_rows():
        sy = str(school_year or '').strip()
        ssid_s = str(ssid or '').strip()
        if not ssid_s or not sy:
            continue
        base = {
            'ssid': ssid_s,
            'stu_annual_key': str(stu_annual_key or '').strip() or None,
            'student_name': student_name,
            'grade': grade,
            'school': school,
            'school_state_id': school_state_id,
            'school_group': school_group,
            'status': status,
            'ell': ell,
            'ltel': ltel,
            'sed': sed,
            'sped': sped,
            'foster': foster,
            'homeless': homeless,
            'migrant': migrant,
            'gate': gate,
            'in_504': in_504,
            'dli': dli,
            'fep_date': _parse_date(fep_date).isoformat() if _parse_date(fep_date) else None,
        }

        entered = _parse_date(enter_date)
        if entered:
            start_year = _school_year_start_year(sy)
            # Keep obvious date-entry mistakes out of the weekly trend.
            if start_year is None or entered.year >= start_year - 1:
                key = (ssid_s, 'New Enrollment', entered.isoformat())
                if key not in seen:
                    seen.add(key)
                    rows.append(_event_row(base, 'New Enrollment', entered, sy, lookup))

        left = _parse_date(leave_date)
        if left and str(status or '').strip().lower() == 'inactive':
            key = (ssid_s, 'Exit', left.isoformat())
            if key not in seen:
                seen.add(key)
                rows.append(_event_row(base, 'Exit', left, sy, lookup))

        fep = _parse_date(fep_date)
        if fep:
            reclass_sy = _school_year_for_date(fep)
            # Only the enrollment row for the FEP year carries the event; this
            # prevents a historical FEP date from being counted every year.
            if reclass_sy == sy:
                key = (ssid_s, 'Reclassified', fep.isoformat())
                if key not in seen:
                    seen.add(key)
                    rows.append(_event_row(base, 'Reclassified', fep, reclass_sy, lookup))

    return rows


def build_weekly_rows() -> list[tuple]:
    """One row per enrolled student per dw_cal instructional week."""
    from clickhouse_utils.student_attendance_analytics import (
        load_instructional_weeks_by_school_year,
    )

    weeks_by_sy = load_instructional_weeks_by_school_year()
    enrollment_rows = _fetch_enrollment_rows()
    rows: list[tuple] = []
    seen: set[tuple] = set()

    # Data freshness cutoff per school year. Active students carry the
    # snapshot date in leave_date, so max(enter, leave) tells us how far the
    # enrollment data actually extends. Calendar weeks beyond that are omitted
    # so the current-year line stops when the source has not been refreshed.
    cutoffs: dict[str, date] = {}
    for row in enrollment_rows:
        sy = str(row[10] or '').strip()
        if not sy:
            continue
        for raw_date in (row[6], row[7]):
            day = _parse_date(raw_date)
            if day and (sy not in cutoffs or day > cutoffs[sy]):
                cutoffs[sy] = day

    for (
        ssid,
        stu_annual_key,
        student_name,
        grade,
        school,
        school_state_id,
        enter_date,
        leave_date,
        fep_date,
        status,
        school_year,
        ell,
        ltel,
        sed,
        sped,
        foster,
        homeless,
        migrant,
        gate,
        in_504,
        dli,
        school_group,
    ) in enrollment_rows:
        sy = str(school_year or '').strip()
        ssid_s = str(ssid or '').strip()
        weeks = weeks_by_sy.get(sy)
        if not ssid_s or not sy or not weeks:
            continue

        entered = _parse_date(enter_date)
        left = _parse_date(leave_date)
        active = str(status or '').strip().lower() == 'active'
        grade_label = _grade_label(grade)
        cutoff = cutoffs.get(sy)

        for wk in weeks:
            week_start = _parse_date(wk.week_start_date)
            week_end = _parse_date(wk.week_end_date)
            if week_end is None:
                continue
            if cutoff and week_start and week_start > cutoff:
                continue
            if entered and entered > week_end:
                continue
            # Active students stay on the roster through the end of the
            # available calendar. Inactive students stop at their leave date.
            if not active and left and week_start and left < week_start:
                continue
            key = (ssid_s, sy, wk.school_week)
            if key in seen:
                continue
            seen.add(key)
            rows.append((
                ssid_s,
                str(stu_annual_key or '').strip() or None,
                student_name,
                grade,
                grade_label,
                school,
                school_state_id,
                (school_group or 'Unknown'),
                status,
                sy,
                wk.school_week,
                wk.week_start_date,
                wk.week_end_date,
                wk.week_label,
                ell,
                ltel,
                sed,
                sped,
                foster,
                homeless,
                migrant,
                gate,
                in_504,
                dli,
            ))
    return rows


def _replace_clickhouse_table(ch, db: str, table: str, ddl: str,
                              columns: list[str], rows: list[tuple]) -> int:
    live = f'{db}.{table}'
    staging = f'{db}.{table}__sync'
    ch.command(ddl.format(table=live))
    ch.command(f'DROP TABLE IF EXISTS {staging}')
    ch.command(ddl.format(table=staging))
    batch_size = 5000
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        if batch:
            ch.insert(staging, batch, column_names=columns)
    ch.command(f'EXCHANGE TABLES {live} AND {staging}')
    ch.command(f'TRUNCATE TABLE {staging}')
    ch.command(f'DROP TABLE IF EXISTS {staging}')
    return len(rows)


def rebuild_weekly_table(rows: list[tuple] | None = None) -> int:
    from django.conf import settings

    from clickhouse_utils.client import get_clickhouse_client

    rows = build_weekly_rows() if rows is None else rows
    ch = get_clickhouse_client()
    if ch is None:
        raise SystemExit('ClickHouse client is not available.')
    db = getattr(settings, 'CLICKHOUSE_DB', 'fastapi_clickhouse_db')
    return _replace_clickhouse_table(
        ch, db, WEEKLY_TABLE, WEEKLY_TABLE_DDL, WEEKLY_COLUMNS, rows,
    )


def rebuild_table(rows: list[tuple] | None = None) -> int:
    from django.conf import settings

    from clickhouse_utils.client import get_clickhouse_client

    rows = build_event_rows() if rows is None else rows
    ch = get_clickhouse_client()
    if ch is None:
        raise SystemExit('ClickHouse client is not available.')

    db = getattr(settings, 'CLICKHOUSE_DB', 'fastapi_clickhouse_db')
    event_count = _replace_clickhouse_table(
        ch, db, TABLE, EVENT_TABLE_DDL, EVENT_COLUMNS, rows,
    )
    weekly_count = rebuild_weekly_table()
    print(f'{WEEKLY_TABLE} rebuilt ({weekly_count} student-week rows).')
    return event_count


def main() -> None:
    from _common import bootstrap_django, enable_dataset_tables

    parser = argparse.ArgumentParser(description='Build the student weekly event table.')
    parser.add_argument('--skip-table-enable', action='store_true',
                        help='Do not add the table to ProjectConfig.enabled_dataset_tables.')
    args = parser.parse_args()

    project_dir = bootstrap_django()
    print(f'Django project: {project_dir}')
    if not args.skip_table_enable:
        enable_dataset_tables([TABLE, WEEKLY_TABLE])
    count = rebuild_table()
    print(f'{TABLE} rebuilt ({count} events).')


if __name__ == '__main__':
    main()
