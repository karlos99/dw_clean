"""
Statistics views for data exploration.
Provides aggregated information about columns (counts, averages, unique distributions).
"""

from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.db import connections
from django.core.cache import cache
import json
import hashlib
import statistics

from ..models import SavedQuery
from ..constants import COLUMN_DEFINITIONS
from ..column_utils import _get_display_name, RESULTS_STUDENT_MATCH_SQL, RESULTS_BLANK_SSID_SQL
from ..utils import can_user_view_query, get_user_folders, is_unrestricted as _is_user_unrestricted, _apply_de_school_filter
from ..survey_utils import get_survey_columns
from .student_data_utils import (
    get_current_student_columns,
    get_student_enroll_columns,
)
from .column_management import (
    get_saved_default_columns,
    get_column_expression,
    is_race_list_column,
    append_race_list_filter,
)

@login_required
def get_column_statistics(request):
    """
    Return statistics for specific columns based on current filters.
    
    Parameters:
    - columns: JSON list of column keys to analyze.
    - filters...: standard filters (search, grade, etc.) matching load_students.
    """
    
    # 1. Parse parameters (Shared logic or similar to load_students)
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

    # Columns to analyze (default to visible columns if not specified, though usually specified)
    target_columns_json = request.GET.get('target_columns', '')
    if target_columns_json:
        try:
            target_columns = json.loads(target_columns_json)
        except:
            target_columns = []
    else:
        # Fallback to selected columns
        selected_columns_json = request.GET.get('columns', '')
        if selected_columns_json:
            try:
                target_columns = json.loads(selected_columns_json)
            except:
                target_columns = get_saved_default_columns()
        elif saved_config.get('columns'):
            target_columns = saved_config.get('columns')
        else:
            target_columns = get_saved_default_columns()

    # Determine "selected columns" context for filtering (if filters rely on cols)
    # Actually, we just need basic filters here.
    
    saved_filters = saved_config.get('filters', {}) if isinstance(saved_config.get('filters'), dict) else {}
    
    search = request.GET.get('search', '') or saved_filters.get('search', '') or saved_config.get('search', '')
    
    _project_default_year = ''
    try:
        from landing.utils import get_project_config
        _cfg = get_project_config()
        _project_default_year = _cfg.default_school_year or ''
    except Exception:
        pass

    _sy_in_stats_url = 'school_year' in request.GET
    if _sy_in_stats_url:
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

    filters_json = request.GET.get('filters', '')
    filters = {}
    if filters_json:
        try:
            filters = json.loads(filters_json)
        except:
            filters = saved_filters.copy()
    else:
        filters = {k: v for k, v in saved_filters.items() if k not in ['search', 'school_year', 'grade', 'status']}

    # --- Reusing Filter Logic (Duplicated from load_students) ---
    def _apply_column_filters(where_clauses, params, filters_to_apply, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll, needs_students=False, needs_eht=False):
        if not isinstance(filters_to_apply, dict):
            return

        for col, values in filters_to_apply.items():
            if not values:
                continue

            if col == 'atendace_rate':
                expr, _ = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
                if expr:
                    conditions = []
                    import re
                    for val in values:
                        val_str = str(val).strip()
                        if not val_str:
                            conditions.append(f"({expr} IS NULL)")
                            continue

                        match = re.match(r'^([<>]=?|=)?\s*(\d+(?:\.\d+)?)$', val_str)
                        if match:
                            op = match.group(1) or '='
                            num = match.group(2)
                            conditions.append(f"{expr} {op} %s")
                            params.append(num)
                            continue

                        match_range = re.match(r'^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)$', val_str)
                        if match_range:
                            low, high = match_range.groups()
                            conditions.append(f"{expr} BETWEEN %s AND %s")
                            params.extend([low, high])
                            continue

                        conditions.append(f"{expr} = %s")
                        params.append(val)

                    if conditions:
                        where_clauses.append(f"({' OR '.join(conditions)})")
                    continue

            has_blank = "" in values
            real_values = [v for v in values if v != ""]

            if col.startswith('test_'):
                test_info = test_col_map.get(col) or test_col_map.get(col.replace('-', '_'))
                if not test_info:
                    continue
                metric_col = test_info['metric']
                window_clause = ""
                window_params = []
                if test_info.get('test_window'):
                    window_clause = "AND t.test_window ILIKE %s "
                    window_params = [test_info['test_window']]

                id_subquery_base = (
                    "SELECT r.local_student_id FROM results r "
                    "INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                    "WHERE r.school_year = %s "
                    f"AND {RESULTS_BLANK_SSID_SQL} "
                    "AND t.test_name ILIKE %s "
                    "AND t.subject ILIKE %s "
                    f"{window_clause}"
                )
                ssid_subquery_base = (
                    "SELECT r.ssid FROM results r "
                    "INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                    "WHERE r.ssid IS NOT NULL AND r.ssid != '' "
                    "AND r.school_year = %s "
                    "AND t.test_name ILIKE %s "
                    "AND t.subject ILIKE %s "
                    f"{window_clause}"
                )
                base_params = [test_info['year'], test_info['test_name'], test_info['subject'], *window_params]

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

                    blank_subconditions.append(
                        f"(cs.student_id NOT IN ({id_subquery_base}) AND cs.ssid NOT IN ({ssid_subquery_base}))"
                    )
                    params.extend(base_params + base_params)

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

            if col.startswith('mark_'):
                mark_info = mark_col_map.get(col) or mark_col_map.get(col.replace('-', '_'))
                if not mark_info:
                    continue

                conditions = []

                if real_values:
                    mark_clause = (
                        "SELECT 1 FROM student_marks_list sm "
                        "WHERE sm.stu_annual_key = cs.stu_annual_key "
                        "AND sm.school_year = %s "
                        "AND sm.subject ILIKE %s "
                        "AND sm.course_name ILIKE %s "
                    )
                    mark_params = [
                        mark_info['year'],
                        f"%{mark_info['subject']}%",
                        f"%{mark_info['course_name']}%",
                    ]

                    if mark_info.get('mark_period'):
                        mark_clause += "AND sm.mark_period ILIKE %s "
                        mark_params.append(mark_info['mark_period'])
                    else:
                        mark_clause += "AND (sm.mark_period IS NULL OR sm.mark_period = '') "

                    mark_clause += "AND sm.mark = ANY(%s)"
                    mark_params.append(real_values)

                    conditions.append(f"EXISTS ({mark_clause})")
                    params.extend(mark_params)

                if has_blank:
                    blank_clause = (
                        "SELECT 1 FROM student_marks_list sm "
                        "WHERE sm.stu_annual_key = cs.stu_annual_key "
                        "AND sm.school_year = %s "
                        "AND sm.subject ILIKE %s "
                        "AND sm.course_name ILIKE %s "
                    )
                    blank_params = [
                        mark_info['year'],
                        f"%{mark_info['subject']}%",
                        f"%{mark_info['course_name']}%",
                    ]
                    if mark_info.get('mark_period'):
                        blank_clause += "AND sm.mark_period ILIKE %s "
                        blank_params.append(mark_info['mark_period'])
                    else:
                        blank_clause += "AND (sm.mark_period IS NULL OR sm.mark_period = '') "

                    blank_clause += "AND (sm.mark IS NULL OR sm.mark = '')"
                    conditions.append(f"EXISTS ({blank_clause})")
                    params.extend(blank_params)

                if conditions:
                    where_clauses.append(f"({' OR '.join(conditions)})")
                continue

            # AP exam performance-level columns (ap_<year>_<subject>_PL)
            if col.startswith('ap_') and not col.startswith('apaward_') and not col.startswith('apawards_all_'):
                ap_info = next((i for i in ap_exam_infos if i['col_name'] == col), None)
                if not ap_info:
                    continue
                sub_base = (
                    "SELECT a.ssid FROM \"dataLoader_apexamresult\" a "
                    "INNER JOIN \"dataLoader_apexamcode\" e ON a.exam_id = e.code "
                    "INNER JOIN \"dataLoader_apimportlog\" il ON a.import_log_id = il.id "
                    "WHERE a.admin_year = %s AND e.name = %s AND il.is_published = true "
                )
                sub_params = [ap_info['year'], ap_info['subject']]
                conditions = []
                if real_values:
                    conditions.append(f"cs.ssid IN ({sub_base} AND a.exam_grade = ANY(%s))")
                    params.extend(sub_params + [real_values])
                if has_blank:
                    conditions.append(
                        f"(cs.ssid NOT IN ({sub_base}) OR "
                        f"cs.ssid IN ({sub_base} AND (a.exam_grade IS NULL OR a.exam_grade = '')))"
                    )
                    params.extend(sub_params + sub_params)
                if conditions:
                    where_clauses.append(f"({' OR '.join(conditions)})")
                continue

            # Individual AP award columns (apaward_<year>_<award>)
            if col.startswith('apaward_') and not col.startswith('apawards_all_'):
                ap_info = next((i for i in ap_award_infos if i['col_name'] == col), None)
                if not ap_info:
                    continue
                sub_base = (
                    "SELECT a.ssid FROM \"dataLoader_apstudentaward\" a "
                    "INNER JOIN \"dataLoader_apawardtype\" t ON a.award_type_id = t.code "
                    "INNER JOIN \"dataLoader_apimportlog\" il ON a.import_log_id = il.id "
                    "WHERE a.award_year = %s AND t.description = %s AND il.is_published = true "
                )
                sub_params = [ap_info['year'], ap_info['award']]
                conditions = []
                if real_values:
                    conditions.append(f"cs.ssid IN ({sub_base})")
                    params.extend(sub_params)
                if has_blank:
                    conditions.append(f"cs.ssid NOT IN ({sub_base})")
                    params.extend(sub_params)
                if conditions:
                    where_clauses.append(f"({' OR '.join(conditions)})")
                continue

            # Combined "All Awards" column (apawards_all_<year>)
            if col.startswith('apawards_all_'):
                year = col[len('apawards_all_'):].replace('_', '-')
                sub_base = (
                    "SELECT a.ssid FROM \"dataLoader_apstudentaward\" a "
                    "INNER JOIN \"dataLoader_apawardtype\" t ON a.award_type_id = t.code "
                    "INNER JOIN \"dataLoader_apimportlog\" il ON a.import_log_id = il.id "
                    "WHERE a.award_year = %s AND il.is_published = true "
                )
                sub_params = [year]
                conditions = []
                if real_values:
                    conditions.append(f"cs.ssid IN ({sub_base} AND t.description = ANY(%s))")
                    params.extend(sub_params + [real_values])
                if has_blank:
                    conditions.append(f"cs.ssid NOT IN ({sub_base})")
                    params.extend(sub_params)
                if conditions:
                    where_clauses.append(f"({' OR '.join(conditions)})")
                continue
            
            # Race List (add_data.l_race) — any-of overlap
            if is_race_list_column(col):
                append_race_list_filter(where_clauses, params, values)
                continue

            # Standard
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

    # --- End Filter Logic ---

    # Prepare metadata needed for filtering
    db_columns = get_current_student_columns()
    enroll_columns = get_student_enroll_columns()
    
    # We need to know about test columns IN FILTERS
    filter_keys = list(filters.keys())
    selected_test_cols = [c for c in filter_keys if c.startswith('test_')]
    # Also need target test cols to analyze them
    target_test_cols = [c for c in target_columns if c.startswith('test_')]
    all_related_test_cols = list(set(selected_test_cols + target_test_cols))
    
    # Same for marks
    selected_mark_cols = [c for c in filter_keys if c.startswith('mark_')]
    target_mark_cols = [c for c in target_columns if c.startswith('mark_')]
    all_related_mark_cols = list(set(selected_mark_cols + target_mark_cols))

    enroll_fields = {'ell', 'birth_date', 'us_enter_date', 'fep_date', 'migrant'}
    all_relevant_cols = set(target_columns) | set(filters.keys())
    needs_enroll = any(c in enroll_fields for c in all_relevant_cols)

    students_fields = {'ltel'}
    needs_students = any(c in students_fields for c in all_relevant_cols)
    
    needs_eht = 'elem_teacher_name' in all_relevant_cols

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

    results_data = {}

    from clickhouse_utils.executor import QueryExecutor
    with QueryExecutor().cursor() as cursor:
        # Build test map
        test_col_map = {}
        if all_related_test_cols:
            def _parse_year_from_test_col(col_key):
                s = (col_key or '').replace('-', '_')
                if not s.startswith('test_'): return None
                tail = s[len('test_'):]
                if len(tail) >= 10 and tail[4] == '_' and tail[9] == '_':
                    return tail[:9].replace('_', '-')
                parts = tail.split('_', 1)
                return parts[0].replace('_', '-') if parts else None

            years = sorted({y for y in (_parse_year_from_test_col(c) for c in all_related_test_cols) if y})
            if years:
                 normalized_to_original = {c.replace('-', '_'): c for c in all_related_test_cols}
                 
                 cursor.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'tests' AND column_name = 'test_window'")
                 has_window = cursor.fetchone() is not None
                 
                 sql = """
                    SELECT DISTINCT t.school_year, t.test_name, t.subject, COALESCE(t.test_window, '')
                    FROM tests t
                    WHERE t.school_year = ANY(%s) AND t.test_name IS NOT NULL
                 """ if has_window else """
                    SELECT DISTINCT t.school_year, t.test_name, t.subject, ''
                    FROM tests t
                    WHERE t.school_year = ANY(%s) AND t.test_name IS NOT NULL
                 """
                 cursor.execute(sql, [years])
                 rows = cursor.fetchall()
                 
                 def _norm_part(v): return (v or '').replace(' ', '_').replace('-', '_')

                 for yr, test_name, subject, test_window in rows:
                     yr_norm = _norm_part(yr)
                     tn_norm = _norm_part(test_name)
                     sub_norm = _norm_part(subject)
                     win_norm = _norm_part(test_window).strip('_')
                     key_base = f"{yr_norm}_{tn_norm}_{sub_norm}_{win_norm}" if win_norm else f"{yr_norm}_{tn_norm}_{sub_norm}"
                     
                     for metric in ['SS', 'PL']:
                         norm_key = f"test_{key_base}_{metric.upper()}"
                         original_key = normalized_to_original.get(norm_key)
                         if original_key:
                             test_col_map[original_key] = {
                                 'year': yr, 'test_name': test_name, 'subject': subject, 'test_window': (test_window or '').strip() or None,
                                 'metric': metric.lower(), 'col_name': original_key
                             }
                             test_col_map[original_key.replace('-', '_')] = test_col_map[original_key]

                 # Other Info logic implementation
                 want_other = any('_OI_' in k for k in normalized_to_original.keys())
                 if want_other:
                    if has_window:
                        cursor.execute(
                            """
                            SELECT DISTINCT r.school_year, t.test_name, t.subject, COALESCE(t.test_window, ''), k.key
                            FROM results r
                            INNER JOIN tests t ON r.test_group_key = t.test_group_key
                            CROSS JOIN LATERAL jsonb_object_keys(r.other_info) AS k(key)
                            WHERE r.school_year = ANY(%s)
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
                        if original_key:
                            info = {
                                'col_name': original_key,
                                'year': yr,
                                'test_name': (test_name or ''),
                                'subject': (subject or ''),
                                'test_window': win_value,
                                'metric': 'oi',
                                'other_info_key': raw_key_str,
                            }
                            test_col_map[original_key] = info
                            test_col_map[original_key.replace('-', '_')] = info


        # Build mark map
        mark_col_map = {}
        if all_related_mark_cols:
            def _parse_year_from_mark_col(col_key):
                s = (col_key or '').replace('-', '_')
                if not s.startswith('mark_'): return None
                tail = s[len('mark_'):]  
                if len(tail) >= 10 and tail[4] == '_' and tail[9] == '_':
                    return tail[:9].replace('_', '-')
                parts = tail.split('_', 1)
                return parts[0].replace('_', '-') if parts else None
            
            normalized_to_original = {c.replace('-', '_'): c for c in all_related_mark_cols}
            years = sorted({y for y in (_parse_year_from_mark_col(c) for c in all_related_mark_cols) if y})
            
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
                        mark_col_map[original_key] = info
                        mark_col_map[original_key.replace('-', '_')] = info

        # Build AP exam + award info maps
        ap_exam_infos = ap_award_infos = []
        selected_ap_cols = list(set(
            [c for c in filter_keys if isinstance(c, str) and c.startswith('ap_') and not c.startswith('apaward_') and not c.startswith('apawards_all_')] +
            [c for c in target_columns if isinstance(c, str) and c.startswith('ap_') and not c.startswith('apaward_') and not c.startswith('apawards_all_')]
        ))
        selected_award_cols = list(set(
            [c for c in filter_keys if isinstance(c, str) and (c.startswith('apaward_') or c.startswith('apawards_all_'))] +
            [c for c in target_columns if isinstance(c, str) and (c.startswith('apaward_') or c.startswith('apawards_all_'))]
        ))
        if selected_ap_cols or selected_award_cols:
            try:
                from dataviz.utils.data_loader import (
                    _build_ap_exam_columns_from_db,
                    _build_ap_award_columns_from_db,
                )
                with connections['default'].cursor() as ap_meta_cursor:
                    if selected_ap_cols:
                        ap_exam_infos, _ = _build_ap_exam_columns_from_db(ap_meta_cursor, selected_ap_cols)
                    if selected_award_cols:
                        ap_award_infos, _ = _build_ap_award_columns_from_db(ap_meta_cursor, selected_award_cols)
            except Exception:
                ap_exam_infos = ap_award_infos = []

        # Build Base Where
        base_where_clauses = ["1=1"]
        base_params = []
        
        # Apply Base Filters
        if search:
            base_where_clauses.append("(cs.student_name ILIKE %s OR cs.ssid ILIKE %s OR cs.student_id ILIKE %s)")
            base_params.extend([f'%{search}%']*3)
        if school_year:
            base_where_clauses.append("cs.school_year = %s")
            base_params.append(school_year)
        if grade:
            base_where_clauses.append("cs.grade = %s")
            base_params.append(grade)
        if status:
            base_where_clauses.append("cs.status = %s")
            base_params.append(status)

        # Check if user is unrestricted (Project Admin OR District Role)
        is_unrestricted = _is_user_unrestricted(request.user)

        # Apply active school context or enforce school permissions
        _apply_de_school_filter(base_where_clauses, base_params, request.user)

        # Apply grade level restrictions for specific roles
        if not is_unrestricted and hasattr(request.user, 'grade_levels'):
            user_grade_levels = request.user.grade_levels
            if isinstance(user_grade_levels, list) and len(user_grade_levels) > 0:
                grade_restricted_roles = {'School Gr View', 'School View', 'School Admin'}
                current_role = None

                if hasattr(request.user, 'active_role') and request.user.active_role:
                    current_role = request.user.active_role.name
                elif request.user.roles.count() == 1:
                    current_role = request.user.roles.first().name

                if current_role in grade_restricted_roles:
                    base_where_clauses.append("cs.grade = ANY(%s)")
                    base_params.append(user_grade_levels)

        # Apply specific row-level permissions (e.g. Teacher seeing only their students)
        if hasattr(request.user, 'get_accessible_student_keys'):
            accessible_keys = request.user.get_accessible_student_keys()
            if accessible_keys is not None:
                if not accessible_keys:
                    base_where_clauses.append("1=0")
                else:
                    base_where_clauses.append("cs.stu_annual_key = ANY(%s)")
                    base_params.append(accessible_keys)
        
        # Apply Column Filters
        _apply_column_filters(base_where_clauses, base_params, filters, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
        
        where_sql = " AND ".join(base_where_clauses)
        
        # Calculate Stats for each target column
        for col in target_columns:
            stats = {
                'count': 0,
                'unique': 0,
                'nulls': 0,
                'avg': None,
                'median': None,
                'distribution': [],
                'type': 'string'
            }
            is_string_stats_column = False
            
            # --- Test Columns ---
            if col.startswith('test_'):
                info = test_col_map.get(col)
                if not info:
                    continue
                
                metric_col = info['metric'] # ss or pl
                is_string_stats_column = metric_col == 'pl'
                window_clause = "AND t.test_window ILIKE %s" if info['test_window'] else ""
                win_params = [info['test_window']] if info['test_window'] else []
                
                # We need to query results table, restricted by students in current_student filter
                col_expr = f"r.{metric_col}" if metric_col in ['ss', 'pl'] else f"(r.other_info ->> '{info.get('other_info_key','')}')"
                
                # Fetch raw values (no SQL cast, handle determining numeric in Python)
                val_expr = col_expr
                
                query = f"""
                    SELECT {val_expr} as val, COUNT(*) as cnt
                    FROM results r
                    INNER JOIN tests t ON r.test_group_key = t.test_group_key
                    WHERE EXISTS (
                        SELECT 1 FROM current_student cs {join_sql}
                        WHERE {where_sql}
                          AND {RESULTS_STUDENT_MATCH_SQL}
                    )
                    AND r.school_year = %s
                    AND t.test_name ILIKE %s
                    AND t.subject ILIKE %s
                    {window_clause}
                    AND {col_expr} IS NOT NULL
                    GROUP BY val
                    ORDER BY val
                """
                q_params = base_params + [info['year'], info['test_name'], info['subject']] + win_params

            # --- Mark Columns ---
            elif col.startswith('mark_'):
                info = mark_col_map.get(col)
                if not info:
                    continue
                is_string_stats_column = True
                
                period_clause = "AND sm.mark_period ILIKE %s" if info.get('mark_period') else "AND (sm.mark_period IS NULL OR sm.mark_period = '')"
                period_params = [info['mark_period']] if info.get('mark_period') else []
                
                query = f"""
                    SELECT sm.mark as val, COUNT(*) as cnt
                    FROM student_marks_list sm
                    WHERE sm.stu_annual_key IN (
                        SELECT cs.stu_annual_key FROM current_student cs {join_sql} WHERE {where_sql}
                    )
                    AND sm.school_year = %s
                    AND sm.subject ILIKE %s
                    AND sm.course_name ILIKE %s
                    {period_clause}
                    AND sm.mark IS NOT NULL
                    GROUP BY val
                    ORDER BY val
                """
                q_params = base_params + [info['year'], f"%{info['subject']}%", f"%{info['course_name']}%"] + period_params

            # --- Survey Columns ---
            elif col.startswith('survey_'):
                _survey_meta = get_survey_columns().get('col_meta', {}).get(col)
                if not _survey_meta:
                    continue
                is_string_stats_column = False  # values are integers 1-N

                query = f"""
                    SELECT sr.value as val, COUNT(*) as cnt
                    FROM "dataLoader_surveyresponse" sr
                    INNER JOIN "dataLoader_surveyquestion" sq ON sr.question_id = sq.id
                    INNER JOIN "dataLoader_surveydefinition" sd ON sq.survey_definition_id = sd.id
                    INNER JOIN "dataLoader_importlog" il ON sr.import_log_id = il.id
                    WHERE sr.student_id IN (
                        SELECT cs.student_id FROM current_student cs {join_sql} WHERE {where_sql}
                    )
                    AND sd.survey_type = %s
                    AND sd.school_year = %s
                    AND sd.testing_window = %s
                    AND sd.grade_range = %s
                    AND sq."order" = %s
                    AND (il.ready_for_exploration = true OR sd.ready_for_exploration = true)
                    AND il.status = 'COMPLETED'
                    AND sr.value IS NOT NULL
                    GROUP BY val
                    ORDER BY val ASC
                """
                q_params = base_params + [
                    _survey_meta['survey_type'], _survey_meta['school_year'],
                    _survey_meta['window'], _survey_meta.get('grade_range', ''),
                    _survey_meta['question_order'],
                ]

            # --- AP Exam PL Columns ---
            elif col.startswith('ap_') and not col.startswith('apaward_') and not col.startswith('apawards_all_'):
                ap_info = next((i for i in ap_exam_infos if i['col_name'] == col), None)
                if not ap_info:
                    continue
                is_string_stats_column = True
                query = f"""
                    SELECT a.exam_grade as val, COUNT(*) as cnt
                    FROM "dataLoader_apexamresult" a
                    INNER JOIN "dataLoader_apexamcode" e ON a.exam_id = e.code
                    INNER JOIN "dataLoader_apimportlog" il ON a.import_log_id = il.id
                    WHERE a.ssid IN (
                        SELECT cs.ssid FROM current_student cs {join_sql} WHERE {where_sql}
                    )
                    AND a.admin_year = %s
                    AND e.name = %s
                    AND il.is_published = true
                    AND a.exam_grade IS NOT NULL AND a.exam_grade <> ''
                    GROUP BY val
                    ORDER BY val
                """
                q_params = base_params + [ap_info['year'], ap_info['subject']]

            # --- AP Award Columns (individual + all-awards) ---
            elif col.startswith('apaward_') or col.startswith('apawards_all_'):
                ap_info = next((i for i in ap_award_infos if i['col_name'] == col), None)
                if not ap_info:
                    continue
                is_string_stats_column = True
                if ap_info['award'] == '__ALL__':
                    query = f"""
                        SELECT t.description as val, COUNT(*) as cnt
                        FROM "dataLoader_apstudentaward" a
                        INNER JOIN "dataLoader_apawardtype" t ON a.award_type_id = t.code
                        INNER JOIN "dataLoader_apimportlog" il ON a.import_log_id = il.id
                        WHERE a.ssid IN (
                            SELECT cs.ssid FROM current_student cs {join_sql} WHERE {where_sql}
                        )
                        AND a.award_year = %s
                        AND il.is_published = true
                        GROUP BY val
                        ORDER BY val
                    """
                    q_params = base_params + [ap_info['year']]
                else:
                    query = f"""
                        SELECT t.description as val, COUNT(*) as cnt
                        FROM "dataLoader_apstudentaward" a
                        INNER JOIN "dataLoader_apawardtype" t ON a.award_type_id = t.code
                        INNER JOIN "dataLoader_apimportlog" il ON a.import_log_id = il.id
                        WHERE a.ssid IN (
                            SELECT cs.ssid FROM current_student cs {join_sql} WHERE {where_sql}
                        )
                        AND a.award_year = %s
                        AND t.description = %s
                        AND il.is_published = true
                        GROUP BY val
                        ORDER BY val
                    """
                    q_params = base_params + [ap_info['year'], ap_info['award']]

            # --- Standard Columns ---
            else:
                expr, _ = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
                if not expr: continue
                
                is_numeric = col in ['gpa', 'attendance_rate'] # approximate list
                
                query = f"""
                    SELECT {expr} as val, COUNT(*) as cnt
                    FROM current_student cs
                    {join_sql}
                    WHERE {where_sql}
                    AND {expr} IS NOT NULL AND {expr} != ''
                    GROUP BY val
                    ORDER BY val
                """
                q_params = base_params

            cursor.execute(query, q_params)
            rows = cursor.fetchall()
            
            # Process Distribution
            total_count = 0
            values = []
            numeric_values = []
            
            # Detect if this column is effectively numeric based on returned values
            numeric_count = 0
            
            for val, cnt in rows:
                total_count += cnt
                if is_string_stats_column and val is not None:
                    val = str(val)
                is_num_val = False
                if val is not None and not is_string_stats_column:
                    # Try to parse as float
                    try:
                        fval = float(val)
                        # Ensure string isn't something like "123 Main St" which float() might handle oddly or allow
                        # Actually float() is strict enough for our needs (excludes "123 Main St", but allows "123").
                        # Also check if it looks numeric if it's a string
                        if isinstance(val, str) and not val.strip().replace('.','',1).isdigit():
                             # E.g. "12.3.4" or "12-4" might fail or pass depending.
                             # Simple float cast is usually okay but let's be safe.
                             # Keep it simple: if float(val) worked, we use it for stats
                             pass
                        
                        numeric_values.extend([fval] * cnt) 
                        numeric_count += cnt
                        is_num_val = True
                    except (ValueError, TypeError):
                        pass
                
                # Check for sorting purposes later
                values.append({'value': val, 'count': cnt, 'numeric_val': fval if is_num_val else None})

            # If > 90% of values are numeric, treat as numeric type
            if is_string_stats_column:
                stats['type'] = 'string'
            elif total_count > 0 and (numeric_count / total_count) > 0.9:
                stats['type'] = 'numeric'
                # Re-sort values numerically
                values.sort(key=lambda x: (x['numeric_val'] if x['numeric_val'] is not None else float('-inf'), x['value']))
            else:
                stats['type'] = 'string'
                # Ensure standard sort (DB usually did it but if we mix types it might vary)
                # DB sorted by string val. That's fine for strings.

            # Cleanup
            for v in values: 
                del v['numeric_val']

            # Calc Percentages
            for item in values:
                item['percent'] = round((item['count'] / total_count * 100), 1) if total_count > 0 else 0
            
            stats['count'] = total_count
            stats['unique'] = len(values)
            stats['distribution'] = values[:50] # Top 50 unique values?
            
            if stats.get('type') == 'numeric' and numeric_values:
                stats['avg'] = round(statistics.mean(numeric_values), 2)
                stats['median'] = round(statistics.median(numeric_values), 2)
                stats['min'] = min(numeric_values)
                stats['max'] = max(numeric_values)
            
            results_data[col] = stats

    return JsonResponse({'columns': results_data})
