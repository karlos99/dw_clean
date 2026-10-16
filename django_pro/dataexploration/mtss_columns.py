"""MTSS (Multi-Tiered System of Supports) column definitions, data fetching, and helpers for Data Explorer."""
from __future__ import annotations

import logging
from django.core.cache import cache
from django.db import connection

logger = logging.getLogger(__name__)

MTSS_DEFINITIONS = {
    'plan_type': 'Plan Type',
    'area_of_concern': 'Area of Concern',
    'learning_gap': 'Learning Gap',
    'plan_name': 'Plan Name',
    'support_start_date': 'Support Start Date',
    'support_end_date': 'Support End Date',
    'case_id': 'Case ID',
}

MTSS_ALIASES = {
    'mtss_plan_type': 'plan_type',
    'mtss_area_of_concern': 'area_of_concern',
    'mtss_learning_gap': 'learning_gap',
    'mtss_plan_name': 'plan_name',
    'mtss_support_start_date': 'support_start_date',
    'mtss_support_end_date': 'support_end_date',
    'mtss_case_id': 'case_id',
}

MTSS_CATEGORY = {
    'title': 'MTSS',
    'columns': list(MTSS_DEFINITIONS.keys()),
}

MTSS_ALL_KEYS = frozenset(MTSS_DEFINITIONS.keys()) | frozenset(MTSS_ALIASES.keys())


def is_mtss_column(col: str | None) -> bool:
    """Return True if col is an MTSS column or alias."""
    if not col or not isinstance(col, str):
        return False
    return col in MTSS_ALL_KEYS


def canonical_mtss_column(col: str | None) -> str | None:
    """Return the canonical MTSS column name or None."""
    if not col or not isinstance(col, str):
        return None
    if col in MTSS_DEFINITIONS:
        return col
    return MTSS_ALIASES.get(col)


def normalize_mtss_school_year(sy: str | None) -> str:
    """Normalize 4-digit or 9-digit school year to 9-digit format (YYYY-YYYY)."""
    if not sy:
        return ''
    sy_str = str(sy).strip()
    if len(sy_str) == 4 and sy_str.isdigit():
        return f"{sy_str}-{int(sy_str) + 1}"
    return sy_str


def get_mtss_school_years() -> list[str]:
    """
    Return list of distinct school years for which MTSS has data (YYYY-YYYY format),
    cached in Redis for fast repeated access.
    """
    cache_key = "dataexp:mtss_school_years:v1"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    years: set[str] = set()
    try:
        with connection.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT school_year FROM mtss_data "
                "WHERE school_year IS NOT NULL AND school_year <> ''"
            )
            for (row_sy,) in cur.fetchall():
                norm = normalize_mtss_school_year(row_sy)
                if norm:
                    years.add(norm)
    except Exception as e:
        logger.warning("Failed to fetch MTSS school years: %s", e)

    result = sorted(years, reverse=True)
    cache.set(cache_key, result, 300)
    return result


def fetch_mtss_data_for_students(students: list[dict]) -> tuple[dict[tuple[str, str], dict[str, str]], dict[str, dict[str, str]]]:
    """
    Bulk fetch MTSS plan data for a list of student dicts.
    Returns:
      (data_by_key, data_by_ssid)
      - data_by_key: mapping (ssid, school_year) -> {field: aggregated_value}
      - data_by_ssid: mapping ssid -> latest {field: aggregated_value}
    """
    ssids = {
        str(s.get('ssid') or '').strip()
        for s in students
        if str(s.get('ssid') or '').strip()
    }
    if not ssids:
        return {}, {}

    query = """
        SELECT m.ssid, m.school_year,
               string_agg(DISTINCT m.plan_type, ', ' ORDER BY m.plan_type) FILTER (WHERE m.plan_type IS NOT NULL AND m.plan_type <> ''),
               string_agg(DISTINCT m.area_of_concern, ', ' ORDER BY m.area_of_concern) FILTER (WHERE m.area_of_concern IS NOT NULL AND m.area_of_concern <> ''),
               string_agg(DISTINCT m.learning_gap, ', ' ORDER BY m.learning_gap) FILTER (WHERE m.learning_gap IS NOT NULL AND m.learning_gap <> ''),
               string_agg(DISTINCT m.plan_name, ', ' ORDER BY m.plan_name) FILTER (WHERE m.plan_name IS NOT NULL AND m.plan_name <> ''),
               string_agg(DISTINCT m.support_start_date, ', ' ORDER BY m.support_start_date) FILTER (WHERE m.support_start_date IS NOT NULL AND m.support_start_date <> ''),
               string_agg(DISTINCT m.support_end_date, ', ' ORDER BY m.support_end_date) FILTER (WHERE m.support_end_date IS NOT NULL AND m.support_end_date <> ''),
               string_agg(DISTINCT m.case_id, ', ' ORDER BY m.case_id) FILTER (WHERE m.case_id IS NOT NULL AND m.case_id <> '')
        FROM mtss_data m
        WHERE m.ssid = ANY(%s)
        GROUP BY m.ssid, m.school_year
    """

    out: dict[tuple[str, str], dict[str, str]] = {}
    by_ssid: dict[str, dict[str, str]] = {}
    try:
        with connection.cursor() as cur:
            cur.execute(query, [list(ssids)])
            for ssid, sy, plan_type, area_of_concern, learning_gap, plan_name, start_date, end_date, case_id in cur.fetchall():
                record = {
                    'plan_type': plan_type or '',
                    'area_of_concern': area_of_concern or '',
                    'learning_gap': learning_gap or '',
                    'plan_name': plan_name or '',
                    'support_start_date': start_date or '',
                    'support_end_date': end_date or '',
                    'case_id': case_id or '',
                }
                ssid_str = str(ssid).strip()
                sy_str = str(sy).strip()
                out[(ssid_str, sy_str)] = record
                by_ssid[ssid_str] = record

                # Also key by alternate school year representations
                if len(sy_str) == 4 and sy_str.isdigit():
                    out[(ssid_str, f"{sy_str}-{int(sy_str) + 1}")] = record
                elif len(sy_str) == 9 and '-' in sy_str:
                    out[(ssid_str, sy_str[:4])] = record
    except Exception as e:
        logger.warning("Failed to fetch MTSS data for students: %s", e)

    return out, by_ssid


def apply_mtss_columns(
    students: list[dict],
    selected_cols: list[str],
    active_school_year: str | list | None = None,
) -> None:
    """Fill selected MTSS columns on student dicts in place."""
    wanted = [c for c in selected_cols if is_mtss_column(c)]
    if not wanted or not students:
        return

    data, by_ssid = fetch_mtss_data_for_students(students)

    active_sy_set = set()
    if active_school_year:
        if isinstance(active_school_year, (list, tuple, set)):
            for y in active_school_year:
                s = str(y).strip()
                if s:
                    active_sy_set.add(s)
                    active_sy_set.add(s[:4])
                    norm = normalize_mtss_school_year(s)
                    if norm:
                        active_sy_set.add(norm)
        else:
            s = str(active_school_year).strip()
            if s:
                active_sy_set.add(s)
                active_sy_set.add(s[:4])
                norm = normalize_mtss_school_year(s)
                if norm:
                    active_sy_set.add(norm)

    for student in students:
        ssid = str(student.get('ssid') or '').strip()
        sy = str(student.get('school_year') or '').strip()

        values = None
        if sy:
            values = data.get((ssid, sy)) or data.get((ssid, sy[:4]))
        if not values and active_sy_set:
            for y in active_sy_set:
                values = data.get((ssid, y))
                if values:
                    break
        if not values:
            values = by_ssid.get(ssid) or {}

        for col in wanted:
            canonical = canonical_mtss_column(col)
            val = values.get(canonical, '') if canonical else ''
            student[col] = val if val else '—'
