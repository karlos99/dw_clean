import hashlib
import json
import logging
import re
from django.db import connection
from django.core.cache import cache
from dataexploration.utils import get_user_roles

logger = logging.getLogger(__name__)

MAX_CHART_ROWS = 50000

TABLE_WHITELIST = {
    'current_student': 'current_student',
    'student_enroll': 'student_enroll',
    'school': 'school',
    'tests': 'tests',
    'results': 'results',
    'schedule_list': 'schedule_list',
    'student_marks_list': 'student_marks_list',
    'users_user': 'users_user',
}

COLUMN_WHITELIST = {
    'current_student': {
        'stu_annual_key', 'ssid', 'student_id', 'student_name', 'grade', 'school',
        'school_state_id', 'gender', 'race', 'ell', 'sed', 'foster', 'homeless',
        'sped', 'gate', 'in_504', 'dli', 'atendace_rate', 'suspension_indicator',
        'ltel', 'status', 'school_year'
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
    Validates a column identifier of the format 'table_name.column_name'.
    Ensures table is active in the query and column is whitelisted.
    Returns (table_name, column_name).
    """
    parts = col_str.split('.')
    if len(parts) != 2:
        raise SQLCompilationError(f"Invalid column format: '{col_str}'. Expected 'table.column'")
    
    table_name, col_name = parts
    
    if table_name not in active_tables:
        raise SQLCompilationError(f"Table '{table_name}' is not in active query tables list.")
    
    if table_name not in COLUMN_WHITELIST:
        raise SQLCompilationError(f"Table '{table_name}' is not in column whitelist.")
        
    if col_name not in COLUMN_WHITELIST[table_name]:
        raise SQLCompilationError(f"Column '{col_name}' is not in whitelist for table '{table_name}'.")
        
    return table_name, col_name


def compile_dataset_to_sql(dataset, user=None, limit=MAX_CHART_ROWS):
    """
    Compiles a Dataset definition object into parameterized SQL.
    Returns (sql_query_string, parameters_list).
    """
    # Cache compiled SQL keyed on dataset content + user context
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
    if base_table not in TABLE_WHITELIST:
        raise SQLCompilationError(f"Base table '{base_table}' is not whitelisted.")
    
    active_tables = {base_table}
    joins_list = dataset.joins or []
    for j in joins_list:
        join_table = j.get('table')
        if join_table not in TABLE_WHITELIST:
            raise SQLCompilationError(f"Joined table '{join_table}' is not whitelisted.")
        active_tables.add(join_table)
        
    columns_list = dataset.columns or []
    if not columns_list:
        raise SQLCompilationError("Dataset has no select columns specified.")
        
    select_clauses = []
    for col_str in columns_list:
        tbl, col = validate_column_identifier(col_str, active_tables)
        # Select as "table_name.column_name" alias
        select_clauses.append(f'"{tbl}"."{col}" AS "{tbl}.{col}"')
        
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
            on_clauses.append(f'"{l_tbl}"."{l_col}" = "{r_tbl}"."{r_col}"')
            
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
        
        if operator == 'exact':
            where_parts.append(f'"{tbl}"."{col}" = %s')
            params.append(val)
        elif operator == 'icontains':
            where_parts.append(f'"{tbl}"."{col}" ILIKE %s')
            params.append(f'%{val}%')
        elif operator == 'gt':
            where_parts.append(f'"{tbl}"."{col}" > %s')
            params.append(val)
        elif operator == 'lt':
            where_parts.append(f'"{tbl}"."{col}" < %s')
            params.append(val)
        elif operator == 'in':
            if isinstance(val, list):
                if val:
                    where_parts.append(f'"{tbl}"."{col}" = ANY(%s)')
                    params.append(val)
                else:
                    where_parts.append("1=0")  # Empty list matches nothing
            else:
                # Fallback to single value
                where_parts.append(f'"{tbl}"."{col}" = %s')
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

    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
        
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
    if base_table not in TABLE_WHITELIST:
        raise SQLCompilationError(f"Base table '{base_table}' is not whitelisted.")

    active_tables = {base_table}
    joins_list = dataset.joins or []
    for j in joins_list:
        join_table = j.get('table')
        if join_table not in TABLE_WHITELIST:
            raise SQLCompilationError(f"Joined table '{join_table}' is not whitelisted.")
        active_tables.add(join_table)

    all_group_cols = list(group_by_cols)
    if series_col and series_col not in all_group_cols:
        all_group_cols.append(series_col)

    select_clauses = []
    group_clauses = []
    for col_str in all_group_cols:
        tbl, col = validate_column_identifier(col_str, active_tables)
        alias = col_str.replace('.', '_')
        select_clauses.append(f'"{tbl}"."{col}" AS "{alias}"')
        group_clauses.append(f'"{tbl}"."{col}"')

    # Add aggregated value column
    y_tbl, y_col_name = validate_column_identifier(y_col, active_tables)
    y_alias = y_col.replace('.', '_')
    select_clauses.append(f'{agg_func}("{y_tbl}"."{y_col_name}") AS "{y_alias}"')

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
            on_clauses.append(f'"{l_tbl}"."{l_col}" = "{r_tbl}"."{r_col}"')
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
        if operator == 'exact':
            where_parts.append(f'"{tbl}"."{col}" = %s')
            params.append(val)
        elif operator == 'icontains':
            where_parts.append(f'"{tbl}"."{col}" ILIKE %s')
            params.append(f'%{val}%')
        elif operator == 'gt':
            where_parts.append(f'"{tbl}"."{col}" > %s')
            params.append(val)
        elif operator == 'lt':
            where_parts.append(f'"{tbl}"."{col}" < %s')
            params.append(val)
        elif operator == 'in':
            if isinstance(val, list):
                if val:
                    where_parts.append(f'"{tbl}"."{col}" = ANY(%s)')
                    params.append(val)
                else:
                    where_parts.append("1=0")
            else:
                where_parts.append(f'"{tbl}"."{col}" = %s')
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

    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    sql += " GROUP BY " + ", ".join(group_clauses)
    sql += f" LIMIT {limit}"

    return sql, params


def execute_compiled_query(sql, params):
    """
    Executes a compiled SQL query in a read-only transaction.
    Returns {'columns': [...], 'rows': [...], 'row_count': N, 'error': None}
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("BEGIN READ ONLY")
            try:
                cursor.execute(sql, params)
                if cursor.description is None:
                    return {
                        'columns': [],
                        'rows': [],
                        'row_count': 0,
                        'error': None
                    }
                columns = [col[0] for col in cursor.description]
                raw = cursor.fetchall()
                rows = [dict(zip(columns, row)) for row in raw]
            finally:
                cursor.execute("ROLLBACK")
                
        return {
            'columns': columns,
            'rows': rows,
            'row_count': len(rows),
            'error': None
        }
    except Exception as exc:
        logger.exception("Error executing compiled visual dataset query")
        return {
            'columns': [],
            'rows': [],
            'row_count': 0,
            'error': str(exc)
        }
