"""Views for data exploration app."""

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseForbidden, HttpResponse, StreamingHttpResponse
from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from django.db.models import Q
from django.db import connections
from django.core.cache import cache
from django.template.loader import render_to_string
from django.conf import settings
from django.urls import reverse
from django.contrib.auth import get_user_model
import json
from decimal import Decimal
from datetime import date, datetime
import hashlib
import os
import csv

from .models import StudentWithTests, DefaultColumnSelection, ReportFolder, SavedQuery, StudentList, StudentListItem, FolderShare, QueryShare
from studata.models import CurrentStudent, StudentEnroll, School
from .column_utils import get_test_columns
from .constants import COLUMN_DEFINITIONS, COLUMN_CATEGORIES, DEFAULT_COLUMNS
from .utils import get_user_folders, can_user_edit_folder, can_user_manage_query, can_user_manage_list, can_create_in_folder
from django.views.decorators.http import require_POST
from django.db import transaction


def _student_summary_cache_version():
    # Include template mtime so edits don't keep serving stale cached HTML.
    # This is especially important in dev where templates are frequently edited.
    try:
        template_path = settings.BASE_DIR / 'templates' / 'dataexploration' / 'partials' / 'student_test_summary_content.html'
        mtime = int(os.path.getmtime(template_path))
    except Exception:
        mtime = 0
    return f"v2:{mtime}"


def _student_summary_cache_keys(ssid: str, user_id=None):
    v = _student_summary_cache_version()
    user_part = f"u{user_id}:" if user_id else ""
    return {
        'student_info': f"dataexp:student_summary:{v}:{user_part}student_info:{ssid}",
        'test_records': f"dataexp:student_summary:{v}:{user_part}test_records:{ssid}",
        'partial_html': f"dataexp:student_summary:{v}:{user_part}partial_html:{ssid}",
    }


def _build_student_info_for_ssid(ssid: str):
    cs_latest = (
        CurrentStudent.objects.filter(ssid=ssid)
        .order_by('-school_year')
        .first()
    )
    se_latest = (
        StudentEnroll.objects.filter(ssid=ssid)
        .order_by('-school_year', '-enter_date')
        .first()
    )

    school_short_name = None
    school_state_id = getattr(cs_latest, 'school_state_id', None) if cs_latest else None
    if school_state_id:
        school_obj = School.objects.filter(state_id=school_state_id).first()
        school_short_name = getattr(school_obj, 'short_name', None) if school_obj else None

    full_name = None
    if se_latest and (se_latest.first_name or se_latest.last_name or se_latest.middle_name):
        full_name = se_latest.full_name
    elif cs_latest and getattr(cs_latest, 'student_name', None):
        full_name = cs_latest.student_name

    return {
        'full_name': full_name,
        'student_id': getattr(cs_latest, 'student_id', None) if cs_latest else None,
        'ssid': getattr(cs_latest, 'ssid', None) if cs_latest else ssid,
        'grade': getattr(cs_latest, 'grade', None) if cs_latest else None,
        'gender': getattr(cs_latest, 'gender', None) if cs_latest else None,
        'race': getattr(cs_latest, 'race', None) if cs_latest else None,
        'birth_date': getattr(se_latest, 'birth_date', None) if se_latest else None,
        'school': getattr(cs_latest, 'school', None) if cs_latest else None,
        'school_state_id': school_state_id,
        'school_short_name': school_short_name,
        'school_year': getattr(cs_latest, 'school_year', None) if cs_latest else None,
        'status': getattr(cs_latest, 'status', None) if cs_latest else None,
        'atendace_rate': getattr(cs_latest, 'atendace_rate', None) if cs_latest else None,
        'instructional_setting': getattr(se_latest, 'instructional_setting', None) if se_latest else None,
        'ell_status': getattr(se_latest, 'ell_status', None) if se_latest else None,
        'ltel': getattr(cs_latest, 'ltel', None) if cs_latest else None,
        'fep_date': getattr(se_latest, 'fep_date', None) if se_latest else None,
        'us_enter_date': getattr(se_latest, 'us_enter_date', None) if se_latest else None,
        'state_enter_date': getattr(se_latest, 'state_enter_date', None) if se_latest else None,
        'special_program_code': getattr(se_latest, 'special_program_code', None) if se_latest else None,
        'sped': getattr(cs_latest, 'sped', None) if cs_latest else None,
        'in_504': getattr(cs_latest, 'in_504', None) if cs_latest else None,
        'gate': getattr(cs_latest, 'gate', None) if cs_latest else None,
        'sed': getattr(cs_latest, 'sed', None) if cs_latest else None,
        'foster': getattr(cs_latest, 'foster', None) if cs_latest else None,
        'homeless': getattr(cs_latest, 'homeless', None) if cs_latest else None,
        'migrant': getattr(se_latest, 'migrant', None) if se_latest else None,
        'dli': getattr(cs_latest, 'dli', None) if cs_latest else None,
        'suspension_indicator': getattr(cs_latest, 'suspension_indicator', None) if cs_latest else None,
    }


def _fetch_test_records_for_ssid(ssid: str):
    with connections['default'].cursor() as cursor:
        cursor.execute(
            """
            SELECT
                r.school_year AS school_year,
                t.test_name AS test_name,
                t.subject AS subject,
                t.test_window AS test_window,
                cs.grade AS grade,
                cs.school AS school,
                cs.school_state_id AS school_state_id,
                sch.short_name AS school_short_name,
                r.pl AS pl,
                r.ss AS ss,
                r.school_code AS school_code,
                r.other_info AS other_info
            FROM results r
            INNER JOIN tests t ON r.test_group_key = t.test_group_key
            LEFT JOIN current_student cs
                ON cs.ssid = r.ssid AND cs.school_year = r.school_year
            LEFT JOIN school sch
                ON sch.assessment_school_code = r.school_code
            WHERE r.ssid = %s
            ORDER BY r.school_year, t.test_name, t.subject
            """,
            [ssid],
        )
        cols = [c[0] for c in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _get_student_info_cached(ssid: str, timeout: int = 300, user_id=None):
    keys = _student_summary_cache_keys(ssid, user_id=user_id)
    cached = cache.get(keys['student_info'])
    if cached is not None:
        return cached
    student_info = _build_student_info_for_ssid(ssid)
    cache.set(keys['student_info'], student_info, timeout=timeout)
    return student_info


def _get_test_records_cached(ssid: str, timeout: int = 300, user_id=None):
    keys = _student_summary_cache_keys(ssid, user_id=user_id)
    cached = cache.get(keys['test_records'])
    if cached is not None:
        return cached
    test_records = _fetch_test_records_for_ssid(ssid)
    cache.set(keys['test_records'], test_records, timeout=timeout)
    return test_records


def _get_student_summary_partial_html_cached(request, ssid: str, timeout: int = 300):
    user_id = getattr(getattr(request, 'user', None), 'pk', None)
    keys = _student_summary_cache_keys(ssid, user_id=user_id)
    cached = cache.get(keys['partial_html'])
    if cached is not None:
        return cached

    context = {
        'ssid': ssid,
        'student_info': _get_student_info_cached(ssid, timeout=timeout, user_id=user_id),
        'student_name': _get_student_info_cached(ssid, timeout=timeout, user_id=user_id).get('full_name'),
        'test_records': _get_test_records_cached(ssid, timeout=timeout, user_id=user_id),
    }
    html = render_to_string('dataexploration/partials/student_test_summary_content.html', context, request=request)
    cache.set(keys['partial_html'], html, timeout=timeout)
    return html


def _get_current_student_columns():
    """Fetch current_student columns from the DB (cached)."""
    columns_key = "dataexp:current_student_columns"
    columns = cache.get(columns_key)
    if not columns:
        with connections['default'].cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'current_student'"
            )
            columns = [row[0] for row in cursor.fetchall()]
            cache.set(columns_key, columns, 3600)
    return set(columns)


def _get_student_enroll_columns():
    """Fetch student_enroll columns from the DB (cached)."""
    columns_key = "dataexp:student_enroll_columns"
    columns = cache.get(columns_key)
    if not columns:
        with connections['default'].cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'student_enroll'"
            )
            columns = [row[0] for row in cursor.fetchall()]
            cache.set(columns_key, columns, 3600)
    return set(columns)


def _get_saved_default_columns():
    selection = DefaultColumnSelection.objects.order_by('-updated_at', '-id').first()
    if selection and selection.columns:
        return selection.columns
    return DEFAULT_COLUMNS


def _get_column_expression(col, db_columns, enroll_columns=None, use_enroll=False, use_students=False):
    if col == 'ltel' and use_students:
        return "s.ltel", 'ltel'
    if col == 'ell':
        ell_candidates = []
        if 'ell' in db_columns:
            ell_candidates.append('cs.ell')
        if use_enroll and enroll_columns:
            if 'ell_status' in enroll_columns:
                ell_candidates.append('se.ell_status')
        if ell_candidates:
            return f"COALESCE({', '.join(ell_candidates)})", 'ell'
        return None, None
    if col == 'atendace_rate' and 'attendance_rate' in db_columns:
        return "cs.attendance_rate", 'atendace_rate'
    if col in {'birth_date', 'us_enter_date', 'fep_date'}:
        if col in db_columns:
            return f"CAST(cs.{col} AS date)", col
        if use_enroll and enroll_columns and col in enroll_columns:
            return f"CAST(se.{col} AS date)", col
    if col == 'migrant':
        if col in db_columns:
            return f"cs.{col}", col
        if use_enroll and enroll_columns and col in enroll_columns:
            return f"se.{col}", col
    if col in db_columns:
        return f"cs.{col}", col
    return None, None


def _user_can_manage_defaults(user):
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    role_name = getattr(getattr(user, 'role', None), 'name', None)
    return role_name in {'Project Admin', 'District Admin'}


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
        default_columns = _get_saved_default_columns()
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
            from .utils import can_user_view_query
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
            selected_columns = saved_config.get('columns') or _get_saved_default_columns()
    elif saved_config.get('columns'):
        selected_columns = saved_config.get('columns')
    else:
        selected_columns = _get_saved_default_columns()
    
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
            expr, _ = _get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students)
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
        db_columns = _get_current_student_columns()
        enroll_columns = _get_student_enroll_columns()
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
                from .utils import can_user_view_query
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
                    from .models import StudentList
                    from .utils import get_user_folders
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
            expr, alias = _get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students)
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


@login_required
def export_students_csv(request):
    """Export filtered student data as CSV."""
    
    # 1. Parse parameters (Shared logic with load_students)
    saved_query_id = request.GET.get('saved_query')
    saved_config = {}
    if saved_query_id:
        try:
            from .utils import can_user_view_query
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
            selected_columns = saved_config.get('columns') or _get_saved_default_columns()
    elif saved_config.get('columns'):
        selected_columns = saved_config.get('columns')
    else:
        selected_columns = _get_saved_default_columns()
    
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

            expr, _ = _get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students)
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
    db_columns = _get_current_student_columns()
    enroll_columns = _get_student_enroll_columns()
    
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
            
            student_list_id = request.GET.get('student_list')
            if student_list_id:
                list_ssids = None

                # Preferred: student lists stored as SavedQuery(query_type='student_list')
                try:
                    from .utils import can_user_view_query
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
                        from .models import StudentList
                        from .utils import get_user_folders
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
                        search = None
                        school_year = None
                        grade = None
                        status = None
                        filters = {}

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

            where_clauses = list(base_where_clauses)
            params = list(base_params)
            _apply_column_filters(where_clauses, params, filters, test_col_map, db_columns, enroll_columns, needs_enroll, needs_students)
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
                expr, alias = _get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students)
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

    response = StreamingHttpResponse(stream_csv(), content_type="text/csv")
    response['Content-Disposition'] = 'attachment; filename="students_export.csv"'
    return response


@login_required
def manage_default_columns(request):
    """Admin interface for setting default columns shown to users."""
    if not _user_can_manage_defaults(request.user):
        return HttpResponseForbidden("You do not have permission to manage default columns.")

    test_structure = get_test_columns()
    default_columns = _get_saved_default_columns()

    if request.method == 'POST':
        selected = request.POST.getlist('columns')
        selection = DefaultColumnSelection.objects.order_by('-updated_at', '-id').first()
        if selection is None:
            selection = DefaultColumnSelection.objects.create(
                columns=selected,
                updated_by=request.user,
            )
        else:
            selection.columns = selected
            selection.updated_by = request.user
            selection.save()
        return redirect('dataexploration:manage_default_columns')

    context = {
        'column_categories': COLUMN_CATEGORIES,
        'column_definitions': COLUMN_DEFINITIONS,
        'test_structure': test_structure,
        'selected_columns': default_columns,
    }

    return render(request, 'dataexploration/manage_default_columns.html', context)


def load_column_structures(request):
    """Load dynamic column structures based on current filters via HTMX."""
    # Get column filters if any
    column_filters_json = request.GET.get('column_filters', '')
    column_filters = {}
    if column_filters_json:
        try:
            column_filters = json.loads(column_filters_json)
        except:
            pass
    
    # Get dynamic test columns with filters
    test_structure = get_test_columns(column_filters)
    
    context = {
        'test_structure': test_structure,
        'column_definitions': COLUMN_DEFINITIONS,
    }
    
    return render(request, 'dataexploration/partials/column_structures.html', context)


class StudentTestSummaryView(LoginRequiredMixin, ListView):
    """Summary view showing all tests for a specific student (by SSID)."""
    model = StudentWithTests
    template_name = 'dataexploration/student_test_summary.html'
    context_object_name = 'tests'
    paginate_by = None  # Show all test records without pagination

    def get_queryset(self):
        ssid = self.kwargs.get('ssid')
        # Keep the ListView contract, but test records are loaded via SQL in get_context_data
        # so we can include results across all years (not limited by the student_with_tests view).
        return StudentWithTests.objects.filter(ssid=ssid).none()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ssid = self.kwargs.get('ssid')
        context['ssid'] = ssid

        user_id = getattr(getattr(self.request, 'user', None), 'pk', None)
        context['student_info'] = _get_student_info_cached(ssid, user_id=user_id)
        context['student_name'] = context['student_info'].get('full_name')
        context['test_records'] = _get_test_records_cached(ssid, user_id=user_id)
        return context


class StudentTestSummaryPartialView(StudentTestSummaryView):
    template_name = 'dataexploration/partials/student_test_summary_content.html'

    def get(self, request, *args, **kwargs):
        ssid = kwargs.get('ssid')
        html = _get_student_summary_partial_html_cached(request, ssid)
        return self._html_response(html)

    @staticmethod
    def _html_response(html):
        from django.http import HttpResponse
        return HttpResponse(html, content_type='text/html; charset=utf-8')


@login_required
@require_GET
def warm_student_test_summary_cache(request):
    """Warm Redis cache for the student summary overlay.

    Called from the list page in the background to make the first summary open instant.
    """
    ssids_raw = (request.GET.get('ssids') or '').strip()
    try:
        limit = int(request.GET.get('limit') or 10)
    except Exception:
        limit = 10
    limit = max(0, min(limit, 25))

    if not ssids_raw:
        return JsonResponse({'warmed': 0, 'limit': limit})

    ssids = [s.strip() for s in ssids_raw.split(',') if s.strip()]
    ssids = ssids[:limit]

    warmed = 0
    for ssid in ssids:
        _get_student_summary_partial_html_cached(request, ssid)
        warmed += 1

    return JsonResponse({'warmed': warmed, 'limit': limit})

@login_required
def folder_browser(request):
    """Return the folder browser HTML partial."""
    # Get only top-level folders (no parent) that user has access to
    folders = get_user_folders(request.user).filter(parent__isnull=True).prefetch_related(
        'queries', 'student_lists', 'subfolders', 'shares'
    ).order_by('folder_type', 'name')
    
    # Get queries shared with this user (via QueryShare)
    # Annotate with permission flags for template
    shared_queries = SavedQuery.objects.filter(
        shares__shared_with=request.user
    ).select_related('owner').prefetch_related('shares').order_by('-updated_at')
    
    # Add permission info to each query
    shared_queries_with_perms = []
    for query in shared_queries:
        share = query.shares.filter(shared_with=request.user).first()
        shared_queries_with_perms.append({
            'query': query,
            'can_edit': share.can_edit if share else False,
            'can_reshare': share.can_reshare if share else False
        })
    
    context = {
        'folders': folders,
        'shared_queries': shared_queries_with_perms
    }
    return render(request, 'dataexploration/partials/folder_browser.html', context)

@login_required
def save_query_modal(request):
    """Return the save query modal partial."""
    # Ensure default user folders exist
    ReportFolder.objects.get_or_create(
        owner=request.user,
        name="My Queries",
        folder_type='USER',
        defaults={'description': 'My personal saved queries'}
    )
    folders = get_user_folders(request.user)
    context = {'folders': folders}
    return render(request, 'dataexploration/partials/save_query_modal.html', context)

@login_required
def save_student_list_modal(request):
    """Return the save student list modal partial."""
    # Ensure default user folders exist
    ReportFolder.objects.get_or_create(
        owner=request.user,
        name="My Student Lists",
        folder_type='USER',
        defaults={'description': 'My personal student lists'}
    )
    folders = get_user_folders(request.user)
    context = {'folders': folders}
    return render(request, 'dataexploration/partials/save_list_modal.html', context)

@require_POST
@login_required
def save_query(request):
    """Save or update a query/student list configuration."""
    from django.contrib.auth import get_user_model
    
    name = request.POST.get('name')
    description = request.POST.get('description', '')
    folder_id = request.POST.get('folder_id')
    config_json = request.POST.get('query_config')  # Expecting JSON string
    query_type = request.POST.get('query_type', 'filter')  # 'filter' or 'student_list'
    privacy = request.POST.get('privacy', 'private')  # 'private', 'shared', or 'public'
    query_id = request.POST.get('query_id')  # If provided, update existing query
    share_with_emails = request.POST.get('share_with_emails', '')  # Comma-separated emails
    
    if not name:
        return JsonResponse({'status': 'error', 'message': 'Name is required'}, status=400)
        
    try:
        User = get_user_model()
        
        # Get or create query
        if query_id:
            query = SavedQuery.objects.get(pk=query_id, owner=request.user)
            query.name = name
            query.description = description
            query.config = json.loads(config_json) if config_json else {}
            query.privacy = privacy
            message = f'{query.get_query_type_display()} updated successfully!'
        else:
            # Validate folder if provided
            folder = None
            if folder_id:
                folder = ReportFolder.objects.get(pk=folder_id)
                if not can_create_in_folder(request.user, folder):
                    return HttpResponseForbidden("You do not have permission to save to this folder")
            
            query = SavedQuery.objects.create(
                name=name,
                description=description,
                query_type=query_type,
                folder=folder,
                owner=request.user,
                config=json.loads(config_json) if config_json else {},
                privacy=privacy
            )
            message = f'{query.get_query_type_display()} saved successfully!'
        
        # Handle sharing
        pending_emails = []
        if privacy == 'shared' and share_with_emails:
            emails = [e.strip() for e in share_with_emails.split(',') if e.strip()]
            
            for email in emails:
                user = User.objects.filter(email=email).first()
                if user:
                    # Create or update share record
                    QueryShare.objects.get_or_create(
                        query=query,
                        shared_with=user,
                        defaults={
                            'shared_by': request.user,
                            'can_edit': False,
                            'can_reshare': False
                        }
                    )
                else:
                    # Store email for pending share
                    pending_emails.append(email)
        
        # Store pending shares
        if pending_emails:
            query.pending_shares = pending_emails
        else:
            query.pending_shares = None
        
        query.save()
        
        response = render(request, 'dataexploration/partials/save_success.html', {
            'message': message,
            'query_id': query.id
        })
        response['HX-Trigger'] = 'refresh-folders'
        return response
        
    except SavedQuery.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Query not found or access denied'}, status=404)
    except ReportFolder.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Folder not found'}, status=404)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@require_POST
@login_required
def save_student_list(request):
    """Save student list - wrapper for save_query with query_type='student_list'."""
    # Convert student list format to unified query format
    ssids_json = request.POST.get('ssids')
    
    if ssids_json:
        ssids = json.loads(ssids_json)
        config = {'student_ids': ssids}
        
        # Create a mutable copy of POST data
        post_data = request.POST.copy()
        post_data['query_config'] = json.dumps(config)
        post_data['query_type'] = 'student_list'
        request.POST = post_data
    
    return save_query(request)

@require_POST
@login_required
def delete_query(request, query_id):
    query = get_object_or_404(SavedQuery, pk=query_id)
    if not can_user_manage_query(request.user, query):
         return HttpResponseForbidden("You do not have permission to delete this query")
    
    query.delete()
    response = HttpResponse("")
    response['HX-Trigger'] = 'refresh-folders'
    return response

@require_POST
@login_required
def rename_query(request, query_id):
    query = get_object_or_404(SavedQuery, pk=query_id)
    if not can_user_manage_query(request.user, query):
         return HttpResponseForbidden("You do not have permission to rename this query")
    
    name = request.POST.get('name')
    description = request.POST.get('description', '')
    if name:
        query.name = name
        query.description = description
        query.save()
        
    response = HttpResponse("")
    response['HX-Trigger'] = 'refresh-folders'
    return response

@require_POST
@login_required
def share_query(request, query_id):
    """Share a query with other users."""
    from django.contrib.auth import get_user_model
    
    query = get_object_or_404(SavedQuery, pk=query_id)
    
    # Only owner can share (or users with can_reshare permission)
    user_share = QueryShare.objects.filter(query=query, shared_with=request.user, can_reshare=True).first()
    if query.owner != request.user and not user_share:
        return JsonResponse({'status': 'error', 'message': 'You do not have permission to share this query.'}, status=403)
         
    user_email = request.POST.get('user_email')
    can_edit = request.POST.get('can_edit') == 'true'
    can_reshare = request.POST.get('can_reshare') == 'true'
    
    if not user_email:
        return JsonResponse({'status': 'error', 'message': 'User email required'}, status=400)
    
    User = get_user_model()
    try:
        user_to_share_with = User.objects.get(email=user_email)
    except User.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'User not found'}, status=404)
        
    if user_to_share_with == request.user:
        return JsonResponse({'status': 'error', 'message': 'Cannot share with yourself'}, status=400)
        
    QueryShare.objects.update_or_create(
        query=query,
        shared_with=user_to_share_with,
        defaults={
            'can_edit': can_edit,
            'can_reshare': can_reshare,
            'shared_by': request.user
        }
    )
    
    response = render(request, 'dataexploration/partials/save_success.html', {
        'message': f'Query shared with {user_email}'
    })
    response['HX-Trigger'] = 'refresh-folders'
    return response


@require_POST
@login_required
def unshare_query(request, query_id, share_id):
    """Remove a user's access to a query."""
    query = get_object_or_404(SavedQuery, pk=query_id)
    share = get_object_or_404(QueryShare, pk=share_id, query=query)
    
    # Only query owner or the person who created the share can unshare
    if query.owner != request.user and share.shared_by != request.user:
        return HttpResponseForbidden("Only the query owner or share creator can remove shares")
    
    share.delete()
    
    response = HttpResponse("")
    response['HX-Trigger'] = 'refresh-folders'
    return response


@require_POST
@login_required  
def get_query_shares(request, query_id):
    """Get list of users a query is shared with."""
    query = get_object_or_404(SavedQuery, pk=query_id)
    
    # Only owner or users with access can see shares
    user_share = QueryShare.objects.filter(query=query, shared_with=request.user).first()
    if query.owner != request.user and not user_share:
        return HttpResponseForbidden("Access denied")
    
    shares = QueryShare.objects.filter(query=query).select_related('shared_with', 'shared_by')
    
    return JsonResponse({
        'shares': [{
            'id': share.id,
            'email': share.shared_with.email,
            'name': share.shared_with.get_full_name() or share.shared_with.email,
            'can_edit': share.can_edit,
            'can_reshare': share.can_reshare,
            'shared_at': share.shared_at.isoformat(),
            'shared_by': share.shared_by.email
        } for share in shares],
        'pending_shares': query.pending_shares or []
    })


# Legacy folder sharing (keep for backward compatibility)
@require_POST
@login_required
def share_folder(request, folder_id):
    """Share a folder with other users (legacy)."""
    from django.contrib.auth import get_user_model
    
    folder = get_object_or_404(ReportFolder, pk=folder_id)
    if folder.owner != request.user:
         return JsonResponse({'status': 'error', 'message': 'You can only share folders you own.'}, status=403)
         
    user_email = request.POST.get('user_email')
    can_edit = request.POST.get('can_edit') == 'true'
    
    if not user_email:
        return JsonResponse({'status': 'error', 'message': 'User email required'}, status=400)
    
    User = get_user_model()
    try:
        user_to_share_with = User.objects.get(email=user_email)
    except User.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'User not found'}, status=404)
        
    if user_to_share_with == request.user:
        return JsonResponse({'status': 'error', 'message': 'Cannot share with yourself'}, status=400)
        
    FolderShare.objects.update_or_create(
        folder=folder,
        shared_with=user_to_share_with,
        defaults={'can_edit': can_edit}
    )
    
    response = render(request, 'dataexploration/partials/save_success.html', {'message': f'Folder shared with {user_email}'})
    response['HX-Trigger'] = 'refresh-folders'
    return response

@require_POST
@login_required
def unshare_folder(request, folder_id, share_id):
    """Remove a user's access to a folder."""
    folder = get_object_or_404(ReportFolder, pk=folder_id)
    
    # Only folder owner can unshare
    if folder.owner != request.user:
        return HttpResponseForbidden("Only the folder owner can remove shares")
    
    share = get_object_or_404(FolderShare, pk=share_id, folder=folder)
    share.delete()
    
    response = HttpResponse("")
    response['HX-Trigger'] = 'refresh-folders'
    return response

@require_POST
@login_required
def delete_student_list(request, list_id):
    slist = get_object_or_404(StudentList, pk=list_id)
    if not can_user_manage_list(request.user, slist):
         return HttpResponseForbidden("You do not have permission to delete this list")
    
    slist.delete()
    response = HttpResponse("")
    response['HX-Trigger'] = 'refresh-folders'
    return response

@require_POST
@login_required
def rename_student_list(request, list_id):
    slist = get_object_or_404(StudentList, pk=list_id)
    if not can_user_manage_list(request.user, slist):
         return HttpResponseForbidden("You do not have permission to rename this list")
    
    name = request.POST.get('name')
    description = request.POST.get('description', '')
    if name:
        slist.name = name
        slist.description = description
        slist.save()

    response = HttpResponse("")
    response['HX-Trigger'] = 'refresh-folders'
    return response

@login_required
def rename_modal(request):
    item_type = request.GET.get('type')
    item_id = request.GET.get('id')
    
    initial_name = ""
    initial_desc = ""
    submit_url = ""
    
    if item_type == 'query':
        query = get_object_or_404(SavedQuery, pk=item_id)
        if not can_user_manage_query(request.user, query):
             return HttpResponseForbidden()
        initial_name = query.name
        initial_desc = query.description
        submit_url = reverse('dataexploration:rename_query', args=[query.id])
    elif item_type == 'list':
        slist = get_object_or_404(StudentList, pk=item_id)
        if not can_user_manage_list(request.user, slist):
             return HttpResponseForbidden()
        initial_name = slist.name
        initial_desc = slist.description
        submit_url = reverse('dataexploration:rename_student_list', args=[slist.id])
    
    return render(request, 'dataexploration/partials/rename_modal.html', {
        'initial_name': initial_name,
        'initial_description': initial_desc,
        'submit_url': submit_url,
        'item_type': item_type
    })

@login_required
def share_modal(request):
    item_type = request.GET.get('type')
    item_id = request.GET.get('id')
    item_name = request.GET.get('name')
    
    submit_url = ""
    folder = None
    current_shares = []
    
    if item_type == 'query':
        query = get_object_or_404(SavedQuery, pk=item_id)
        folder = query.folder
        submit_url = reverse('dataexploration:share_query', args=[item_id])
    elif item_type == 'list':
        slist = get_object_or_404(StudentList, pk=item_id)
        folder = slist.folder
        # For lists, we share the folder too (same logic)
        submit_url = reverse('dataexploration:share_query', args=[item_id])  # We'll need to add share_list endpoint
    
    # Get current shares for the folder
    if folder and folder.owner == request.user:
        current_shares = folder.shares.select_related('shared_with').all()

    return render(request, 'dataexploration/partials/share_modal.html', {
        'item_name': item_name,
        'submit_url': submit_url,
        'item_type': item_type,
        'folder': folder,
        'current_shares': current_shares,
        'is_owner': folder and folder.owner == request.user
    })

@login_required
def create_folder_modal(request):
    parent_id = request.GET.get('parent_id')
    return render(request, 'dataexploration/partials/create_folder_modal.html', {
        'parent_id': parent_id
    })

@require_POST
@login_required
def create_folder(request):
    parent_id = request.POST.get('parent_id')
    name = request.POST.get('name')
    folder_type = request.POST.get('folder_type', 'USER')
    
    if not name:
         return JsonResponse({'status': 'error', 'message': 'Name is required'}, status=400)
    
    parent = None
    if parent_id:
        try:
            parent = ReportFolder.objects.get(pk=parent_id)
            if not can_create_in_folder(request.user, parent):
                 return HttpResponseForbidden("You cannot create a folder here.")
            folder_type = parent.folder_type
        except ReportFolder.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Parent folder not found'}, status=404)
    else:
        # Creating root folder check
         if folder_type in ['SCHOOL', 'DISTRICT']:
             role_name = getattr(request.user.role, 'name', '') if request.user.role else ''
             if not (request.user.is_superuser or role_name in ['Project Admin', 'District Admin']):
                  return HttpResponseForbidden()

    # Determine school context if SCHOOL folder
    school_context = None
    if folder_type == 'SCHOOL':
        if parent:
            school_context = parent.school
        else:
            # If root school folder, try to infer or require selection?
            # For simplicity, use user's first school
            school_context = request.user.schools.first()

    ReportFolder.objects.create(
        name=name,
        folder_type=folder_type,
        parent=parent,
        owner=request.user if folder_type == 'USER' else None,
        school=school_context
    )
    
    response = render(request, 'dataexploration/partials/save_success.html', {'message': 'Folder created successfully!'})
    response['HX-Trigger'] = 'refresh-folders'
    return response

@require_POST
@login_required
def rename_folder(request, folder_id):
    """Rename a folder."""
    folder = get_object_or_404(ReportFolder, pk=folder_id)
    
    if not can_user_edit_folder(request.user, folder):
        return HttpResponseForbidden("You do not have permission to rename this folder")
    
    name = request.POST.get('name')
    description = request.POST.get('description', '')
    
    if name:
        folder.name = name
        folder.description = description
        folder.save()
    
    response = HttpResponse("")
    response['HX-Trigger'] = 'refresh-folders'
    return response

@require_POST
@login_required
def delete_folder(request, folder_id):
    """Delete a folder."""
    folder = get_object_or_404(ReportFolder, pk=folder_id)
    
    if not can_user_edit_folder(request.user, folder):
        return HttpResponseForbidden("You do not have permission to delete this folder")
    
    # Check if folder has content
    if folder.queries.exists() or folder.student_lists.exists() or folder.subfolders.exists():
        return JsonResponse({
            'status': 'error',
            'message': 'Cannot delete folder with content. Please move or delete items first.'
        }, status=400)
    
    folder.delete()
    
    response = HttpResponse("")
    response['HX-Trigger'] = 'refresh-folders'
    return response

@require_POST
@login_required
def copy_query_to_folder(request, query_id):
    """Copy a shared query to user's folder."""
    from django.contrib.auth import get_user_model
    
    query = get_object_or_404(SavedQuery, pk=query_id)
    
    # Check if user can view this query
    if not can_user_view_query(request.user, query):
        return HttpResponseForbidden("You do not have access to this query")
    
    folder_id = request.POST.get('folder_id')
    folder = None
    
    if folder_id:
        folder = get_object_or_404(ReportFolder, pk=folder_id)
        if not can_create_in_folder(request.user, folder):
            return HttpResponseForbidden("You cannot add queries to this folder")
    
    # Create a copy of the query
    new_query = SavedQuery.objects.create(
        name=f"{query.name} (Copy)",
        description=query.description,
        query_type=query.query_type,
        folder=folder,
        owner=request.user,
        config=query.config.copy() if query.config else {},
        privacy='private'
    )
    
    response = render(request, 'dataexploration/partials/save_success.html', {
        'message': f'Query copied to your folder!'
    })
    response['HX-Trigger'] = 'refresh-folders'
    return response

@login_required
def get_query_details(request, query_id):
    """Get query details including applied filters for display."""
    query = get_object_or_404(SavedQuery, pk=query_id)
    
    if not can_user_view_query(request.user, query):
        return HttpResponseForbidden("You do not have access to this query")
    
    config = query.config or {}
    filters = config.get('filters', {})
    
    # Format filters for display
    filter_display = []
    
    # Basic filters
    if filters.get('search'):
        filter_display.append(f"Search: {filters['search']}")
    if filters.get('school_year'):
        filter_display.append(f"School Year: {filters['school_year']}")
    if filters.get('grade'):
        filter_display.append(f"Grade: {filters['grade']}")
    if filters.get('status'):
        filter_display.append(f"Status: {filters['status']}")
    
    # Column filters
    for key, value in filters.items():
        if key not in ['search', 'school_year', 'grade', 'status'] and value:
            if isinstance(value, list):
                filter_display.append(f"{key}: {', '.join(map(str, value))}")
            else:
                filter_display.append(f"{key}: {value}")
    
    context = {
        'query': query,
        'filters': filter_display,
        'column_count': len(config.get('columns', [])),
        'can_edit': can_user_manage_query(request.user, query)
    }
    
    return render(request, 'dataexploration/partials/query_details.html', context)
