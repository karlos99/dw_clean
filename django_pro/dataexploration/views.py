"""Views for data exploration app."""

from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponseForbidden
from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from django.db.models import Q
from django.db import connections
from django.core.cache import cache
from django.template.loader import render_to_string
from django.conf import settings
import json
from decimal import Decimal
from datetime import date, datetime
import hashlib
import os

from .models import StudentWithTests, DefaultColumnSelection
from studata.models import CurrentStudent, StudentEnroll, School
from .column_utils import get_test_columns
from .constants import COLUMN_DEFINITIONS, COLUMN_CATEGORIES, DEFAULT_COLUMNS


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


def _get_column_expression(col, db_columns, enroll_columns=None, use_enroll=False):
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
        
        # Current filter values
        context['search_query'] = self.request.GET.get('search', '')
        context['selected_school_year'] = self.request.GET.get('school_year', '')
        context['selected_grade'] = self.request.GET.get('grade', '')
        context['selected_status'] = self.request.GET.get('status', 'Active')
        
        return context


def load_students(request):
    """Load student data with dynamic test columns via HTMX."""
    page = int(request.GET.get('page', 1))
    limit = int(request.GET.get('limit', 50))
    
    # Get selected columns
    selected_columns_json = request.GET.get('columns', '')
    if selected_columns_json:
        try:
            selected_columns = json.loads(selected_columns_json)
        except:
            selected_columns = _get_saved_default_columns()
    else:
        selected_columns = _get_saved_default_columns()
    
    # Get filters
    search = request.GET.get('search', '')
    school_year = request.GET.get('school_year', '')
    grade = request.GET.get('grade', '')
    status = request.GET.get('status', 'Active')
    sort_by = request.GET.get('sort_by', '')
    sort_dir = request.GET.get('sort_dir', 'asc').lower()
    filters_json = request.GET.get('filters', '')
    filters = {}
    if filters_json:
        try:
            filters = json.loads(filters_json)
        except:
            filters = {}
    
    def _apply_column_filters(where_clauses, params, filters_to_apply, test_col_map, db_columns, enroll_columns, needs_enroll):
        """Append SQL filter clauses + params for the provided filters dict."""
        if not isinstance(filters_to_apply, dict):
            return

        for col, values in filters_to_apply.items():
            if not values:
                continue
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
                clause = (
                    "EXISTS ("
                    "SELECT 1 FROM results r "
                    "INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                    "WHERE r.local_student_id = cs.student_id "
                    "AND r.school_year = %s "
                    "AND t.test_name ILIKE %s "
                    "AND t.subject ILIKE %s "
                    f"{window_clause}"
                )
                if metric_col in {'ss', 'pl'}:
                    clause += f"AND r.{metric_col} = ANY(%s)"
                else:
                    clause += "AND (r.other_info ->> %s) = ANY(%s)"
                clause += ")"
                where_clauses.append(clause)
                params.extend([
                    test_info['year'],
                    f"%{test_info['test_name']}%",
                    f"%{test_info['subject']}%",
                    *window_params,
                    *( [test_info['other_info_key']] if metric_col == 'oi' else [] ),
                    values,
                ])
                continue

            expr, _ = _get_column_expression(col, db_columns, enroll_columns, needs_enroll)
            if expr:
                where_clauses.append(f"{expr} = ANY(%s)")
                params.append(values)

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

            return infos, col_map
        
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

        join_sql = ""
        if needs_enroll:
            join_sql = "LEFT JOIN student_enroll se ON se.student_id = cs.student_id AND se.school_year = cs.school_year"

        # Column filters (applied to the main query)
        where_clauses = list(base_where_clauses)
        params = list(base_params)
        _apply_column_filters(where_clauses, params, filters, test_col_map, db_columns, enroll_columns, needs_enroll)

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
            expr, alias = _get_column_expression(col, db_columns, enroll_columns, needs_enroll)
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

        # Build filter options for repeatable columns
        filter_options = {}
        if total > 0:
            excluded_filters = {'ssid', 'student_id', 'student_name'}
            always_include = {'school'}
            for col in base_cols:
                if col in excluded_filters:
                    continue
                expr = base_col_expressions.get(col)
                if not expr:
                    continue
                # Faceted filtering: compute this column's options without its own filter applied.
                option_where = list(base_where_clauses)
                option_params = list(base_params)
                if isinstance(filters, dict) and col in filters:
                    other_filters = {k: v for k, v in filters.items() if k != col}
                else:
                    other_filters = filters
                _apply_column_filters(option_where, option_params, other_filters, test_col_map, db_columns, enroll_columns, needs_enroll)
                option_where_sql = " AND ".join(option_where)

                distinct_query = f"SELECT COUNT(DISTINCT {expr}) FROM current_student cs {join_sql} WHERE {option_where_sql}"
                cursor.execute(distinct_query, option_params)
                distinct_count = cursor.fetchone()[0] or 0
                if distinct_count <= 1 or (distinct_count > 30 and col not in always_include):
                    continue
                options_query = f"""
                    SELECT {expr}
                    FROM current_student cs
                    {join_sql}
                    WHERE {option_where_sql} AND {expr} IS NOT NULL
                    GROUP BY {expr}
                    ORDER BY {expr} ASC
                    LIMIT 30
                """
                cursor.execute(options_query, option_params)
                values = [row[0] for row in cursor.fetchall()]
                if values:
                    filter_options[col] = values

            # Add filter options for test PL columns and other_info columns
            for test_info in test_cols_info:
                if test_info['metric'] not in {'pl', 'oi'}:
                    continue
                col_key = test_info['col_name']
                option_where = list(base_where_clauses)
                option_params = list(base_params)
                if isinstance(filters, dict) and col_key in filters:
                    other_filters = {k: v for k, v in filters.items() if k != col_key}
                else:
                    other_filters = filters
                _apply_column_filters(option_where, option_params, other_filters, test_col_map, db_columns, enroll_columns, needs_enroll)
                option_where_sql = " AND ".join(option_where)
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
                      AND r.pl IS NOT NULL
                    GROUP BY v
                    ORDER BY v ASC
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
                      AND (r.other_info ->> %s) IS NOT NULL
                    GROUP BY v
                    ORDER BY v ASC
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
                        other_key,
                    ])
                values = [row[0] for row in cursor.fetchall()]
                if values:
                    filter_options[col_key] = values
    
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

    if request.GET.get('format') == 'json':
        return JsonResponse({
            'students': students,
            'selected_columns': selected_columns,
            'total': total,
            'page': page,
            'total_pages': total_pages,
            'column_definitions': COLUMN_DEFINITIONS,
            'filter_options': filter_options,
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
