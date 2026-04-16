"""
data_loader.py — Execute queries for dataviz charts.

Supports:
  - run_saved_query(query_id, extra_filters, user) → list[dict]
  - run_raw_sql(sql)                               → dict(columns, rows, ...)
"""
import hashlib
import json
import re

from django.core.cache import cache
from django.db import connection


# ── SQL safety guards (copied verbatim from original views.py) ────────────────

_SQL_READONLY_RE = re.compile(
    r'^\s*(select|with|explain|show|describe|desc)\b',
    re.IGNORECASE,
)
_SQL_DANGER_RE = re.compile(
    r'\b(insert|update|delete|drop|truncate|alter|create|replace|call|exec|execute|grant|revoke|copy|vacuum)\b',
    re.IGNORECASE,
)
MAX_SQL_ROWS       = 2000   # safety cap for run_raw_sql (SQL Lab)
MAX_CHART_ROWS     = 50000  # higher cap for chart queries (aggregation needs all rows)


def run_raw_sql(sql: str) -> dict:
    """
    Execute a read-only SQL query inside a BEGIN READ ONLY transaction.
    Returns {'columns', 'col_types', 'rows', 'row_count', 'has_more', 'error'}.
    """
    sql = (sql or '').strip()
    if not sql:
        return {'error': 'No SQL provided'}
    if not _SQL_READONLY_RE.match(sql):
        return {'error': 'Only SELECT / WITH queries are allowed.'}
    if _SQL_DANGER_RE.search(sql):
        return {'error': 'Query contains disallowed keywords.'}

    try:
        with connection.cursor() as cursor:
            cursor.execute("BEGIN READ ONLY")
            try:
                cursor.execute(sql)
                if cursor.description is None:
                    return {'columns': [], 'rows': [], 'row_count': 0, 'has_more': False}
                columns = [col[0] for col in cursor.description]
                raw = cursor.fetchmany(MAX_SQL_ROWS)
                rows = [dict(zip(columns, row)) for row in raw]
                has_more = len(raw) == MAX_SQL_ROWS
            finally:
                cursor.execute("ROLLBACK")

        col_types: dict = {}
        for col in columns:
            for row in rows:
                v = row.get(col)
                if v is not None:
                    col_types[col] = 'number' if isinstance(v, (int, float)) else 'string'
                    break
            col_types.setdefault(col, 'string')

        return {
            'columns': columns, 'col_types': col_types,
            'rows': rows, 'row_count': len(rows), 'has_more': has_more,
        }
    except Exception as exc:
        return {'error': str(exc)}



# All columns that exist directly in the current_student table
_CS_COLUMNS = frozenset([
    'stu_annual_key', 'ssid', 'student_id', 'student_name', 'grade', 'school',
    'school_state_id', 'gender', 'race', 'ell', 'sed', 'foster', 'homeless',
    'sped', 'gate', 'in_504', 'dli', 'atendace_rate', 'suspension_indicator',
    'ltel', 'status', 'school_year',
])

# ── Helpers for identifying & resolving mark / test columns ───────────────────

_SAFE_IDENT_RE = re.compile(r'^[a-zA-Z0-9_]+$')


def _norm_part(v: str) -> str:
    """Normalise a DB value for column-key comparison (spaces/hyphens → underscores)."""
    return (v or '').replace(' ', '_').replace('-', '_')


def _parse_year(col_key: str, prefix: str) -> str | None:
    """Extract school_year from a column key like ``test_2025_2026_...`` or ``mark_2025_2026_...``."""
    s = (col_key or '').replace('-', '_')
    if not s.startswith(prefix):
        return None
    tail = s[len(prefix):]
    if len(tail) >= 10 and tail[4] == '_' and tail[9] == '_':
        return tail[:9].replace('_', '-')
    parts = tail.split('_', 1)
    return parts[0].replace('_', '-') if parts else None


def _build_test_columns_from_db(cursor, selected_test_cols: list[str]) -> tuple[list[dict], dict]:
    """Map ``test_*`` column keys to DB metadata (year, test_name, subject, …).

    Returns ``(infos_list, col_map_dict)``.  Uses a 10-minute cache.
    """
    if not selected_test_cols:
        return [], {}

    cache_key = f"dv_test_cols:{hashlib.md5(json.dumps(sorted(selected_test_cols)).encode()).hexdigest()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    normalized_to_original = {c.replace('-', '_'): c for c in selected_test_cols}
    years = sorted({y for y in (_parse_year(c, 'test_') for c in selected_test_cols) if y})
    if not years:
        return [], {}

    # Check for test_window column
    cursor.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = 'tests' AND column_name = 'test_window'"
    )
    has_test_window = cursor.fetchone() is not None

    window_expr = "COALESCE(t.test_window, '')" if has_test_window else "''"
    cursor.execute(
        f"""
        SELECT DISTINCT r.school_year, t.test_name, t.subject, {window_expr}
        FROM results r
        INNER JOIN tests t ON r.test_group_key = t.test_group_key
        WHERE r.school_year = ANY(%s) AND r.school_year IS NOT NULL AND t.test_name IS NOT NULL
        """,
        [years],
    )

    infos: list[dict] = []
    col_map: dict[str, dict] = {}

    for yr, test_name, subject, test_window in cursor.fetchall():
        yr_norm = _norm_part(yr)
        tn_norm = _norm_part(test_name)
        sub_norm = _norm_part(subject)
        win_norm = _norm_part(test_window).strip('_')
        win_value = (test_window or '').strip() or None

        key_base = f"{yr_norm}_{tn_norm}_{sub_norm}_{win_norm}" if win_norm else f"{yr_norm}_{tn_norm}_{sub_norm}"

        for suffix, metric in (('SS', 'ss'), ('PL', 'pl')):
            norm_key = f"test_{key_base}_{suffix}"
            original_key = normalized_to_original.get(norm_key)
            if not original_key:
                continue
            info = {
                'col_name': original_key,
                'year': yr,
                'test_name': test_name or '',
                'subject': subject or '',
                'test_window': win_value if win_norm else None,
                'metric': metric,
            }
            infos.append(info)
            col_map[original_key] = info
            col_map[original_key.replace('-', '_')] = info

    # Handle other_info-backed columns (test_*_OI_*)
    if any('_OI_' in k for k in normalized_to_original):
        cursor.execute(
            f"""
            SELECT DISTINCT r.school_year, t.test_name, t.subject, {window_expr}, k.key
            FROM results r
            INNER JOIN tests t ON r.test_group_key = t.test_group_key
            CROSS JOIN LATERAL jsonb_object_keys(r.other_info) AS k(key)
            WHERE r.school_year = ANY(%s)
              AND r.school_year IS NOT NULL AND t.test_name IS NOT NULL AND r.other_info IS NOT NULL
            """,
            [years],
        )
        for yr, test_name, subject, test_window, raw_key in cursor.fetchall():
            if not raw_key:
                continue
            yr_norm = _norm_part(yr)
            tn_norm = _norm_part(test_name)
            sub_norm = _norm_part(subject)
            win_norm = _norm_part(test_window).strip('_')
            win_value = (test_window or '').strip() or None
            key_base = f"{yr_norm}_{tn_norm}_{sub_norm}_{win_norm}" if win_norm else f"{yr_norm}_{tn_norm}_{sub_norm}"
            oi_norm = _norm_part(str(raw_key))
            oi_hash = hashlib.md5(str(raw_key).encode()).hexdigest()[:6]
            other_norm = f"test_{key_base}_OI_{oi_norm}_{oi_hash}"
            original_key = normalized_to_original.get(other_norm)
            if not original_key:
                continue
            info = {
                'col_name': original_key,
                'year': yr,
                'test_name': test_name or '',
                'subject': subject or '',
                'test_window': win_value if win_norm else None,
                'metric': 'oi',
                'other_info_key': str(raw_key),
            }
            infos.append(info)
            col_map[original_key] = info
            col_map[original_key.replace('-', '_')] = info

    result = (infos, col_map)
    cache.set(cache_key, result, 600)
    return result


def _build_mark_columns_from_db(cursor, selected_mark_cols: list[str]) -> tuple[list[dict], dict]:
    """Map ``mark_*`` column keys to student-marks metadata.

    Returns ``(infos_list, col_map_dict)``.  Uses a 10-minute cache.
    """
    if not selected_mark_cols:
        return [], {}

    cache_key = f"dv_mark_cols:{hashlib.md5(json.dumps(sorted(selected_mark_cols)).encode()).hexdigest()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    normalized_to_original = {c.replace('-', '_'): c for c in selected_mark_cols}
    years = sorted({y for y in (_parse_year(c, 'mark_') for c in selected_mark_cols) if y})
    if not years:
        return [], {}

    cursor.execute(
        """
        SELECT DISTINCT school_year, subject, course_name, mark_period
        FROM student_marks_list
        WHERE school_year = ANY(%s) AND school_year IS NOT NULL
        """,
        [years],
    )

    infos: list[dict] = []
    col_map: dict[str, dict] = {}

    for year, subject, course_name, mark_period in cursor.fetchall():
        year_norm = _norm_part(year)
        subject_norm = _norm_part(subject)
        course_norm = _norm_part(course_name)
        period_norm = _norm_part(mark_period)
        mark_norm = f"mark_{year_norm}_{subject_norm}_{course_norm}_{period_norm}"

        original_key = normalized_to_original.get(mark_norm)
        if not original_key:
            continue
        info = {
            'col_name': original_key,
            'year': year or '',
            'subject': subject or '',
            'course_name': course_name or '',
            'mark_period': (mark_period or '').strip() or None,
        }
        infos.append(info)
        col_map[original_key] = info
        col_map[original_key.replace('-', '_')] = info

    result = (infos, col_map)
    cache.set(cache_key, result, 600)
    return result


def _fetch_test_data(cursor, test_cols_info: list[dict], student_ids: list[str], students: list[dict]):
    """Bulk-fetch test scores and merge into student dicts (mutates *students* in-place)."""
    if not test_cols_info or not student_ids:
        return
    for info in test_cols_info:
        metric = info['metric']
        window_clause, window_params = '', []
        if info.get('test_window'):
            window_clause = 'AND t.test_window ILIKE %s'
            window_params = [info['test_window']]
        if metric in ('ss', 'pl'):
            select_expr = f'r.{metric}'
            extra_params: list = []
        else:
            select_expr = "(r.other_info ->> %s)"
            extra_params = [info.get('other_info_key', '')]

        cursor.execute(
            f"""
            SELECT r.local_student_id, {select_expr}
            FROM results r
            INNER JOIN tests t ON r.test_group_key = t.test_group_key
            WHERE r.local_student_id = ANY(%s)
              AND r.school_year = %s
              AND t.test_name ILIKE %s
              AND t.subject ILIKE %s
              {window_clause}
            """,
            [*extra_params, student_ids, info['year'],
             f"%{info['test_name']}%", f"%{info['subject']}%", *window_params],
        )
        data = {row[0]: row[1] for row in cursor.fetchall()}
        col_name = info['col_name']
        for s in students:
            s[col_name] = data.get(s.get('student_id'), '\u2014')


def _fetch_mark_data(cursor, mark_cols_info: list[dict], student_keys: list[str], students: list[dict]):
    """Bulk-fetch student marks and merge into student dicts (mutates *students* in-place)."""
    if not mark_cols_info or not student_keys:
        return
    for info in mark_cols_info:
        period_clause, period_params = '', []
        if info.get('mark_period'):
            period_clause = 'AND sm.mark_period ILIKE %s'
            period_params = [info['mark_period']]
        else:
            period_clause = "AND (sm.mark_period IS NULL OR sm.mark_period = '')"

        cursor.execute(
            f"""
            SELECT sm.stu_annual_key, sm.mark
            FROM student_marks_list sm
            WHERE sm.stu_annual_key = ANY(%s)
              AND sm.school_year = %s
              AND sm.subject ILIKE %s
              AND sm.course_name ILIKE %s
              {period_clause}
            """,
            [student_keys, info['year'],
             f"%{info['subject']}%", f"%{info['course_name']}%", *period_params],
        )
        data = {row[0]: row[1] for row in cursor.fetchall()}
        col_name = info['col_name']
        for s in students:
            s[col_name] = data.get(s.get('stu_annual_key'), '\u2014')


# ── Public entry points ──────────────────────────────────────────────────────

def run_saved_query(query_id: int, extra_filters: dict, user, extra_columns: list | None = None) -> dict:
    """
    Execute a ``dataexploration.SavedQuery`` and return rows as a list of dicts.

    ``extra_filters`` are merged on top of the query's saved filters before execution.
    ``extra_columns`` are appended to the query's column list so the drill table
    can request columns beyond what the original chart query included.
    Returns {'rows': [...], 'columns': [...], 'error': str|None}.
    """
    from dataexploration.models import SavedQuery
    from dataexploration.utils import can_user_view_query

    try:
        query = SavedQuery.objects.get(pk=query_id)
    except SavedQuery.DoesNotExist:
        return {'error': f'SavedQuery {query_id} not found', 'rows': [], 'columns': []}

    if not can_user_view_query(user, query):
        return {'error': 'Access denied', 'rows': [], 'columns': []}

    config = dict(query.config or {})

    # Merge extra_filters into the query config filters
    if extra_filters:
        merged = dict(config.get('filters', {}))
        merged.update(extra_filters)
        config['filters'] = merged

    # Append extra columns requested by the drill table
    if extra_columns:
        existing = list(config.get('columns', []))
        existing_set = set(existing)
        for c in extra_columns:
            if c not in existing_set:
                existing.append(c)
                existing_set.add(c)
        config['columns'] = existing

    return _run_saved_query_sql(config)


def run_column_query(selected_columns: list[str], filters: dict | None = None) -> dict:
    """
    Execute a query using directly-selected columns (Column Picker source type).

    Builds a config dict compatible with ``_run_saved_query_sql`` and delegates to it.
    ``selected_columns`` is a list of column keys (base, test_*, mark_*).
    ``filters`` is a dict of {column_key: value/list} filters.
    Returns {'rows': [...], 'columns': [...], 'error': str|None}.
    """
    if not selected_columns:
        return {'error': 'No columns selected', 'rows': [], 'columns': []}

    config = {
        'columns': selected_columns,
        'filters': filters or {},
    }
    return _run_saved_query_sql(config)


def _run_saved_query_sql(config: dict) -> dict:
    """
    Execute a saved query config against ``current_student`` using raw SQL.

    Base columns are selected from ``current_student``.  Mark (``mark_*``) and
    test (``test_*``) columns are fetched via separate bulk queries against
    ``student_marks_list`` and ``results``/``tests`` respectively, then merged
    into each row dict.
    """
    requested = config.get('columns', [])
    filters   = config.get('filters', {}) if isinstance(config.get('filters'), dict) else {}

    # Partition requested columns into base / test / mark buckets.
    # Base columns go directly into SQL (cs.{col}) so must be safe identifiers.
    # Mark/test columns are only used as Python dict keys — the actual SQL uses
    # parameterised metadata values — so they can contain / and other chars.
    base_cols:  list[str] = []
    test_cols:  list[str] = []
    mark_cols:  list[str] = []
    for c in requested:
        if c in _CS_COLUMNS and _SAFE_IDENT_RE.match(c):
            base_cols.append(c)
        elif c.startswith('test_'):
            test_cols.append(c)
        elif c.startswith('mark_'):
            mark_cols.append(c)

    # Ensure we always have join keys so we can do bulk fetches
    need_test = bool(test_cols)
    need_mark = bool(mark_cols)
    if need_test and 'student_id' not in base_cols:
        base_cols.append('student_id')
    if need_mark and 'stu_annual_key' not in base_cols:
        base_cols.append('stu_annual_key')

    if not base_cols:
        base_cols = ['student_id', 'student_name', 'grade', 'school', 'school_year']

    # Build WHERE clauses — apply ALL saved-query filters generically.
    where_parts: list[str] = []
    params: list = []

    # Fields that live in student_enroll (not current_student)
    _ENROLL_FIELDS = {'ell': 'se.ell_status', 'birth_date': 'se.birth_date',
                      'us_enter_date': 'se.us_enter_date', 'fep_date': 'se.fep_date',
                      'migrant': 'se.migrant'}
    needs_enroll = False

    def _add_filter(expr: str, value):
        """Add an IN / = filter using the given SQL expression."""
        if not value:
            return
        if isinstance(value, list):
            value = [v for v in value if v]
            if not value:
                return
            placeholders = ', '.join(['%s'] * len(value))
            where_parts.append(f'{expr} IN ({placeholders})')
            params.extend(value)
        else:
            where_parts.append(f'{expr} = %s')
            params.append(value)

    # Apply every filter whose key is a base CS column or an enroll field
    for fkey, fval in filters.items():
        if fkey == 'search':
            continue  # handled separately below
        if fkey.startswith(('test_', 'mark_')):
            continue  # dynamic columns, not base CS filters
        if fkey in _ENROLL_FIELDS:
            needs_enroll = True
            _add_filter(_ENROLL_FIELDS[fkey], fval)
        elif fkey in _CS_COLUMNS and _SAFE_IDENT_RE.match(fkey):
            _add_filter(f'cs.{fkey}', fval)

    # Search filter
    search = filters.get('search', '')
    if search:
        where_parts.append(
            "(cs.student_name ILIKE %s OR cs.ssid ILIKE %s OR cs.student_id ILIKE %s)"
        )
        like = f'%{search}%'
        params.extend([like, like, like])

    # Also check if any requested SELECT columns need the enroll join
    for c in base_cols:
        if c in _ENROLL_FIELDS:
            needs_enroll = True
            break

    # Build JOIN and SELECT
    join_sql = ''
    if needs_enroll:
        join_sql = (
            " LEFT JOIN ("
            " SELECT DISTINCT ON (student_id, school_year) *"
            " FROM student_enroll"
            " ORDER BY student_id, school_year, enter_date DESC NULLS LAST, leave_date DESC NULLS LAST"
            " ) se ON se.student_id = cs.student_id AND se.school_year = cs.school_year"
        )

    # Build SELECT expressions — ell comes from COALESCE(cs.ell, se.ell_status) when enroll is joined
    select_exprs = []
    for c in base_cols:
        if c == 'ell' and needs_enroll:
            select_exprs.append('COALESCE(cs.ell, se.ell_status) AS ell')
        else:
            select_exprs.append(f'cs.{c}')

    where_sql = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''
    col_sql   = ', '.join(select_exprs)
    sql       = f'SELECT {col_sql} FROM current_student cs{join_sql} {where_sql} LIMIT {MAX_CHART_ROWS}'

    try:
        with connection.cursor() as cursor:
            # Phase 1: fetch base rows from current_student
            cursor.execute(sql, params)
            if cursor.description is None:
                return {'columns': base_cols, 'rows': [], 'row_count': 0, 'error': None}
            db_cols = [col[0] for col in cursor.description]
            raw     = cursor.fetchmany(MAX_CHART_ROWS)
            rows    = [dict(zip(db_cols, row)) for row in raw]

            # Phase 2: resolve test/mark column metadata & bulk-fetch
            if rows and (need_test or need_mark):
                if need_test:
                    test_infos, _ = _build_test_columns_from_db(cursor, test_cols)
                    student_ids = [r['student_id'] for r in rows if r.get('student_id')]
                    _fetch_test_data(cursor, test_infos, student_ids, rows)

                if need_mark:
                    mark_infos, _ = _build_mark_columns_from_db(cursor, mark_cols)
                    student_keys = [r['stu_annual_key'] for r in rows if r.get('stu_annual_key')]
                    _fetch_mark_data(cursor, mark_infos, student_keys, rows)

        # Build final ordered column list (base + test + mark)
        all_cols = list(db_cols)
        for tc in test_cols:
            if tc not in all_cols:
                all_cols.append(tc)
        for mc in mark_cols:
            if mc not in all_cols:
                all_cols.append(mc)

        col_types: dict = {}
        for col in all_cols:
            for row in rows:
                v = row.get(col)
                if v is not None and v != '\u2014':
                    col_types[col] = 'number' if isinstance(v, (int, float)) else 'string'
                    break
            col_types.setdefault(col, 'string')

        return {
            'columns':   all_cols,
            'col_types': col_types,
            'rows':      rows,
            'row_count': len(rows),
            'has_more':  len(raw) == MAX_CHART_ROWS,
            'error':     None,
        }
    except Exception as exc:
        return {'error': str(exc), 'rows': [], 'columns': []}



