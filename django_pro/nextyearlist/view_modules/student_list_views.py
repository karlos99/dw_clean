"""Main student list view and HTMX data-loading for the Next Year List app.

The core data model is:
    nextyearlist_placement nyl
    INNER JOIN current_student cs ON cs.ssid = nyl.ssid AND cs.school_year = nyl.school_year
    LEFT JOIN mathr_recommendation mr ON mr.ssid = nyl.ssid AND mr.school_year = nyl.school_year

School-level access is scoped by nyl.next_year_school_state_id (not cs.school_state_id).
"""
from django.shortcuts import render
from django.http import JsonResponse
from django.views.generic import ListView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.decorators import method_decorator
from django.contrib.auth.decorators import login_required
from django.db import connections
from django.core.cache import cache
import hashlib
import json

from users.decorators import page_access_required

from dataexploration.models import SavedQuery, StudentList
from dataexploration.column_utils import get_test_columns, get_mark_columns
from dataexploration.school_year_utils import get_year_labels_map
from ..constants import COLUMN_DEFINITIONS, COLUMN_CATEGORIES
from ..utils import can_user_view_query, get_user_folders
from ..view_modules.column_management import get_saved_default_columns, get_column_expression
from dataexploration.view_modules.student_data_utils import (
    get_current_student_columns,
    get_student_enroll_columns,
)


# ----- helpers shared with export / statistics -----

def _is_unrestricted(user):
    if user.is_superuser:
        return True
    if getattr(user, 'is_project_admin', False):
        return True
    if hasattr(user, 'active_role') and user.active_role and 'District' in user.active_role.name:
        return True
    return False


def _apply_nyl_school_filter(where_clauses, params, user):
    """Restrict results to students whose current school OR next-year school matches the user's school(s).

    A student is visible when:
      - cs.school_state_id matches (they currently attend the school), OR
      - nyl.next_year_school_state_id matches (they are placed into the school for next year).

    Requires that the query already JOINs nextyearlist_placement as 'nyl'.
    """
    if _is_unrestricted(user):
        return
    if hasattr(user, 'active_school') and user.active_school:
        sid = user.active_school.state_id
        where_clauses.append("(cs.school_state_id = %s OR nyl.next_year_school_state_id = %s)")
        params.extend([sid, sid])
    elif hasattr(user, 'schools') and isinstance(user.schools, list) and user.schools:
        sids = user.schools
        where_clauses.append("(cs.school_state_id = ANY(%s) OR nyl.next_year_school_state_id = ANY(%s))")
        params.extend([sids, sids])
    else:
        where_clauses.append("1=0")


# ----- list view -----

@method_decorator(page_access_required('nextyearlist'), name='dispatch')
class NextYearListView(LoginRequiredMixin, ListView):
    """Display list of next-year students mirroring the dataexploration list view."""
    template_name = 'nextyearlist/student_list.html'
    context_object_name = 'students'
    paginate_by = 50

    def get_queryset(self):
        return []

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        test_structure = get_test_columns()
        marks_structure = get_mark_columns()

        school_years_key = "nyl:school_years"
        school_years = cache.get(school_years_key)
        if not school_years:
            with connections['default'].cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT school_year FROM current_student"
                    " WHERE school_year IS NOT NULL ORDER BY school_year DESC"
                )
                school_years = [row[0] for row in cursor.fetchall()]
                cache.set(school_years_key, school_years, 3600)

        is_unres = _is_unrestricted(self.request.user)
        grade_filter_clauses = ["cs.grade IS NOT NULL"]
        grade_filter_params = []

        if hasattr(self.request.user, 'active_school') and self.request.user.active_school:
            grade_filter_clauses.append("cs.school_state_id = %s")
            grade_filter_params.append(self.request.user.active_school.state_id)
        elif not is_unres:
            schools = getattr(self.request.user, 'schools', None)
            if isinstance(schools, list) and schools:
                grade_filter_clauses.append("cs.school_state_id = ANY(%s)")
                grade_filter_params.append(schools)
            else:
                grade_filter_clauses.append("1=0")

        active_role_name = getattr(getattr(self.request.user, 'active_role', None), 'name', None)
        if active_role_name == 'School Gr View':
            delegated_grades = getattr(self.request.user, 'grade_levels', None)
            if isinstance(delegated_grades, list) and delegated_grades:
                grade_filter_clauses.append("cs.grade = ANY(%s)")
                grade_filter_params.append(delegated_grades)

        active_school_id = getattr(getattr(self.request.user, 'active_school', None), 'state_id', None)
        schools_key = json.dumps(getattr(self.request.user, 'schools', []) or [], sort_keys=True)
        role_key = active_role_name or ''
        grade_levels_key = json.dumps(getattr(self.request.user, 'grade_levels', None) or [], sort_keys=True)
        grades_key = f"nyl:grades:{self.request.user.id}:{active_school_id}:{role_key}:{hashlib.md5(schools_key.encode()).hexdigest()}:{hashlib.md5(grade_levels_key.encode()).hexdigest()}"
        grades = cache.get(grades_key)
        if not grades:
            with connections['default'].cursor() as cursor:
                where_sql = " AND ".join(grade_filter_clauses)
                cursor.execute(
                    f"SELECT DISTINCT cs.grade"
                    f" FROM current_student cs"
                    f" LEFT JOIN nextyearlist_placement nyl ON nyl.ssid = cs.ssid AND nyl.school_year = cs.school_year"
                    f" WHERE {where_sql} ORDER BY cs.grade",
                    grade_filter_params,
                )
                grades = [row[0] for row in cursor.fetchall()]
                cache.set(grades_key, grades, 3600)

        context['school_years'] = school_years
        context['year_labels'] = get_year_labels_map(list(test_structure.keys()) + list(marks_structure.keys()))
        context['grades'] = grades
        context['test_structure'] = test_structure
        context['marks_structure'] = marks_structure
        context['column_categories'] = COLUMN_CATEGORIES
        context['column_definitions'] = COLUMN_DEFINITIONS
        default_columns = get_saved_default_columns()
        context['default_columns'] = default_columns
        context['default_columns_json'] = json.dumps(default_columns)
        context['column_definitions_json'] = json.dumps(COLUMN_DEFINITIONS)

        saved_query_id = self.request.GET.get('saved_query')
        query_config = {}
        if saved_query_id:
            try:
                sq = SavedQuery.objects.get(pk=saved_query_id)
                query_config = sq.config or {}
                if 'columns' in query_config:
                    context['default_columns'] = query_config['columns']
                    context['default_columns_json'] = json.dumps(query_config['columns'])
            except SavedQuery.DoesNotExist:
                pass

        filters = query_config.get('filters', {})

        _project_default_year = ''
        try:
            from landing.utils import get_project_config
            _cfg = get_project_config()
            _project_default_year = _cfg.default_school_year or ''
        except Exception:
            pass

        def get_param(name, default=''):
            return self.request.GET.get(name) or filters.get(name, default)

        context['search_query'] = get_param('search')
        if 'school_year' in self.request.GET:
            selected_year = self.request.GET.get('school_year')
        elif _project_default_year:
            selected_year = _project_default_year
        else:
            selected_year = filters.get('school_year', '')

        if not selected_year and school_years:
            selected_year = school_years[0]

        try:
            from dataexploration.school_year_utils import resolve_school_year
            if selected_year:
                selected_year = resolve_school_year(selected_year)
        except Exception:
            pass
        context['selected_school_year'] = selected_year
        context['selected_grade'] = get_param('grade')
        context['selected_status'] = get_param('status', 'Active')
        return context


# ----- HTMX data loader -----

@login_required
@page_access_required('nextyearlist')
def load_students(request):
    """Load student data with dynamic columns via HTMX."""
    page = int(request.GET.get('page', 1))
    limit = int(request.GET.get('limit', 50))

    saved_query_id = request.GET.get('saved_query')
    saved_config = {}
    if saved_query_id:
        try:
            query_obj = SavedQuery.objects.get(pk=saved_query_id)
            if can_user_view_query(request.user, query_obj):
                saved_config = query_obj.config
                query_obj.execute()
        except (SavedQuery.DoesNotExist, Exception):
            pass

    selected_columns_json = request.GET.get('columns', '')
    if selected_columns_json:
        try:
            selected_columns = json.loads(selected_columns_json)
        except Exception:
            selected_columns = saved_config.get('columns') or get_saved_default_columns()
    elif saved_config.get('columns'):
        selected_columns = saved_config.get('columns')
    else:
        selected_columns = get_saved_default_columns()

    saved_filters = saved_config.get('filters', {}) if isinstance(saved_config.get('filters'), dict) else {}

    def _parse_multi(param_val, fallback):
        if not param_val:
            return fallback
        if isinstance(param_val, list):
            return param_val if param_val else fallback
        val_str = str(param_val).strip()
        if val_str.startswith('['):
            try:
                parsed = json.loads(val_str)
                if isinstance(parsed, list) and parsed:
                    return parsed
                return fallback
            except Exception:
                pass
        return val_str if val_str else fallback

    search = request.GET.get('search', '') or saved_filters.get('search', '') or saved_config.get('search', '')
    
    _project_default_year = ''
    try:
        from landing.utils import get_project_config
        _cfg = get_project_config()
        _project_default_year = _cfg.default_school_year or ''
    except Exception:
        pass

    _sy_in_url = 'school_year' in request.GET
    if _sy_in_url:
        school_year = _parse_multi(request.GET.get('school_year', ''), '')
    elif _project_default_year:
        school_year = _project_default_year
    else:
        school_year = _parse_multi(
            saved_filters.get('school_year', ''),
            _parse_multi(saved_config.get('school_year', ''), '')
        )

    if not school_year:
        # Get latest year from DB/cache
        school_year_cache = cache.get("nyl:school_years")
        if school_year_cache and isinstance(school_year_cache, list):
            school_year = school_year_cache[0]
        else:
            with connections['default'].cursor() as cursor:
                cursor.execute("SELECT DISTINCT school_year FROM current_student WHERE school_year IS NOT NULL ORDER BY school_year DESC LIMIT 1")
                row = cursor.fetchone()
                school_year = row[0] if row else ''

    grade = _parse_multi(request.GET.get('grade', ''), _parse_multi(saved_filters.get('grade', ''), _parse_multi(saved_config.get('grade', ''), '')))

    status_param = request.GET.get('status')
    if status_param is not None:
        status = _parse_multi(status_param, '')
    elif 'status' in saved_filters:
        status = _parse_multi(saved_filters['status'], '')
    elif 'status' in saved_config:
        status = _parse_multi(saved_config['status'], '')
    else:
        status = 'Active'

    sort_by = request.GET.get('sort_by', '') or saved_config.get('sort_by', '')
    sort_dir = request.GET.get('sort_dir', 'asc').lower()

    filters_json = request.GET.get('filters', '')
    filters = {}
    if filters_json:
        try:
            filters = json.loads(filters_json)
        except Exception:
            filters = saved_filters.copy()
    else:
        filters = {k: v for k, v in saved_filters.items() if k not in ['search', 'school_year', 'grade', 'status']}

    def _apply_column_filters(where_clauses, params, filters_to_apply, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll, needs_students=False, needs_eht=False):
        if not isinstance(filters_to_apply, dict):
            return
        import re
        for col, values in filters_to_apply.items():
            if not values:
                continue
            if col == 'atendace_rate':
                expr, _ = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
                if expr:
                    conditions = []
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
                    "AND (r.ssid IS NULL OR r.ssid = '') "
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
                    blank_subconditions = [f"(cs.student_id NOT IN ({id_subquery_base}) AND cs.ssid NOT IN ({ssid_subquery_base}))"]
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
                mark_subquery_base = (
                    "SELECT sm.stu_annual_key FROM student_marks_list sm "
                    "WHERE sm.school_year = %s AND sm.subject ILIKE %s AND sm.course_name ILIKE %s "
                )
                mark_base_params = [mark_info['year'], f"%{mark_info['subject']}%", f"%{mark_info['course_name']}%"]
                if mark_info.get('mark_period'):
                    mark_subquery_base += "AND sm.mark_period ILIKE %s "
                    mark_base_params.append(mark_info['mark_period'])
                else:
                    mark_subquery_base += "AND (sm.mark_period IS NULL OR sm.mark_period = '') "

                if real_values:
                    val_clause = mark_subquery_base + "AND sm.mark = ANY(%s)"
                    val_params = list(mark_base_params) + [real_values]
                    conditions.append(f"cs.stu_annual_key IN ({val_clause})")
                    params.extend(val_params)
                if has_blank:
                    blank_clause = mark_subquery_base + "AND (sm.mark IS NULL OR sm.mark = '')"
                    blank_params = list(mark_base_params)
                    conditions.append(f"(cs.stu_annual_key NOT IN ({mark_subquery_base}) OR cs.stu_annual_key IN ({blank_clause}))")
                    params.extend(mark_base_params + blank_params)
                if conditions:
                    where_clauses.append(f"({' OR '.join(conditions)})")
                continue

            # NYL or standard column
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

    with connections['default'].cursor() as cursor:
        db_columns = get_current_student_columns()
        enroll_columns = get_student_enroll_columns()
        base_where_clauses = ["1=1"]
        base_params = []

        def _parse_year_from_test_col(col_key):
            s = (col_key or '').replace('-', '_')
            if not s.startswith('test_'):
                return None
            tail = s[len('test_'):]
            if len(tail) >= 10 and tail[4] == '_' and tail[9] == '_':
                return tail[:9].replace('_', '-')
            parts = tail.split('_', 1)
            return parts[0].replace('_', '-') if parts else None

        def _build_test_columns_from_db(selected_test_cols):
            if not selected_test_cols:
                return [], {}
            test_cols_cache_key = f"test_cols:{hashlib.md5(json.dumps(sorted(selected_test_cols)).encode()).hexdigest()}"
            cached = cache.get(test_cols_cache_key)
            if cached is not None:
                return cached
            normalized_to_original = {c.replace('-', '_'): c for c in selected_test_cols}
            years = sorted({y for y in (_parse_year_from_test_col(c) for c in selected_test_cols) if y})
            if not years:
                return [], {}
            cursor.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'tests' AND column_name = 'test_window'")
            has_test_window = cursor.fetchone() is not None
            if has_test_window:
                cursor.execute("SELECT DISTINCT t.school_year, t.test_name, t.subject, COALESCE(t.test_window, '') FROM tests t WHERE t.school_year = ANY(%s) AND t.school_year IS NOT NULL AND t.test_name IS NOT NULL", [years])
            else:
                cursor.execute("SELECT DISTINCT t.school_year, t.test_name, t.subject, '' FROM tests t WHERE t.school_year = ANY(%s) AND t.school_year IS NOT NULL AND t.test_name IS NOT NULL", [years])
            infos, col_map = [], {}

            def _np(v):
                return (v or '').replace(' ', '_').replace('-', '_')

            for (yr, test_name, subject, test_window) in cursor.fetchall():
                win_norm = _np(test_window).strip('_')
                key_base = f"{_np(yr)}_{_np(test_name)}_{_np(subject)}_{win_norm}" if win_norm else f"{_np(yr)}_{_np(test_name)}_{_np(subject)}"
                win_value = (test_window or '').strip() or None
                for norm_key, metric in ((f"test_{key_base}_SS", 'ss'), (f"test_{key_base}_PL", 'pl'), (f"test_{key_base}_DFP", 'dfp')):
                    ok = normalized_to_original.get(norm_key)
                    if not ok:
                        continue
                    info = {'col_name': ok, 'year': yr, 'test_name': test_name or '', 'subject': subject or '', 'test_window': win_value, 'metric': metric}
                    if metric == 'dfp':
                        info['other_info_key'] = 'dfp'
                    infos.append(info)
                    col_map[ok] = info
                    col_map[ok.replace('-', '_')] = info
            result = (infos, col_map)
            cache.set(test_cols_cache_key, result, 600)
            return result

        def _parse_year_from_mark_col(col_key):
            s = (col_key or '').replace('-', '_')
            if not s.startswith('mark_'):
                return None
            tail = s[len('mark_'):]
            if len(tail) >= 10 and tail[4] == '_' and tail[9] == '_':
                return tail[:9].replace('_', '-')
            parts = tail.split('_', 1)
            return parts[0].replace('_', '-') if parts else None

        def _build_mark_columns_from_db(selected_mark_cols):
            if not selected_mark_cols:
                return [], {}
            mark_cols_cache_key = f"mark_cols:{hashlib.md5(json.dumps(sorted(selected_mark_cols)).encode()).hexdigest()}"
            cached = cache.get(mark_cols_cache_key)
            if cached:
                return cached
            normalized_to_original = {c.replace('-', '_'): c for c in selected_mark_cols}
            years = sorted({y for y in (_parse_year_from_mark_col(c) for c in selected_mark_cols) if y})
            if not years:
                return [], {}
            cursor.execute("SELECT DISTINCT school_year, subject, course_name, mark_period FROM student_marks_list WHERE school_year = ANY(%s) AND school_year IS NOT NULL", [years])
            infos, col_map = [], {}

            def _np(v):
                return (v or '').replace(' ', '_').replace('-', '_')

            for (year, subject, course_name, mark_period) in cursor.fetchall():
                key_base = f"{_np(year)}_{_np(subject)}_{_np(course_name)}_{_np(mark_period)}"
                ok = normalized_to_original.get(f"mark_{key_base}")
                if not ok:
                    continue
                info = {'col_name': ok, 'year': year or '', 'subject': subject or '', 'course_name': course_name or '', 'mark_period': (mark_period or '').strip() or None}
                infos.append(info)
                col_map[ok] = info
                col_map[ok.replace('-', '_')] = info
            result = (infos, col_map)
            cache.set(mark_cols_cache_key, result, 600)
            return result

        # Student list filter
        student_list_id = request.GET.get('student_list')
        if student_list_id:
            list_ssids = None
            try:
                slq = SavedQuery.objects.get(pk=student_list_id, query_type='student_list')
                if can_user_view_query(request.user, slq):
                    list_ssids = slq.config.get('student_ids') or []
            except (SavedQuery.DoesNotExist, Exception):
                list_ssids = None
            if isinstance(list_ssids, list) and list_ssids:
                normalized_ssids = [str(s).strip() for s in list_ssids if str(s).strip()]
                if normalized_ssids:
                    base_where_clauses.append("cs.ssid = ANY(%s)")
                    base_params.append(normalized_ssids)
                    search = None
                    school_year = None
                    grade = None
                    status = None
                    filters = {}

        if search:
            if isinstance(search, list):
                search = search[0] if search else ''
            if search:
                base_where_clauses.append("(cs.student_name ILIKE %s OR cs.ssid ILIKE %s OR cs.student_id ILIKE %s)")
                sp = f'%{search}%'
                base_params.extend([sp, sp, sp])

        if school_year:
            if isinstance(school_year, list):
                if len(school_year) == 1:
                    base_where_clauses.append("cs.school_year = %s")
                    base_params.append(school_year[0])
                elif len(school_year) > 1:
                    base_where_clauses.append("cs.school_year = ANY(%s)")
                    base_params.append(school_year)
            else:
                base_where_clauses.append("cs.school_year = %s")
                base_params.append(school_year)

        if grade:
            if isinstance(grade, list):
                if len(grade) == 1:
                    base_where_clauses.append("cs.grade = %s")
                    base_params.append(grade[0])
                elif len(grade) > 1:
                    base_where_clauses.append("cs.grade = ANY(%s)")
                    base_params.append(grade)
            else:
                base_where_clauses.append("cs.grade = %s")
                base_params.append(grade)

        if status:
            if isinstance(status, list):
                if len(status) == 1:
                    base_where_clauses.append("cs.status = %s")
                    base_params.append(status[0])
                elif len(status) > 1:
                    base_where_clauses.append("cs.status = ANY(%s)")
                    base_params.append(status)
            else:
                base_where_clauses.append("cs.status = %s")
                base_params.append(status)

        # Next-year school access filter (the key NYL difference)
        _apply_nyl_school_filter(base_where_clauses, base_params, request.user)

        active_role_name = getattr(getattr(request.user, 'active_role', None), 'name', None)
        if active_role_name == 'School Gr View':
            delegated_grades = getattr(request.user, 'grade_levels', None)
            if isinstance(delegated_grades, list) and delegated_grades:
                base_where_clauses.append("cs.grade = ANY(%s)")
                base_params.append(delegated_grades)

        # Row-level student access (Teacher)
        if hasattr(request.user, 'get_accessible_student_keys'):
            accessible_keys = request.user.get_accessible_student_keys()
            if accessible_keys is not None:
                if not accessible_keys:
                    base_where_clauses.append("1=0")
                else:
                    base_where_clauses.append("cs.stu_annual_key = ANY(%s)")
                    base_params.append(accessible_keys)

        filter_keys = list(filters.keys()) if isinstance(filters, dict) else []
        selected_test_cols = list(set(
            [c for c in selected_columns if isinstance(c, str) and c.startswith('test_')] +
            [k for k in filter_keys if isinstance(k, str) and k.startswith('test_')]
        ))
        selected_mark_cols = list(set(
            [c for c in selected_columns if isinstance(c, str) and c.startswith('mark_')] +
            [k for k in filter_keys if isinstance(k, str) and k.startswith('mark_')]
        ))
        base_cols = [c for c in selected_columns if isinstance(c, str) and not c.startswith(('test_', 'mark_')) and c in COLUMN_DEFINITIONS]

        test_cols_info, test_col_map = _build_test_columns_from_db(selected_test_cols)
        mark_cols_info, mark_col_map = _build_mark_columns_from_db(selected_mark_cols)

        enroll_fields = {'ell', 'birth_date', 'us_enter_date', 'fep_date', 'migrant'}
        needs_enroll = any(c in enroll_fields for c in selected_columns) or (isinstance(filters, dict) and any(k in enroll_fields for k in filters)) or sort_by in enroll_fields
        needs_students = any(c in {'ltel'} for c in selected_columns) or (isinstance(filters, dict) and 'ltel' in filters) or sort_by == 'ltel'
        needs_eht = (isinstance(filters, dict) and 'elem_teacher_name' in filters) or sort_by == 'elem_teacher_name'

        # Base JOIN: nyl -> cs (required) + optional enroll/students/eht
        join_sql = ""
        if needs_enroll:
            join_sql += (
                " LEFT JOIN (SELECT DISTINCT ON (student_id, school_year) * FROM student_enroll"
                " ORDER BY student_id, school_year, enter_date DESC NULLS LAST, leave_date DESC NULLS LAST"
                ") se ON se.student_id = cs.student_id AND se.school_year = cs.school_year"
            )
        if needs_students:
            join_sql += " LEFT JOIN students s ON s.student_annual_key = cs.stu_annual_key"
            join_sql += (
                " LEFT JOIN (SELECT DISTINCT ON (state_student_number) * FROM students"
                " ORDER BY state_student_number, school_year DESC NULLS LAST, enter_date DESC NULLS LAST, leave_date DESC NULLS LAST"
                ") sssid ON sssid.state_student_number = cs.ssid"
            )
        if needs_eht:
            join_sql += " LEFT JOIN elementary_homeroom_teacher eht ON eht.stu_annual_key = cs.stu_annual_key"

        where_clauses = list(base_where_clauses)
        params = list(base_params)
        _apply_column_filters(where_clauses, params, filters, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
        where_sql = " AND ".join(where_clauses)

        # Count
        count_cache_key = f"nyl:count:{hashlib.md5(json.dumps({'w': where_clauses, 'p': [str(p) for p in params], 'j': join_sql}, sort_keys=True).encode()).hexdigest()}"
        total = cache.get(count_cache_key)
        if total is None:
            cursor.execute(
                f"SELECT COUNT(DISTINCT cs.ssid)"
                f" FROM current_student cs"
                f" LEFT JOIN nextyearlist_placement nyl ON nyl.ssid = cs.ssid AND nyl.school_year = cs.school_year"
                f" LEFT JOIN mathr_recommendation mr ON mr.ssid = cs.ssid AND mr.school_year = cs.school_year"
                f" {join_sql} WHERE {where_sql}",
                params,
            )
            total = cursor.fetchone()[0]
            cache.set(count_cache_key, total, 300)

        filter_params = list(params)
        offset = (page - 1) * limit

        if 'ssid' not in base_cols:
            base_cols.insert(0, 'ssid')
        if test_cols_info and 'student_id' not in base_cols:
            base_cols.insert(0, 'student_id')
        if (mark_cols_info or 'elem_teacher_name' in base_cols) and 'stu_annual_key' not in base_cols:
            base_cols.insert(0, 'stu_annual_key')

        base_col_selects = []
        base_col_expressions = {}
        for col in base_cols:
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
            if expr:
                order_by_expr = expr
            elif sort_by in mark_col_map:
                mark_info = mark_col_map[sort_by]
                period_clause = "AND sm.mark_period ILIKE %s" if mark_info.get('mark_period') else "AND (sm.mark_period IS NULL OR sm.mark_period = '')"
                order_by_expr = (
                    "(SELECT sm.mark FROM student_marks_list sm WHERE sm.stu_annual_key = cs.stu_annual_key"
                    " AND sm.school_year = %s AND sm.subject ILIKE %s AND sm.course_name ILIKE %s "
                    f"{period_clause} LIMIT 1)"
                )
                params.extend([mark_info['year'], f"%{mark_info['subject']}%", f"%{mark_info['course_name']}%"])
                if mark_info.get('mark_period'):
                    params.append(mark_info['mark_period'])
            elif sort_by in test_col_map:
                test_info = test_col_map[sort_by]
                metric_col = test_info['metric']
                window_clause = f"AND t.test_window ILIKE %s" if test_info.get('test_window') else ""
                window_params = [test_info['test_window']] if test_info.get('test_window') else []
                if metric_col == 'ss':
                    select_expr = "CAST(NULLIF(regexp_replace(r.ss, '[^0-9.]', '', 'g'), '') AS NUMERIC)"
                    extra_params = []
                elif metric_col == 'pl':
                    select_expr = "CASE WHEN r.pl ~ '^[0-9]+$' THEN LPAD(r.pl, 10, '0') ELSE r.pl END"
                    extra_params = []
                else:
                    select_expr = "(r.other_info ->> %s)"
                    extra_params = [test_info.get('other_info_key', '')]
                order_by_expr = (
                    f"(SELECT {select_expr} FROM results r INNER JOIN tests t ON r.test_group_key = t.test_group_key"
                    " WHERE (r.ssid IS NOT NULL AND r.ssid != '' AND cs.ssid IS NOT NULL AND cs.ssid != '' AND r.ssid = cs.ssid) OR ((r.ssid IS NULL OR r.ssid = '') AND r.local_student_id = cs.student_id) AND r.school_year = %s"
                    " AND t.test_name ILIKE %s AND t.subject ILIKE %s "
                    f"{window_clause} LIMIT 1)"
                )
                params.extend(extra_params)
                params.extend([test_info['year'], test_info['test_name'], test_info['subject']])
                params.extend(window_params)

        sort_dir = 'desc' if sort_dir == 'desc' else 'asc'

        # Include selected columns in the cache key so that adding/removing a column
        # (e.g. next_year_school_name, math_selection, mark columns) always fetches fresh data.
        _cache_col_key = json.dumps(
            sorted(base_cols)
            + sorted([mi['col_name'] for mi in mark_cols_info])
            + sorted([ti['col_name'] for ti in test_cols_info]),
            sort_keys=True,
        )
        page_cache_key = f"nyl:page:{hashlib.md5(json.dumps({'h': hashlib.md5((where_sql + join_sql).encode()).hexdigest(), 'c': hashlib.md5(_cache_col_key.encode()).hexdigest(), 'p': [str(p) for p in params], 's': f'{sort_by}:{sort_dir}', 'l': limit, 'o': offset}, sort_keys=True).encode()).hexdigest()}"

        rows = None
        columns = None
        if cache.get(page_cache_key):
            rows = cache.get(page_cache_key)

        if not rows:
            # Build combined SELECT including test/mark subqueries.
            # select_params are collected separately because SELECT subquery %s markers appear
            # before WHERE %s markers in the SQL string, but params already holds WHERE+ORDER BY
            # params. Final execute uses: select_params + params (correct positional order).
            select_params = []
            test_selects = []
            for ti in test_cols_info:
                metric_col = ti['metric']
                window_clause = "AND t.test_window ILIKE %s " if ti.get('test_window') else ""
                window_params = [ti['test_window']] if ti.get('test_window') else []
                if metric_col == 'ss':
                    val_expr = "r.ss"
                elif metric_col == 'pl':
                    val_expr = "r.pl"
                else:
                    val_expr = f"(r.other_info ->> '{ti.get('other_info_key', '')}')"
                test_sub = (
                    f"(SELECT {val_expr} FROM results r INNER JOIN tests t ON r.test_group_key = t.test_group_key"
                    f" WHERE (r.ssid IS NOT NULL AND r.ssid != '' AND cs.ssid IS NOT NULL AND cs.ssid != '' AND r.ssid = cs.ssid) OR ((r.ssid IS NULL OR r.ssid = '') AND r.local_student_id = cs.student_id) AND r.school_year = %s"
                    f" AND t.test_name ILIKE %s AND t.subject ILIKE %s {window_clause}LIMIT 1)"
                    f" AS \"{ti['col_name']}\""
                )
                test_selects.append(test_sub)
                select_params.append(ti['year'])
                select_params.append(ti['test_name'])
                select_params.append(ti['subject'])
                select_params.extend(window_params)

            mark_selects = []
            for mi in mark_cols_info:
                period_clause = "AND sm.mark_period ILIKE %s " if mi.get('mark_period') else "AND (sm.mark_period IS NULL OR sm.mark_period = '') "
                mark_sub = (
                    f"(SELECT sm.mark FROM student_marks_list sm WHERE sm.stu_annual_key = cs.stu_annual_key"
                    f" AND sm.school_year = %s AND sm.subject ILIKE %s AND sm.course_name ILIKE %s {period_clause}LIMIT 1)"
                    f" AS \"{mi['col_name']}\""
                )
                mark_selects.append(mark_sub)
                select_params.append(mi['year'])
                select_params.append(f"%{mi['subject']}%")
                select_params.append(f"%{mi['course_name']}%")
                if mi.get('mark_period'):
                    select_params.append(mi['mark_period'])

            all_selects = base_col_sql
            if test_selects:
                all_selects += ", " + ", ".join(test_selects)
            if mark_selects:
                all_selects += ", " + ", ".join(mark_selects)

            query = (
                f"SELECT {all_selects}"
                f" FROM current_student cs"
                f" LEFT JOIN nextyearlist_placement nyl ON nyl.ssid = cs.ssid AND nyl.school_year = cs.school_year"
                f" LEFT JOIN mathr_recommendation mr ON mr.ssid = cs.ssid AND mr.school_year = cs.school_year"
                f" {join_sql}"
                f" WHERE {where_sql}"
                f" ORDER BY {order_by_expr} {sort_dir} NULLS LAST"
                f" LIMIT {limit} OFFSET {offset}"
            )

            from clickhouse_utils.executor import QueryExecutor

            ny_exec = QueryExecutor()
            ny_result = ny_exec.execute(query, select_params + params)
            columns = ny_result['columns']
            rows = ny_result['rows']
            cache.set(page_cache_key, rows, 60)

        if columns is None and rows:
            columns = list(rows[0].keys()) if rows else []

        total_pages = (total + limit - 1) // limit if total else 0

        # Build dynamic column definitions for test/mark columns
        dynamic_definitions = {k: v for k, v in COLUMN_DEFINITIONS.items()}
        if test_cols_info:
            for info in test_cols_info:
                win = info.get('test_window') or ''
                label = f"{info['test_name']} ({info['subject']}) {win}\n{info['year']}".strip()
                dynamic_definitions[info['col_name']] = label
        if mark_cols_info:
            for info in mark_cols_info:
                period = info.get('mark_period') or 'Unknown'
                label = f"{info['course_name']} ({info['subject']}) {period}\n{info['year']}"
                dynamic_definitions[info['col_name']] = label

        # Serialize date/decimal values so they are JSON-safe
        safe_rows = []
        for row in rows:
            safe_row = {}
            for k, v in row.items():
                if hasattr(v, 'isoformat'):
                    safe_row[k] = v.isoformat()
                else:
                    safe_row[k] = v
            safe_rows.append(safe_row)

        # Build filter options for filterable columns (JSON requests only)
        filter_options = {}
        if request.GET.get('format') == 'json' and total > 0:
            excluded_filters = {'ssid', 'student_id', 'student_name', 'stu_annual_key'}
            always_include = {'school', 'next_year_school_name', 'next_year_school_state_id', 'math_selection'}
            NYL_JOIN = (
                " LEFT JOIN nextyearlist_placement nyl ON nyl.ssid = cs.ssid AND nyl.school_year = cs.school_year"
                " LEFT JOIN mathr_recommendation mr ON mr.ssid = cs.ssid AND mr.school_year = cs.school_year"
            )

            def _norm_fv(v):
                if isinstance(v, (list, tuple, set)):
                    return sorted([str(x) for x in v if x is not None])
                return str(v) if v is not None else ''

            def _filt_ck(column, other_f):
                norm = {k: _norm_fv(v) for k, v in other_f.items()} if isinstance(other_f, dict) else {}
                sy_val = school_year[0] if isinstance(school_year, list) and school_year else (school_year or '')
                gr_val = grade[0] if isinstance(grade, list) and grade else (grade or '')
                st_val = status[0] if isinstance(status, list) and status else (status or '')
                # Include user school scope so different users don't share cached filter options
                if hasattr(request.user, 'active_school') and request.user.active_school:
                    user_scope = f"school:{request.user.active_school.state_id}"
                elif hasattr(request.user, 'schools') and isinstance(request.user.schools, list) and request.user.schools:
                    user_scope = f"schools:{','.join(sorted(request.user.schools))}"
                else:
                    user_scope = 'unrestricted'
                payload = {
                    'base': {'s': str(search or ''), 'sy': str(sy_val), 'gr': str(gr_val), 'st': str(st_val)},
                    'filters': norm or 'no_filters',
                    'scope': user_scope,
                }
                return f"nyl:filter_opts:{column}:{hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()}"

            def _get_other_f(col_key):
                if isinstance(filters, dict) and col_key in filters:
                    return {k: v for k, v in filters.items() if k != col_key}
                return filters if isinstance(filters, dict) else {}

            # Standard (base) columns
            for col in base_cols:
                if col in excluded_filters:
                    continue
                expr = base_col_expressions.get(col)
                if not expr:
                    continue
                other_f = _get_other_f(col)
                ck = _filt_ck(col, other_f)
                cached_vals = cache.get(ck)
                if cached_vals is not None:
                    filter_options[col] = cached_vals
                    continue
                opt_where = list(base_where_clauses)
                opt_params = list(base_params)
                _apply_column_filters(opt_where, opt_params, other_f, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
                opt_sql = " AND ".join(opt_where)
                cursor.execute(
                    f"SELECT COUNT(DISTINCT {expr}) FROM current_student cs {NYL_JOIN} {join_sql} WHERE {opt_sql}",
                    opt_params,
                )
                dc = cursor.fetchone()[0] or 0
                if col not in always_include and (dc <= 1 or dc > 30):
                    continue
                lim = "" if col in always_include else "LIMIT 30"
                cursor.execute(
                    f"SELECT {expr} FROM current_student cs {NYL_JOIN} {join_sql}"
                    f" WHERE {opt_sql} GROUP BY {expr} ORDER BY {expr} ASC NULLS FIRST {lim}",
                    opt_params,
                )
                values = ['' if r[0] is None else r[0] for r in cursor.fetchall()]
                if values:
                    filter_options[col] = values
                    cache.set(ck, values, 300)

            # Test PL / OI filter options — scoped to students in the current filtered list
            for ti in test_cols_info:
                if ti['metric'] not in {'pl', 'oi'}:
                    continue
                col_key = ti['col_name']
                other_f = _get_other_f(col_key)
                ck = _filt_ck(col_key, other_f)
                cached_vals = cache.get(ck)
                if cached_vals is not None:
                    filter_options[col_key] = cached_vals
                    continue
                opt_where = list(base_where_clauses)
                opt_params = list(base_params)
                _apply_column_filters(opt_where, opt_params, other_f, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
                opt_sql = " AND ".join(opt_where)
                win_cl = "AND t.test_window ILIKE %s" if ti.get('test_window') else ""
                win_p = [ti['test_window']] if ti.get('test_window') else []
                student_subquery = (
                    f"SELECT cs.student_id FROM current_student cs {NYL_JOIN} {join_sql} WHERE {opt_sql}"
                )
                if ti['metric'] == 'pl':
                    cursor.execute(
                        f"SELECT r.pl AS v FROM results r"
                        f" INNER JOIN tests t ON r.test_group_key = t.test_group_key"
                        f" WHERE r.local_student_id IN ({student_subquery})"
                        f" AND r.school_year = %s AND t.test_name ILIKE %s AND t.subject ILIKE %s {win_cl}"
                        f" GROUP BY v ORDER BY v ASC NULLS FIRST LIMIT 30",
                        opt_params + [ti['year'], ti['test_name'], ti['subject']] + win_p,
                    )
                else:
                    cursor.execute(
                        f"SELECT (r.other_info ->> %s) AS v FROM results r"
                        f" INNER JOIN tests t ON r.test_group_key = t.test_group_key"
                        f" WHERE r.local_student_id IN ({student_subquery})"
                        f" AND r.school_year = %s AND t.test_name ILIKE %s AND t.subject ILIKE %s {win_cl}"
                        f" GROUP BY v ORDER BY v ASC NULLS FIRST LIMIT 30",
                        [ti.get('other_info_key', '')] + opt_params + [ti['year'], ti['test_name'], ti['subject']] + win_p,
                    )
                values = ['' if r[0] is None else r[0] for r in cursor.fetchall()]
                if values:
                    filter_options[col_key] = values
                    cache.set(ck, values, 300)

            # Mark filter options — scoped to students in the current filtered list
            for mi in mark_cols_info:
                col_key = mi['col_name']
                other_f = _get_other_f(col_key)
                ck = _filt_ck(col_key, other_f)
                cached_vals = cache.get(ck)
                if cached_vals is not None:
                    filter_options[col_key] = cached_vals
                    continue
                opt_where = list(base_where_clauses)
                opt_params = list(base_params)
                _apply_column_filters(opt_where, opt_params, other_f, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
                opt_sql = " AND ".join(opt_where)
                per_cl = "AND sm.mark_period ILIKE %s" if mi.get('mark_period') else "AND (sm.mark_period IS NULL OR sm.mark_period = '')"
                per_p = [mi['mark_period']] if mi.get('mark_period') else []
                stu_annual_subquery = (
                    f"SELECT cs.stu_annual_key FROM current_student cs {NYL_JOIN} {join_sql} WHERE {opt_sql}"
                )
                cursor.execute(
                    f"SELECT sm.mark AS v FROM student_marks_list sm"
                    f" WHERE sm.stu_annual_key IN ({stu_annual_subquery})"
                    f" AND sm.school_year = %s AND sm.subject ILIKE %s AND sm.course_name ILIKE %s {per_cl}"
                    f" GROUP BY v ORDER BY v ASC NULLS FIRST LIMIT 30",
                    opt_params + [mi['year'], f"%{mi['subject']}%", f"%{mi['course_name']}%"] + per_p,
                )
                values = ['' if r[0] is None else r[0] for r in cursor.fetchall()]
                if values:
                    filter_options[col_key] = values
                    cache.set(ck, values, 300)

            # Ensure JSON serialization safety for filter option values
            from decimal import Decimal
            from datetime import date as _date, datetime as _datetime
            for fcol, opts in list(filter_options.items()):
                normalized = []
                for v in opts:
                    if isinstance(v, Decimal):
                        normalized.append(float(v))
                    elif isinstance(v, (_date, _datetime)):
                        normalized.append(v.isoformat())
                    else:
                        normalized.append(v)
                filter_options[fcol] = normalized

        if request.GET.get('format') == 'json':
            return JsonResponse({
                'students': safe_rows,
                'selected_columns': selected_columns,
                'total': total,
                'page': page,
                'total_pages': total_pages,
                'column_definitions': dynamic_definitions,
                'filter_options': filter_options,
                'filters': filters if isinstance(filters, dict) else {},
                'search': search or '',
                'school_year': school_year[0] if isinstance(school_year, list) and school_year else (school_year or ''),
                'grade': grade[0] if isinstance(grade, list) and grade else (grade or ''),
                'status': status[0] if isinstance(status, list) and status else (status or ''),
                'sort_by': sort_by,
                'sort_dir': sort_dir,
            })

        return render(request, 'nextyearlist/partials/student_table.html', {
            'students': safe_rows,
            'columns': columns,
            'total': total,
            'page': page,
            'limit': limit,
            'total_pages': total_pages,
            'selected_columns': selected_columns,
            'column_definitions': dynamic_definitions,
            'sort_by': sort_by,
            'sort_dir': sort_dir,
        })
