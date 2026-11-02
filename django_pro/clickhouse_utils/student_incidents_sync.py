"""Load PostgreSQL student_incidents into ClickHouse (explicit columns, JSON as text)."""

from __future__ import annotations

import functools

from django.db import connection

# Canonical ClickHouse column order (must match CREATE_STUDENT_INCIDENTS).
STUDENT_INCIDENTS_CH_COLUMNS = (
    'stu_annual_key',
    'ssid',
    'student_id',
    'incidents',
    'updated_at',
)

# Kept for tests; runtime SQL is built by _sync_select_sql().
STUDENT_INCIDENTS_PG_SELECT = """
    SELECT
        cs.stu_annual_key,
        COALESCE(si.ssid, cs.ssid) AS ssid,
        COALESCE(si.student_id, cs.student_id) AS student_id,
        COALESCE(si.incidents::text, '') AS incidents,
        si.updated_at
    FROM student_incidents si
    INNER JOIN current_student cs
        ON cs.ssid = si.ssid AND cs.school_year = si.school_year
"""


@functools.lru_cache(maxsize=1)
def pg_student_incidents_columns() -> frozenset[str]:
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'student_incidents'
                """
            )
            return frozenset(row[0] for row in cursor.fetchall())
    except Exception:
        return frozenset()


def clear_pg_student_incidents_columns_cache() -> None:
    pg_student_incidents_columns.cache_clear()


def _incidents_text_expr(si_alias: str = 'si') -> str:
    return f"COALESCE({si_alias}.incidents::text, '')"


def current_student_join_sql(si_alias: str = 'si', cs_alias: str = 'cs') -> str:
    """JOIN predicate linking student_incidents to current_student."""
    cols = pg_student_incidents_columns()
    if 'stu_annual_key' in cols:
        return f"{cs_alias}.stu_annual_key = {si_alias}.stu_annual_key"
    if 'ssid' in cols and 'school_year' in cols:
        return (
            f"{cs_alias}.ssid = {si_alias}.ssid "
            f"AND {cs_alias}.school_year = {si_alias}.school_year"
        )
    if 'student_id' in cols and 'school_year' in cols:
        return (
            f"{cs_alias}.student_id = {si_alias}.student_id::text "
            f"AND {cs_alias}.school_year = {si_alias}.school_year"
        )
    raise RuntimeError(
        'student_incidents must include stu_annual_key or (ssid + school_year) '
        'or (student_id + school_year) to join current_student'
    )


def _sync_select_sql(changed_only: bool = False) -> str:
    cols = pg_student_incidents_columns()
    if 'incidents' not in cols:
        raise RuntimeError('student_incidents.incidents column is missing in PostgreSQL')

    if 'stu_annual_key' in cols:
        base = f"""
            SELECT
                stu_annual_key,
                ssid,
                student_id,
                {_incidents_text_expr('student_incidents')},
                updated_at
            FROM student_incidents
        """
        if changed_only:
            return base + "\n    WHERE updated_at > %s"
        return base

    join = current_student_join_sql('si', 'cs')
    ssid_expr = 'COALESCE(si.ssid, cs.ssid)' if 'ssid' in cols else 'cs.ssid'
    student_id_expr = (
        'COALESCE(si.student_id, cs.student_id)'
        if 'student_id' in cols
        else 'cs.student_id'
    )
    base = f"""
        SELECT
            cs.stu_annual_key,
            {ssid_expr} AS ssid,
            {student_id_expr} AS student_id,
            {_incidents_text_expr('si')} AS incidents,
            si.updated_at
        FROM student_incidents si
        INNER JOIN current_student cs ON {join}
    """
    if changed_only:
        return base + "\n    WHERE si.updated_at > %s"
    return base


def sanitize_incident_row(row: tuple) -> list:
    """Normalize a PG row for ClickHouse insert."""
    stu_annual_key, ssid, student_id, incidents, updated_at = row
    sid = student_id
    if sid is not None and sid != '':
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            sid = None
    else:
        sid = None
    return [
        '' if stu_annual_key is None else str(stu_annual_key),
        '' if ssid is None else str(ssid),
        sid,
        '' if incidents is None else str(incidents),
        updated_at,
    ]


def fetch_all_student_incidents_from_pg():
    with connection.cursor() as cursor:
        cursor.execute(_sync_select_sql(changed_only=False))
        return list(cursor.fetchall())


def fetch_changed_student_incidents_from_pg(watermark):
    with connection.cursor() as cursor:
        cursor.execute(_sync_select_sql(changed_only=True), [watermark])
        return list(cursor.fetchall())
