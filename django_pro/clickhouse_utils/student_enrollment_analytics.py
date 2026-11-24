"""Enrollment events and weekly actives from PostgreSQL ``enroll_history`` + ``dw_cal``."""

from __future__ import annotations

from bisect import bisect_left
from datetime import date, datetime, timedelta
import json

from django.db import connection

from clickhouse_utils.student_attendance_analytics import load_instructional_weeks_by_school_year
from clickhouse_utils.student_incidents_analytics import load_dw_cal_lookup

ANALYTICS_STUDENT_ENROLLMENT_EVENTS_COLUMNS = [
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

ANALYTICS_STUDENT_ENROLLMENT_WEEKLY_COLUMNS = [
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
    start_year = day.year if day.month >= 7 else day.year - 1
    return f'{start_year}-{start_year + 1}'


def _student_enroll_attrs_map() -> dict[tuple[str, str], tuple]:
    """Migrant + FEP per (ssid, school_year) — one grouped scan, not per-row LATERAL."""
    out: dict[tuple[str, str], tuple] = {}
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT ssid, school_year,
                   max(migrant) AS migrant,
                   max(fep_date) AS fep_date
            FROM student_enroll
            WHERE ssid IS NOT NULL AND trim(ssid) != ''
              AND school_year IS NOT NULL AND trim(school_year) != ''
            GROUP BY ssid, school_year
            """
        )
        for ssid, sy, migrant, fep in cur.fetchall():
            key = (str(ssid or '').strip(), str(sy or '').strip())
            if key[0] and key[1]:
                out[key] = (migrant, fep)
    return out


def _school_group_by_state_id() -> dict[str, str]:
    out: dict[str, str] = {}
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT state_id, max(school_group) AS school_group
            FROM school
            WHERE state_id IS NOT NULL AND trim(state_id) != ''
            GROUP BY state_id
            """
        )
        for state_id, school_group in cur.fetchall():
            sid = str(state_id or '').strip()
            if sid:
                out[sid] = str(school_group or '').strip() or 'Unknown'
    return out


def _student_display_name(first, last, middle, fallback) -> str:
    parts = [str(first or '').strip(), str(middle or '').strip(), str(last or '').strip()]
    parts = [p for p in parts if p]
    if parts:
        return ' '.join(parts)
    return str(fallback or '').strip()


def _coerce_enrollment_json(payload) -> dict:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode('utf-8', errors='replace')
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _segments_from_json(payload, sy: str) -> list[dict]:
    payload = _coerce_enrollment_json(payload)
    if not payload:
        return []
    segments = payload.get('enrollment_segments') or []
    out: list[dict] = []
    for raw in segments:
        if not isinstance(raw, dict):
            continue
        state_id = str(
            raw.get('STATE_SCHOOL_CODE') or raw.get('state_school_code') or ''
        ).strip()
        out.append({
            'segment_seq': raw.get('segment_seq'),
            'school': str(raw.get('school') or '').strip(),
            'state_school_id': state_id,
            'grade': str(raw.get('grade') or '').strip(),
            'enter_date': _parse_date(raw.get('enter_date')),
            'leave_date': _parse_date(raw.get('leave_date')),
            'returned_after_this_exit': bool(raw.get('returned_after_this_exit')),
        })
    out.sort(key=lambda s: (s.get('segment_seq') or 0, s['enter_date'] or date.min))
    return out


def fetch_enroll_history_records() -> list[dict]:
    """One dict per student/school-year with parsed enrollment segments."""
    group_lookup = _school_group_by_state_id()
    enroll_attrs = _student_enroll_attrs_map()
    sql = """
        SELECT
            eh.school_year AS school_year,
            eh.ssid AS ssid,
            eh.first_name,
            eh.middle_name,
            eh.last_name,
            eh.enrollment_status_json,
            cs.stu_annual_key,
            cs.student_name,
            cs.status,
            cs.ell,
            cs.ltel,
            cs.sed,
            cs.sped,
            cs.foster,
            cs.homeless,
            cs.gate,
            cs.in_504,
            cs.dli
        FROM enroll_history eh
        LEFT JOIN current_student cs
            ON cs.ssid = eh.ssid
           AND cs.school_year = eh.school_year
        WHERE eh.ssid IS NOT NULL AND trim(eh.ssid) != ''
          AND eh.school_year IS NOT NULL AND trim(eh.school_year) != ''
          AND lower(trim(coalesce(eh.active_in_year, ''))) IN ('true', 't', '1', 'yes')
          AND coalesce(nullif(trim(eh.days_attended), '')::int, 0) >= 1
        ORDER BY eh.school_year, eh.ssid
    """
    records: list[dict] = []
    with connection.cursor() as cur:
        cur.execute(sql)
        cols = [c[0] for c in cur.description]
        while True:
            chunk = cur.fetchmany(2000)
            if not chunk:
                break
            for row in chunk:
                data = dict(zip(cols, row))
                sy = str(data.get('school_year') or '').strip()
                ssid_s = str(data.get('ssid') or '').strip()
                if not ssid_s or not sy:
                    continue
                payload = _coerce_enrollment_json(data.get('enrollment_status_json'))
                segments = _segments_from_json(payload, sy)
                if not segments:
                    continue
                migrant, fep_date = enroll_attrs.get((ssid_s, sy), (None, None))
                records.append({
                    'school_year': sy,
                    'ssid': ssid_s,
                    'stu_annual_key': (
                        str(data.get('stu_annual_key') or '').strip() or f'{ssid_s}|{sy}'
                    ),
                    'student_name': _student_display_name(
                        data.get('first_name'),
                        data.get('last_name'),
                        data.get('middle_name'),
                        data.get('student_name'),
                    ) or None,
                    'status': str(data.get('status') or payload.get('current_status') or ''),
                    'is_currently_active': bool(payload.get('is_currently_active')),
                    'ell': data.get('ell'),
                    'ltel': data.get('ltel'),
                    'sed': data.get('sed'),
                    'sped': data.get('sped'),
                    'foster': data.get('foster'),
                    'homeless': data.get('homeless'),
                    'migrant': migrant,
                    'gate': data.get('gate'),
                    'in_504': data.get('in_504'),
                    'dli': data.get('dli'),
                    'fep_date': fep_date,
                    'segments': segments,
                    'payload': payload,
                    'group_lookup': group_lookup,
                })
    return records


def _school_group_for_segment(seg: dict, group_lookup: dict[str, str]) -> str:
    sid = str(seg.get('state_school_id') or '').strip()
    return group_lookup.get(sid, 'Unknown')


def _base_from_record(rec: dict, seg: dict) -> dict:
    group_lookup = rec.get('group_lookup') or {}
    grade = seg.get('grade') or ''
    return {
        'ssid': rec['ssid'],
        'stu_annual_key': rec['stu_annual_key'],
        'student_name': rec['student_name'],
        'grade': grade,
        'school': seg.get('school') or '',
        'school_state_id': seg.get('state_school_id') or '',
        'school_group': _school_group_for_segment(seg, group_lookup),
        'status': rec['status'],
        'ell': rec['ell'],
        'ltel': rec['ltel'],
        'sed': rec['sed'],
        'sped': rec['sped'],
        'foster': rec['foster'],
        'homeless': rec['homeless'],
        'migrant': rec['migrant'],
        'gate': rec['gate'],
        'in_504': rec['in_504'],
        'dli': rec['dli'],
        'fep_date': rec['fep_date'],
    }


def _record_cutoff(rec: dict) -> date | None:
    dates: list[date] = []
    for seg in rec.get('segments') or []:
        for key in ('enter_date', 'leave_date'):
            d = seg.get(key)
            if d:
                dates.append(d)
    latest = _parse_date((rec.get('payload') or {}).get('latest_leave_date'))
    if latest:
        dates.append(latest)
    return max(dates) if dates else None


def _prepare_parsed_weeks() -> tuple[dict[str, list[tuple]], dict[str, dict[int, object]]]:
    """school_year -> sorted week spans and school_week -> week object."""
    weeks_by_sy = load_instructional_weeks_by_school_year()
    parsed: dict[str, list[tuple]] = {}
    by_num: dict[str, dict[int, object]] = {}
    for sy, weeks in weeks_by_sy.items():
        items: list[tuple] = []
        index: dict[int, object] = {}
        for wk in weeks:
            ws = _parse_date(wk.week_start_date)
            we = _parse_date(wk.week_end_date)
            if ws and we:
                items.append((wk, ws, we))
                index[wk.school_week] = wk
        items.sort(key=lambda x: x[1])
        if items:
            parsed[sy] = items
            by_num[sy] = index
    return parsed, by_num


def _segments_active_weeks(
    sy_weeks: list[tuple],
    segments: list[dict],
    *,
    cutoff: date | None,
    span_end_default: date,
) -> dict[int, dict]:
    """Map school_week -> segment using enrollment date spans (skip non-overlapping weeks)."""
    if not sy_weeks:
        return {}
    week_starts = [w[1] for w in sy_weeks]
    active: dict[int, dict] = {}
    for seg in segments:
        enter = seg.get('enter_date')
        if not enter:
            continue
        leave = seg.get('leave_date') or span_end_default
        lo = bisect_left(week_starts, enter)
        while lo > 0 and sy_weeks[lo - 1][2] >= enter:
            lo -= 1
        while lo < len(sy_weeks) and sy_weeks[lo][2] < enter:
            lo += 1
        for wk, ws, we in sy_weeks[lo:]:
            if cutoff and ws > cutoff:
                break
            if leave and ws > leave:
                break
            if enter <= we and leave >= ws:
                active[wk.school_week] = seg
    return active


def _event_row(base: dict, event_type: str, event_date: date,
               school_year: str, lookup: dict) -> tuple:
    day = event_date.isoformat()
    meta = lookup.get((school_year, day), {})
    fep = base.get('fep_date')
    fep_s = str(fep).strip()[:10] if fep else None
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
        base['school_group'] or 'Unknown',
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
        fep_s,
    )


def build_enrollment_analytics_rows() -> tuple[list[tuple], list[tuple]]:
    """Build event + weekly rows in one ``enroll_history`` scan."""
    records = fetch_enroll_history_records()
    lookup = load_dw_cal_lookup()
    weeks_parsed, week_by_num = _prepare_parsed_weeks()

    cutoffs: dict[str, date] = {}
    for rec in records:
        sy = rec['school_year']
        end = _record_cutoff(rec)
        if end and (sy not in cutoffs or end > cutoffs[sy]):
            cutoffs[sy] = end

    event_rows: list[tuple] = []
    event_seen: set[tuple] = set()
    weekly_rows: list[tuple] = []
    weekly_seen: set[tuple] = set()

    for rec in records:
        sy = rec['school_year']
        ssid_s = rec['ssid']
        segments = rec.get('segments') or []
        cutoff = cutoffs.get(sy)
        sy_weeks = weeks_parsed.get(sy)
        if not sy_weeks:
            continue

        span_end_default = sy_weeks[-1][2]

        for seg in segments:
            base = _base_from_record(rec, seg)
            entered = seg.get('enter_date')
            if entered:
                start_year = _school_year_start_year(sy)
                if start_year is None or entered.year >= start_year - 1:
                    key = (ssid_s, 'New Enrollment', entered.isoformat())
                    if key not in event_seen:
                        event_seen.add(key)
                        event_rows.append(
                            _event_row(base, 'New Enrollment', entered, sy, lookup))
            left = seg.get('leave_date')
            if left:
                key = (ssid_s, 'Exit', left.isoformat())
                if key not in event_seen:
                    event_seen.add(key)
                    event_rows.append(_event_row(base, 'Exit', left, sy, lookup))

        fep = _parse_date(rec.get('fep_date'))
        if fep and _school_year_for_date(fep) == sy:
            key = (ssid_s, 'Reclassified', fep.isoformat())
            if key not in event_seen:
                event_seen.add(key)
                base = _base_from_record(rec, segments[-1])
                event_rows.append(_event_row(base, 'Reclassified', fep, sy, lookup))

        week_to_seg = _segments_active_weeks(
            sy_weeks,
            segments,
            cutoff=cutoff,
            span_end_default=span_end_default,
        )

        wk_index = week_by_num.get(sy) or {}
        for week_num, seg in week_to_seg.items():
            wk = wk_index.get(week_num)
            if wk is None:
                continue
            key = (ssid_s, sy, week_num)
            if key in weekly_seen:
                continue
            weekly_seen.add(key)
            grade = seg.get('grade') or ''
            base = _base_from_record(rec, seg)
            weekly_rows.append((
                ssid_s,
                rec['stu_annual_key'],
                base['student_name'],
                grade,
                _grade_label(grade),
                base['school'],
                base['school_state_id'],
                base['school_group'],
                base['status'],
                sy,
                week_num,
                wk.week_start_date,
                wk.week_end_date,
                wk.week_label,
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
            ))

    return event_rows, weekly_rows


def build_analytics_student_enrollment_events_rows() -> list[tuple]:
    events, _weekly = build_enrollment_analytics_rows()
    return events


def build_analytics_student_enrollment_weekly_rows() -> list[tuple]:
    """Prefer ``build_enrollment_analytics_rows()`` when building both tables."""
    _events, weekly = build_enrollment_analytics_rows()
    return weekly