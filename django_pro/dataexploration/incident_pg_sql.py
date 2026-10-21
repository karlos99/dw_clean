"""PostgreSQL SQL fragments for student_incidents (PG table may omit stu_annual_key)."""

from __future__ import annotations

from clickhouse_utils.student_incidents_sync import (
    _incidents_text_expr,
    current_student_join_sql,
    pg_student_incidents_columns,
)


def incident_match_predicate(si_alias: str, cs_alias: str) -> str:
    """WHERE fragment: one student_incidents row for a current_student row."""
    cols = pg_student_incidents_columns()
    if 'stu_annual_key' in cols:
        return f"{si_alias}.stu_annual_key = {cs_alias}.stu_annual_key"
    return current_student_join_sql(si_alias, cs_alias)


def _pg_incidents_jsonb(column_ref: str) -> str:
    """Normalize json/jsonb/text incidents column to jsonb for extraction."""
    return f"NULLIF(trim(COALESCE({column_ref}::text, '')), '')::jsonb"


def incident_count_sql_expr(severity: str, student_alias: str = 'cs') -> str:
    """PostgreSQL json count subquery (MTSS-mixed reads; matches PG join keys)."""
    cols = pg_student_incidents_columns()
    match = incident_match_predicate('si', student_alias)
    if 'incidents' not in cols:
        return '0'
    payload = _pg_incidents_jsonb('si.incidents')
    return (
        f"COALESCE(("
        f"SELECT ({payload}->'{severity}'->>'count')::int "
        f"FROM student_incidents si WHERE {match}"
        f"), 0)"
    )


def incident_count_ch_filter_expr(severity: str, join_alias: str = '_si_incident') -> str:
    """ClickHouse incident count from a LEFT JOIN on student_incidents (no correlated subquery)."""
    return incident_count_ch_join_order_expr(severity, join_alias)


def incident_count_ch_join_order_expr(severity: str, alias: str = '_si_sort') -> str:
    return (
        f"toInt64OrZero(JSONExtractString(assumeNotNull({alias}.incidents), '{severity}', 'count'))"
    )


def incident_count_pg_join_order_expr(severity: str, alias: str = '_si_incident') -> str:
    payload = _pg_incidents_jsonb(f'{alias}.incidents')
    return f"COALESCE(({payload}->'{severity}'->>'count')::int, 0)"


def incident_count_filter_expr(
    severity: str,
    *,
    backend: str = 'ch',
    join_alias: str = '_si_incident',
) -> str:
    if backend == 'pg':
        return incident_count_pg_join_order_expr(severity, join_alias)
    return incident_count_ch_join_order_expr(severity, join_alias)


def _pg_suspension_days_json_expr(payload: str) -> str:
    """Best-effort suspension day total from jsonb (explicit fields; not disposition rollups)."""
    root = (
        f"COALESCE("
        f"NULLIF(trim(({payload})->>'suspension_days'), '')::numeric, "
        f"NULLIF(trim(({payload})->>'Suspension_Days'), '')::numeric, "
        f"NULLIF(trim(({payload})->>'SuspensionDays'), '')::numeric"
        f")"
    )
    major = f"COALESCE(NULLIF(trim(({payload})->'Major'->>'suspension_days'), '')::numeric, 0)"
    minor = f"COALESCE(NULLIF(trim(({payload})->'Minor'->>'suspension_days'), '')::numeric, 0)"
    major_alt = f"COALESCE(NULLIF(trim(({payload})->'Major'->>'Suspension_Days'), '')::numeric, 0)"
    minor_alt = f"COALESCE(NULLIF(trim(({payload})->'Minor'->>'Suspension_Days'), '')::numeric, 0)"
    return (
        f"CASE WHEN {root} IS NOT NULL AND {root} > 0 THEN {root} "
        f"ELSE ({major} + {minor} + {major_alt} + {minor_alt}) END"
    )


def suspension_days_sql_expr(student_alias: str = 'cs') -> str:
    """PostgreSQL subquery: suspension days from incidents JSON."""
    cols = pg_student_incidents_columns()
    match = incident_match_predicate('si', student_alias)
    if 'incidents' not in cols:
        return '0'
    payload = _pg_incidents_jsonb('si.incidents')
    inner = _pg_suspension_days_json_expr(payload)
    return f"COALESCE((SELECT {inner} FROM student_incidents si WHERE {match}), 0)"


def suspension_days_ch_join_order_expr(alias: str = '_si_incident') -> str:
    inc = f"assumeNotNull({alias}.incidents)"
    root = (
        f"coalesce("
        f"nullIf(JSONExtractString({inc}, 'suspension_days'), ''), "
        f"nullIf(JSONExtractString({inc}, 'Suspension_Days'), ''), "
        f"nullIf(JSONExtractString({inc}, 'SuspensionDays'), '')"
        f")"
    )
    block_sum = (
        f"("
        f"toFloat64OrZero(JSONExtractString({inc}, 'Major', 'suspension_days')) + "
        f"toFloat64OrZero(JSONExtractString({inc}, 'Minor', 'suspension_days')) + "
        f"toFloat64OrZero(JSONExtractString({inc}, 'Major', 'Suspension_Days')) + "
        f"toFloat64OrZero(JSONExtractString({inc}, 'Minor', 'Suspension_Days'))"
        f")"
    )
    return f"if(toFloat64OrZero({root}) > 0, toFloat64OrZero({root}), {block_sum})"


def suspension_days_filter_expr(
    *,
    backend: str = 'ch',
    student_alias: str = 'cs',
    join_alias: str = '_si_incident',
) -> str:
    if backend == 'pg':
        return suspension_days_sql_expr(student_alias)
    return suspension_days_ch_join_order_expr(join_alias)


def incident_join_sql_fragment(existing_join_sql: str = '', *, backend: str = 'ch') -> str:
    """LEFT JOIN student_incidents for ClickHouse reads (skip if already present)."""
    if backend != 'ch' or '_si_incident' in (existing_join_sql or ''):
        return ''
    return (
        " LEFT JOIN student_incidents _si_incident"
        " ON _si_incident.stu_annual_key = cs.stu_annual_key"
    )


def fetch_incidents_by_keys_pg_sql() -> str:
    cols = pg_student_incidents_columns()
    if 'stu_annual_key' in cols:
        return """
            SELECT stu_annual_key, incidents
            FROM student_incidents
            WHERE stu_annual_key = ANY(%s)
        """
    join = current_student_join_sql('si', 'cs')
    return f"""
        SELECT cs.stu_annual_key, {_incidents_text_expr('si')} AS incidents
        FROM student_incidents si
        INNER JOIN current_student cs ON {join}
        WHERE cs.stu_annual_key = ANY(%s)
    """


def fetch_incidents_by_ssid_pg_sql() -> str:
    cols = pg_student_incidents_columns()
    if 'stu_annual_key' in cols:
        return """
            SELECT cs.school_year, si.stu_annual_key, si.incidents
            FROM student_incidents si
            JOIN current_student cs ON cs.stu_annual_key = si.stu_annual_key
            WHERE si.ssid = %s
            ORDER BY cs.school_year DESC
        """
    join = current_student_join_sql('si', 'cs')
    return f"""
        SELECT cs.school_year, cs.stu_annual_key, {_incidents_text_expr('si')} AS incidents
        FROM student_incidents si
        INNER JOIN current_student cs ON {join}
        WHERE cs.ssid = %s
        ORDER BY cs.school_year DESC
    """
