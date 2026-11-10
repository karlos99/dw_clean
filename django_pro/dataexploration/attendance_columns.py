"""Student attendance detail for the student summary Attendance tab."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from django.db import connection

from dataexploration.incident_columns import format_incident_date_display

logger = logging.getLogger(__name__)


def _parse_json_list(raw) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, list):
        payload = raw
    else:
        text = str(raw).strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _normalize_event(item: dict, *, include_period: bool = False) -> dict:
    date_raw = (item.get('date') or '').strip()
    code = (item.get('code') or '').strip()
    meaning = (item.get('code_meaning') or code or 'Unknown').strip()
    out = {
        'date': date_raw,
        'date_display': format_incident_date_display(date_raw),
        'code': code,
        'code_meaning': meaning,
    }
    if include_period:
        period = item.get('period')
        try:
            out['period'] = int(period) if period is not None and str(period).strip() != '' else None
        except (TypeError, ValueError):
            out['period'] = None
    return out


def _group_events(events: list[dict], label_key: str = 'code_meaning') -> list[dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    labels: dict[str, str] = {}
    codes: dict[str, str] = {}
    for ev in events:
        label = (ev.get(label_key) or ev.get('code') or 'Unknown').strip() or 'Unknown'
        key = label.lower()
        buckets[key].append(ev)
        labels[key] = label
        if ev.get('code'):
            codes[key] = ev['code']
    groups = []
    for key, items in buckets.items():
        items.sort(key=lambda r: (r.get('date') or ''), reverse=True)
        groups.append({
            'label': labels[key],
            'code': codes.get(key, ''),
            'count': len(items),
            'events': items,
        })
    groups.sort(key=lambda g: (-g['count'], g['label']))
    return groups


def fetch_attendance_by_ssid(ssid: str) -> list[dict]:
    """All ``student_attendance`` rows for an SSID, newest school year first."""
    ssid = str(ssid or '').strip()
    if not ssid:
        return []
    try:
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT
                    school_year,
                    attendance_percent,
                    days_enrolled,
                    absent_fte,
                    tardy_count,
                    absents_percent,
                    absence_dates,
                    tardy_dates
                FROM student_attendance
                WHERE ssid = %s
                  AND school_year IS NOT NULL
                  AND trim(school_year) != ''
                ORDER BY school_year DESC
                """,
                [ssid],
            )
            rows = cur.fetchall()
    except Exception as exc:
        logger.warning('Failed to fetch attendance for ssid %s: %s', ssid, exc)
        return []

    out = []
    for (
        school_year,
        attendance_percent,
        days_enrolled,
        absent_fte,
        tardy_count,
        absents_percent,
        absence_dates,
        tardy_dates,
    ) in rows:
        absences = [_normalize_event(item) for item in _parse_json_list(absence_dates)]
        tardies = [_normalize_event(item, include_period=True) for item in _parse_json_list(tardy_dates)]
        tardies.sort(key=lambda r: (r.get('date') or ''), reverse=True)
        out.append({
            'school_year': str(school_year or '').strip(),
            'attendance_percent': _format_percent(attendance_percent),
            'days_enrolled': _format_int(days_enrolled),
            'absent_fte': _format_decimal(absent_fte),
            'tardy_count': _format_int(tardy_count),
            'absents_percent': _format_percent(absents_percent),
            'absence_groups': _group_events(absences),
            'tardy_events': tardies,
            'absence_count': len(absences),
            'tardy_event_count': len(tardies),
        })
    return out


def _format_percent(raw) -> str:
    if raw is None or str(raw).strip() == '':
        return ''
    try:
        return f'{float(str(raw).strip()):.2f}'
    except (TypeError, ValueError):
        return str(raw).strip()


def _format_int(raw) -> str:
    if raw is None or str(raw).strip() == '':
        return ''
    try:
        return str(int(float(str(raw).strip())))
    except (TypeError, ValueError):
        return str(raw).strip()


def _format_decimal(raw) -> str:
    if raw is None or str(raw).strip() == '':
        return ''
    try:
        return f'{float(str(raw).strip()):.2f}'
    except (TypeError, ValueError):
        return str(raw).strip()


def build_student_attendance_summary(ssid: str) -> dict:
    """Multi-year attendance, absences by type, and tardies for the student summary."""
    years = fetch_attendance_by_ssid(ssid)
    total_absences = sum(y.get('absence_count') or 0 for y in years)
    total_tardies = sum(y.get('tardy_event_count') or 0 for y in years)
    return {
        'has_any': bool(years),
        'years': years,
        'total_absences': total_absences,
        'total_tardies': total_tardies,
        'year_count': len(years),
    }
