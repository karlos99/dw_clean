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
    Results are cached for 60 seconds keyed by SQL hash.
    """
    sql = (sql or '').strip()
    if not sql:
        return {'error': 'No SQL provided'}
    if not _SQL_READONLY_RE.match(sql):
        return {'error': 'Only SELECT / WITH queries are allowed.'}
    if _SQL_DANGER_RE.search(sql):
        return {'error': 'Query contains disallowed keywords.'}

    cache_key = f"dv_raw_sql:{hashlib.md5(sql.encode()).hexdigest()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

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

        result = {
            'columns': columns, 'col_types': col_types,
            'rows': rows, 'row_count': len(rows), 'has_more': has_more,
        }
        cache.set(cache_key, result, 60)
        return result
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


def _fetch_test_data(cursor, test_cols_info: list[dict], student_ids: list[str], students: list[dict],
                     tested_school_codes: list[str] | None = None):
    """Bulk-fetch test scores and merge into student dicts (mutates *students* in-place).

    ``tested_school_codes``: when provided, restricts results to rows where
    ``results.school_code`` is one of the given codes (tested/assessment school filter).
    """
    if not test_cols_info or not student_ids:
        return

    # Compound key: (student_id, school_year) — a student may appear in
    # multiple rows when multi-year enrollment is queried. Each key maps to a
    # LIST so that all enrollment rows for the same (student, year) get data
    # (e.g. a student who transferred mid-year has two rows, same year).
    # Test results only populate rows whose school_year equals the result's year,
    # preventing 2023-2024 results from appearing on 2024-2025 rows and vice versa.
    student_map: dict[tuple, list] = {}
    for s in students:
        sid = s.get('student_id')
        sy = s.get('school_year')
        if sid and sy:
            key = (str(sid).strip(), str(sy).strip())
            student_map.setdefault(key, []).append(s)
    where_parts = []
    params = []
    for info in test_cols_info:
        part = "(r.school_year = %s AND t.test_name ILIKE %s AND t.subject ILIKE %s"
        part_params = [info['year'], f"%{info['test_name']}%", f"%{info['subject']}%"]
        if info.get('test_window'):
            part += " AND t.test_window ILIKE %s"
            part_params.append(info['test_window'])
        else:
            part += " AND (t.test_window IS NULL OR t.test_window = '')"
        part += ")"
        where_parts.append(part)
        params.extend(part_params)

    tested_school_clause = ''
    if tested_school_codes:
        codes = [c for c in tested_school_codes if c]
        if codes:
            tested_school_clause = f"AND r.school_code = ANY(%s)"
            params.append(codes)

    sql = f"""
        SELECT r.local_student_id, r.ss, r.pl, r.other_info,
               r.school_year, t.test_name, t.subject, t.test_window
        FROM results r
        INNER JOIN tests t ON r.test_group_key = t.test_group_key
        WHERE r.local_student_id = ANY(%s)
          AND ({" OR ".join(where_parts)})
          {tested_school_clause}
    """
    cursor.execute(sql, [student_ids, *params])
    
    for row in cursor.fetchall():
        sid, ss, pl, oi, ry, rt, rs, rw = row
        # Look up by compound (student_id, result_school_year). Result can only
        # populate enrollment rows for the exact same year \u2014 no cross-year bleed.
        lookup_key = (str(sid).strip(), str(ry).strip())
        s_objs = student_map.get(lookup_key)
        if not s_objs:
            continue
        for info in test_cols_info:
            if (info['year'] == ry and info['test_name'].lower() in rt.lower() and
                info['subject'].lower() in rs.lower()):
                # Window match
                iw = info.get('test_window')
                if not iw and not rw: pass
                elif iw and rw and iw.lower() == rw.lower(): pass
                else: continue

                metric = info['metric']
                col_name = info['col_name']
                if metric == 'ss':
                    val = str(ss).strip() if ss is not None else '\u2014'
                elif metric == 'pl':
                    val = str(pl).strip() if pl else '\u2014'
                elif metric == 'oi':
                    oi_key = info.get('other_info_key')
                    if oi_key and isinstance(oi, dict):
                        v = oi.get(oi_key)
                        val = str(v).strip() if v is not None else '\u2014'
                    else:
                        val = '\u2014'
                else:
                    continue
                # Write to ALL enrollment rows for this (student, year) pair
                for s_obj in s_objs:
                    s_obj[col_name] = val


def _fetch_mark_data(cursor, mark_cols_info: list[dict], student_keys: list[str], students: list[dict]):
    """Bulk-fetch student marks and merge into student dicts (mutates *students* in-place)."""
    if not mark_cols_info or not student_keys:
        return

    student_map = {s.get('stu_annual_key'): s for s in students if s.get('stu_annual_key')}
    where_parts = []
    params = []
    for info in mark_cols_info:
        part = "(sm.school_year = %s AND sm.subject ILIKE %s AND sm.course_name ILIKE %s"
        part_params = [info['year'], f"%{info['subject']}%", f"%{info['course_name']}%"]
        if info.get('mark_period'):
            part += " AND sm.mark_period ILIKE %s"
            part_params.append(info['mark_period'])
        else:
            part += " AND (sm.mark_period IS NULL OR sm.mark_period = '')"
        part += ")"
        where_parts.append(part)
        params.extend(part_params)

    sql = f"""
        SELECT sm.stu_annual_key, sm.mark, sm.school_year, sm.subject, sm.course_name, sm.mark_period
        FROM student_marks_list sm
        WHERE sm.stu_annual_key = ANY(%s)
          AND ({" OR ".join(where_parts)})
    """
    cursor.execute(sql, [student_keys, *params])

    for row in cursor.fetchall():
        sk, mark, ry, rs, rc, rp = row
        s_obj = student_map.get(sk)
        if not s_obj: continue
        for info in mark_cols_info:
            if (info['year'] == ry and info['subject'].lower() in rs.lower() and 
                info['course_name'].lower() in rc.lower()):
                ip = info.get('mark_period')
                if not ip and not rp: pass
                elif ip and rp and ip.lower() == rp.lower(): pass
                else: continue
                s_obj[info['col_name']] = (str(mark).strip() if mark else '\u2014')


# ── Public entry points ──────────────────────────────────────────────────────

def run_saved_query_preview(saved_query) -> dict:
    """Return the first 5 rows + column list for a SavedQuery for preview.

    Reuses the existing query SQL from the config but limits to 5 rows.
    Does NOT apply user filters — this is a schema-only preview.
    """
    config = dict(saved_query.config or {})
    columns = config.get('columns', [])
    if not columns:
        return {'error': 'No columns configured in saved query', 'rows': [], 'columns': []}

    # Build a minimal SELECT from current_student for preview
    base_cols = [c for c in columns if c in _CS_COLUMNS and _SAFE_IDENT_RE.match(c)]
    if not base_cols:
        base_cols = ['student_id', 'student_name', 'grade', 'school', 'school_year']

    select_exprs = [f'cs.{c}' for c in base_cols]
    col_sql = ', '.join(select_exprs)
    sql = f'SELECT {col_sql} FROM current_student cs WHERE cs.status = %s LIMIT 5'

    try:
        with connection.cursor() as cursor:
            cursor.execute("BEGIN READ ONLY")
            try:
                cursor.execute(sql, ['Active'])
                if cursor.description is None:
                    return {'columns': base_cols, 'rows': [], 'row_count': 0}
                db_cols = [col[0] for col in cursor.description]
                raw = cursor.fetchmany(5)
                rows = [dict(zip(db_cols, row)) for row in raw]
            finally:
                cursor.execute("ROLLBACK")

        return {
            'columns': base_cols,
            'rows': rows,
            'row_count': len(rows),
        }
    except Exception as exc:
        return {'error': str(exc), 'rows': [], 'columns': []}


def run_saved_query(query_id: int, extra_filters: dict, user, extra_columns: list | None = None, apply_user_filters: bool = False) -> dict:
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

    return _run_saved_query_sql(config, user=user if apply_user_filters else None)


def run_column_query(selected_columns: list[str], filters: dict | None = None, user=None, apply_user_filters: bool = False) -> dict:
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
    return _run_saved_query_sql(config, user=user if apply_user_filters else None)


def _run_saved_query_sql(config: dict, user=None) -> dict:
    """
    Execute a saved query config against ``current_student`` using raw SQL.

    Base columns are selected from ``current_student``.  Mark (``mark_*``) and
    test (``test_*``) columns are fetched via separate bulk queries against
    ``student_marks_list`` and ``results``/``tests`` respectively, then merged
    into each row dict.

    If ``user`` is provided, school-level access filters are applied based on
    the user's role and permitted schools.
    """
    # ── Cache check ───────────────────────────────────────────────────────────
    # We cache the entire result dict (rows, columns, types) for 5 minutes.
    # The key is based on the hash of the JSON-serialized config.
    # If a user is provided, we include the user's ID and active role/school 
    # to ensure they only get data they are allowed to see.
    user_part = ""
    if user:
        user_role = getattr(user, 'active_role', None)
        role_name = user_role.name if user_role else 'None'
        active_school = getattr(user, 'active_school', None)
        school_id = active_school.state_id if active_school else 'All'
        user_part = f":u{user.pk}:r{role_name}:s{school_id}"

    config_hash = hashlib.md5(json.dumps(config, sort_keys=True).encode()).hexdigest()
    cache_key = f"dv_query_result:{config_hash}{user_part}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    requested = config.get('columns', [])
    filters   = config.get('filters', {}) if isinstance(config.get('filters'), dict) else {}

    # Resolve school-year aliases (e.g. '__sy_this__') to concrete year values.
    if 'school_year' in filters:
        try:
            from dataexploration.school_year_utils import resolve_school_year
            sy_val = filters['school_year']
            if isinstance(sy_val, list):
                filters = {**filters, 'school_year': [resolve_school_year(v) for v in sy_val]}
            else:
                filters = {**filters, 'school_year': resolve_school_year(sy_val)}
        except Exception:
            pass

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

    # Resolve relative column key aliases (e.g. test___sy_this___... → test_2025_2026_...)
    # before they reach _parse_year / _build_test_columns_from_db which expect real years.
    try:
        from dataexploration.school_year_utils import resolve_relative_column_key
        test_cols = [resolve_relative_column_key(c) for c in test_cols]
        mark_cols = [resolve_relative_column_key(c) for c in mark_cols]
    except Exception:
        pass

    # Ensure we always have join keys so we can do bulk fetches
    need_test = bool(test_cols)
    need_mark = bool(mark_cols)
    if need_test and 'student_id' not in base_cols:
        base_cols.append('student_id')
    if need_test and 'school_year' not in base_cols:
        base_cols.append('school_year')
    if need_mark and 'stu_annual_key' not in base_cols:
        base_cols.append('stu_annual_key')

    if not base_cols:
        base_cols = ['student_id', 'student_name', 'grade', 'school', 'school_year']

    # Build WHERE clauses — apply ALL saved-query filters generically.
    where_parts: list[str] = []
    params: list = []
    dynamic_filters: dict[str, object] = {}

    # Always exclude Inactive/Deleted students unless the caller explicitly filters on status
    if 'status' not in filters:
        where_parts.append("cs.status = 'Active'")

    # ── Role-based access ──────────────────────────────────────────────
    if user:
        is_unrestricted = False
        if user.is_superuser:
            is_unrestricted = True
        else:
            # Check for Project Admin or District roles
            active_role = getattr(user, 'active_role', None)
            role_name = active_role.name if active_role else ''
            
            # If no active role, check all roles
            user_roles = [r.name for r in user.roles.all()] if not role_name else [role_name]
            
            unrestricted_names = {'Project Admin', 'District Admin', 'District View'}
            if any(name in unrestricted_names or 'District' in name for name in user_roles):
                is_unrestricted = True

        # School Gr View grade restrictions
        active_role_name = getattr(getattr(user, 'active_role', None), 'name', None)
        if active_role_name == 'School Gr View':
            delegated_grades = getattr(user, 'grade_levels', None)
            if isinstance(delegated_grades, list) and delegated_grades:
                where_parts.append("cs.grade = ANY(%s)")
                params.append(delegated_grades)

        # Teacher / Homeroom restriction
        if hasattr(user, 'get_accessible_student_keys'):
            keys = user.get_accessible_student_keys()
            if keys is not None:
                if keys:
                    where_parts.append("cs.stu_annual_key = ANY(%s)")
                    params.append(list(keys))
                else:
                    where_parts.append("1=0")  # No students assigned

        if hasattr(user, 'active_school') and user.active_school:
            # Explicitly filtered to one school via the UI selector
            where_parts.append("cs.school_state_id = %s")
            params.append(user.active_school.state_id)
        elif not is_unrestricted:
            # Filter to the list of schools the user is permitted to see
            user_schools = getattr(user, 'schools', [])
            if isinstance(user_schools, list) and user_schools:
                where_parts.append("cs.school_state_id = ANY(%s)")
                params.append(user_schools)
            else:
                where_parts.append("1=0")  # Deny access if no schools assigned

    # Fields that live in student_enroll (not current_student)
    _ENROLL_FIELDS = {'ell': 'se.ell_status', 'birth_date': 'se.birth_date',
                      'us_enter_date': 'se.us_enter_date', 'fep_date': 'se.fep_date',
                      'migrant': 'se.migrant', 'grade': 'se.grade'}
    needs_enroll = False

    # When a school_year filter is present (especially multi-year or past),
    # force the enroll join so grade comes from the enrollment record for that year.
    _needs_year_grade = False
    if 'school_year' in filters:
        try:
            from dataexploration.school_year_utils import resolve_school_year
            default_sy = resolve_school_year('__sy_this__')
            sy_val = filters['school_year']
            if isinstance(sy_val, list):
                if len(sy_val) > 1 or (len(sy_val) == 1 and sy_val[0] != default_sy):
                    _needs_year_grade = True
            elif isinstance(sy_val, str) and sy_val and sy_val != default_sy:
                _needs_year_grade = True
        except Exception:
            pass
    if _needs_year_grade:
        needs_enroll = True

    def _add_filter(expr: str, value):
        """Add an IN / = filter using the given SQL expression."""
        if not value:
            return

        if isinstance(value, list):
            value = [v for v in value if v]
            if not value:
                return
            has_blank = '(Blank)' in value
            others = [v for v in value if v != '(Blank)']
            parts = []
            if others:
                placeholders = ', '.join(['%s'] * len(others))
                parts.append(f'{expr} IN ({placeholders})')
                params.extend(others)
            if has_blank:
                parts.append(f"({expr} IS NULL OR {expr} = '' OR {expr} = '—')")
            
            if len(parts) > 1:
                where_parts.append(f"({' OR '.join(parts)})")
            elif parts:
                where_parts.append(parts[0])
        else:
            if value == '(Blank)':
                where_parts.append(f"({expr} IS NULL OR {expr} = '' OR {expr} = '—')")
            else:
                where_parts.append(f'{expr} = %s')
                params.append(value)

    # Apply every filter whose key is a base CS column or an enroll field.
    # Pull out special non-column filters first.
    tested_school_filter = filters.pop('tested_school', None) if isinstance(filters, dict) else None
    if tested_school_filter and not isinstance(tested_school_filter, list):
        tested_school_filter = [tested_school_filter]

    for fkey, fval in filters.items():
        if fkey == 'search':
            continue  # handled separately below
        if fkey.startswith(('test_', 'mark_')):
            dynamic_filters[fkey] = fval
            continue  # dynamic columns are filtered after enrichment below
        if fkey in _ENROLL_FIELDS:
            needs_enroll = True
            _add_filter(_ENROLL_FIELDS[fkey], fval)
        elif fkey in _CS_COLUMNS and _SAFE_IDENT_RE.match(fkey):
            _add_filter(f'cs.{fkey}', fval)

    def _matches_dynamic_filter(row_value, filter_value) -> bool:
        blank_tokens = {'', '—', '(Blank)'}

        def _normalize(value):
            if value is None:
                return ''
            return str(value).strip()

        normalized_row = _normalize(row_value)
        if isinstance(filter_value, list):
            selected = [_normalize(item) for item in filter_value if _normalize(item)]
            if not selected:
                return True
            if '(Blank)' in selected and normalized_row in {'', '—'}:
                return True
            return normalized_row in {item for item in selected if item != '(Blank)'}

        normalized_filter = _normalize(filter_value)
        if not normalized_filter:
            return True
        if normalized_filter == '(Blank)':
            return normalized_row in {'', '—'}
        return normalized_row == normalized_filter

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

    # Build SELECT expressions — use enrollment values when join is active
    select_exprs = []
    for c in base_cols:
        if c == 'ell' and needs_enroll:
            select_exprs.append('COALESCE(cs.ell, se.ell_status) AS ell')
        elif c == 'grade' and needs_enroll:
            select_exprs.append('COALESCE(se.grade, cs.grade) AS grade')
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
                # When multiple school years are selected, the column key encodes only
                # the year that was visible in the picker (usually the most recent).
                # Expand infos to cover all selected years so each enrolled-year row
                # gets its matching assessment/mark data via the (student_id, year) key.
                _multi_years = filters.get('school_year') if isinstance(filters.get('school_year'), list) and len(filters.get('school_year', [])) > 1 else None

                if need_test:
                    test_infos, _ = _build_test_columns_from_db(cursor, test_cols)
                    if _multi_years and test_infos:
                        _seen = set()
                        _expanded = []
                        for _info in test_infos:
                            for _yr in _multi_years:
                                _key = (_yr, _info['test_name'], _info['subject'], _info.get('test_window'), _info['metric'])
                                if _key not in _seen:
                                    _seen.add(_key)
                                    _expanded.append({**_info, 'year': _yr})
                        test_infos = _expanded
                    student_ids = [r['student_id'] for r in rows if r.get('student_id')]
                    _fetch_test_data(cursor, test_infos, student_ids, rows,
                                     tested_school_codes=tested_school_filter)

                if need_mark:
                    mark_infos, _ = _build_mark_columns_from_db(cursor, mark_cols)
                    if _multi_years and mark_infos:
                        _seen = set()
                        _expanded = []
                        for _info in mark_infos:
                            for _yr in _multi_years:
                                _key = (_yr, _info['subject'], _info['course_name'], _info.get('mark_period'))
                                if _key not in _seen:
                                    _seen.add(_key)
                                    _expanded.append({**_info, 'year': _yr})
                        mark_infos = _expanded
                    student_keys = [r['stu_annual_key'] for r in rows if r.get('stu_annual_key')]
                    _fetch_mark_data(cursor, mark_infos, student_keys, rows)

            if rows and dynamic_filters:
                rows = [
                    row for row in rows
                    if all(_matches_dynamic_filter(row.get(filter_key), filter_value) for filter_key, filter_value in dynamic_filters.items())
                ]

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

        res = {
            'columns':   all_cols,
            'col_types': col_types,
            'rows':      rows,
            'row_count': len(rows),
            'has_more':  len(raw) == MAX_CHART_ROWS,
            'error':     None,
        }
        # Cache for 5 minutes (300s)
        cache.set(cache_key, res, 300)
        return res
    except Exception as exc:
        return {'error': str(exc), 'rows': [], 'columns': []}



