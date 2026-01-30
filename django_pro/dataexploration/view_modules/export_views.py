"""CSV export functionality for student data."""

from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse
from django.db import connections
from django.core.cache import cache
import json
import csv
import hashlib

from ..models import SavedQuery, StudentList
from ..constants import COLUMN_DEFINITIONS
from ..utils import can_user_view_query, get_user_folders
from .student_data_utils import (
    get_current_student_columns,
    get_student_enroll_columns,
)
from .column_management import (
    get_saved_default_columns,
    get_column_expression,
)

@login_required
def export_students_csv(request):
    """Export filtered student data as CSV."""
    
    # 1. Parse parameters (Shared logic with load_students)
    saved_query_id = request.GET.get('saved_query')
    saved_config = {}
    if saved_query_id:
        try:
            query_obj = SavedQuery.objects.get(pk=saved_query_id)
            if can_user_view_query(request.user, query_obj):
                saved_config = query_obj.config
        except SavedQuery.DoesNotExist:
            pass
        except Exception:
            pass
    
    selected_columns_json = request.GET.get('columns', '')
    if selected_columns_json:
        try:
            selected_columns = json.loads(selected_columns_json)
        except:
            selected_columns = saved_config.get('columns') or get_saved_default_columns()
    elif saved_config.get('columns'):
        selected_columns = saved_config.get('columns')
    else:
        selected_columns = get_saved_default_columns()
    
    # Extract saved filters (they may be nested under 'filters' key or at top level)
    saved_filters = saved_config.get('filters', {}) if isinstance(saved_config.get('filters'), dict) else {}
    
    # Get filters - URL params take precedence over saved config
    search = request.GET.get('search', '') or saved_filters.get('search', '') or saved_config.get('search', '')
    school_year = request.GET.get('school_year', '') or saved_filters.get('school_year', '') or saved_config.get('school_year', '')
    grade = request.GET.get('grade', '') or saved_filters.get('grade', '') or saved_config.get('grade', '')
    
    status_param = request.GET.get('status')
    if status_param is not None:
        status = status_param
    elif 'status' in saved_filters:
        status = saved_filters['status']
    elif 'status' in saved_config:
        status = saved_config['status']
    else:
        status = 'Active'

    sort_by = request.GET.get('sort_by', '') or saved_config.get('sort_by', '')
    sort_dir = request.GET.get('sort_dir', 'asc').lower()

    filters_json = request.GET.get('filters', '')
    filters = {}
    if filters_json:
        try:
            filters = json.loads(filters_json)
        except:
            filters = saved_filters.copy()
    else:
        # Use saved filters, excluding the basic search/school_year/grade/status which are handled separately
        filters = {k: v for k, v in saved_filters.items() if k not in ['search', 'school_year', 'grade', 'status']}

    # Helper for column filters (Duplicated from load_students to avoid refactoring risk)
    def _apply_column_filters(where_clauses, params, filters_to_apply, test_col_map, db_columns, enroll_columns, needs_enroll, needs_students=False):
        if not isinstance(filters_to_apply, dict): return
        for col, values in filters_to_apply.items():
            if not values: continue

            # Handle blank values (represented by empty string)
            has_blank = "" in values
            real_values = [v for v in values if v != ""]

            if col.startswith('test_'):
                test_info = test_col_map.get(col) or test_col_map.get(col.replace('-', '_'))
                if not test_info: continue
                metric_col = test_info['metric']
                window_clause = ""
                window_params = []
                if test_info.get('test_window'):
                    window_clause = "AND t.test_window ILIKE %s "
                    window_params = [test_info['test_window']]

                exist_query_base = (
                    "SELECT 1 FROM results r "
                    "INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                    "WHERE r.local_student_id = cs.student_id "
                    "AND r.school_year = %s "
                    "AND t.test_name ILIKE %s "
                    "AND t.subject ILIKE %s "
                    f"{window_clause}"
                )
                exist_params_base = [
                    test_info['year'], f"%{test_info['test_name']}%", f"%{test_info['subject']}%",
                    *window_params,
                ]

                conditions = []

                if real_values:
                    val_clause = exist_query_base
                    if metric_col in {'ss', 'pl'}:
                        val_clause += f"AND r.{metric_col} = ANY(%s)"
                    else:
                        val_clause += "AND (r.other_info ->> %s) = ANY(%s)"
                    
                    val_params = list(exist_params_base)
                    if metric_col == 'oi':
                         val_params.append(test_info['other_info_key'])
                    val_params.append(real_values)
                    
                    conditions.append(f"EXISTS ({val_clause})")
                    params.extend(val_params)

                if has_blank:
                    blank_subconditions = []
                    
                    # 1. No record exists
                    blank_subconditions.append(f"NOT EXISTS ({exist_query_base})")
                    params.extend(exist_params_base)

                    # 2. Record exists but value is NULL
                    null_clause = exist_query_base
                    null_params = list(exist_params_base)
                    
                    if metric_col in {'ss', 'pl'}:
                        null_clause += f"AND r.{metric_col} IS NULL"
                    else:
                        null_clause += "AND (r.other_info ->> %s) IS NULL"
                        null_params.append(test_info['other_info_key'])
                    
                    blank_subconditions.append(f"EXISTS ({null_clause})")
                    params.extend(null_params)
                    
                    conditions.append(f"({' OR '.join(blank_subconditions)})")
                
                if conditions:
                    where_clauses.append(f"({' OR '.join(conditions)})")
                continue

            expr, _ = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students)
            if expr:
                 conditions = []
                 if real_values:
                      conditions.append(f"{expr} = ANY(%s)")
                      params.append(real_values)
                 if has_blank:
                      if col in {'birth_date', 'us_enter_date', 'fep_date'}:
                           conditions.append(f"{expr} IS NULL")
                      else:
                           conditions.append(f"({expr} IS NULL OR {expr} = '')")
                 
                 if conditions:
                      where_clauses.append(f"({' OR '.join(conditions)})")

    # 2. Preparation for Query
    db_columns = get_current_student_columns()
    enroll_columns = get_student_enroll_columns()
    
    # Pre-calculate test column metadata
    selected_test_cols = [c for c in selected_columns if isinstance(c, str) and c.startswith('test_')]
    base_cols = [c for c in selected_columns if isinstance(c, str) and (not c.startswith('test_')) and c in COLUMN_DEFINITIONS]

    enroll_fields = {'ell', 'birth_date', 'us_enter_date', 'fep_date', 'migrant'}
    needs_enroll = (
        any(c in enroll_fields for c in selected_columns)
        or (isinstance(filters, dict) and any(k in enroll_fields for k in filters.keys()))
        or sort_by in enroll_fields
    )

    # Determine if we need students join (for ltel overrides)
    students_fields = {'ltel'}
    needs_students = (
        any(c in students_fields for c in selected_columns)
        or (isinstance(filters, dict) and any(k in students_fields for k in filters.keys()))
        or sort_by in students_fields
    )

    join_sql = ""
    if needs_enroll:
        join_sql += " LEFT JOIN student_enroll se ON se.student_id = cs.student_id AND se.school_year = cs.school_year"
    if needs_students:
        join_sql += " LEFT JOIN students s ON s.state_student_number = cs.ssid"

    # Streaming Response setup
    class Echo:
        def write(self, value): return value
    
    pseudo_buffer = Echo()
    writer = csv.writer(pseudo_buffer)
    
    def stream_csv():
        try:
            # Yield Header
            header_labels = [COLUMN_DEFINITIONS.get(col, col) for col in selected_columns]
            yield writer.writerow(header_labels)

            with connections['default'].cursor() as cursor:
                # Re-implement helper logic inline or simplified
                def _parse_year_from_test_col(col_key):
                    s = (col_key or '').replace('-', '_')
                    if not s.startswith('test_'): return None
                    tail = s[len('test_'):]
                    if len(tail) >= 10 and tail[4] == '_' and tail[9] == '_':
                        return tail[:9].replace('_', '-')
                    parts = tail.split('_', 1)
                    return parts[0].replace('_', '-') if parts else None

                # Build test info
                test_cols_info = []
                test_col_map = {}
                if selected_test_cols:
                    normalized_to_original = {c.replace('-', '_'): c for c in selected_test_cols}
                    years = sorted({y for y in (_parse_year_from_test_col(c) for c in selected_test_cols) if y})
                    if years:
                        cursor.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'tests' AND column_name = 'test_window'")
                        has_test_window = cursor.fetchone() is not None
                        
                        sql = """
                            SELECT DISTINCT r.school_year, t.test_name, t.subject, COALESCE(t.test_window, '')
                            FROM results r INNER JOIN tests t ON r.test_group_key = t.test_group_key
                            WHERE r.school_year = ANY(%s) AND r.school_year IS NOT NULL AND t.test_name IS NOT NULL
                        """ if has_test_window else """
                            SELECT DISTINCT r.school_year, t.test_name, t.subject, ''
                            FROM results r INNER JOIN tests t ON r.test_group_key = t.test_group_key
                            WHERE r.school_year = ANY(%s) AND r.school_year IS NOT NULL AND t.test_name IS NOT NULL
                        """
                        cursor.execute(sql, [years])
                        
                        def _norm_part(v): return (v or '').replace(' ', '_').replace('-', '_')
                        
                        rows = cursor.fetchall()
                        for (yr, test_name, subject, test_window) in rows:
                            yr_norm = _norm_part(yr)
                            tn_norm = _norm_part(test_name)
                            sub_norm = _norm_part(subject)
                            win_norm = _norm_part(test_window).strip('_')
                            key_base = f"{yr_norm}_{tn_norm}_{sub_norm}_{win_norm}" if win_norm else f"{yr_norm}_{tn_norm}_{sub_norm}"
                            
                            for metric in ('ss', 'pl'):
                                norm_key = f"test_{key_base}_{metric.upper()}"
                                original_key = normalized_to_original.get(norm_key)
                                if original_key:
                                    info = {
                                        'col_name': original_key, 'year': yr, 'test_name': (test_name or ''), 
                                        'subject': (subject or ''), 'test_window': (test_window or '').strip() or None, 'metric': metric
                                    }
                                    test_cols_info.append(info)
                                    test_col_map[original_key] = info
                                    test_col_map[original_key.replace('-', '_')] = info

                        # Other Info logic implementation from load_students
                        want_other = any('_OI_' in k for k in normalized_to_original.keys())
                        if want_other:
                            if has_test_window:
                                cursor.execute(
                                    """
                                    SELECT DISTINCT r.school_year, t.test_name, t.subject, COALESCE(t.test_window, ''), k.key
                                    FROM results r
                                    INNER JOIN tests t ON r.test_group_key = t.test_group_key
                                    CROSS JOIN LATERAL jsonb_object_keys(r.other_info) AS k(key)
                                    WHERE r.school_year = ANY(%s)
                                    AND r.school_year IS NOT NULL
                                    AND t.test_name IS NOT NULL
                                    AND r.other_info IS NOT NULL
                                    """,
                                    [years],
                                )
                            else:
                                cursor.execute(
                                    """
                                    SELECT DISTINCT r.school_year, t.test_name, t.subject, '', k.key
                                    FROM results r
                                    INNER JOIN tests t ON r.test_group_key = t.test_group_key
                                    CROSS JOIN LATERAL jsonb_object_keys(r.other_info) AS k(key)
                                    WHERE r.school_year = ANY(%s)
                                    AND r.school_year IS NOT NULL
                                    AND t.test_name IS NOT NULL
                                    AND r.other_info IS NOT NULL
                                    """,
                                    [years],
                                )

                            for (yr, test_name, subject, test_window, raw_key) in cursor.fetchall():
                                if not raw_key: continue
                                yr_norm = _norm_part(yr)
                                tn_norm = _norm_part(test_name)
                                sub_norm = _norm_part(subject)
                                win_norm = _norm_part(test_window).strip('_')

                                if win_norm:
                                    key_base = f"{yr_norm}_{tn_norm}_{sub_norm}_{win_norm}"
                                    win_value = (test_window or '').strip() or None
                                else:
                                    key_base = f"{yr_norm}_{tn_norm}_{sub_norm}"
                                    win_value = None

                                raw_key_str = str(raw_key)
                                oi_norm = _norm_part(raw_key_str)
                                oi_hash = hashlib.md5(raw_key_str.encode('utf-8')).hexdigest()[:6]
                                other_norm = f"test_{key_base}_OI_{oi_norm}_{oi_hash}"

                                original_key = normalized_to_original.get(other_norm)
                                if not original_key: continue
                                
                                info = {
                                    'col_name': original_key,
                                    'year': yr,
                                    'test_name': (test_name or ''),
                                    'subject': (subject or ''),
                                    'test_window': win_value,
                                    'metric': 'oi',
                                    'other_info_key': raw_key_str,
                                }
                                test_cols_info.append(info)
                                test_col_map[original_key] = info
                                test_col_map[original_key.replace('-', '_')] = info

                # Build Where Clauses
                base_where_clauses = ["1=1"]
                base_params = []

                # Local copies for streaming scope
                search_value = search
                school_year_value = school_year
                grade_value = grade
                status_value = status
                filters_value = filters
                
                student_list_id = request.GET.get('student_list')
                if student_list_id:
                    list_ssids = None

                    # Preferred: student lists stored as SavedQuery(query_type='student_list')
                    try:
                        slq = SavedQuery.objects.get(pk=student_list_id, query_type='student_list')
                        if can_user_view_query(request.user, slq):
                            list_ssids = slq.config.get('student_ids') or []
                    except SavedQuery.DoesNotExist:
                        list_ssids = None
                    except Exception:
                        list_ssids = None

                    # Legacy fallback: StudentList / StudentListItem
                    if list_ssids is None:
                        try:
                            slist = StudentList.objects.get(pk=student_list_id)
                            if slist.folder in get_user_folders(request.user):
                                base_where_clauses.append(
                                    "cs.ssid IN (SELECT ssid FROM dataexploration_studentlistitem WHERE student_list_id = %s)"
                                )
                                base_params.append(student_list_id)
                                list_ssids = None
                        except Exception:
                            pass

                    if isinstance(list_ssids, list) and list_ssids:
                        normalized_ssids = [str(s).strip() for s in list_ssids if str(s).strip()]
                        if normalized_ssids:
                            base_where_clauses.append("cs.ssid = ANY(%s)")
                            base_params.append(normalized_ssids)

                            # Override user-driven filters; the list defines membership.
                            search_value = None
                            school_year_value = None
                            grade_value = None
                            status_value = None
                            filters_value = {}

                if search_value:
                    base_where_clauses.append("(cs.student_name ILIKE %s OR cs.ssid ILIKE %s OR cs.student_id ILIKE %s)")
                    base_params.extend([f'%{search_value}%']*3)
                if school_year_value:
                    base_where_clauses.append("cs.school_year = %s")
                    base_params.append(school_year_value)
                if grade_value:
                    base_where_clauses.append("cs.grade = %s")
                    base_params.append(grade_value)
                if status_value:
                    base_where_clauses.append("cs.status = %s")
                    base_params.append(status_value)

                where_clauses = list(base_where_clauses)
                params = list(base_params)
                _apply_column_filters(where_clauses, params, filters_value, test_col_map, db_columns, enroll_columns, needs_enroll, needs_students)
                where_sql = " AND ".join(where_clauses)
                
                # Fetch Loop
                chunk_size = 500
                offset = 0
                
                # Ensure we fetch minimal required columns plus sorting
                query_base_cols = list(base_cols)
                if 'ssid' not in query_base_cols: query_base_cols.insert(0, 'ssid')
                if test_cols_info and 'student_id' not in query_base_cols: query_base_cols.insert(0, 'student_id')
                
                base_col_selects = []
                base_col_expressions = {}
                for col in query_base_cols:
                    expr, alias = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students)
                    if expr and alias:
                        base_col_expressions[col] = expr
                        base_col_selects.append(f"{expr} AS {alias}" if expr != f"cs.{alias}" else expr)
                
                if not base_col_selects:
                    base_col_selects.append("cs.ssid")

                base_col_sql = ", ".join(base_col_selects)
                
                order_by_expr = "cs.student_name"
                if sort_by:
                    expr = base_col_expressions.get(sort_by)
                    if expr: order_by_expr = expr
                
                dir_sql = 'DESC' if sort_dir == 'desc' else 'ASC'

                while True:
                    query = f"""
                        SELECT {base_col_sql}
                        FROM current_student cs
                        {join_sql}
                        WHERE {where_sql}
                        ORDER BY {order_by_expr} {dir_sql}
                        LIMIT %s OFFSET %s
                    """
                    cursor.execute(query, params + [chunk_size, offset])
                    
                    cols = [desc[0] for desc in cursor.description]
                    rows = cursor.fetchall()
                    if not rows:
                        break
                        
                    chunk_students = [dict(zip(cols, row)) for row in rows]
                    student_ids = [s.get('student_id') for s in chunk_students if s.get('student_id')]
                    
                    # Fetch Test Data
                    if test_cols_info and student_ids:
                        for test_info in test_cols_info:
                            metric_col = test_info['metric']
                            window_clause = "AND t.test_window ILIKE %s" if test_info.get('test_window') else ""
                            window_params = [test_info['test_window']] if test_info.get('test_window') else []
                            
                            select_expr = f"r.{metric_col}" if metric_col in {'ss', 'pl'} else "(r.other_info ->> %s)"
                            extra_params = [] if metric_col in {'ss', 'pl'} else [test_info.get('other_info_key', '')]
                            
                            tquery = f"""
                                SELECT r.local_student_id, {select_expr}
                                FROM results r
                                INNER JOIN tests t ON r.test_group_key = t.test_group_key
                                WHERE r.local_student_id = ANY(%s)
                                AND r.school_year = %s
                                AND t.test_name ILIKE %s
                                AND t.subject ILIKE %s
                                {window_clause}
                            """
                            cursor.execute(tquery, [
                                *extra_params, student_ids, test_info['year'], 
                                f"%{test_info['test_name']}%", f"%{test_info['subject']}%", *window_params
                            ])
                            tdata = {row[0]: row[1] for row in cursor.fetchall()}
                            for student in chunk_students:
                                student[test_info['col_name']] = tdata.get(student.get('student_id'), '')

                    # Yield Rows
                    for student in chunk_students:
                        row = []
                        for col in selected_columns:
                            val = student.get(col)
                            if val is None: val = ""
                            row.append(str(val))
                        yield writer.writerow(row)
                    
                    offset += chunk_size
        except Exception as e:
            import traceback
            traceback.print_exc()
            # If header hasn't been sent, we can't do much inside generator but we can log it


    response = StreamingHttpResponse(stream_csv(), content_type="text/csv")
    response['Content-Disposition'] = 'attachment; filename="students_export.csv"'
    return response


