"""Weekly cumulative attendance from student_attendance absence_dates + dw_cal."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime

from django.db import connection

from clickhouse_utils.student_incidents_analytics import (
    _format_us_date,
    load_dw_cal_lookup,
)
from dataexploration.incident_pg_sql import current_student_join_sql

def _grade_label(raw) -> str:
    g = str(raw or '').strip()
    u = g.upper()
    if u in ('TK',):
        return 'TK'
    if u in ('KN', 'K', 'KG'):
        return 'KN'
    if u in ('US', '250'):
        return 'US.'
    if g.isdigit():
        return str(int(g)).zfill(2)
    return g or 'Unknown'


ANALYTICS_STUDENT_ATTENDANCE_WEEKLY_COLUMNS = [
    'ssid',
    'stu_annual_key',
    'student_name',
    'grade',
    'grade_label',
    'school',
    'school_state_id',
    'status',
    'school_year',
    'school_week',
    'week_start_date',
    'week_end_date',
    'week_label',
    'attendance_rate_week',
]


@dataclass(frozen=True)
class _InstructionalWeek:
    school_week: int
    week_start_date: str
    week_end_date: str
    week_label: str
    cumulative_instructional_days: int


def _parse_date(text: str) -> date | None:
    s = (text or '').strip()[:10]
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y-%m-%d').date()
    except ValueError:
        return None


def _school_year_cutoffs(as_of: date) -> dict[str, date]:
    """Last instructional calendar date per school year, capped at ``as_of`` for in-progress years."""
    cutoffs: dict[str, date] = {}
    try:
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT school_year_label, max(calendar_date::date)
                FROM dw_cal
                WHERE trim(coalesce(is_instructional_day, '')) = '1'
                  AND calendar_date IS NOT NULL
                  AND trim(calendar_date) != ''
                GROUP BY school_year_label
                """
            )
            for sy_raw, max_raw in cur.fetchall():
                sy = str(sy_raw or '').strip()
                max_day = _parse_date(str(max_raw or ''))
                if not sy or max_day is None:
                    continue
                cutoffs[sy] = min(max_day, as_of)
    except Exception:
        pass
    return cutoffs


def load_instructional_weeks_by_school_year(
    *,
    as_of: date | None = None,
) -> dict[str, list[_InstructionalWeek]]:
    """Per school year, instructional weeks with cumulative day counts through each week."""
    as_of = as_of or date.today()
    sy_cutoff = _school_year_cutoffs(as_of)
    weeks: dict[str, list[_InstructionalWeek]] = {}
    try:
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT
                    school_year_label,
                    calendar_date,
                    school_week_number,
                    week_start_monday
                FROM dw_cal
                WHERE trim(coalesce(is_instructional_day, '')) = '1'
                  AND calendar_date IS NOT NULL
                  AND trim(calendar_date) != ''
                ORDER BY school_year_label, calendar_date
                """
            )
            rows = cur.fetchall()
    except Exception:
        return weeks

    by_sy: dict[str, list[tuple[date, int, str]]] = {}
    for sy_raw, cal_raw, week_raw, week_start_raw in rows:
        sy = str(sy_raw or '').strip()
        day = _parse_date(str(cal_raw or ''))
        if not sy or day is None:
            continue
        cutoff = sy_cutoff.get(sy, as_of)
        if day > cutoff:
            continue
        week_int = None
        if week_raw is not None and str(week_raw).strip().isdigit():
            week_int = int(str(week_raw).strip())
        if week_int is None:
            continue
        week_start_s = str(week_start_raw or '').strip()[:10]
        by_sy.setdefault(sy, []).append((day, week_int, week_start_s))

    for sy, day_rows in by_sy.items():
        cum = 0
        current_week: int | None = None
        week_start_s = ''
        week_end: date | None = None
        out: list[_InstructionalWeek] = []
        for day, week_int, wstart in day_rows:
            cum += 1
            if week_int != current_week:
                if current_week is not None and week_end is not None and cum > 0:
                    out.append(_finalize_week(current_week, week_start_s, week_end, cum))
                current_week = week_int
                week_start_s = wstart or day.isoformat()
                week_end = day
            else:
                week_end = day
        if current_week is not None and week_end is not None:
            out.append(_finalize_week(current_week, week_start_s, week_end, cum))
        if out:
            weeks[sy] = out
    return weeks


def _finalize_week(
    week_int: int,
    week_start_s: str,
    week_end: date,
    cum_days: int,
) -> _InstructionalWeek:
    start_s = (week_start_s or week_end.isoformat())[:10]
    end_s = week_end.isoformat()
    week_end_s = end_s
    label = (
        f"Week {week_int} ({_format_us_date(start_s)}"
        f"–{_format_us_date(week_end_s)})"
    )
    return _InstructionalWeek(
        school_week=week_int,
        week_start_date=start_s,
        week_end_date=end_s,
        week_label=label,
        cumulative_instructional_days=cum_days,
    )


def _parse_absence_dates(raw) -> set[str]:
    if raw is None:
        return set()
    if isinstance(raw, list):
        payload = raw
    else:
        text = str(raw).strip()
        if not text:
            return set()
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return set()
    if not isinstance(payload, list):
        return set()
    out: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        day = (item.get('date') or '').strip()[:10]
        if day:
            out.add(day)
    return out


def _fetch_attendance_students_sql() -> str:
    join = current_student_join_sql('sa', 'cs')
    return f"""
        SELECT
            sa.stu_annual_key,
            sa.ssid,
            sa.school_year,
            sa.absence_dates,
            cs.student_name,
            cs.grade,
            cs.school,
            cs.school_state_id,
            cs.status
        FROM student_attendance sa
        INNER JOIN current_student cs ON {join}
        WHERE cs.status = 'Active'
          AND sa.school_year IS NOT NULL
          AND trim(sa.school_year) != ''
    """


def build_analytics_student_attendance_weekly_rows(
    *,
    weeks_by_sy: dict[str, list[_InstructionalWeek]] | None = None,
    dw_cal_lookup: dict[tuple[str, str], dict] | None = None,
    as_of: date | None = None,
) -> list[tuple]:
    """One row per (student, school_year, instructional week) with cumulative attendance %."""
    weeks_by_sy = weeks_by_sy if weeks_by_sy is not None else load_instructional_weeks_by_school_year(as_of=as_of)
    cal_lookup = dw_cal_lookup if dw_cal_lookup is not None else load_dw_cal_lookup()

    out: list[tuple] = []
    sql = _fetch_attendance_students_sql()
    with connection.cursor() as cur:
        cur.execute(sql)
        for (
            stu_annual_key,
            ssid,
            school_year,
            absence_raw,
            student_name,
            grade,
            school,
            school_state_id,
            status,
        ) in cur.fetchall():
            sy = str(school_year or '').strip()
            week_rows = weeks_by_sy.get(sy)
            if not week_rows:
                continue
            absence_dates = _parse_absence_dates(absence_raw)
            absence_on_instr = sorted(
                d for d in absence_dates if (sy, d) in cal_lookup
            )
            abs_idx = 0
            cum_abs = 0
            ssid_s = str(ssid or '').strip()
            sak = str(stu_annual_key or '').strip()
            for wk in week_rows:
                end_s = wk.week_end_date
                while abs_idx < len(absence_on_instr) and absence_on_instr[abs_idx] <= end_s:
                    cum_abs += 1
                    abs_idx += 1
                denom = wk.cumulative_instructional_days
                if denom <= 0:
                    continue
                rate = round(100.0 * (denom - cum_abs) / denom, 2)
                out.append((
                    ssid_s,
                    sak,
                    student_name,
                    grade,
                    _grade_label(grade),
                    school,
                    school_state_id,
                    status,
                    sy,
                    wk.school_week,
                    wk.week_start_date,
                    wk.week_end_date,
                    wk.week_label,
                    rate,
                ))
    return out
