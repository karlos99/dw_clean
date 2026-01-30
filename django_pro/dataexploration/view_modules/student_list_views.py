"""Student list views with filtering and data loading."""

from django.shortcuts import render
from django.http import JsonResponse
from django.views.generic import ListView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import connections
from django.core.cache import cache
from django.conf import settings
from decimal import Decimal
from datetime import date, datetime
import hashlib
import json

from ..models import SavedQuery, StudentList
from studata.models import CurrentStudent
from ..column_utils import get_test_columns
from ..constants import COLUMN_DEFINITIONS, COLUMN_CATEGORIES
from ..utils import can_user_view_query, get_user_folders
from .student_data_utils import (
    get_current_student_columns,
    get_student_enroll_columns,
)
from .column_management import (
    get_saved_default_columns,
    get_column_expression,
)

class StudentWithTestsListView(LoginRequiredMixin, ListView):
    """Display list of students with their test results (one row per student)."""
    model = CurrentStudent
    template_name = 'dataexploration/student_with_tests_list.html'
    context_object_name = 'students'
    paginate_by = 50

    def get_context_data(self, **kwargs):
        """Add additional context for filters and column selection."""
        context = super().get_context_data(**kwargs)
        
        # Get dynamic test columns
        test_structure = get_test_columns()
        
        # Get unique values for filters using cache
        school_years_key = "dataexp:school_years"
        school_years = cache.get(school_years_key)
        if not school_years:
            with connections['default'].cursor() as cursor:
                cursor.execute("SELECT DISTINCT school_year FROM current_student WHERE school_year IS NOT NULL ORDER BY school_year DESC")
                school_years = [row[0] for row in cursor.fetchall()]
                cache.set(school_years_key, school_years, 3600)
        
        grades_key = "dataexp:grades"
        grades = cache.get(grades_key)
        if not grades:
            with connections['default'].cursor() as cursor:
                cursor.execute("SELECT DISTINCT grade FROM current_student WHERE grade IS NOT NULL ORDER BY grade")
                grades = [row[0] for row in cursor.fetchall()]
                cache.set(grades_key, grades, 3600)
        
        context['school_years'] = school_years
        context['grades'] = grades
        context['test_structure'] = test_structure
        context['column_categories'] = COLUMN_CATEGORIES
        context['column_definitions'] = COLUMN_DEFINITIONS
        default_columns = get_saved_default_columns()
        context['default_columns'] = default_columns
        context['default_columns_json'] = json.dumps(default_columns)
        context['column_definitions_json'] = json.dumps(COLUMN_DEFINITIONS)
        
        # Handle Saved Query
        saved_query_id = self.request.GET.get('saved_query')
        query_config = {}
        if saved_query_id:
            try:
                sq = SavedQuery.objects.get(pk=saved_query_id)
                query_config = sq.config or {}
                # If query has column selection, override default
                if 'columns' in query_config:
                    context['default_columns'] = query_config['columns']
                    context['default_columns_json'] = json.dumps(query_config['columns'])
            except SavedQuery.DoesNotExist:
                pass

        filters = query_config.get('filters', {})

        def get_param(name, default=''):
            # URL param takes precedence over saved query, which takes precedence over default
            return self.request.GET.get(name) or filters.get(name, default)

        # Current filter values
        context['search_query'] = get_param('search')
        context['selected_school_year'] = get_param('school_year')
        context['selected_grade'] = get_param('grade')
        context['selected_status'] = get_param('status', 'Active')
        
        return context



def load_students(request):
    """Load student data with dynamic test columns via HTMX."""
    page = int(request.GET.get('page', 1))
    limit = int(request.GET.get('limit', 50))
    
    # Load saved query config
    saved_query_id = request.GET.get('saved_query')
    saved_config = {}
    if saved_query_id:
        try:
            query_obj = SavedQuery.objects.get(pk=saved_query_id)
            if can_user_view_query(request.user, query_obj):
                saved_config = query_obj.config
                # Track query execution
                query_obj.execute()
        except SavedQuery.DoesNotExist:
            pass
        except Exception:
            pass
    
    # Get selected columns
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
    
    def _apply_column_filters(where_clauses, params, filters_to_apply, test_col_map, db_columns, enroll_columns, needs_enroll, needs_students=False):
        """Append SQL filter clauses + params for the provided filters dict."""
        if not isinstance(filters_to_apply, dict):
            return

        for col, values in filters_to_apply.items():
            if not values:
                continue

            # Handle blank values (represented by empty string)
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
                    test_info['year'],
                    f"%{test_info['test_name']}%",
                    f"%{test_info['subject']}%",
                    *window_params,
                ]

                conditions = []

                if real_values:
                    # Exists and matches value
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
                    # Does not exist (Blank) OR Exists but value is null
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

            # Standard columns
            expr, _ = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students)
            if expr:
                 conditions = []
                 if real_values:
                      conditions.append(f"{expr} = ANY(%s)")
                      params.append(real_values)
                 if has_blank:
                      # Check if date (only IS NULL) or string (IS NULL or Empty)
                      if col in {'birth_date', 'us_enter_date', 'fep_date'}:
                           conditions.append(f"{expr} IS NULL")
                      else:
                           conditions.append(f"({expr} IS NULL OR {expr} = '')")
                 
                 if conditions:
                      where_clauses.append(f"({' OR '.join(conditions)})")

    with connections['default'].cursor() as cursor:
        db_columns = get_current_student_columns()
        enroll_columns = get_student_enroll_columns()
        base_where_clauses = ["1=1"]
        base_params = []

        def _parse_year_from_test_col(col_key):
            """Extract school_year from a test_* column key (robust for YYYY_YYYY)."""
            s = (col_key or '').replace('-', '_')
            if not s.startswith('test_'):
                return None
            tail = s[len('test_'):]
            # Expect YYYY_YYYY_... at the start
            if len(tail) >= 10 and tail[4] == '_' and tail[9] == '_':
                return tail[:9].replace('_', '-')
            # Fallback: first token
            parts = tail.split('_', 1)
            if parts:
                return parts[0].replace('_', '-')
            return None

        def _build_test_columns_from_db(selected_test_cols):
            """Map selected test_* columns to DB metadata using the tests/results tables.

            This avoids brittle parsing when test_name/subject/test_window contain underscores.
            """
            if not selected_test_cols:
                return [], {}

            # Try cache first
            test_cols_cache_key = f"test_cols:{hashlib.md5(json.dumps(sorted(selected_test_cols)).encode()).hexdigest()}"
            cached_result = cache.get(test_cols_cache_key)
            if cached_result is not None:
                return cached_result

            normalized_to_original = {c.replace('-', '_'): c for c in selected_test_cols}
            years = sorted({y for y in (_parse_year_from_test_col(c) for c in selected_test_cols) if y})
            if not years:
                return [], {}

            cursor.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_name = 'tests' AND column_name = 'test_window'"
            )
            has_test_window = cursor.fetchone() is not None

            if has_test_window:
                cursor.execute(
                    """
                    SELECT DISTINCT r.school_year, t.test_name, t.subject, COALESCE(t.test_window, '')
                    FROM results r
                    INNER JOIN tests t ON r.test_group_key = t.test_group_key
                    WHERE r.school_year = ANY(%s) AND r.school_year IS NOT NULL AND t.test_name IS NOT NULL
                    """,
                    [years],
                )
            else:
                cursor.execute(
                    """
                    SELECT DISTINCT r.school_year, t.test_name, t.subject, ''
                    FROM results r
                    INNER JOIN tests t ON r.test_group_key = t.test_group_key
                    WHERE r.school_year = ANY(%s) AND r.school_year IS NOT NULL AND t.test_name IS NOT NULL
                    """,
                    [years],
                )

            infos = []
            col_map = {}

            def _norm_part(v):
                return (v or '').replace(' ', '_').replace('-', '_')

            for (yr, test_name, subject, test_window) in cursor.fetchall():
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

                ss_norm = f"test_{key_base}_SS"
                pl_norm = f"test_{key_base}_PL"

                for norm_key, metric in ((ss_norm, 'ss'), (pl_norm, 'pl')):
                    original_key = normalized_to_original.get(norm_key)
                    if not original_key:
                        continue
                    info = {
                        'col_name': original_key,
                        'year': yr,
                        # Keep raw DB values so ILIKE matches tests like "Initial_ELPAC".
                        'test_name': (test_name or ''),
                        'subject': (subject or ''),
                        'test_window': win_value,
                        'metric': metric,
                    }
                    infos.append(info)
                    # Map both exact and normalized keys for lookups
                    col_map[original_key] = info
                    col_map[original_key.replace('-', '_')] = info

            # Add mapping for other_info-backed columns (test_*_OI_*)
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
                    if not raw_key:
                        continue
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
                    if not original_key:
                        continue
                    info = {
                        'col_name': original_key,
                        'year': yr,
                        'test_name': (test_name or ''),
                        'subject': (subject or ''),
                        'test_window': win_value,
                        'metric': 'oi',
                        'other_info_key': raw_key_str,
                    }
                    infos.append(info)
                    col_map[original_key] = info
                    col_map[original_key.replace('-', '_')] = info

            # Cache the result for 10 minutes
            result = (infos, col_map)
            cache.set(test_cols_cache_key, result, 600)
            return result
        
        # Apply student list filter
        # A student list is a fixed set of students; when present it should override other
        # user-selected filters (grade/status/year/search/column-filters).
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
                        list_ssids = None  # legacy path handled by SQL above
                except Exception:
                    pass

            # If we have an explicit SSID list, apply it and ignore other filters.
            if isinstance(list_ssids, list) and list_ssids:
                normalized_ssids = [str(s).strip() for s in list_ssids if str(s).strip()]
                if normalized_ssids:
                    base_where_clauses.append("cs.ssid = ANY(%s)")
                    base_params.append(normalized_ssids)

                    # Override user-driven filters; the list defines membership.
                    search = None
                    school_year = None
                    grade = None
                    status = None
                    filters = {}

        # Apply filters
        if search:
            base_where_clauses.append("(cs.student_name ILIKE %s OR cs.ssid ILIKE %s OR cs.student_id ILIKE %s)")
            search_param = f'%{search}%'
            base_params.extend([search_param, search_param, search_param])
        
        if school_year:
            base_where_clauses.append("cs.school_year = %s")
            base_params.append(school_year)
        
        if grade:
            base_where_clauses.append("cs.grade = %s")
            base_params.append(grade)

        if status:
            base_where_clauses.append("cs.status = %s")
            base_params.append(status)

        # Separate base columns from test columns
        selected_test_cols = [c for c in selected_columns if isinstance(c, str) and c.startswith('test_')]
        base_cols = [c for c in selected_columns if isinstance(c, str) and (not c.startswith('test_')) and c in COLUMN_DEFINITIONS]

        # Build test column metadata from DB to avoid parsing issues (e.g. multi-word subjects)
        test_cols_info, test_col_map = _build_test_columns_from_db(selected_test_cols)

        # Determine if we need student_enroll join
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

        # Column filters (applied to the main query)
        where_clauses = list(base_where_clauses)
        params = list(base_params)
        _apply_column_filters(where_clauses, params, filters, test_col_map, db_columns, enroll_columns, needs_enroll, needs_students)

        where_sql = " AND ".join(where_clauses)
        
        # Count total
        count_query = f"""
            SELECT COUNT(DISTINCT cs.ssid)
            FROM current_student cs
            {join_sql}
            WHERE {where_sql}
        """
        cursor.execute(count_query, params)
        total = cursor.fetchone()[0]

        # Copy params for later reuse
        filter_params = list(params)
        
        # Get paginated results
        offset = (page - 1) * limit
        
        # Always include ssid for joining
        if 'ssid' not in base_cols:
            base_cols.insert(0, 'ssid')

        if test_cols_info and 'student_id' not in base_cols:
            base_cols.insert(0, 'student_id')

        # Build base column SELECT with safe mapping
        base_col_selects = []
        base_col_expressions = {}
        for col in base_cols:
            expr, alias = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students)
            if expr and alias:
                base_col_expressions[col] = expr
                if expr != f"cs.{alias}":
                    base_col_selects.append(f"{expr} AS {alias}")
                else:
                    base_col_selects.append(expr)

        if not base_col_selects:
            base_col_selects.append("cs.ssid") if 'ssid' in db_columns else base_col_selects.append("NULL AS ssid")

        base_col_sql = ", ".join(base_col_selects)

        # Sorting
        order_by_expr = "cs.student_name"
        if sort_by:
            expr = base_col_expressions.get(sort_by)
            if expr:
                order_by_expr = expr
        sort_dir = 'desc' if sort_dir == 'desc' else 'asc'
        
        # Build query with base columns
        query = f"""
        SELECT {base_col_sql}
        FROM current_student cs
        {join_sql}
        WHERE {where_sql}
        ORDER BY {order_by_expr} {sort_dir}
        LIMIT %s OFFSET %s
        """
        
        params.extend([limit, offset])
        cursor.execute(query, params)
        
        columns = [desc[0] for desc in cursor.description]
        students = [dict(zip(columns, row)) for row in cursor.fetchall()]
        
        student_ids = [s.get('student_id') for s in students if s.get('student_id')]
        
        # Fetch test data in bulk for selected test columns
        if test_cols_info and student_ids:
            for test_info in test_cols_info:
                metric_col = test_info['metric']
                test_name_db = test_info['test_name'].replace(' ', '_')
                subject_db = test_info['subject'].replace(' ', '_')

                window_clause = ""
                window_params = []
                if test_info.get('test_window'):
                    window_clause = "AND t.test_window ILIKE %s"
                    window_params = [test_info['test_window']]
                
                if metric_col in {'ss', 'pl'}:
                    select_expr = f"r.{metric_col}"
                    extra_params = []
                else:
                    select_expr = "(r.other_info ->> %s)"
                    extra_params = [test_info.get('other_info_key', '')]

                query = f"""
                    SELECT r.local_student_id, {select_expr}
                    FROM results r
                    INNER JOIN tests t ON r.test_group_key = t.test_group_key
                    WHERE r.local_student_id = ANY(%s)
                      AND r.school_year = %s
                      AND t.test_name ILIKE %s
                      AND t.subject ILIKE %s
                      {window_clause}
                """
                cursor.execute(query, [
                    *extra_params,
                    student_ids,
                    test_info['year'],
                    f"%{test_info['test_name']}%",
                    f"%{test_info['subject']}%",
                    *window_params,
                ])
                
                test_data = {row[0]: row[1] for row in cursor.fetchall()}
                
                # Add test data to each student
                for student in students:
                    student_id = student.get('student_id')
                    student[test_info['col_name']] = test_data.get(student_id, '—')

        # Ensure JSON serialization safety (e.g. Decimal fields like atendace_rate)
        for student in students:
            for key, value in list(student.items()):
                if isinstance(value, Decimal):
                    student[key] = float(value)
                elif isinstance(value, (datetime, date)):
                    student[key] = value.isoformat()

        # Build filter options for repeatable columns (with Redis caching)
        filter_options = {}
        if total > 0:
            excluded_filters = {'ssid', 'student_id', 'student_name'}
            always_include = {'school'}
            
            # Create cache key based on active filters (excluding current column)
            def _get_filter_cache_key(column, active_filters):
                filter_str = json.dumps(sorted(active_filters.items()), sort_keys=True) if active_filters else "no_filters"
                return f"filter_opts:{column}:{hashlib.md5(filter_str.encode()).hexdigest()}"
            
            for col in base_cols:
                if col in excluded_filters:
                    continue
                expr = base_col_expressions.get(col)
                if not expr:
                    continue
                
                # Faceted filtering: compute this column's options without its own filter applied.
                if isinstance(filters, dict) and col in filters:
                    other_filters = {k: v for k, v in filters.items() if k != col}
                else:
                    other_filters = filters
                
                # Try cache first
                cache_key = _get_filter_cache_key(col, other_filters)
                cached_values = cache.get(cache_key)
                
                if cached_values is not None:
                    filter_options[col] = cached_values
                    continue
                
                # Cache miss - query database
                option_where = list(base_where_clauses)
                option_params = list(base_params)
                _apply_column_filters(option_where, option_params, other_filters, test_col_map, db_columns, enroll_columns, needs_enroll, needs_students)
                option_where_sql = " AND ".join(option_where) if option_where else "1=1"

                distinct_query = f"SELECT COUNT(DISTINCT {expr}) FROM current_student cs {join_sql} WHERE {option_where_sql}"
                cursor.execute(distinct_query, option_params)
                distinct_count = cursor.fetchone()[0] or 0
                if distinct_count <= 1 or (distinct_count > 30 and col not in always_include):
                    continue
                
                limit_clause = "LIMIT 30"
                if col in always_include:
                    limit_clause = ""

                options_query = f"""
                    SELECT {expr}
                    FROM current_student cs
                    {join_sql}
                    WHERE {option_where_sql}
                    GROUP BY {expr}
                    ORDER BY {expr} ASC NULLS FIRST
                    {limit_clause}
                """
                cursor.execute(options_query, option_params)
                values = []
                for row in cursor.fetchall():
                    val = row[0]
                    if val is None:
                        val = ""
                    values.append(val)

                if values:
                    filter_options[col] = values
                    # Cache for 5 minutes
                    cache.set(cache_key, values, 300)

            # Add filter options for test PL columns and other_info columns (with caching)
            for test_info in test_cols_info:
                if test_info['metric'] not in {'pl', 'oi'}:
                    continue
                col_key = test_info['col_name']
                
                if isinstance(filters, dict) and col_key in filters:
                    other_filters = {k: v for k, v in filters.items() if k != col_key}
                else:
                    other_filters = filters
                
                # Try cache first
                cache_key = _get_filter_cache_key(col_key, other_filters)
                cached_values = cache.get(cache_key)
                
                if cached_values is not None:
                    filter_options[col_key] = cached_values
                    continue
                
                # Cache miss - query database
                option_where = list(base_where_clauses)
                option_params = list(base_params)
                _apply_column_filters(option_where, option_params, other_filters, test_col_map, db_columns, enroll_columns, needs_enroll)
                option_where_sql = " AND ".join(option_where) if option_where else "1=1"
                window_clause = ""
                window_params = []
                if test_info.get('test_window'):
                    window_clause = "AND t.test_window ILIKE %s"
                    window_params = [test_info['test_window']]

                if test_info['metric'] == 'pl':
                    options_query = f"""
                    SELECT r.pl AS v
                    FROM results r
                    INNER JOIN tests t ON r.test_group_key = t.test_group_key
                    INNER JOIN current_student cs ON r.local_student_id = cs.student_id
                    {join_sql}
                    WHERE {option_where_sql}
                      AND r.school_year = %s
                      AND t.test_name ILIKE %s
                      AND t.subject ILIKE %s
                      {window_clause}
                    GROUP BY v
                    ORDER BY v ASC NULLS FIRST
                    LIMIT 30
                """
                    cursor.execute(options_query, option_params + [
                        test_info['year'],
                        f"%{test_info['test_name']}%",
                        f"%{test_info['subject']}%",
                        *window_params,
                    ])
                else:
                    options_query = f"""
                    SELECT (r.other_info ->> %s) AS v
                    FROM results r
                    INNER JOIN tests t ON r.test_group_key = t.test_group_key
                    INNER JOIN current_student cs ON r.local_student_id = cs.student_id
                    {join_sql}
                    WHERE {option_where_sql}
                      AND r.school_year = %s
                      AND t.test_name ILIKE %s
                      AND t.subject ILIKE %s
                      {window_clause}
                    GROUP BY v
                    ORDER BY v ASC NULLS FIRST
                    LIMIT 30
                    """
                    other_key = test_info.get('other_info_key', '')
                    cursor.execute(options_query, [
                        other_key,
                        *option_params,
                        test_info['year'],
                        f"%{test_info['test_name']}%",
                        f"%{test_info['subject']}%",
                        *window_params,
                    ])
                
                values = []
                for row in cursor.fetchall():
                    val = row[0]
                    if val is None:
                        val = ""
                    if val not in values:
                        values.append(val)

                # Check if there are any students with NO result record (Blank)
                check_blank_query = f"""
                    SELECT 1
                    FROM current_student cs
                    {join_sql}
                    WHERE {option_where_sql}
                    AND NOT EXISTS (
                        SELECT 1 FROM results r
                        INNER JOIN tests t ON r.test_group_key = t.test_group_key
                        WHERE r.local_student_id = cs.student_id
                        AND r.school_year = %s
                        AND t.test_name ILIKE %s
                        AND t.subject ILIKE %s
                        {window_clause}
                    )
                    LIMIT 1
                """
                check_params = option_params + [
                    test_info['year'],
                    f"%{test_info['test_name']}%",
                    f"%{test_info['subject']}%",
                    *window_params,
                ]
                cursor.execute(check_blank_query, check_params)
                if cursor.fetchone():
                    if "" not in values:
                        values.insert(0, "")

                if values:
                    filter_options[col_key] = values
                    # Cache for 5 minutes
                    cache.set(cache_key, values, 300)
    
    total_pages = (total + limit - 1) // limit

    # Ensure JSON serialization safety for filter options (e.g. Decimal values)
    for col, opts in list(filter_options.items()):
        if isinstance(opts, list):
            normalized = []
            for v in opts:
                if isinstance(v, Decimal):
                    normalized.append(float(v))
                elif isinstance(v, (datetime, date)):
                    normalized.append(v.isoformat())
                else:
                    normalized.append(v)
            filter_options[col] = normalized

    # filter_options is built inside the DB cursor context

    # Build dynamic definitions including test windows
    dynamic_definitions = COLUMN_DEFINITIONS.copy()
    if test_cols_info:
        for info in test_cols_info:
            metric_map = {'ss': 'Scale Score', 'pl': 'Perf Lvl', 'oi': info.get('other_info_key', 'Info')}
            metric_label = metric_map.get(info['metric'], info['metric'])
            label = f"{info['test_name']} {info['subject']} {metric_label}"
            if info.get('test_window'):
                label += f"\n{info['test_window']}"
            dynamic_definitions[info['col_name']] = label

    if request.GET.get('format') == 'json':
        return JsonResponse({
            'students': students,
            'selected_columns': selected_columns,
            'total': total,
            'page': page,
            'total_pages': total_pages,
            'column_definitions': dynamic_definitions,
            'filter_options': filter_options,
            'filters': filters,
            'search': search or '',
            'school_year': school_year or '',
            'grade': grade or '',
            'status': status or '',
            'sort_by': sort_by,
            'sort_dir': sort_dir,
        })

    # Convert students list to JSON for legacy/template rendering (non-API path)
    # Use Django's JSON encoder indirectly via JsonResponse in the API path.
    students_json = [json.dumps(student, default=str) for student in students]

    context = {
        'students': students,
        'students_json': students_json,
        'selected_columns': selected_columns,
        'total': total,
        'page': page,
        'total_pages': total_pages,
        'column_definitions': COLUMN_DEFINITIONS,
    }

    return render(request, 'dataexploration/partials/student_table.html', context)


