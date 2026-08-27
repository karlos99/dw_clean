import hashlib
import json
import logging
import re
from collections import Counter
from django.db import connection
from django.core.cache import cache
from dataexploration.utils import get_user_roles

from dataviz.utils.status_filter import _UNSET

logger = logging.getLogger(__name__)


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


def _allowed(tbl: str) -> set:
    return set(get_columns_for_table(tbl)) | set(COLUMN_WHITELIST.get(tbl, set()))


_DYNAMIC_ASSESSMENT_COL_RE = re.compile(r'^(test|mark)_(?:\d{4}|__sy_)')


def _is_dynamic_assessment_filter_key(fkey: str) -> bool:
    """True for exploration keys like test_2024_2025_iready_reading_PL, not tests.test_name."""
    return bool(_DYNAMIC_ASSESSMENT_COL_RE.match((fkey or '').replace('-', '_')))


def _resolve_prefixed_table_filter_key(fkey: str, col_index: dict, priority: list[str]) -> str | None:
    """Resolve dataset-builder collision keys like current_student_school_year."""
    for tbl in priority:
        prefix = f'{tbl}_'
        if not fkey.startswith(prefix):
            continue
        bare = fkey[len(prefix):]
        resolved = col_index.get(bare)
        if resolved and resolved.split('.', 1)[0] == tbl:
            return resolved
        if bare in _allowed(tbl):
            return f'{tbl}.{bare}'
    return None


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

    priority = [dataset.base_table] + [t for t in active_tables if t != dataset.base_table]
    allowed_tables = get_allowed_dataset_tables()

    out: dict = {}
    for fk, fval in filters.items():
        if not fval:
            continue
        if _is_dynamic_assessment_filter_key(fk):
            continue
        fval = _resolve_year_token(fval)
        if not fval:
            continue
        if '.' in fk:
            tbl, col = fk.split('.', 1)
            base = col.split('->', 1)[0]
            if tbl in allowed_tables and (col in _allowed(tbl) or base in _allowed(tbl)):
                out[fk] = fval
        else:
            resolved = col_index.get(fk)
            if not resolved:
                resolved = _resolve_prefixed_table_filter_key(fk, col_index, priority)
            if not resolved:
                for tbl in priority:
                    if fk in _allowed(tbl):
                        resolved = f"{tbl}.{fk}"
                        break
            if not resolved:
                # Resolve to current_student or student_enroll if they exist in schema and allowed,
                # so we can dynamically join them during SQL compilation.
                for tbl in ('current_student', 'student_enroll'):
                    if tbl in allowed_tables and fk in _allowed(tbl):
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
                    if table_name == 'current_student' and c == 'add_data' and 'school_year' in base_cols:
                        cursor.execute(
                            'SELECT DISTINCT jsonb_object_keys('
                            "CASE WHEN jsonb_typeof(add_data) = 'object' THEN add_data "
                            "WHEN jsonb_typeof(add_data) = 'string' "
                            "AND left(BTRIM(add_data #>> '{}'), 1) = '{' "
                            "THEN (add_data #>> '{}')::jsonb ELSE NULL END"
                            ') AS key FROM current_student '
                            'WHERE add_data IS NOT NULL AND school_year = ('
                            'SELECT MAX(school_year) FROM current_student'
                            ')'
                        )
                        for row in cursor.fetchall():
                            if row[0]:
                                final_cols.add(f"add_data->'{row[0]}'")
    except Exception:
        logger.warning(f"Failed to extract JSON columns for table {table_name}", exc_info=True)

    if table_name == 'current_student':
        from dataexploration.view_modules.column_management import known_add_data_json_paths
        final_cols.update(known_add_data_json_paths())

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
    JSON-list arrays (add_data->'l_race') are rendered as comma-joined text.
    """
    if '->' in col:
        base_col, key = col.split('->', 1)
        key_clean = key.strip("'\"")
        if base_col == 'add_data':
            from dataexploration.view_modules.column_management import (
                json_list_key_for_column,
                json_list_display_expr,
                JSON_SCALAR_COLUMNS,
                json_scalar_expr,
            )
            json_key = json_list_key_for_column(f"add_data->'{key_clean}'")
            if json_key:
                return json_list_display_expr(json_key, f'"{tbl}"')
            virt = next((c for c, k in JSON_SCALAR_COLUMNS.items() if k == key_clean), None)
            if virt:
                return json_scalar_expr(virt, f'"{tbl}"')
        return f'"{tbl}"."{base_col}"->>\'{key_clean}\''
    return f'"{tbl}"."{col}"'


def _append_dataset_json_list_filter(where_parts, params, tbl, json_key, val, operator='in'):
    """Any-of overlap filter for current_student.add_data JSON-list keys."""
    from dataexploration.view_modules.column_management import append_json_list_filter
    alias = f'"{tbl}"'
    if operator == 'exact':
        values = [val] if val not in (None, '') else []
    elif isinstance(val, list):
        values = val
    else:
        values = [val] if val not in (None, '') else []
    append_json_list_filter(where_parts, params, values, json_key, table_alias=alias)


def find_stu_annual_key_column(active_tables, columns_list=None) -> str | None:
    """Return ``table.stu_annual_key`` from selected columns or active tables."""
    if columns_list:
        for col_str in columns_list:
            if not isinstance(col_str, str):
                continue
            if col_str.endswith('.stu_annual_key') or col_str.split('.')[-1] == 'stu_annual_key':
                return col_str if '.' in col_str else None
    preferred = (
        'current_student', 'student_enroll', 'schedule_list',
        'student_marks_list', 'results',
    )
    for tbl in preferred:
        if tbl in active_tables and 'stu_annual_key' in _allowed(tbl):
            return f'{tbl}.stu_annual_key'
    for tbl in sorted(active_tables):
        if 'stu_annual_key' in _allowed(tbl):
            return f'{tbl}.stu_annual_key'
    return None


def dedupe_rows_by_stu_annual_key(rows: list) -> list:
    """Keep the first row for each non-blank ``stu_annual_key`` value."""
    if not rows:
        return rows
    key_col = None
    for k in rows[0].keys():
        if k == 'stu_annual_key' or str(k).split('.')[-1] == 'stu_annual_key':
            key_col = k
            break
    if not key_col:
        return rows
    seen = set()
    out = []
    for row in rows:
        val = row.get(key_col)
        if val is None or not str(val).strip():
            out.append(row)
            continue
        key = str(val).strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _is_results_json_column(col_str: str) -> bool:
    """True for ``results.other_info`` / ``results.other_info->'key'`` selections.

    The ``results`` table stores per-test additional info (DFP, writing domain
    scores, etc.) in ``other_info``. That info is frequently loaded as a
    *separate* ``results`` row from the one carrying ``ss``/``pl``, so the same
    student/test can appear twice — once with the JSON key populated and once
    without. ``SELECT DISTINCT`` cannot collapse those rows because the
    extracted ``other_info->'key'`` value differs (blank vs populated).
    """
    if not col_str.startswith('results.'):
        return False
    return '->' in col_str or col_str == 'results.other_info'


def compile_dataset_to_sql(
    dataset,
    user=None,
    limit=None,
    offset=None,
    interactive_filters=None,
    status_filter=_UNSET,
    order_by=None,
    one_row_per_stu_annual_key=False,
):
    """
    Compiles a Dataset definition object into parameterized SQL.
    Returns (sql_query_string, parameters_list).

    When ``one_row_per_stu_annual_key`` is True, emit
    ``SELECT DISTINCT ON (stu_annual_key)`` so join fan-out collapses to one
    enrollment-year record (mirrors chart / drill counts).
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
            'offset': offset,
            'updated': str(getattr(dataset, 'updated_at', '')),
            'interactive_filters': interactive_filters or {},
            'status_filter': '__unset__' if status_filter is _UNSET else status_filter,
            'order_by': order_by or getattr(dataset, 'order_by', None) or [],
            'one_row_per_stu_annual_key': bool(one_row_per_stu_annual_key),
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

    # Dynamic join injection for unjoined whitelisted tables referenced in filters
    if interactive_filters:
        for target_tbl in ('current_student', 'student_enroll'):
            has_filter = any(fkey.startswith(f'{target_tbl}.') for fkey in interactive_filters)
            if has_filter and target_tbl not in active_tables:
                join_target = None
                join_col_self = None
                join_col_target = None
                
                # Priority 1: stu_annual_key
                for t in active_tables:
                    if 'stu_annual_key' in _allowed(t) and 'stu_annual_key' in _allowed(target_tbl):
                        join_target = t
                        join_col_self = 'stu_annual_key'
                        join_col_target = 'stu_annual_key'
                        break
                # Priority 2: student_id
                if not join_target:
                    for t in active_tables:
                        if 'student_id' in _allowed(t) and 'student_id' in _allowed(target_tbl):
                            join_target = t
                            join_col_self = 'student_id'
                            join_col_target = 'student_id'
                            break
                # Priority 3: ssid
                if not join_target:
                    for t in active_tables:
                        if 'ssid' in _allowed(t) and 'ssid' in _allowed(target_tbl):
                            join_target = t
                            join_col_self = 'ssid'
                            join_col_target = 'ssid'
                            break
                            
                if join_target:
                    extra_join_conds = []
                    if 'school_year' in _allowed(join_target) and 'school_year' in _allowed(target_tbl):
                        extra_join_conds.append({
                            'on_left': f'{target_tbl}.school_year',
                            'on_right': f'{join_target}.school_year'
                        })
                    joins_list = list(joins_list)
                    joins_list.append({
                        'table': target_tbl,
                        'join_type': 'left',
                        'conditions': [
                            {
                                'on_left': f'{target_tbl}.{join_col_self}',
                                'on_right': f'{join_target}.{join_col_target}'
                            }
                        ] + extra_join_conds
                    })
                    active_tables.add(target_tbl)
        
    columns_list = list(dataset.columns or [])
    if not columns_list:
        raise SQLCompilationError("Dataset has no select columns specified.")

    sak_col = None
    sak_expr = None
    if one_row_per_stu_annual_key:
        sak_col = find_stu_annual_key_column(active_tables, columns_list)
        if sak_col and sak_col not in columns_list:
            columns_list.append(sak_col)
        if sak_col:
            sak_tbl, sak_name = validate_column_identifier(sak_col, active_tables)
            sak_expr = format_column_expr(sak_tbl, sak_name)

    # Duplicate `results` rows (one `other_info` carries the DFP/additional-info
    # JSON, one doesn't) survive `SELECT DISTINCT` because the extracted
    # `results.other_info->'key'` value differs between them — the same student
    # then shows up twice, once with data and once blank. When a results JSON
    # column is selected, collapse each set of rows that are identical in every
    # other selected column to one, keeping the non-blank JSON value via MAX.
    results_json_cols = [c for c in columns_list if _is_results_json_column(c)]
    dedupe_results = bool(results_json_cols) and not sak_expr

    select_clauses = []
    group_by_exprs = []
    for col_str in columns_list:
        tbl, col = validate_column_identifier(col_str, active_tables)
        expr = format_column_expr(tbl, col)
        alias = f'"{tbl}.{col}"'
        if dedupe_results and col_str in results_json_cols:
            agg_expr = f"MAX(NULLIF({expr}, ''))" if '->' in col_str else f"MAX({expr})"
            select_clauses.append(f'{agg_expr} AS {alias}')
        else:
            select_clauses.append(f'{expr} AS {alias}')
            group_by_exprs.append(expr)

    select_sql = ", ".join(select_clauses)

    # Construct base query. DISTINCT ON (stu_annual_key) wins over row-level DISTINCT
    # when the chart asks for one enrollment-year record; results-JSON dedupe uses
    # GROUP BY (which also collapses the duplicates) and overrides row-level DISTINCT.
    if sak_expr:
        select_keyword = f"SELECT DISTINCT ON ({sak_expr})"
    elif dedupe_results:
        select_keyword = "SELECT"
    elif getattr(dataset, 'distinct', True):
        select_keyword = "SELECT DISTINCT"
    else:
        select_keyword = "SELECT"
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

        from dataexploration.view_modules.column_management import json_list_key_for_column
        json_key = json_list_key_for_column(col_str) or json_list_key_for_column(col)
        if json_key:
            _append_dataset_json_list_filter(where_parts, params, tbl, json_key, val, operator)
            continue

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
        if 'current_student' in active_tables and not is_unrestricted:
            if hasattr(user, 'active_school') and user.active_school:
                where_parts.append('"current_student"."school_state_id" = %s')
                params.append(user.active_school.state_id)
            else:
                user_schools = getattr(user, 'schools', [])
                if isinstance(user_schools, list) and user_schools:
                    where_parts.append('"current_student"."school_state_id" = ANY(%s)')
                    params.append(user_schools)
                else:
                    where_parts.append("1=0")
                    
        # Inject context filter on student_enroll if present and current_student not present
        elif 'student_enroll' in active_tables and not is_unrestricted:
            if hasattr(user, 'active_school') and user.active_school:
                where_parts.append('"student_enroll"."state_school_code" = %s')
                params.append(user.active_school.state_id)
            else:
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

    # Status filter — callers pass this explicitly. Dataset compilation must not
    # implicitly restrict rows to Active when no status was selected.
    from dataviz.utils.status_filter import append_quoted_status_where, normalize_status_value
    if status_filter is not _UNSET:
        resolved_status = status_filter
    elif interactive_filters:
        scratch = dict(interactive_filters)
        resolved_status = normalize_status_value(scratch, pop=True)
        interactive_filters = scratch
    else:
        resolved_status = None
    if 'current_student' in active_tables:
        append_quoted_status_where(where_parts, params, resolved_status, table='current_student')

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

            from dataexploration.view_modules.column_management import json_list_key_for_column
            json_key = json_list_key_for_column(fname) or json_list_key_for_column(fkey)
            if json_key:
                _append_dataset_json_list_filter(where_parts, params, ftbl, json_key, fval, 'in')
                continue
            
            # Add filter condition
            expr = format_column_expr(ftbl, fname)
            if isinstance(fval, dict):
                gte = fval.get('gte')
                lte = fval.get('lte')
                exclude = fval.get('exclude')
                if exclude:
                    conds = []
                    if gte:
                        conds.append(f'{expr} < %s')
                        params.append(gte)
                    if lte:
                        conds.append(f'{expr} > %s')
                        params.append(lte)
                    if conds:
                        where_parts.append(f"({' OR '.join(conds)} OR {expr} IS NULL)")
                else:
                    if gte:
                        where_parts.append(f'{expr} >= %s')
                        params.append(gte)
                    if lte:
                        where_parts.append(f'{expr} <= %s')
                        params.append(lte)
            elif isinstance(fval, list):
                vals = [v for v in fval if v]
                if not vals:
                    continue
                has_blank = '(Blank)' in vals
                others = [v for v in vals if v != '(Blank)']
                parts_list = []
                if others:
                    parts_list.append(f'{expr} = ANY(%s)')
                    params.append(others)
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

    # Results-JSON dedupe: GROUP BY on every non-JSON selected column collapses
    # the duplicate `results` rows into one per unique combination of those
    # columns (the MAX'd JSON column carries the populated value).
    if dedupe_results and group_by_exprs:
        sql += " GROUP BY " + ", ".join(group_by_exprs)

    # ORDER BY clause — accepts a list of {"column": "table.col", "direction": "asc"|"desc"}
    # DISTINCT ON requires its expressions to be the leftmost ORDER BY keys.
    order_list = order_by or getattr(dataset, 'order_by', None) or []
    order_parts = []
    if sak_expr:
        order_parts.append(f'{sak_expr} ASC')
        # one_row_per_stu_annual_key: DISTINCT ON (stu_annual_key) picks ONE row
        # per student — among duplicate `results` rows (one carries the JSON
        # value e.g. dfp, one doesn't) it must prefer the populated row, or the
        # chart/drill show a blank DFP for that student.
        for col_str in results_json_cols:
            _tbl, _col = validate_column_identifier(col_str, active_tables)
            _expr = format_column_expr(_tbl, _col)
            if '->' in col_str:
                order_parts.append(f'(NULLIF({_expr}, \'\') IS NOT NULL) DESC')
            else:
                order_parts.append(f'({_expr} IS NOT NULL) DESC')
    if order_list:
        for ob in order_list:
            col_str = ob.get('column')
            direction = (ob.get('direction') or 'asc').lower()
            if direction not in ('asc', 'desc'):
                direction = 'asc'
            if not col_str:
                continue
            tbl, col = validate_column_identifier(col_str, active_tables)
            expr = format_column_expr(tbl, col)
            if dedupe_results and _is_results_json_column(col_str):
                expr = f"MAX(NULLIF({expr}, ''))" if '->' in col_str else f"MAX({expr})"
            if sak_expr and expr == sak_expr:
                continue
            order_parts.append(f'{expr} {direction.upper()}')
    if order_parts:
        sql += " ORDER BY " + ", ".join(order_parts)
    
    if limit is not None:
        sql += f" LIMIT {limit}"
        if offset is not None and offset > 0:
            sql += f" OFFSET {int(offset)}"
    
    result = (sql, params)
    try:
        cache.set(cache_key, result, 300)
    except Exception:
        logger.warning("Cache unavailable for compiled SQL write", exc_info=True)
    return result


def compile_aggregated_dataset_to_sql(dataset, group_by_cols, y_col, aggregation, series_col=None, user=None, limit=None):
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
        from dataexploration.view_modules.column_management import json_list_key_for_column
        json_key = json_list_key_for_column(col_str) or json_list_key_for_column(col)
        if json_key:
            _append_dataset_json_list_filter(where_parts, params, tbl, json_key, val, operator)
            continue
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
        if 'current_student' in active_tables and not is_unrestricted:
            if hasattr(user, 'active_school') and user.active_school:
                where_parts.append('"current_student"."school_state_id" = %s')
                params.append(user.active_school.state_id)
            else:
                user_schools = getattr(user, 'schools', [])
                if isinstance(user_schools, list) and user_schools:
                    where_parts.append('"current_student"."school_state_id" = ANY(%s)')
                    params.append(user_schools)
                else:
                    where_parts.append("1=0")
        elif 'student_enroll' in active_tables and not is_unrestricted:
            if hasattr(user, 'active_school') and user.active_school:
                where_parts.append('"student_enroll"."state_school_code" = %s')
                params.append(user.active_school.state_id)
            else:
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
    if limit is not None:
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


def load_dataset_result(
    dataset,
    *,
    user=None,
    interactive_filters=None,
    limit=None,
    status_filter=_UNSET,
    one_per_sak=False,
    apply_aliases=True,
):
    """Execute a Dataset (visual join or custom SQL) and return row dicts."""
    from dataviz.models import Dataset as DatasetModel
    from dataviz.utils.data_loader import execute_sql_dataset
    from dataviz.utils.status_filter import dataset_status_value

    source_type = getattr(dataset, 'source_type', None) or DatasetModel.SOURCE_VISUAL
    col_mappings = dataset.column_mappings or generate_column_aliases(dataset.columns or [], {})

    if source_type == DatasetModel.SOURCE_SQL:
        res = execute_sql_dataset(dataset.source_sql or '', limit=limit)
        if res.get('error'):
            return res
        rows = res.get('rows') or []
        if apply_aliases and col_mappings:
            rows = apply_column_aliases(rows, col_mappings)
        if one_per_sak:
            rows = dedupe_rows_by_stu_annual_key(rows)
        columns = list(rows[0].keys()) if rows else (res.get('columns') or [])
        return {
            'columns': columns,
            'rows': rows,
            'row_count': len(rows),
            'error': None,
        }

    if status_filter is _UNSET:
        status_filter = dataset_status_value(dataset)
    sql, params = compile_dataset_to_sql(
        dataset,
        user=user,
        interactive_filters=interactive_filters or {},
        limit=limit,
        status_filter=status_filter,
        one_row_per_stu_annual_key=one_per_sak,
    )
    if apply_aliases:
        return execute_compiled_query_aliased(sql, params, col_mappings)
    return execute_compiled_query(sql, params)
