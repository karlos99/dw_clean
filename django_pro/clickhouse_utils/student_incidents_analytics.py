"""Flatten student_incidents JSON into analytics_student_incidents rows (PG + dw_cal)."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db import connection

from clickhouse_utils.student_incidents_sync import (
    _incidents_text_expr,
    pg_student_incidents_columns,
)
from dataexploration.incident_columns import (
    incident_suspension_days_for_item,
    incidents_list_for_severity,
    parse_incidents_payload,
)
from dataexploration.incident_pg_sql import current_student_join_sql

ANALYTICS_STUDENT_INCIDENTS_COLUMNS = [
    'ssid',
    'stu_annual_key',
    'student_id',
    'student_name',
    'grade',
    'school',
    'school_state_id',
    'status',
    'school_year',
    'severity',
    'incident_id',
    'incident_date',
    'violation',
    'ed_code',
    'review_status',
    'description',
    'suspension_days',
    'is_suspended',
    'school_week',
    'week_start_date',
    'week_end_date',
    'week_label',
]


def _format_us_date(iso_date: str) -> str:
    text = (iso_date or '').strip()[:10]
    if not text:
        return ''
    try:
        return datetime.strptime(text, '%Y-%m-%d').strftime('%m/%d/%Y')
    except ValueError:
        return text


def load_dw_cal_lookup() -> dict[tuple[str, str], dict]:
    """Map (school_year_label, YYYY-MM-DD) -> week metadata from PostgreSQL dw_cal."""
    lookup: dict[tuple[str, str], dict] = {}
    try:
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT school_year_label, calendar_date, school_week_number, week_start_monday
                FROM dw_cal
                WHERE calendar_date IS NOT NULL AND trim(calendar_date) != ''
                """
            )
            for sy, cal_date, week_num, week_start in cur.fetchall():
                sy_key = str(sy or '').strip()
                day_key = str(cal_date or '').strip()[:10]
                if not sy_key or not day_key:
                    continue
                week_start_s = str(week_start or '').strip()[:10]
                week_end_s = ''
                if week_start_s:
                    try:
                        start_dt = datetime.strptime(week_start_s, '%Y-%m-%d')
                        week_end_s = (start_dt + timedelta(days=6)).strftime('%Y-%m-%d')
                    except ValueError:
                        week_end_s = week_start_s
                week_int = None
                if week_num is not None and str(week_num).strip().isdigit():
                    week_int = int(str(week_num).strip())
                label = ''
                if week_int is not None and week_start_s:
                    label = (
                        f"Week {week_int} ({_format_us_date(week_start_s)}"
                        f"–{_format_us_date(week_end_s)})"
                    )
                lookup[(sy_key, day_key)] = {
                    'school_week': week_int,
                    'week_start_date': week_start_s or None,
                    'week_end_date': week_end_s or None,
                    'week_label': label or None,
                }
    except Exception:
        return lookup
    return lookup


def _fetch_incidents_with_students_sql() -> str:
    cols = pg_student_incidents_columns()
    if 'stu_annual_key' in cols:
        return """
            SELECT
                cs.ssid,
                cs.stu_annual_key,
                cs.student_id,
                cs.student_name,
                cs.grade,
                cs.school,
                cs.school_state_id,
                cs.status,
                cs.school_year,
                si.incidents
            FROM student_incidents si
            INNER JOIN current_student cs ON cs.stu_annual_key = si.stu_annual_key
            WHERE si.incidents IS NOT NULL
        """
    join = current_student_join_sql('si', 'cs')
    incidents_expr = _incidents_text_expr('si')
    return f"""
        SELECT
            cs.ssid,
            cs.stu_annual_key,
            cs.student_id,
            cs.student_name,
            cs.grade,
            cs.school,
            cs.school_state_id,
            cs.status,
            cs.school_year,
            {incidents_expr} AS incidents
        FROM student_incidents si
        INNER JOIN current_student cs ON {join}
        WHERE {incidents_expr} IS NOT NULL AND trim({incidents_expr}) != ''
    """


def _week_fields_for_date(
    lookup: dict[tuple[str, str], dict],
    school_year: str,
    incident_date: str,
) -> dict:
    day = (incident_date or '').strip()[:10]
    if not day:
        return {
            'school_week': None,
            'week_start_date': None,
            'week_end_date': None,
            'week_label': None,
        }
    meta = lookup.get((str(school_year or '').strip(), day))
    if not meta:
        return {
            'school_week': None,
            'week_start_date': None,
            'week_end_date': None,
            'week_label': None,
        }
    return meta


def build_analytics_student_incidents_rows(
    *,
    dw_cal_lookup: dict[tuple[str, str], dict] | None = None,
) -> list[tuple]:
    """One output row per (student, school_year, severity, incident_id)."""
    lookup = dw_cal_lookup if dw_cal_lookup is not None else load_dw_cal_lookup()
    sql = _fetch_incidents_with_students_sql()
    out: list[tuple] = []

    with connection.cursor() as cur:
        cur.execute(sql)
        for (
            ssid,
            stu_annual_key,
            student_id,
            student_name,
            grade,
            school,
            school_state_id,
            status,
            school_year,
            raw_incidents,
        ) in cur.fetchall():
            payload = parse_incidents_payload(raw_incidents)
            if not payload:
                continue
            base = {
                'ssid': str(ssid or '').strip(),
                'stu_annual_key': str(stu_annual_key or '').strip(),
                'student_id': str(student_id).strip() if student_id is not None else None,
                'student_name': student_name,
                'grade': grade,
                'school': school,
                'school_state_id': school_state_id,
                'status': status,
                'school_year': str(school_year or '').strip(),
            }
            for severity in ('Major', 'Minor'):
                for item in incidents_list_for_severity(payload, severity):
                    if not isinstance(item, dict):
                        continue
                    raw_item = _find_raw_item(payload, severity, item)
                    susp = incident_suspension_days_for_item(raw_item or {})
                    try:
                        susp_f = float(susp or 0)
                    except (TypeError, ValueError):
                        susp_f = 0.0
                    is_suspended = 1 if susp_f > 0 else 0
                    incident_date = (item.get('date') or '').strip()[:10]
                    week_meta = _week_fields_for_date(
                        lookup, base['school_year'], incident_date,
                    )
                    incident_id = item.get('incident_id')
                    out.append((
                        base['ssid'],
                        base['stu_annual_key'],
                        base['student_id'],
                        base['student_name'],
                        base['grade'],
                        base['school'],
                        base['school_state_id'],
                        base['status'],
                        base['school_year'],
                        severity,
                        str(incident_id) if incident_id is not None else '',
                        incident_date or None,
                        item.get('violations') or None,
                        item.get('ed_codes') or None,
                        item.get('review_status') or None,
                        item.get('description') or None,
                        susp_f,
                        is_suspended,
                        week_meta['school_week'],
                        week_meta['week_start_date'],
                        week_meta['week_end_date'],
                        week_meta['week_label'],
                    ))
    return out


def _find_raw_item(payload: dict, severity: str, normalized: dict) -> dict | None:
    block = payload.get(severity)
    if not isinstance(block, dict):
        return None
    raw_list = block.get('incidents')
    if not isinstance(raw_list, list):
        return None
    target_id = normalized.get('incident_id')
    target_date = normalized.get('date')
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        if target_id is not None and item.get('incident_id') == target_id:
            return item
        if target_date and (item.get('date') or '').strip() == target_date:
            return item
    return None
