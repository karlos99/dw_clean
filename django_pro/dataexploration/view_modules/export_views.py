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
from ..column_utils import (
    _get_display_name,
    RESULTS_BULK_MATCH_SQL, RESULTS_BLANK_SSID_SQL,
    assign_result_value_maps, lookup_result_value,
)
from ..utils import can_user_view_query, get_user_folders
from .student_data_utils import (
    get_current_student_columns,
    get_student_enroll_columns,
)
from .column_management import (
    get_saved_default_columns,
    get_column_expression,
)
from dataexploration.school_year_utils import resolve_relative_column_key

@login_required
def export_students_csv(request):
    """Export filtered student data as CSV."""
    
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"CSV Export: Function called, user={request.user.username}")
    
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
    
    # Resolve any relative column keys (e.g. test___sy_this___...) to real year keys
    selected_columns = [resolve_relative_column_key(c) for c in selected_columns]
    
    # Extract saved filters (they may be nested under 'filters' key or at top level)
    saved_filters = saved_config.get('filters', {}) if isinstance(saved_config.get('filters'), dict) else {}
    
    # Get filters - URL params take precedence over saved config
    search = request.GET.get('search', '') or saved_filters.get('search', '') or saved_config.get('search', '')
    
    _project_default_year = ''
    try:
        from landing.utils import get_project_config
        _cfg = get_project_config()
        _project_default_year = _cfg.default_school_year or ''
    except Exception:
        pass

    _sy_in_export_url = 'school_year' in request.GET
    if _sy_in_export_url:
        school_year = request.GET.get('school_year', '')
    elif _project_default_year:
        school_year = _project_default_year
    else:
        school_year = saved_filters.get('school_year', '') or saved_config.get('school_year', '')

    # Resolve school-year aliases (e.g. '__sy_this__') to concrete year strings
    try:
        from dataexploration.school_year_utils import resolve_school_year
        if school_year:
            school_year = resolve_school_year(school_year)
    except Exception:
        pass
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

    if sort_by:
        sort_by = resolve_relative_column_key(sort_by)
    if isinstance(filters, dict):
        filters = {resolve_relative_column_key(k): v for k, v in filters.items()}

    # Helper for column filters (Duplicated from load_students to avoid refactoring risk)
    def _apply_column_filters(where_clauses, params, filters_to_apply, test_col_map, db_columns, enroll_columns, needs_enroll, needs_students=False, needs_eht=False):
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
                    window_clause = "AND LOWER(t.test_window) = LOWER(%s) "
                    window_params = [test_info['test_window']]

                id_subquery_base = (
                    "SELECT r.local_student_id FROM results r "
                    "INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                    "WHERE r.school_year = %s "
                    f"AND {RESULTS_BLANK_SSID_SQL} "
                    "AND LOWER(t.test_name) = LOWER(%s) "
                    "AND LOWER(t.subject) = LOWER(%s) "
                    f"{window_clause}"
                )
                ssid_subquery_base = (
                    "SELECT r.ssid FROM results r "
                    "INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                    "WHERE r.ssid IS NOT NULL AND r.ssid != '' AND r.ssid != 'NaN' "
                    "AND r.school_year = %s "
                    "AND LOWER(t.test_name) = LOWER(%s) "
                    "AND LOWER(t.subject) = LOWER(%s) "
                    f"{window_clause}"
                )
                base_params = [
                    test_info['year'], test_info['test_name'], test_info['subject'],
                    *window_params,
                ]

                conditions = []

                if real_values:
                    val_id = id_subquery_base
                    val_ssid = ssid_subquery_base
                    if metric_col in {'ss', 'pl'}:
                        val_id += f"AND r.{metric_col} = ANY(%s)"
                        val_ssid += f"AND r.{metric_col} = ANY(%s)"
                    else:
                        val_id += "AND (r.other_info ->> %s) = ANY(%s)"
                        val_ssid += "AND (r.other_info ->> %s) = ANY(%s)"
                    
                    val_id_params = list(base_params)
                    val_ssid_params = list(base_params)
                    if metric_col in {'oi', 'dfp'}:
                         val_id_params.append(test_info.get('other_info_key', ''))
                         val_ssid_params.append(test_info.get('other_info_key', ''))
                    val_id_params.append(real_values)
                    val_ssid_params.append(real_values)
                    
                    conditions.append(f"(cs.student_id IN ({val_id}) OR cs.ssid IN ({val_ssid}))")
                    params.extend(val_id_params + val_ssid_params)

                if has_blank:
                    blank_subconditions = []
                    
                    # 1. No record exists
                    blank_subconditions.append(
                        f"(cs.student_id NOT IN ({id_subquery_base}) AND cs.ssid NOT IN ({ssid_subquery_base}))"
                    )
                    params.extend(base_params + base_params)

                    # 2. Record exists but value is NULL
                    null_id = id_subquery_base
                    null_ssid = ssid_subquery_base
                    null_id_params = list(base_params)
                    null_ssid_params = list(base_params)
                    
                    if metric_col in {'ss', 'pl'}:
                        null_id += f"AND r.{metric_col} IS NULL"
                        null_ssid += f"AND r.{metric_col} IS NULL"
                    else:
                        null_id += "AND (r.other_info ->> %s) IS NULL"
                        null_ssid += "AND (r.other_info ->> %s) IS NULL"
                        null_id_params.append(test_info.get('other_info_key', ''))
                        null_ssid_params.append(test_info.get('other_info_key', ''))
                    
                    blank_subconditions.append(f"(cs.student_id IN ({null_id}) OR cs.ssid IN ({null_ssid}))")
                    params.extend(null_id_params + null_ssid_params)
                    
                    conditions.append(f"({' OR '.join(blank_subconditions)})")
                
                if conditions:
                    where_clauses.append(f"({' OR '.join(conditions)})")
                continue

            expr, _ = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
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
    filter_keys = list(filters.keys()) if isinstance(filters, dict) else []
    selected_test_cols = list(set(
        [c for c in selected_columns if isinstance(c, str) and c.startswith('test_')] +
        [k for k in filter_keys if isinstance(k, str) and k.startswith('test_')]
    ))
    selected_mark_cols = list(set(
        [c for c in selected_columns if isinstance(c, str) and c.startswith('mark_')] +
        [k for k in filter_keys if isinstance(k, str) and k.startswith('mark_')]
    ))
    base_cols = [c for c in selected_columns if isinstance(c, str) and (not c.startswith(('test_', 'mark_'))) and c in COLUMN_DEFINITIONS]

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

    # Determine if we need elementary_homeroom_teacher join
    needs_eht = (
        'elem_teacher_name' in selected_columns
        or (isinstance(filters, dict) and 'elem_teacher_name' in filters.keys())
        or sort_by == 'elem_teacher_name'
    )

    join_sql = ""
    if needs_enroll:
        join_sql += (
            " LEFT JOIN ("
            " SELECT DISTINCT ON (student_id, school_year) *"
            " FROM student_enroll"
            " ORDER BY student_id, school_year, enter_date DESC NULLS LAST, leave_date DESC NULLS LAST"
            " ) se ON se.student_id = cs.student_id AND se.school_year = cs.school_year"
        )
    if needs_students:
        join_sql += " LEFT JOIN students s ON s.student_annual_key = cs.stu_annual_key"
        join_sql += (
            " LEFT JOIN ("
            " SELECT DISTINCT ON (state_student_number) *"
            " FROM students"
            " ORDER BY state_student_number, school_year DESC NULLS LAST, enter_date DESC NULLS LAST, leave_date DESC NULLS LAST"
            " ) sssid ON sssid.state_student_number = cs.ssid"
        )
    if needs_eht:
        join_sql += " LEFT JOIN elementary_homeroom_teacher eht ON eht.stu_annual_key = cs.stu_annual_key"

    # Streaming Response setup
    class Echo:
        def write(self, value): return value
    
    pseudo_buffer = Echo()
    writer = csv.writer(pseudo_buffer)
    
    def stream_csv():
        import logging
        logger = logging.getLogger(__name__)
        try:
            from clickhouse_utils.executor import QueryExecutor
            with QueryExecutor().cursor() as cursor:
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
                            SELECT DISTINCT t.school_year, t.test_name, t.subject, COALESCE(t.test_window, '')
                            FROM tests t
                            WHERE t.school_year = ANY(%s) AND t.school_year IS NOT NULL AND t.test_name IS NOT NULL
                        """ if has_test_window else """
                            SELECT DISTINCT t.school_year, t.test_name, t.subject, ''
                            FROM tests t
                            WHERE t.school_year = ANY(%s) AND t.school_year IS NOT NULL AND t.test_name IS NOT NULL
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
                            
                            for metric in ('ss', 'pl', 'dfp'):
                                norm_key = f"test_{key_base}_{metric.upper()}"
                                original_key = normalized_to_original.get(norm_key)
                                if original_key:
                                    info = {
                                        'col_name': original_key, 'year': yr, 'test_name': (test_name or ''), 
                                        'subject': (subject or ''), 'test_window': (test_window or '').strip() or None, 'metric': metric
                                    }
                                    if metric == 'dfp':
                                        info['other_info_key'] = 'dfp'
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

                # Build marks metadata (BEFORE the fetch loop)
                mark_cols_info = []
                mark_col_map = {}
                if selected_mark_cols:
                    # Build mark column metadata
                    def _parse_year_from_mark_col(col_key):
                        s = (col_key or '').replace('-', '_')
                        if not s.startswith('mark_'): return None
                        tail = s[len('mark_'):]  
                        if len(tail) >= 10 and tail[4] == '_' and tail[9] == '_':
                            return tail[:9].replace('_', '-')
                        parts = tail.split('_', 1)
                        return parts[0].replace('_', '-') if parts else None
                    
                    normalized_to_original = {c.replace('-', '_'): c for c in selected_mark_cols}
                    years = sorted({y for y in (_parse_year_from_mark_col(c) for c in selected_mark_cols) if y})
                    
                    if years:
                        cursor.execute(
                            """
                            SELECT DISTINCT school_year, subject, course_name, mark_period
                            FROM student_marks_list
                            WHERE school_year = ANY(%s) AND school_year IS NOT NULL
                            """,
                            [years],
                        )
                        
                        def _norm_part(v): return (v or '').replace(' ', '_').replace('-', '_')
                        
                        for (year, subject, course_name, mark_period) in cursor.fetchall():
                            year_val = (year or '').strip() or 'Unknown'
                            subject_val = (subject or '').strip() or 'Unknown'
                            course_val = (course_name or '').strip() or 'Course'
                            period_val = (mark_period or '').strip() or 'Unknown'
                            
                            key_base = f"{_norm_part(year_val)}_{_norm_part(subject_val)}_{_norm_part(course_val)}_{_norm_part(period_val)}"
                            mark_key = f"mark_{key_base}"
                            
                            original_key = normalized_to_original.get(mark_key)
                            if original_key:
                                info = {
                                    'col_name': original_key,
                                    'year': year,
                                    'subject': (subject or ''),
                                    'course_name': (course_name or ''),
                                    'mark_period': (mark_period or '').strip() or None,
                                }
                                mark_cols_info.append(info)
                                mark_col_map[original_key] = info
                                mark_col_map[original_key.replace('-', '_')] = info

                # Prepare Dynamic Headers
                dynamic_labels = COLUMN_DEFINITIONS.copy()
                if test_cols_info:
                    for info in test_cols_info:
                        metric_map = {'ss': 'Scale Score', 'pl': 'Perf Lvl', 'dfp': 'DFP', 'oi': info.get('other_info_key', 'Info')}
                        metric_label = metric_map.get(info['metric'], info['metric'])
                        
                        display_test = _get_display_name(info['test_name'])
                        display_subject = _get_display_name(info['subject'])
                        
                        label = f"{display_test} {display_subject} ({info['year']}) {metric_label}"
                        if info.get('test_window'):
                            label += f" {info['test_window']}"
                        dynamic_labels[info['col_name']] = label

                if mark_cols_info:
                    for info in mark_cols_info:
                        period = info.get('mark_period') or 'Unknown'
                        label = f"{info['course_name']} ({info['subject']}) {period} {info['year']}"
                        dynamic_labels[info['col_name']] = label

                # Yield Header
                header_labels = [dynamic_labels.get(col, col) for col in selected_columns]
                yield writer.writerow(header_labels)
                logger.info(f"CSV Export: Header yielded, starting data export with {len(selected_columns)} columns")

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

                # Apply active school context or enforce school permissions
                if hasattr(request.user, 'active_school') and request.user.active_school:
                     # User explicitly selected a school
                     base_where_clauses.append("cs.school_state_id = %s")
                     base_params.append(request.user.active_school.state_id)
                else:
                     # Check if user is unrestricted (Project Admin OR District Role)
                     is_unrestricted = False
                     if request.user.is_superuser:
                         is_unrestricted = True
                     elif getattr(request.user, 'is_project_admin', False):
                         is_unrestricted = True
                     elif hasattr(request.user, 'active_role') and request.user.active_role and 'District' in request.user.active_role.name:
                         is_unrestricted = True
                         
                     if not is_unrestricted:
                         # No specific school selected, so limit to ALL assigned schools
                         if hasattr(request.user, 'schools') and isinstance(request.user.schools, list) and request.user.schools:
                              base_where_clauses.append("cs.school_state_id = ANY(%s)")
                              base_params.append(request.user.schools)
                         else:
                              # Non-admin user with no assigned schools sees nothing
                              base_where_clauses.append("1=0")

                # Apply specific row-level permissions (e.g. Teacher seeing only their students)
                if hasattr(request.user, 'get_accessible_student_keys'):
                    accessible_keys = request.user.get_accessible_student_keys()
                    if accessible_keys is not None:
                        # If restricted set returned
                        if not accessible_keys:
                            base_where_clauses.append("1=0")
                        else:
                            base_where_clauses.append("cs.stu_annual_key = ANY(%s)")
                            base_params.append(accessible_keys)

                where_clauses = list(base_where_clauses)
                params = list(base_params)
                _apply_column_filters(where_clauses, params, filters_value, test_col_map, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
                where_sql = " AND ".join(where_clauses)
                
                # Fetch Loop
                chunk_size = 500
                offset = 0
                
                # Ensure we fetch minimal required columns plus sorting
                query_base_cols = list(base_cols)
                if 'ssid' not in query_base_cols: query_base_cols.insert(0, 'ssid')
                if test_cols_info and 'student_id' not in query_base_cols: query_base_cols.insert(0, 'student_id')
                if selected_mark_cols and 'stu_annual_key' not in query_base_cols: query_base_cols.insert(0, 'stu_annual_key')
                
                base_col_selects = []
                base_col_expressions = {}
                for col in query_base_cols:
                    expr, alias = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
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
                    logger.info(f"CSV Export: Fetched {len(rows)} rows at offset {offset}")
                    if not rows:
                        logger.info("CSV Export: No more rows, exiting loop")
                        break
                        
                    chunk_students = [dict(zip(cols, row)) for row in rows]
                    student_ids = [s.get('student_id') for s in chunk_students if s.get('student_id')]
                    
                    # Fetch Test Data
                    if test_cols_info and student_ids:
                        student_ssids = [s.get('ssid') for s in chunk_students if s.get('ssid')]
                        for test_info in test_cols_info:
                            metric_col = test_info['metric']
                            window_clause = ""
                            window_params = []
                            if test_info.get('test_window'):
                                window_clause = "AND LOWER(t.test_window) = LOWER(%s)"
                                window_params = [test_info['test_window']]

                            select_expr = f"r.{metric_col}" if metric_col in {'ss', 'pl'} else "(r.other_info ->> %s)"
                            extra_params = [] if metric_col in {'ss', 'pl'} else [test_info.get('other_info_key', '')]

                            tquery = f"""
                                SELECT r.local_student_id, r.ssid, {select_expr}
                                FROM results r
                                INNER JOIN tests t ON r.test_group_key = t.test_group_key
                                WHERE {RESULTS_BULK_MATCH_SQL}
                                AND r.school_year = %s
                                AND LOWER(t.test_name) = LOWER(%s)
                                AND LOWER(t.subject) = LOWER(%s)
                                {window_clause}
                            """
                            cursor.execute(tquery, [
                                *extra_params, student_ssids, student_ids, test_info['year'],
                                test_info['test_name'], test_info['subject'], *window_params,
                            ])
                            test_data_by_id, test_data_by_ssid = assign_result_value_maps(
                                cursor.fetchall()
                            )
                            for student in chunk_students:
                                student[test_info['col_name']] = lookup_result_value(
                                    student, test_data_by_id, test_data_by_ssid,
                                )
                    
                    # Fetch marks data for this chunk
                    student_keys = [s.get('stu_annual_key') for s in chunk_students if s.get('stu_annual_key')]
                    if mark_cols_info and student_keys:
                        for mark_info in mark_cols_info:
                            period_clause = "AND sm.mark_period ILIKE %s" if mark_info.get('mark_period') else "AND (sm.mark_period IS NULL OR sm.mark_period = '')"
                            period_params = [mark_info['mark_period']] if mark_info.get('mark_period') else []
                            
                            mquery = f"""
                                SELECT sm.stu_annual_key, sm.mark
                                FROM student_marks_list sm
                                WHERE sm.stu_annual_key = ANY(%s)
                                AND sm.school_year = %s
                                AND sm.subject ILIKE %s
                                AND sm.course_name ILIKE %s
                                {period_clause}
                            """
                            cursor.execute(mquery, [
                                student_keys, mark_info['year'],
                                f"%{mark_info['subject']}%", f"%{mark_info['course_name']}%",
                                *period_params
                            ])
                            mdata = {row[0]: row[1] for row in cursor.fetchall()}
                            
                            for student in chunk_students:
                                student[mark_info['col_name']] = mdata.get(student.get('stu_annual_key'), '')

                    # Yield Rows
                    for student in chunk_students:
                        row = []
                        for col in selected_columns:
                            val = student.get(col)
                            if val is None: val = ""
                            row.append(str(val))
                        yield writer.writerow(row)
                
                    logger.info(f"CSV Export: Yielded {len(chunk_students)} rows")
                    offset += chunk_size
            
            logger.info(f"CSV Export: Completed successfully")
        except Exception as e:
            import traceback
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"CSV Export Error: {str(e)}")
            logger.error(traceback.format_exc())
            # Yield an error row so the user knows something went wrong
            yield writer.writerow([f"Error exporting data: {str(e)}"])

    logger.info(f"CSV Export: Creating StreamingHttpResponse with {len(selected_columns)} columns")
    response = StreamingHttpResponse(stream_csv(), content_type="text/csv")
    response['Content-Disposition'] = 'attachment; filename="students_export.csv"'
    logger.info(f"CSV Export: Returning response")
    return response


