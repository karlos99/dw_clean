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
import math
import statistics

from ..models import SavedQuery
from ..constants import COLUMN_DEFINITIONS
from ..column_utils import _get_display_name, RESULTS_STUDENT_MATCH_SQL, RESULTS_BLANK_SSID_SQL, normalize_score_value, parse_sat_column_key, sat_column_db_field, parse_apall_sort_key
from ..utils import can_user_view_query, get_user_folders, is_unrestricted as _is_user_unrestricted, _apply_de_school_filter
from .schedule_filter_sql import parse_schedule_marks_filters, apply_schedule_marks_filters
from ..survey_utils import get_survey_columns
from .student_data_utils import (
    get_current_student_columns,
    get_student_enroll_columns,
)
from ..incident_columns import (
    is_incident_column,
    incident_value_sql_expr,
)
from ..incident_pg_sql import incident_join_sql_fragment
from ..catalog_columns import (
    apply_course_program_filters_for_clickhouse,
    split_course_program_filters,
)
from .filter_sql import (
    apply_column_filters,
    append_de_student_status_where,
    incident_sql_backend_for_request,
    list_query_uses_postgres,
)
from .student_list_views import _append_school_year_filter
from .column_management import (
    get_saved_default_columns,
    get_column_expression,
)
from ..mtss_columns import (
    is_mtss_column,
    canonical_mtss_column,
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
    status_param = request.GET.get('status')
    if status_param is not None:
        status = status_param
    elif 'status' in saved_filters:
        status = saved_filters['status']
    elif 'status' in saved_config:
        status = saved_config['status']
    else:
        status = 'Active'

    CORE_FILTER_KEYS = {
        'search', 'school_year', 'status',
        'schedule_period', 'schedule_subject', 'schedule_course_name', 'schedule_staff',
    }
    filters_json = request.GET.get('filters', '')
    filters = {}
    if filters_json:
        try:
            filters = json.loads(filters_json)
        except Exception:
            filters = saved_filters.copy()
    else:
        filters = {
            k: v for k, v in saved_filters.items()
            if k not in CORE_FILTER_KEYS
        }

    if isinstance(filters, dict):
        filters = {k: v for k, v in filters.items() if k not in CORE_FILTER_KEYS}

    schedule_marks_filters = parse_schedule_marks_filters(
        request, saved_filters, column_filters=filters, user=request.user,
    )

    cp_filters, filters_without_cp = split_course_program_filters(filters)
    list_uses_pg = list_query_uses_postgres(
        filters_without_cp,
        None,
        search=search,
        schedule_marks_filters=schedule_marks_filters,
    )

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

    results_data = {}

    from clickhouse_utils.executor import QueryExecutor
    with QueryExecutor(analytics=True, force_postgres=list_uses_pg).cursor() as cursor:
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

                     for metric in ['SS', 'PL', 'DFP']:
                         norm_key = f"test_{key_base}_{metric.upper()}"
                         original_key = normalized_to_original.get(norm_key)
                         if original_key:
                             info = {
                                 'year': yr, 'test_name': test_name, 'subject': subject, 'test_window': (test_window or '').strip() or None,
                                 'metric': metric.lower(), 'col_name': original_key
                             }
                             if metric == 'DFP':
                                 # DFP is promoted out of other_info (see get_test_columns),
                                 # backed by the "dfp" key in results.other_info.
                                 info['other_info_key'] = 'dfp'
                             test_col_map[original_key] = info
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

        selected_sat_cols = list(set(
            [c for c in filter_keys if isinstance(c, str) and c.startswith('sat_')] +
            [c for c in target_columns if isinstance(c, str) and c.startswith('sat_')]
        ))
        sat_infos = []
        for _col in selected_sat_cols:
            _parsed = parse_sat_column_key(_col)
            if _parsed:
                sat_infos.append({
                    'col_name': _col,
                    'year': _parsed['year'],
                    'kind': _parsed['kind'],
                    'field': _parsed.get('field'),
                    'region': _parsed.get('region'),
                    'metric': _parsed.get('metric'),
                })

        incident_sql_backend = incident_sql_backend_for_request(
            filters_without_cp,
            None,
            search=search,
            schedule_marks_filters=schedule_marks_filters,
        )
        needs_incident_predicate = any(is_incident_column(c) for c in all_relevant_cols)
        needs_incident_ch_join = needs_incident_predicate and incident_sql_backend == 'ch'

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
        if needs_incident_ch_join:
            join_sql += incident_join_sql_fragment(join_sql, backend='ch')

        # Build Base Where
        base_where_clauses = ["1=1"]
        base_params = []
        
        # Apply Base Filters
        if search:
            base_where_clauses.append("(cs.student_name ILIKE %s OR cs.ssid ILIKE %s OR cs.student_id ILIKE %s)")
            base_params.extend([f'%{search}%']*3)
        _append_school_year_filter(base_where_clauses, base_params, school_year)
        append_de_student_status_where(base_where_clauses, base_params, status)

        # Check if user is unrestricted (Project Admin OR District Role)
        is_unrestricted = _is_user_unrestricted(request.user)

        # Apply active school context or enforce school permissions
        _apply_de_school_filter(base_where_clauses, base_params, request.user)

        apply_schedule_marks_filters(
            base_where_clauses,
            base_params,
            request.user,
            schedule_marks_filters,
            school_year,
            filters,
        )

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
        
        scope_where = list(base_where_clauses)
        scope_params = list(base_params)
        where_clauses = list(base_where_clauses)
        params = list(base_params)

        apply_column_filters(
            where_clauses,
            params,
            filters_without_cp,
            test_col_map,
            mark_col_map,
            db_columns,
            enroll_columns,
            needs_enroll,
            needs_students,
            needs_eht,
            ap_exam_infos,
            ap_award_infos,
            incident_sql_backend=incident_sql_backend,
        )
        apply_course_program_filters_for_clickhouse(
            where_clauses,
            params,
            cp_filters,
            scope_where,
            scope_params,
        )

        survey_filters = {
            k: v for k, v in filters.items()
            if isinstance(k, str) and k.startswith('survey_') and v
        } if isinstance(filters, dict) else {}
        if survey_filters:
            _survey_col_meta = get_survey_columns().get('col_meta', {})
            for _sr_col_key, _sr_values in survey_filters.items():
                _sr_meta = _survey_col_meta.get(_sr_col_key)
                if not _sr_meta:
                    continue
                _sr_has_blank = '' in _sr_values
                _sr_real_values = [v for v in _sr_values if v != '']
                _sr_conditions = []
                _sr_base_query = (
                    'SELECT sr.student_id FROM "dataLoader_surveyresponse" sr '
                    'INNER JOIN "dataLoader_surveyquestion" sq ON sr.question_id = sq.id '
                    'INNER JOIN "dataLoader_surveydefinition" sd ON sq.survey_definition_id = sd.id '
                    'INNER JOIN "dataLoader_importlog" il ON sr.import_log_id = il.id '
                    "WHERE sd.survey_type = %s AND sd.school_year = %s "
                    "AND sd.testing_window = %s AND sd.grade_range = %s AND sq.\"order\" = %s "
                    "AND (il.ready_for_exploration = true OR sd.ready_for_exploration = true) AND il.status = 'COMPLETED'"
                )
                _sr_base_params = [
                    _sr_meta['survey_type'], _sr_meta['school_year'],
                    _sr_meta['window'], _sr_meta.get('grade_range', ''),
                    _sr_meta['question_order'],
                ]
                if _sr_real_values:
                    _sr_conditions.append(
                        f"cs.student_id IN ({_sr_base_query} AND sr.value = ANY(%s))"
                    )
                    params.extend(_sr_base_params + [_sr_real_values])
                if _sr_has_blank:
                    _sr_conditions.append(f"cs.student_id NOT IN ({_sr_base_query})")
                    params.extend(_sr_base_params)
                if _sr_conditions:
                    where_clauses.append(f"({' OR '.join(_sr_conditions)})")

        try:
            from landing.utils import get_disabled_school_years
            _disabled_years = get_disabled_school_years()
            if _disabled_years:
                where_clauses.append("cs.school_year NOT IN (%s)")
                params.append(_disabled_years)
        except Exception:
            pass

        where_sql = " AND ".join(where_clauses)
        
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
            query = None
            q_params = params
            
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

                # Restrict to the filtered cohort by joining current_student.
                # A correlated EXISTS reading outer r.* columns is rejected by the
                # ClickHouse analytics read model ("Resolve identifier 'r.ssid' from
                # parent scope..."), so join instead (current_student is unique per
                # student/year, so this does not multiply counts).
                query = f"""
                    SELECT {val_expr} as val, COUNT(*) as cnt
                    FROM results r
                    INNER JOIN tests t ON r.test_group_key = t.test_group_key
                    INNER JOIN current_student cs ON {RESULTS_STUDENT_MATCH_SQL}
                    {join_sql}
                    WHERE {where_sql}
                      AND r.school_year = %s
                      AND t.test_name ILIKE %s
                      AND t.subject ILIKE %s
                      {window_clause}
                      AND {col_expr} IS NOT NULL
                    GROUP BY val
                    ORDER BY val
                """
                q_params = params + [info['year'], info['test_name'], info['subject']] + win_params

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
                    WHERE sm.ssid IN (
                        SELECT cs.ssid FROM current_student cs {join_sql} WHERE {where_sql}
                    )
                    AND sm.school_year = %s
                    AND sm.subject ILIKE %s
                    AND sm.course_name ILIKE %s
                    {period_clause}
                    AND sm.mark IS NOT NULL
                    GROUP BY val
                    ORDER BY val
                """
                q_params = params + [info['year'], f"%{info['subject']}%", f"%{info['course_name']}%"] + period_params

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
                q_params = params + [
                    _survey_meta['survey_type'], _survey_meta['school_year'],
                    _survey_meta['window'], _survey_meta.get('grade_range', ''),
                    _survey_meta['question_order'],
                ]

            # --- AP "All Results" wide-pair columns (apall_<year>_<i>_SUBJECT|PL) ---
            elif col.startswith('apall_'):
                apall_parsed = parse_apall_sort_key(col)
                if not apall_parsed:
                    continue
                is_string_stats_column = True
                _apall_year = apall_parsed['year']
                _apall_excludes = set()
                _excl_vals = filters.get(f"apall_{_apall_year.replace('-', '_')}") if isinstance(filters, dict) else None
                if isinstance(_excl_vals, list):
                    _apall_excludes = {
                        str(v)[len('exclude:'):] for v in _excl_vals if str(v).startswith('exclude:')
                    }
                _excl_clause = ""
                _excl_params = []
                if _apall_excludes:
                    _placeholders = ", ".join(["%s"] * len(_apall_excludes))
                    _excl_clause = f" AND e.name NOT IN ({_placeholders})"
                    _excl_params = sorted(_apall_excludes)
                _val_expr = "e.name" if apall_parsed['field'] == 'SUBJECT' else "a.exam_grade"
                query = f"""
                    SELECT val, COUNT(*) as cnt
                    FROM (
                        SELECT {_val_expr} AS val,
                               row_number() OVER (PARTITION BY a.ssid ORDER BY e.name) AS rn
                        FROM "dataLoader_apexamresult" a
                        INNER JOIN "dataLoader_apexamcode" e ON a.exam_id = e.code
                        INNER JOIN "dataLoader_apimportlog" il ON a.import_log_id = il.id
                        WHERE a.ssid IN (
                            SELECT cs.ssid FROM current_student cs {join_sql} WHERE {where_sql}
                        )
                        AND a.admin_year = %s
                        AND il.is_published = true
                        {_excl_clause}
                    ) t
                    WHERE rn = %s AND val IS NOT NULL AND val <> ''
                    GROUP BY val
                    ORDER BY val
                """
                q_params = params + [_apall_year] + _excl_params + [apall_parsed['index']]

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
                q_params = params + [ap_info['year'], ap_info['subject']]

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
                    q_params = params + [ap_info['year']]
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
                    q_params = params + [ap_info['year'], ap_info['award']]

            # --- SAT Score / Percentile Columns ---
            elif col.startswith('sat_'):
                sat_info = next((i for i in sat_infos if i['col_name'] == col), None)
                if not sat_info:
                    continue
                sat_db_field = sat_column_db_field(col)
                if not sat_db_field:
                    continue
                is_string_stats_column = True
                query = f"""
                    SELECT r.{sat_db_field}::text as val, COUNT(*) as cnt
                    FROM "dataLoader_satresult" r
                    INNER JOIN "dataLoader_satimportlog" il ON r.import_log_id = il.id
                    WHERE r.ssid IN (
                        SELECT cs.ssid FROM current_student cs {join_sql} WHERE {where_sql}
                    )
                    AND r.school_year = %s
                    AND il.is_published = true
                    AND r.{sat_db_field} IS NOT NULL
                    GROUP BY val
                    ORDER BY val
                """
                q_params = params + [sat_info['year']]

            # --- MTSS Columns ---
            elif is_mtss_column(col):
                mtss_field = canonical_mtss_column(col)
                if not mtss_field:
                    continue
                is_string_stats_column = True
                query = f"""
                    SELECT m.{mtss_field} as val, COUNT(*) as cnt
                    FROM mtss_data m
                    JOIN current_student cs ON m.ssid = cs.ssid
                        AND (m.school_year = cs.school_year OR m.school_year = SUBSTRING(cs.school_year FROM 1 FOR 4) OR cs.school_year = SUBSTRING(m.school_year FROM 1 FOR 4))
                    {join_sql}
                    WHERE {where_sql}
                      AND m.{mtss_field} IS NOT NULL AND m.{mtss_field} <> ''
                    GROUP BY val
                    ORDER BY val
                """
                q_params = params

            # --- Incident columns (Major / Minor / Suspension Days) ---
            elif is_incident_column(col):
                expr = incident_value_sql_expr(
                    col,
                    backend=incident_sql_backend,
                    student_alias='cs',
                    join_alias='_si_incident',
                )
                if not expr:
                    continue
                is_string_stats_column = False
                query = f"""
                    SELECT COALESCE({expr}, 0) as val, COUNT(*) as cnt
                    FROM current_student cs
                    {join_sql}
                    WHERE {where_sql}
                    GROUP BY val
                    ORDER BY val
                """
                q_params = params

            # --- Standard Columns ---
            else:

                expr, _ = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
                if not expr:
                    continue
                
                query = f"""
                    SELECT {expr} as val, COUNT(*) as cnt
                    FROM current_student cs
                    {join_sql}
                    WHERE {where_sql}
                    AND {expr} IS NOT NULL AND {expr} != ''
                    GROUP BY val
                    ORDER BY val
                """
                q_params = params

            if not query:
                continue
            try:
                cursor.execute(query, q_params)
                rows = cursor.fetchall()
            except Exception:
                continue
            
            # Process Distribution
            total_count = 0
            values = []
            numeric_values = []
            
            # Detect if this column is effectively numeric based on returned values
            numeric_count = 0
            
            for val, cnt in rows:
                # Placeholder score values (NaN/NS/N/A/None/...) are treated as
                # blank everywhere else in the data explorer (load_students,
                # export_cell) — never surface them on the summary chart.
                norm_val = normalize_score_value(val)
                if norm_val is None:
                    continue
                total_count += cnt
                is_num_val = False
                fval = None
                if not is_string_stats_column:
                    # Try to parse as float
                    try:
                        fval = float(norm_val)
                    except (ValueError, TypeError):
                        fval = None
                    # Skip non-finite values (e.g. an "NaN" sentinel that
                    # slipped through) so averages never render as NaN.
                    if fval is not None and math.isfinite(fval):
                        numeric_values.extend([fval] * cnt)
                        numeric_count += cnt
                        is_num_val = True

                # Check for sorting purposes later
                values.append({'value': norm_val, 'count': cnt, 'numeric_val': fval if is_num_val else None})

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
