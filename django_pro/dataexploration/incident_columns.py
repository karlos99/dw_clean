"""Student discipline incidents: parsing, counts, and Data Explorer column enrichment."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from django.db import connection

logger = logging.getLogger(__name__)

INCIDENT_DEFINITIONS = {
    'incidents_major': 'Major Incidents',
    'incidents_minor': 'Minor Incidents',
    'suspension_days': 'Suspension Days',
}

INCIDENT_ALL_KEYS = frozenset(INCIDENT_DEFINITIONS.keys())


def incident_severity_for_column(col: str | None) -> str | None:
    if col == 'incidents_major':
        return 'Major'
    if col == 'incidents_minor':
        return 'Minor'
    return None


def incident_count_sql_expr(severity: str, _student_alias: str = 'cs') -> str:
    """Incident count expression; requires LEFT JOIN student_incidents AS _si_incident."""
    from .incident_pg_sql import incident_count_ch_filter_expr

    return incident_count_ch_filter_expr(severity)


def incident_count_join_order_expr(severity: str, alias: str = '_si_sort') -> str:
    from .incident_pg_sql import incident_count_ch_join_order_expr

    return incident_count_ch_join_order_expr(severity, alias)


def is_incident_column(col: str | None) -> bool:
    if not col or not isinstance(col, str):
        return False
    return col in INCIDENT_ALL_KEYS


def parse_incidents_payload(raw: Any) -> dict | None:
    """Parse incidents JSON from DB (string or dict)."""
    if raw is None or raw == '':
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _count_for_severity(payload: dict, severity: str) -> int:
    block = payload.get(severity)
    if not isinstance(block, dict):
        return 0
    count = block.get('count')
    if count is not None:
        try:
            return int(count)
        except (TypeError, ValueError):
            pass
    incidents = block.get('incidents')
    if isinstance(incidents, list):
        return len(incidents)
    return 0


def incident_counts(payload: dict | None) -> tuple[int, int]:
    """Return (major_count, minor_count) from parsed payload."""
    if not payload:
        return 0, 0
    return _count_for_severity(payload, 'Major'), _count_for_severity(payload, 'Minor')


_SUSPENSION_DAY_KEY_ALIASES = frozenset({
    'suspension_days',
    'suspensiondays',
})


def _normalize_json_key(key: str) -> str:
    return (key or '').lower().replace('_', '')


def _coerce_nonneg_number(raw: Any) -> int | float | None:
    if raw is None or raw == '':
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        n = float(raw)
        if n < 0:
            return None
        return int(n) if n == int(n) else n
    try:
        n = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return int(n) if n == int(n) else n


def _scalar_suspension_days_from_mapping(mapping: dict) -> int | float | None:
    for key, value in mapping.items():
        if _normalize_json_key(key) in _SUSPENSION_DAY_KEY_ALIASES:
            return _coerce_nonneg_number(value)
    return None


def _duration_from_disposition(disposition: dict) -> int | float | None:
    for key in ('duration_days', 'Duration_Days', 'days', 'Days'):
        if key in disposition:
            return _coerce_nonneg_number(disposition[key])
    for key, value in disposition.items():
        if _normalize_json_key(key) in ('durationdays', 'days'):
            return _coerce_nonneg_number(value)
    return None


def _disposition_counts_as_suspension(label: str) -> bool:
    text = (label or '').lower()
    if not text:
        return False
    markers = (
        'suspend',
        'suspension',
        'oss',
        'iss',
        'out-of-school',
        'out of school',
        'in-school suspension',
        'in school suspension',
    )
    return any(m in text for m in markers)


def _sum_disposition_duration_days(payload: dict, *, suspension_only: bool) -> int | float:
    total = 0.0
    for severity in ('Major', 'Minor'):
        block = payload.get(severity)
        if not isinstance(block, dict):
            continue
        incidents = block.get('incidents')
        if not isinstance(incidents, list):
            continue
        for item in incidents:
            if not isinstance(item, dict):
                continue
            dispositions = item.get('dispositions')
            if not isinstance(dispositions, list):
                continue
            for disposition in dispositions:
                if not isinstance(disposition, dict):
                    continue
                label = (
                    disposition.get('disposition')
                    or disposition.get('Disposition')
                    or ''
                )
                if suspension_only and not _disposition_counts_as_suspension(str(label)):
                    continue
                days = _duration_from_disposition(disposition)
                if days is not None:
                    total += float(days)
    if total <= 0:
        return 0
    return int(total) if total == int(total) else total


def suspension_days_from_payload(payload: dict | None) -> int | float | None:
    """
    Suspension day total from incidents JSON.
    None when there is no incident row; otherwise 0+ from explicit fields or dispositions.
    """
    if not payload:
        return None

    root = _scalar_suspension_days_from_mapping(payload)
    if root is not None:
        return root

    block_total = 0.0
    block_found = False
    for severity in ('Major', 'Minor'):
        block = payload.get(severity)
        if not isinstance(block, dict):
            continue
        block_val = _scalar_suspension_days_from_mapping(block)
        if block_val is not None:
            block_total += float(block_val)
            block_found = True
    if block_found:
        return int(block_total) if block_total == int(block_total) else block_total

    from_dispositions = _sum_disposition_duration_days(payload, suspension_only=True)
    if from_dispositions:
        return from_dispositions

    return _sum_disposition_duration_days(payload, suspension_only=False)


def incident_value_sql_expr(
    col: str,
    *,
    backend: str = 'ch',
    student_alias: str = 'cs',
    join_alias: str = '_si_incident',
) -> str | None:
    """SQL expression for filter/sort on incident-derived numeric columns."""
    from .incident_pg_sql import (
        incident_count_filter_expr,
        incident_count_sql_expr,
        suspension_days_filter_expr,
    )

    sev = incident_severity_for_column(col)
    if sev:
        if backend == 'pg':
            return incident_count_sql_expr(sev, student_alias)
        return incident_count_filter_expr(sev, backend='ch', join_alias=join_alias)
    if col == 'suspension_days':
        return suspension_days_filter_expr(
            backend=backend,
            student_alias=student_alias,
            join_alias=join_alias,
        )
    return None


def _join_violations(violations: Any) -> str:
    if not isinstance(violations, list):
        return ''
    parts = []
    for v in violations:
        if isinstance(v, dict):
            label = v.get('violation') or ''
            if label:
                parts.append(str(label).strip())
        elif v:
            parts.append(str(v).strip())
    return '; '.join(parts)


def _join_ed_codes(violations: Any) -> str:
    if not isinstance(violations, list):
        return ''
    parts = []
    seen = set()
    for v in violations:
        if not isinstance(v, dict):
            continue
        for key in ('ed_code', 'ED_Code', 'EdCode', 'edcode'):
            code = v.get(key)
            if code is None:
                continue
            text = str(code).strip()
            if text and text not in seen:
                seen.add(text)
                parts.append(text)
    return '; '.join(parts)


def _join_dispositions(dispositions: Any) -> str:
    if not isinstance(dispositions, list):
        return ''
    parts = []
    for d in dispositions:
        if not isinstance(d, dict):
            continue
        label = (d.get('disposition') or '').strip()
        if not label:
            continue
        days = d.get('duration_days')
        if days is not None:
            try:
                days_f = float(days)
                if days_f == int(days_f):
                    label = f"{label} ({int(days_f)} day{'s' if days_f != 1 else ''})"
                else:
                    label = f"{label} ({days_f} days)"
            except (TypeError, ValueError):
                pass
        parts.append(label)
    return '; '.join(parts)


def _first_nonempty_str(item: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        val = item.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ''


def _incident_review_status(item: dict) -> str:
    """Review status from incident JSON (several key spellings in source data)."""
    return _first_nonempty_str(
        item,
        (
            'review_status',
            'Review_Status',
            'ReviewStatus',
            'review',
            'Review',
            'Review status',
            'Review Status',
        ),
    )


def _incident_description(item: dict) -> str:
    return _first_nonempty_str(
        item,
        (
            'description',
            'Description',
            'incident_description',
            'Incident_Description',
            'IncidentDescription',
            'incident_desc',
            'narrative',
            'Narrative',
        ),
    )


def format_incident_date_display(raw: str | None) -> str:
    """Format incident date for display as MM/DD/YYYY."""
    text = (raw or '').strip()
    if not text:
        return ''
    chunk = text[:10]
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(chunk, fmt).strftime('%m/%d/%Y')
        except ValueError:
            continue
    return text


def incident_suspension_days_for_item(item: dict) -> int | float:
    """Suspension day total for one incident object (dispositions + explicit fields)."""
    if not isinstance(item, dict):
        return 0
    total = 0.0
    found = False
    dispositions = item.get('dispositions')
    if isinstance(dispositions, list):
        for disposition in dispositions:
            if not isinstance(disposition, dict):
                continue
            label = (
                disposition.get('disposition')
                or disposition.get('Disposition')
                or ''
            )
            if not _disposition_counts_as_suspension(label):
                continue
            days = _duration_from_disposition(disposition)
            if days is not None:
                total += days
                found = True
    for key, value in item.items():
        if _normalize_json_key(key) in _SUSPENSION_DAY_KEY_ALIASES:
            n = _coerce_nonneg_number(value)
            if n is not None:
                return n
    if found:
        return int(total) if total == int(total) else total
    return 0


def normalize_incident_record(severity: str, item: dict) -> dict:
    """Flatten one incident object for templates."""
    date_raw = (item.get('date') or '').strip()
    return {
        'severity': severity,
        'date': date_raw,
        'date_display': format_incident_date_display(date_raw),
        'school': (item.get('school') or '').strip(),
        'location': (item.get('location') or '').strip(),
        'motivation': (item.get('motivation') or '').strip(),
        'incident_id': item.get('incident_id'),
        'referring_staff': (item.get('referring_staff') or '').strip(),
        'review_status': _incident_review_status(item),
        'description': _incident_description(item),
        'violations': _join_violations(item.get('violations')),
        'ed_codes': _join_ed_codes(item.get('violations'))
        or _first_nonempty_str(item, ('ed_code', 'ED_Code', 'EdCode', 'edcode')),
        'dispositions': _join_dispositions(item.get('dispositions')),
    }


def incidents_list_for_severity(payload: dict | None, severity: str) -> list[dict]:
    if not payload:
        return []
    block = payload.get(severity)
    if not isinstance(block, dict):
        return []
    raw_list = block.get('incidents')
    if not isinstance(raw_list, list):
        return []
    out = []
    for item in raw_list:
        if isinstance(item, dict):
            out.append(normalize_incident_record(severity, item))
    out.sort(key=lambda r: r.get('date') or '', reverse=True)
    return out


def fetch_incidents_for_keys(stu_annual_keys: list[str]) -> dict[str, dict | None]:
    """Bulk load incidents JSON keyed by stu_annual_key (PostgreSQL is source of truth)."""
    keys = [str(k).strip() for k in stu_annual_keys if k]
    if not keys:
        return {}

    out: dict[str, dict | None] = {}

    def _ingest_rows(rows: list[tuple]) -> None:
        for row in rows:
            key = str(row[0]).strip()
            if key:
                out[key] = parse_incidents_payload(row[1])

    try:
        from .incident_pg_sql import fetch_incidents_by_keys_pg_sql

        with connection.cursor() as cur:
            cur.execute(fetch_incidents_by_keys_pg_sql(), [keys])
            _ingest_rows(list(cur.fetchall()))
    except Exception as e:
        logger.warning("Failed to fetch student incidents from PostgreSQL: %s", e)

    missing = [k for k in keys if k not in out]
    if not missing:
        return out

    ch_sql = """
        SELECT stu_annual_key, incidents
        FROM student_incidents
        WHERE stu_annual_key = ANY(%s)
    """
    try:
        from clickhouse_utils.executor import QueryExecutor

        cur = QueryExecutor(analytics=False).cursor()
        cur.execute(ch_sql, [missing])
        _ingest_rows(list(cur.fetchall()))
    except Exception as e:
        logger.warning("Failed to fetch student incidents from ClickHouse fallback: %s", e)

    return out


def fetch_incidents_by_ssid(ssid: str) -> list[tuple[str, str, dict | None]]:
    """
    All incident rows for one SSID with school_year from current_student.
    Returns list of (school_year, stu_annual_key, parsed_payload).
    """
    ssid = str(ssid or '').strip()
    if not ssid:
        return []

    from .incident_pg_sql import fetch_incidents_by_ssid_pg_sql

    try:
        with connection.cursor() as cur:
            cur.execute(fetch_incidents_by_ssid_pg_sql(), [ssid])
            rows = cur.fetchall()
    except Exception as e:
        logger.warning("Failed to fetch incidents for ssid %s: %s", ssid, e)
        return []

    result = []
    for sy, key, raw in rows:
        result.append((str(sy or '').strip(), str(key).strip(), parse_incidents_payload(raw)))
    return result


def combine_incidents_for_year(payload: dict | None) -> list[dict]:
    """Major + minor incidents for one school year, newest first."""
    combined = (
        incidents_list_for_severity(payload, 'Major')
        + incidents_list_for_severity(payload, 'Minor')
    )
    combined.sort(
        key=lambda r: (r.get('date') or '', str(r.get('incident_id') or '')),
        reverse=True,
    )
    return combined


def build_student_incidents_summary(ssid: str) -> dict:
    """Multi-year structure for the student summary Behavior tab."""
    rows = fetch_incidents_by_ssid(ssid)
    years = []
    total = 0
    for school_year, _key, payload in rows:
        major, minor = incident_counts(payload)
        if major == 0 and minor == 0 and not payload:
            continue
        total += major + minor
        sd = suspension_days_from_payload(payload)
        years.append({
            'school_year': school_year,
            'major_count': major,
            'minor_count': minor,
            'suspension_days': sd if sd is not None else 0,
            'major_incidents': incidents_list_for_severity(payload, 'Major'),
            'minor_incidents': incidents_list_for_severity(payload, 'Minor'),
            'all_incidents': combine_incidents_for_year(payload),
        })

    return {
        'has_any': bool(years),
        'total_count': total,
        'years': years,
    }


def apply_incident_columns(
    students: list[dict],
    selected_cols: list[str],
    active_school_year: str | list | None = None,
) -> None:
    """Fill incident-derived columns on student dicts in place."""
    wanted = [c for c in selected_cols if is_incident_column(c)]
    if not wanted or not students:
        return

    keys = [str(s.get('stu_annual_key') or '').strip() for s in students]
    by_key = fetch_incidents_for_keys(keys)

    for student in students:
        key = str(student.get('stu_annual_key') or '').strip()
        payload = by_key.get(key) if key else None
        if payload is None:
            for col in wanted:
                student[col] = 0
            continue
        major, minor = incident_counts(payload)
        suspension_days = suspension_days_from_payload(payload)
        for col in wanted:
            if col == 'incidents_major':
                student[col] = major
            elif col == 'incidents_minor':
                student[col] = minor
            elif col == 'suspension_days':
                student[col] = suspension_days if suspension_days is not None else 0
