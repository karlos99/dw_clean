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
    'outcome': 'Outcome',
    'outcome_action': 'Outcome Action',
}

MTSS_ALIASES = {
    'mtss_plan_type': 'plan_type',
    'mtss_area_of_concern': 'area_of_concern',
    'mtss_learning_gap': 'learning_gap',
    'mtss_plan_name': 'plan_name',
    'mtss_support_start_date': 'support_start_date',
    'mtss_support_end_date': 'support_end_date',
    'mtss_case_id': 'case_id',
    'mtss_outcome': 'outcome',
    'mtss_outcome_action': 'outcome_action',
}

MTSS_CATEGORY = {
    'title': 'MTSS (Tier 2)',
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
               string_agg(DISTINCT m.case_id, ', ' ORDER BY m.case_id) FILTER (WHERE m.case_id IS NOT NULL AND m.case_id <> ''),
               string_agg(DISTINCT m.outcome, ', ' ORDER BY m.outcome) FILTER (WHERE m.outcome IS NOT NULL AND m.outcome <> ''),
               string_agg(DISTINCT m.outcome_action, ', ' ORDER BY m.outcome_action) FILTER (WHERE m.outcome_action IS NOT NULL AND m.outcome_action <> '')
        FROM mtss_data m
        WHERE m.ssid = ANY(%s)
        GROUP BY m.ssid, m.school_year
    """

    out: dict[tuple[str, str], dict[str, str]] = {}
    by_ssid: dict[str, dict[str, str]] = {}
    try:
        with connection.cursor() as cur:
            cur.execute(query, [list(ssids)])
            for row in cur.fetchall():
                (
                    ssid, sy, plan_type, area_of_concern, learning_gap, plan_name,
                    start_date, end_date, case_id, outcome, outcome_action,
                ) = row
                record = {
                    'plan_type': plan_type or '',
                    'area_of_concern': area_of_concern or '',
                    'learning_gap': learning_gap or '',
                    'plan_name': plan_name or '',
                    'support_start_date': start_date or '',
                    'support_end_date': end_date or '',
                    'case_id': case_id or '',
                    'outcome': outcome or '',
                    'outcome_action': outcome_action or '',
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


_MTSS_YEAR_MATCH_SQL = """(
    m.school_year = cs.school_year
    OR m.school_year = SUBSTRING(cs.school_year FROM 1 FOR 4)
    OR cs.school_year = SUBSTRING(m.school_year FROM 1 FOR 4)
)"""


def mtss_filter_options_sql(canonical: str, option_where_sql: str, join_sql: str) -> str:
    return f"""
        SELECT DISTINCT m.{canonical} AS v
        FROM mtss_data m
        WHERE NULLIF(BTRIM(m.{canonical}::text), '') IS NOT NULL
          AND EXISTS (
            SELECT 1
            FROM current_student cs
            {join_sql}
            WHERE cs.ssid = m.ssid
              AND ({option_where_sql})
              AND {_MTSS_YEAR_MATCH_SQL}
          )
        ORDER BY v ASC
        LIMIT 50
    """


def mtss_filter_blank_check_sql(canonical: str, option_where_sql: str, join_sql: str) -> str:
    return f"""
        SELECT 1
        FROM current_student cs
        {join_sql}
        WHERE {option_where_sql}
          AND NOT EXISTS (
            SELECT 1 FROM mtss_data m
            WHERE m.ssid = cs.ssid
              AND {_MTSS_YEAR_MATCH_SQL}
              AND NULLIF(TRIM(m.{canonical}::text), '') IS NOT NULL
          )
        LIMIT 1
    """


def _mtss_global_distinct(canonical: str) -> list:
    try:
        with connection.cursor() as cur:
            cur.execute(
                f"""
                SELECT DISTINCT {canonical} AS v
                FROM mtss_data
                WHERE NULLIF(BTRIM({canonical}::text), '') IS NOT NULL
                ORDER BY v ASC
                LIMIT 50
                """
            )
            return [row[0] for row in cur.fetchall() if row[0] is not None and str(row[0]).strip()]
    except Exception as e:
        logger.warning("MTSS global filter options failed for %s: %s", canonical, e)
        return []


def fetch_mtss_filter_options(
    canonical: str,
    option_where_sql: str,
    join_sql: str,
    params,
) -> list:
    """Load distinct MTSS facet values for the filtered student population (PostgreSQL)."""
    values: list = []
    try:
        with connection.cursor() as cur:
            cur.execute(
                mtss_filter_options_sql(canonical, option_where_sql, join_sql),
                params,
            )
            for row in cur.fetchall():
                val = row[0]
                if val is None:
                    val = ""
                if val not in values:
                    values.append(val)
        with connection.cursor() as cur:
            cur.execute(
                mtss_filter_blank_check_sql(canonical, option_where_sql, join_sql),
                params,
            )
            if cur.fetchone() and "" not in values:
                values.insert(0, "")
    except Exception as e:
        logger.exception("MTSS filter options failed for %s", canonical)
        values = []

    if not values:
        values = _mtss_global_distinct(canonical)
        if values and "" not in values:
            values.insert(0, "")
    return values


def mirror_mtss_filter_options(filter_options: dict, selected_mtss_cols: list[str]) -> None:
    """Ensure filter_options keys cover MTSS column aliases (plan_type, mtss_plan_type, …)."""
    for col in selected_mtss_cols:
        canonical = canonical_mtss_column(col)
        if not canonical:
            continue
        vals = filter_options.get(col)
        if vals is None:
            vals = filter_options.get(canonical)
        if vals is None:
            continue
        filter_options[col] = vals
        filter_options[canonical] = vals
        for alias, can in MTSS_ALIASES.items():
            if can == canonical:
                filter_options[alias] = vals


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
