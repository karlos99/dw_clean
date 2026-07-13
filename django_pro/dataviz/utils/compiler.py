import hashlib
import json
import logging
import re
from collections import Counter
from django.db import connection
from django.core.cache import cache
from dataexploration.utils import get_user_roles

logger = logging.getLogger(__name__)

MAX_CHART_ROWS = 150000


def generate_column_aliases(columns: list, existing_mappings: dict | None = None) -> dict:
    """
    Compute a clean display alias for every column in a dataset's column list.
    Returns dict: {"table.column" -> "clean_alias"}.

    Rules (in priority order):
      1. Entries already in *existing_mappings* are kept unchanged.
      2. Standard column  "current_student.grade"         → "grade"
      3. JSONB key suffix "current_student.add_data->'x'" → "x"
      4. Collision (same natural alias from different columns): both get a
         table-prefixed alias, e.g. "current_student_grade" / "student_enroll_grade".
    """
    existing = dict(existing_mappings or {})
    # Columns whose aliases are already user-defined
    user_cols = set(existing)

    # Pass 1 – natural alias for each unmapped column
    natural: dict[str, str] = {}
    for col_str in columns:
        if col_str in user_cols:
            continue
        if '.' not in col_str:
            natural[col_str] = col_str
            continue
        tbl, col = col_str.split('.', 1)
        if '->' in col:
            key = col.split('->', 1)[1].strip("'\"")
            natural[col_str] = key or col.replace('->', '_').strip("'\"")
        else:
            natural[col_str] = col

    # Pass 2 – resolve collisions with table-prefixed aliases
    counts = Counter(natural.values())
    result = dict(existing)
    for col_str, alias in natural.items():
        if counts[alias] > 1:
            tbl = col_str.split('.', 1)[0] if '.' in col_str else col_str
            result[col_str] = f"{tbl}_{alias}"
        else:
            result[col_str] = alias

    return result


def apply_column_aliases(rows: list, column_mappings: dict | None) -> list:
    """
    Rename row-dict keys from "table.column" format to their clean aliases
    using *column_mappings*.  Keys absent from the mapping are kept as-is.
    """
    if not rows or not column_mappings:
        return rows
    return [{column_mappings.get(k, k): v for k, v in row.items()} for row in rows]


def build_dataset_col_index(dataset) -> dict:
    """
    Build a reverse index from the dataset's column list + column_mappings:
        bare_name / JSONB_key / exploration_alias  →  "table.column" (full form)

    Used to resolve bare filter keys (from page filters, chart config) back to
    the table-qualified form expected by compile_dataset_to_sql's interactive_filters.
    """
    index: dict[str, str] = {}
    for ds_col in (dataset.columns or []):
        if '.' not in ds_col:
            continue
        _tbl, _col = ds_col.split('.', 1)
        if '->' in _col:
            key = _col.split('->', 1)[1].strip("'\"")
            if key and key not in index:
                index[key] = ds_col
        else:
            if _col not in index:
                index[_col] = ds_col
    # Inverse of column_mappings: exploration_alias → dataset full col
    col_mappings = getattr(dataset, 'column_mappings', None) or {}
    if not col_mappings:
        col_mappings = generate_column_aliases(dataset.columns or [], {})
    for ds_col, alias in col_mappings.items():
        if alias and alias not in index:
            index[alias] = ds_col
    return index


def _resolve_year_token(val):
    """Resolve __sy_*__ year-alias tokens to concrete year strings (e.g. '2025-2026')."""
    try:
        from dataexploration.school_year_utils import resolve_school_year
    except ImportError:
        return val
    if isinstance(val, str) and val.startswith('__sy_'):
        return resolve_school_year(val)
    if isinstance(val, list):
        return [resolve_school_year(v) if isinstance(v, str) and v.startswith('__sy_') else v for v in val]
    return val


def resolve_dataset_filters(filters: dict, dataset, active_tables: set) -> dict:
    """
    Resolve a mixed filter dict (keys may be bare exploration names, JSONB key
    suffixes, or already-qualified "table.column" strings) into a dict suitable
    for compile_dataset_to_sql's *interactive_filters* parameter.

    Bare keys are resolved via the dataset's column index first (deterministic,
    handles JSONB), then via COLUMN_WHITELIST scanning with base_table priority.
    Already-qualified keys are validated and passed through unchanged.
    Dynamic test_*/mark_* keys are skipped (post-fetch Python filtering).
    Year-alias tokens (e.g. __sy_this__) are resolved to concrete year values.
    """
    col_index = build_dataset_col_index(dataset)

    def _allowed(tbl: str) -> set:
        return set(get_columns_for_table(tbl)) | set(COLUMN_WHITELIST.get(tbl, set()))

    priority = [dataset.base_table] + [t for t in active_tables if t != dataset.base_table]

    out: dict = {}
    for fk, fval in filters.items():
        if not fval:
            continue
        if fk.startswith(('test_', 'mark_')):
            continue
        fval = _resolve_year_token(fval)
        if not fval:
            continue
        if '.' in fk:
            tbl, col = fk.split('.', 1)
            base = col.split('->', 1)[0]
            if tbl in active_tables and (col in _allowed(tbl) or base in _allowed(tbl)):
                out[fk] = fval
        else:
            resolved = col_index.get(fk)
            if not resolved:
                for tbl in priority:
                    if fk in _allowed(tbl):
                        resolved = f"{tbl}.{fk}"
                        break
            if resolved:
                out[resolved] = fval
            else:
                base_allowed = _allowed(dataset.base_table)
                if fk in base_allowed:
                    out[f"{dataset.base_table}.{fk}"] = fval
    return out

TABLE_WHITELIST = {
    'current_student': 'current_student',
    'student_enroll': 'student_enroll',
    'school': 'school',
    'tests': 'tests',
    'results': 'results',
    'schedule_list': 'schedule_list',
    'student_marks_list': 'student_marks_list',
    'users_user': 'users_user',
    'relative_year': 'relative_year',
}


def get_db_tables():
    """
    Query database for all public tables, excluding Django/system tables.
    """
    from django.db import connection
    exclude_prefixes = (
        'django_', 'auth_', 'socialaccount_', 'account_', 
        'landing_', 'dataviz_', 'dataexploration_', 'dataLoader_',
        'placementcards_', 'nextyearlist_', 'sessions_', 'contenttypes_'
    )
    tables = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' ORDER BY table_name"
            )
            for row in cursor.fetchall():
                t = row[0]
                if any(t.startswith(prefix) for prefix in exclude_prefixes):
                    if t != 'users_user':
                        continue
                tables[t] = t
    except Exception:
        return TABLE_WHITELIST
    return tables


def get_columns_for_table(table_name: str) -> set:
    """
    Return columns for table_name. First gets base columns (from static whitelist
    or DB dynamically), then discovers any JSON/JSONB fields to append their keys.
    """
    allowed_tables = get_allowed_dataset_tables()
    if table_name not in allowed_tables:
        return set()

    # Get base columns
    if table_name in COLUMN_WHITELIST:
        base_cols = set(COLUMN_WHITELIST[table_name])
    else:
        from django.db import connection
        base_cols = set()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name = %s",
                    [table_name]
                )
                raw_cols = [row[0] for row in cursor.fetchall()]
                sensitive = {'password', 'secret', 'token', 'session', 'hash', 'crypt', 'salt'}
                for c in raw_cols:
                    if not any(s in c.lower() for s in sensitive):
                        base_cols.add(c)
        except Exception:
            pass

    # For each base column, check if it's JSON/JSONB and find keys
    final_cols = set(base_cols)
    from django.db import connection
    try:
        with connection.cursor() as cursor:
            # Query column types for the table
            cursor.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name = %s",
                [table_name]
            )
            col_types = {row[0]: row[1] for row in cursor.fetchall()}
            
            for c in base_cols:
                dtype = col_types.get(c)
                if dtype in ('json', 'jsonb'):
                    # Fetch keys
                    key_query = f'SELECT DISTINCT key FROM (SELECT jsonb_object_keys(CAST("{c}" AS jsonb)) AS key FROM "{table_name}" WHERE "{c}" IS NOT NULL LIMIT 100000) sub'
                    cursor.execute(key_query)
                    keys = [row[0] for row in cursor.fetchall() if row[0]]
                    for k in keys:
                        final_cols.add(f"{c}->'{k}'")
    except Exception:
        logger.warning(f"Failed to extract JSON columns for table {table_name}", exc_info=True)

    return final_cols


def get_allowed_dataset_tables():
    """
    Return the set of database tables allowed for dataset construction.
    Filters the dynamically discovered tables based on settings config.
    """
    from landing.utils import get_project_config
    
    db_tables = get_db_tables()
    try:
        cfg = get_project_config()
        enabled = getattr(cfg, 'enabled_dataset_tables', None)
        if enabled is not None:
            # Filter based on enabled list
            return {t: db_tables[t] for t in enabled if t in db_tables}
    except Exception:
        pass
    return db_tables

COLUMN_WHITELIST = {
    'relative_year': {
        'school_year', 'relative_alias', 'relative_label', 'offset_years', 'is_current'
    },
    'current_student': {
        'stu_annual_key', 'ssid', 'student_id', 'student_name', 'grade', 'school',
        'school_state_id', 'gender', 'race', 'ell', 'sed', 'foster', 'homeless',
        'sped', 'gate', 'in_504', 'dli', 'atendace_rate', 'suspension_indicator',
        'ltel', 'status', 'school_year', 'add_data'
    },
    'student_enroll': {
        'stu_enrollment_key', 'stu_annual_key', 'student_id', 'ssid', 'last_name',
        'middle_name', 'first_name', 'birth_date', 'gender', 'race', 'status',
        'enter_date', 'leave_date', 'us_enter_date', 'state_enter_date', 'ell_status',
        'fep_date', 'instructional_setting', 'grade', 'special_program_code',
        'school_name', 'state_school_code', 'migrant', 'homeless', 'foster', 'school_year'
    },
    'school': {
        'dw_schoolkey', 'state_id', 'sis_key', 'assessment_school_code', 'short_name',
        'school_name', 'school_group'
    },
    'tests': {
        'test_group_key', 'test_name', 'subject', 'school_year', 'test_window'
    },
    'results': {
        'test_id', 'test_group_key', 'ssid', 'local_student_id', 'school_year',
        'school_code', 'pl', 'ss', 'other_info'
    },
    'schedule_list': {
        'schedule_id', 'stu_annual_key', 'ssid', 'school_year', 'student_id',
        'school_name', 'state_school_id', 'course_state_equivalence_code', 'course_code',
        'course_key', 'course_subject', 'course_name', 'period', 'staff_name',
        'staff_email', 'staff_employee_id', 'schedule_status'
    },
    'student_marks_list': {
        'marks_id', 'stu_annual_key', 'ssid', 'school_year', 'student_id',
        'school_name', 'state_school_id', 'subject', 'course_name', 'staff_name',
        'staff_employee_id', 'mark_period', 'mark'
    },
    'users_user': {
        'id', 'username', 'email', 'name', 'first_name', 'last_name', 'is_active',
        'is_superuser', 'is_staff'
    }
}


class SQLCompilationError(Exception):
    pass


def validate_column_identifier(col_str, active_tables):
    """
    Validates a column identifier of the format 'table_name.column_name'
    or 'table_name.column_name->key_name'.
    Ensures table is active in the query and column is whitelisted.
    Returns (table_name, column_name).
    """
    parts = col_str.split('.')
    if len(parts) != 2:
        raise SQLCompilationError(f"Invalid column format: '{col_str}'. Expected 'table.column'")
    
    table_name, col_name = parts
    
    if table_name not in active_tables:
        raise SQLCompilationError(f"Table '{table_name}' is not in active query tables list.")
    
    allowed_columns = get_columns_for_table(table_name)
    if not allowed_columns:
        raise SQLCompilationError(f"Table '{table_name}' has no allowed columns or is not whitelisted.")
    
    # Extract base column if JSON
    base_col = col_name.split('->', 1)[0]
    
    if base_col not in allowed_columns:
        raise SQLCompilationError(f"Column '{base_col}' is not in whitelist for table '{table_name}'.")
        
    return table_name, col_name


def format_column_expr(tbl, col):
    """
    Given a table name and a column name, returns the SQL expression.
    If the column name contains a JSON path like 'other_info->\'lexile\'',
    formats it as '"table"."other_info"->>\'lexile\''.
    """
    if '->' in col:
        base_col, key = col.split('->', 1)
        key_clean = key.strip("'\"")
        return f'"{tbl}"."{base_col}"->>\'{key_clean}\''
    return f'"{tbl}"."{col}"'


def compile_dataset_to_sql(dataset, user=None, limit=MAX_CHART_ROWS, interactive_filters=None):
    """
    Compiles a Dataset definition object into parameterized SQL.
    Returns (sql_query_string, parameters_list).
    """
    # Cache compiled SQL keyed on dataset content + user context + interactive filters
    user_part = ""
    if user:
        user_role = getattr(getattr(user, 'active_role', None), 'name', 'None')
        school_id = getattr(getattr(user, 'active_school', None), 'state_id', 'All')
        user_part = f":u{user.pk}:r{user_role}:s{school_id}"
    ds_hash = hashlib.md5(
        json.dumps({
            'base_table': dataset.base_table,
            'distinct': dataset.distinct,
            'joins': dataset.joins or [],
            'filters': dataset.filters or [],
            'columns': dataset.columns or [],
            'limit': limit,
            'updated': str(getattr(dataset, 'updated_at', '')),
            'interactive_filters': interactive_filters or {},
        }, sort_keys=True, default=str).encode()
    ).hexdigest()
    cache_key = f"dv:compiled_sql:{ds_hash}{user_part}"
    try:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
    except Exception:
        logger.warning("Cache unavailable for compiled SQL", exc_info=True)

    base_table = dataset.base_table
    allowed_tables = get_allowed_dataset_tables()
    if base_table not in allowed_tables:
        raise SQLCompilationError(f"Base table '{base_table}' is not whitelisted.")
    
    active_tables = {base_table}
    joins_list = dataset.joins or []
    for j in joins_list:
        join_table = j.get('table')
        if join_table not in allowed_tables:
            raise SQLCompilationError(f"Joined table '{join_table}' is not whitelisted.")
        active_tables.add(join_table)
        
    columns_list = dataset.columns or []
    if not columns_list:
        raise SQLCompilationError("Dataset has no select columns specified.")
        
    select_clauses = []
    for col_str in columns_list:
        tbl, col = validate_column_identifier(col_str, active_tables)
        expr = format_column_expr(tbl, col)
        select_clauses.append(f'{expr} AS "{tbl}.{col}"')
        
    select_sql = ", ".join(select_clauses)
    
    # Construct base query
    select_keyword = "SELECT DISTINCT" if getattr(dataset, 'distinct', True) else "SELECT"
    sql = f'{select_keyword} {select_sql} FROM "{base_table}"'
    
    # Construct joins
    join_clauses = []
    for j in joins_list:
        join_table = j.get('table')
        on_left = j.get('on_left')
        on_right = j.get('on_right')
        join_type = j.get('join_type', 'left').lower()
        
        join_keyword_map = {
            'left': 'LEFT JOIN',
            'inner': 'INNER JOIN',
            'right': 'RIGHT JOIN'
        }
        join_keyword = join_keyword_map.get(join_type, 'LEFT JOIN')
        
        conditions = j.get('conditions', [])
        if not conditions:
            # Fallback to single root join mapping
            conditions = [{'on_left': on_left, 'on_right': on_right}]
            
        on_clauses = []
        for cond in conditions:
            cond_left = cond.get('on_left')
            cond_right = cond.get('on_right')
            if not cond_left or not cond_right:
                raise SQLCompilationError(f"Missing columns in join condition for table '{join_table}'.")
            l_tbl, l_col = validate_column_identifier(cond_left, active_tables)
            r_tbl, r_col = validate_column_identifier(cond_right, active_tables)
            on_clauses.append(f'{format_column_expr(l_tbl, l_col)} = {format_column_expr(r_tbl, r_col)}')
            
        join_clauses.append(f'{join_keyword} "{join_table}" ON ' + " AND ".join(on_clauses))
        
    if join_clauses:
        sql += " " + " ".join(join_clauses)
        
    # Construct filters
    where_parts = []
    params = []
    
    filters_list = dataset.filters or []
    for f in filters_list:
        col_str = f.get('column')
        operator = f.get('operator', 'exact').lower()
        val = f.get('value')
        
        tbl, col = validate_column_identifier(col_str, active_tables)
        expr = format_column_expr(tbl, col)
        
        if operator == 'exact':
            where_parts.append(f'{expr} = %s')
            params.append(val)
        elif operator == 'icontains':
            where_parts.append(f'{expr} ILIKE %s')
            params.append(f'%{val}%')
        elif operator == 'gt':
            where_parts.append(f'{expr} > %s')
            params.append(val)
        elif operator == 'lt':
            where_parts.append(f'{expr} < %s')
            params.append(val)
        elif operator == 'in':
            if isinstance(val, list):
                if val:
                    where_parts.append(f'{expr} = ANY(%s)')
                    params.append(val)
                else:
                    where_parts.append("1=0")  # Empty list matches nothing
            else:
                # Fallback to single value
                where_parts.append(f'{expr} = %s')
                params.append(val)
                
    # School Security Filter Injection
    if user:
        is_unrestricted = False
        if user.is_superuser:
            is_unrestricted = True
        else:
            user_roles = get_user_roles(user)
            unrestricted_names = {'Project Admin', 'District Admin', 'District View'}
            if any(name in unrestricted_names or 'District' in name for name in user_roles):
                is_unrestricted = True
                
        # Inject context filter on current_student if present
        if 'current_student' in active_tables:
            if hasattr(user, 'active_school') and user.active_school:
                where_parts.append('"current_student"."school_state_id" = %s')
                params.append(user.active_school.state_id)
            elif not is_unrestricted:
                user_schools = getattr(user, 'schools', [])
                if isinstance(user_schools, list) and user_schools:
                    where_parts.append('"current_student"."school_state_id" = ANY(%s)')
                    params.append(user_schools)
                else:
                    where_parts.append("1=0")
                    
        # Inject context filter on student_enroll if present and current_student not present
        elif 'student_enroll' in active_tables:
            if hasattr(user, 'active_school') and user.active_school:
                where_parts.append('"student_enroll"."state_school_code" = %s')
                params.append(user.active_school.state_id)
            elif not is_unrestricted:
                user_schools = getattr(user, 'schools', [])
                if isinstance(user_schools, list) and user_schools:
                    where_parts.append('"student_enroll"."state_school_code" = ANY(%s)')
                    params.append(user_schools)
                else:
                    where_parts.append("1=0")
                    
        # Inject grade level restrictions for School Gr View role
        active_role_name = getattr(getattr(user, 'active_role', None), 'name', None)
        if active_role_name == 'School Gr View':
            delegated_grades = getattr(user, 'grade_levels', None)
            if isinstance(delegated_grades, list) and delegated_grades:
                if 'current_student' in active_tables:
                    where_parts.append('"current_student"."grade" = ANY(%s)')
                    params.append(delegated_grades)
                elif 'student_enroll' in active_tables:
                    where_parts.append('"student_enroll"."grade" = ANY(%s)')
                    params.append(delegated_grades)

        # Inject teacher / homeroom student-key restrictions
        if hasattr(user, 'get_accessible_student_keys'):
            keys = user.get_accessible_student_keys()
            if keys is not None:
                if keys:
                    if 'current_student' in active_tables:
                        where_parts.append('"current_student"."stu_annual_key" = ANY(%s)')
                        params.append(list(keys))
                    elif 'student_enroll' in active_tables:
                        where_parts.append('"student_enroll"."stu_annual_key" = ANY(%s)')
                        params.append(list(keys))
                else:
                    where_parts.append("1=0")  # No students assigned

    # Construct interactive filters
    if interactive_filters:
        allowed_tables = get_allowed_dataset_tables()
        for fkey, fval in interactive_filters.items():
            if not fval:
                continue
            parts = fkey.split('.', 1)
            if len(parts) != 2:
                continue
            ftbl, fname = parts
            if ftbl not in active_tables:
                continue
            
            base_col = fname.split('->', 1)[0]
            if ftbl not in allowed_tables or base_col not in get_columns_for_table(ftbl):
                continue
            
            # Add filter condition
            expr = format_column_expr(ftbl, fname)
            if isinstance(fval, list):
                vals = [v for v in fval if v]
                if not vals:
                    continue
                has_blank = '(Blank)' in vals
                others = [v for v in vals if v != '(Blank)']
                parts_list = []
                if others:
                    ph = ', '.join(['%s'] * len(others))
                    parts_list.append(f'{expr} IN ({ph})')
                    params.extend(others)
                if has_blank:
                    parts_list.append(f'({expr} IS NULL OR {expr} = \'\')')
                if len(parts_list) > 1:
                    where_parts.append(f"({' OR '.join(parts_list)})")
                elif parts_list:
                    where_parts.append(parts_list[0])
            else:
                if fval == '(Blank)':
                    where_parts.append(f'({expr} IS NULL OR {expr} = \'\')')
                else:
                    where_parts.append(f'{expr} = %s')
                    params.append(fval)

    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
        
    if limit is not None:
        sql += f" LIMIT {limit}"
    
    result = (sql, params)
    try:
        cache.set(cache_key, result, 300)
    except Exception:
        logger.warning("Cache unavailable for compiled SQL write", exc_info=True)
    return result


def compile_aggregated_dataset_to_sql(dataset, group_by_cols, y_col, aggregation, series_col=None, user=None, limit=MAX_CHART_ROWS):
    """
    Compiles a Dataset definition into aggregated SQL with GROUP BY.

    ``group_by_cols`` — list of ``table.column`` strings for the GROUP BY clause.
    ``y_col``          — ``table.column`` string for the value column to aggregate.
    ``aggregation``    — one of ``count, sum, avg, min, max``.
    ``series_col``     — optional ``table.column`` for series grouping (added to GROUP BY).

    Returns ``(sql_query_string, parameters_list)``.
    """
    SQL_FUNCS = {'count': 'COUNT', 'sum': 'SUM', 'avg': 'AVG', 'min': 'MIN', 'max': 'MAX'}
    agg_func = SQL_FUNCS.get(aggregation)
    if not agg_func:
        raise SQLCompilationError(f"Unsupported aggregation: {aggregation}")

    base_table = dataset.base_table
    allowed_tables = get_allowed_dataset_tables()
    if base_table not in allowed_tables:
        raise SQLCompilationError(f"Base table '{base_table}' is not whitelisted.")

    active_tables = {base_table}
    joins_list = dataset.joins or []
    for j in joins_list:
        join_table = j.get('table')
        if join_table not in allowed_tables:
            raise SQLCompilationError(f"Joined table '{join_table}' is not whitelisted.")
        active_tables.add(join_table)

    all_group_cols = list(group_by_cols)
    if series_col and series_col not in all_group_cols:
        all_group_cols.append(series_col)

    select_clauses = []
    group_clauses = []
    for col_str in all_group_cols:
        tbl, col = validate_column_identifier(col_str, active_tables)
        expr = format_column_expr(tbl, col)
        alias = col_str.replace('.', '_')
        select_clauses.append(f'{expr} AS "{alias}"')
        group_clauses.append(expr)

    # Add aggregated value column
    y_tbl, y_col_name = validate_column_identifier(y_col, active_tables)
    y_expr = format_column_expr(y_tbl, y_col_name)
    y_alias = y_col.replace('.', '_')
    select_clauses.append(f'{agg_func}({y_expr}) AS "{y_alias}"')

    select_sql = ", ".join(select_clauses)
    sql = f'SELECT {select_sql} FROM "{base_table}"'

    # Joins (same as compile_dataset_to_sql)
    join_clauses = []
    for j in joins_list:
        join_table = j.get('table')
        join_type = j.get('join_type', 'left').lower()
        join_keyword_map = {'left': 'LEFT JOIN', 'inner': 'INNER JOIN', 'right': 'RIGHT JOIN'}
        join_keyword = join_keyword_map.get(join_type, 'LEFT JOIN')
        conditions = j.get('conditions', [])
        if not conditions:
            conditions = [{'on_left': j.get('on_left'), 'on_right': j.get('on_right')}]
        on_clauses = []
        for cond in conditions:
            cond_left = cond.get('on_left')
            cond_right = cond.get('on_right')
            l_tbl, l_col = validate_column_identifier(cond_left, active_tables)
            r_tbl, r_col = validate_column_identifier(cond_right, active_tables)
            on_clauses.append(f'{format_column_expr(l_tbl, l_col)} = {format_column_expr(r_tbl, r_col)}')
        join_clauses.append(f'{join_keyword} "{join_table}" ON ' + " AND ".join(on_clauses))
    if join_clauses:
        sql += " " + " ".join(join_clauses)

    # Filters (same as compile_dataset_to_sql)
    where_parts = []
    params = []
    filters_list = dataset.filters or []
    for f in filters_list:
        col_str = f.get('column')
        operator = f.get('operator', 'exact').lower()
        val = f.get('value')
        tbl, col = validate_column_identifier(col_str, active_tables)
        expr = format_column_expr(tbl, col)
        if operator == 'exact':
            where_parts.append(f'{expr} = %s')
            params.append(val)
        elif operator == 'icontains':
            where_parts.append(f'{expr} ILIKE %s')
            params.append(f'%{val}%')
        elif operator == 'gt':
            where_parts.append(f'{expr} > %s')
            params.append(val)
        elif operator == 'lt':
            where_parts.append(f'{expr} < %s')
            params.append(val)
        elif operator == 'in':
            if isinstance(val, list):
                if val:
                    where_parts.append(f'{expr} = ANY(%s)')
                    params.append(val)
                else:
                    where_parts.append("1=0")
            else:
                where_parts.append(f'{expr} = %s')
                params.append(val)

    # School security filter injection (same as compile_dataset_to_sql)
    if user:
        is_unrestricted = False
        if user.is_superuser:
            is_unrestricted = True
        else:
            user_roles = get_user_roles(user)
            unrestricted_names = {'Project Admin', 'District Admin', 'District View'}
            if any(name in unrestricted_names or 'District' in name for name in user_roles):
                is_unrestricted = True
        if 'current_student' in active_tables:
            if hasattr(user, 'active_school') and user.active_school:
                where_parts.append('"current_student"."school_state_id" = %s')
                params.append(user.active_school.state_id)
            elif not is_unrestricted:
                user_schools = getattr(user, 'schools', [])
                if isinstance(user_schools, list) and user_schools:
                    where_parts.append('"current_student"."school_state_id" = ANY(%s)')
                    params.append(user_schools)
                else:
                    where_parts.append("1=0")
        elif 'student_enroll' in active_tables:
            if hasattr(user, 'active_school') and user.active_school:
                where_parts.append('"student_enroll"."state_school_code" = %s')
                params.append(user.active_school.state_id)
            elif not is_unrestricted:
                user_schools = getattr(user, 'schools', [])
                if isinstance(user_schools, list) and user_schools:
                    where_parts.append('"student_enroll"."state_school_code" = ANY(%s)')
                    params.append(user_schools)
                else:
                    where_parts.append("1=0")
        active_role_name = getattr(getattr(user, 'active_role', None), 'name', None)
        if active_role_name == 'School Gr View':
            delegated_grades = getattr(user, 'grade_levels', None)
            if isinstance(delegated_grades, list) and delegated_grades:
                if 'current_student' in active_tables:
                    where_parts.append('"current_student"."grade" = ANY(%s)')
                    params.append(delegated_grades)
                elif 'student_enroll' in active_tables:
                    where_parts.append('"student_enroll"."grade" = ANY(%s)')
                    params.append(delegated_grades)

        # Inject teacher / homeroom student-key restrictions
        if hasattr(user, 'get_accessible_student_keys'):
            keys = user.get_accessible_student_keys()
            if keys is not None:
                if keys:
                    if 'current_student' in active_tables:
                        where_parts.append('"current_student"."stu_annual_key" = ANY(%s)')
                        params.append(list(keys))
                    elif 'student_enroll' in active_tables:
                        where_parts.append('"student_enroll"."stu_annual_key" = ANY(%s)')
                        params.append(list(keys))
                else:
                    where_parts.append("1=0")  # No students assigned

    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    sql += " GROUP BY " + ", ".join(group_clauses)
    sql += f" LIMIT {limit}"

    return sql, params


def execute_compiled_query(sql, params):
    """
    Executes a compiled SQL query, routing through ClickHouse when available.
    Returns {'columns': [...], 'rows': [...], 'row_count': N, 'error': None}
    """
    from clickhouse_utils.executor import QueryExecutor

    executor = QueryExecutor()
    result = executor.execute(sql, params)
    return result


def execute_compiled_query_aliased(sql, params, column_mappings):
    """
    Same as execute_compiled_query(), but builds rows with column_mappings
    applied directly — skipping the intermediate "table.column"-keyed dict
    that execute_compiled_query() + apply_column_aliases() would otherwise
    build and then immediately discard. Only use this where the raw,
    unaliased dict is never needed (chart render/preview); other callers
    (drill-through, dataset preview) need the raw "table.column" keys and
    must keep using execute_compiled_query() + apply_column_aliases().
    """
    from clickhouse_utils.executor import QueryExecutor

    executor = QueryExecutor()
    result = executor.execute(sql, params)

    if result['error']:
        return result

    if column_mappings:
        aliased_rows = []
        for row in result['rows']:
            aliased_row = {column_mappings.get(k, k): v for k, v in row.items()}
            aliased_rows.append(aliased_row)
        result['rows'] = aliased_rows

    return result
