"""Student list views with filtering and data loading."""

from django.shortcuts import render
from django.http import JsonResponse
from django.views.generic import ListView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import connections
from django.core.cache import cache
from django.conf import settings
from decimal import Decimal
from datetime import date, datetime, timezone
import hashlib
import json
import logging
import time

from clickhouse_utils.executor import QueryExecutor
from clickhouse_utils.pagination import decode_cursor, cursor_from_row, keyset_clause
from clickhouse_utils.thread_pool import get_io_pool
from clickhouse_utils.timing import current_request_id, timed, timed_fn
from ..models import SavedQuery, StudentList
from studata.models import CurrentStudent
from ..column_utils import (
    get_test_columns, get_mark_columns, get_ap_columns, get_sat_columns, _get_display_name,
    RESULTS_STUDENT_MATCH_SQL, RESULTS_BLANK_SSID_SQL,
    enrich_students_tests_marks,
    _fetch_marks_live_from_alt,
    group_marks_by_ssid_course,
    markall_course_filter_fn,
    PL_SORT_SQL, SS_SORT_SQL, parse_apall_sort_key,
    parse_sat_column_key, sat_column_db_field,
)
from ..survey_utils import get_survey_columns, get_survey_data_for_students
from ..constants import COLUMN_DEFINITIONS, COLUMN_CATEGORIES, VIRTUAL_COLUMNS
from ..utils import can_user_view_query, get_user_folders, is_unrestricted as _is_user_unrestricted, _apply_de_school_filter
from .student_data_utils import (
    get_current_student_columns,
    get_student_enroll_columns,
)
from .column_management import (
    get_saved_default_columns,
    get_column_expression,
    get_enabled_columns,
    json_list_key_for_column,
    append_json_list_filter,
    json_list_unnest_select_expr,
)
from ..school_year_utils import get_school_year_choices, get_year_labels_map, get_year_alias_map, resolve_relative_column_key
from .filter_sql import (
    apply_column_filters,
    normalize_filter_param as _normalize_filter_param,
    extract_markall_course_excludes,
)
from landing.utils import get_project_config

logger = logging.getLogger(__name__)


def _fetchall_ch_or_pg(sql, params, pg_sql=None):
    """Run *sql* on ClickHouse; on error, run *pg_sql* (or *sql*) on PostgreSQL."""
    try:
        cur = QueryExecutor(analytics=True).cursor()
        cur.execute(sql, params)
        return list(cur.fetchall())
    except Exception:
        with connections['default'].cursor() as cur:
            cur.execute(pg_sql or sql, params)
            return list(cur.fetchall())


def _append_school_year_filter(where_clauses: list, params: list, school_year) -> None:
    """Append a school_year predicate; no-op when school_year is unset."""
    if not school_year:
        return
    if isinstance(school_year, list):
        if len(school_year) == 1:
            where_clauses.append("cs.school_year = %s")
            params.append(school_year[0])
        elif len(school_year) > 1:
            where_clauses.append("cs.school_year = ANY(%s)")
            params.append(school_year)
    else:
        where_clauses.append("cs.school_year = %s")
        params.append(school_year)


def _serialize_context_filter(val):
    """Normalize a filter value for JSON responses and UI sync."""
    if isinstance(val, list):
        return val[0] if len(val) == 1 else (val if val else '')
    return val or ''


def _run_option_query(sql, params, use_pg=False):
    """Execute one filter-option query on its own cursor/connection.

    Safe to call from worker threads: ClickHouse clients are thread-local
    (see clickhouse_utils.client) and Django connections are thread-local.
    The thread's PostgreSQL connection is closed afterwards so pooled worker
    threads do not hold it open past the request (which would leak connections
    and block test-database teardown).
    """
    try:
        if use_pg:
            with connections['default'].cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
        cur = QueryExecutor(analytics=True).cursor()
        cur.execute(sql, params)
        return list(cur.fetchall())
    finally:
        try:
            connections['default'].close()
        except Exception:
            pass


def _option_values(rows):
    """First column of *rows* as a de-duplicated list, None -> ''."""
    values = []
    for row in rows:
        val = row[0]
        if val is None:
            val = ""
        if val not in values:
            values.append(val)
    return values


def _run_option_tasks(tasks):
    """Run (col_key, cache_key, fn) filter-option tasks concurrently.

    ``fn`` returns the option values for its column (or None to skip). The
    shared bounded ClickHouse I/O pool caps total concurrency per worker
    process. Results are returned in submission order; a failing column is
    logged and skipped so one bad option query cannot fail the whole page.
    """
    if not tasks:
        return []
    pool = get_io_pool()
    futures = [pool.submit(task[2]) for task in tasks]
    results = []
    for (col_key, cache_key, _fn), future in zip(tasks, futures):
        try:
            values = future.result()
        except Exception:
            logger.exception("filter option query failed for column %s", col_key)
            values = None
        results.append((col_key, cache_key, values))
    return results


def _make_json_option_task(sql, params):
    def _fn():
        rows = _run_option_query(sql, params)
        values = [row[0] for row in rows if row[0] is not None and str(row[0]).strip()]
        return values or None
    return _fn


def _make_base_option_task(sql, params, col, always_include):
    def _fn():
        rows = _run_option_query(sql, params)
        num_rows = len(rows)
        if num_rows <= 1 or (num_rows > 30 and col not in always_include):
            return None
        values = [("" if row[0] is None else row[0]) for row in rows]
        if col not in always_include:
            values = values[:30]
        return values or None
    return _fn


def _make_options_task(sql, params, use_pg=False):
    def _fn():
        return _option_values(_run_option_query(sql, params, use_pg=use_pg)) or None
    return _fn


def _make_options_with_blank_task(options_sql, options_params,
                                  check_sql, check_params, use_pg=False):
    def _fn():
        try:
            values = _option_values(
                _run_option_query(options_sql, options_params, use_pg=use_pg))
        except Exception:
            return None
        try:
            if _run_option_query(check_sql, check_params, use_pg=use_pg):
                if "" not in values:
                    values.insert(0, "")
        except Exception:
            pass
        return values or None
    return _fn


def _make_markall_marks_task(period_sql, mark_sql, params):
    def _fn():
        period_values = []
        for row in _run_option_query(period_sql, params, use_pg=True):
            v = row[0]
            if v is not None and v not in period_values:
                period_values.append(v)
        mark_values = []
        for row in _run_option_query(mark_sql, params, use_pg=True):
            v = row[0]
            if v is not None and v not in mark_values:
                mark_values.append(v)
        return (period_values + mark_values) or None
    return _fn


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
        marks_structure = get_mark_columns()
        survey_structure = get_survey_columns()
        ap_structure = get_ap_columns()
        sat_structure = get_sat_columns()
        
        # Get unique values for filters using cache
        school_years_key = "dataexp:school_years"
        school_years = cache.get(school_years_key)
        if not school_years:
            with connections['default'].cursor() as cursor:
                cursor.execute("SELECT DISTINCT school_year FROM current_student WHERE school_year IS NOT NULL ORDER BY school_year DESC")
                school_years = [row[0] for row in cursor.fetchall()]
                cache.set(school_years_key, school_years, 3600)

        # Hide school years disabled via Project Settings
        try:
            from landing.utils import get_disabled_school_years
            _disabled_years = get_disabled_school_years()
            if _disabled_years:
                school_years = [sy for sy in school_years if sy not in _disabled_years]
        except Exception:
            pass

        # Apply role-based restrictions for grade filter options
        is_unrestricted = _is_user_unrestricted(self.request.user)

        grade_filter_clauses = ["grade IS NOT NULL"]
        grade_filter_params = []

        # Hide school years disabled via Project Settings
        try:
            from landing.utils import get_disabled_school_years
            _disabled_years = get_disabled_school_years()
            if _disabled_years:
                grade_filter_clauses.append("school_year NOT IN (%s)")
                grade_filter_params.append(_disabled_years)
        except Exception:
            pass

        if hasattr(self.request.user, 'active_school') and self.request.user.active_school:
            grade_filter_clauses.append("school_state_id = %s")
            grade_filter_params.append(self.request.user.active_school.state_id)
        else:
            if not is_unrestricted:
                if hasattr(self.request.user, 'schools') and isinstance(self.request.user.schools, list) and self.request.user.schools:
                    grade_filter_clauses.append("school_state_id = ANY(%s)")
                    grade_filter_params.append(self.request.user.schools)
                else:
                    grade_filter_clauses.append("1=0")

        active_role_name = getattr(getattr(self.request.user, 'active_role', None), 'name', None)
        if active_role_name == 'School Gr View':
            delegated_grades = getattr(self.request.user, 'grade_levels', None)
            if isinstance(delegated_grades, list) and delegated_grades:
                grade_filter_clauses.append("grade = ANY(%s)")
                grade_filter_params.append(delegated_grades)

        active_school_id = getattr(getattr(self.request.user, 'active_school', None), 'state_id', None)
        schools_key = json.dumps(self.request.user.schools or [], sort_keys=True)
        role_key = active_role_name or ''
        grade_levels_key = json.dumps(getattr(self.request.user, 'grade_levels', None) or [], sort_keys=True)
        grades_key = f"dataexp:grades:{self.request.user.id}:{active_school_id}:{role_key}:{hashlib.md5(schools_key.encode()).hexdigest()}:{hashlib.md5(grade_levels_key.encode()).hexdigest()}"
        grades = cache.get(grades_key)
        if not grades:
            with connections['default'].cursor() as cursor:
                where_sql = " AND ".join(grade_filter_clauses)
                cursor.execute(
                    f"SELECT DISTINCT grade FROM current_student WHERE {where_sql} ORDER BY grade",
                    grade_filter_params,
                )
                grades = [row[0] for row in cursor.fetchall()]
                cache.set(grades_key, grades, 3600)
        
        context['school_years'] = school_years
        context['school_year_choices'] = get_school_year_choices(school_years)
        all_years = list(test_structure.keys()) + list(marks_structure.keys()) + list(ap_structure.keys()) + list(sat_structure.keys())
        context['year_labels'] = get_year_labels_map(all_years)
        year_to_alias = get_year_alias_map()
        context['year_to_alias'] = year_to_alias
        # underscore-format keys for JS (column keys use underscores not hyphens)
        context['year_alias_map_json'] = json.dumps({y.replace('-', '_'): a for y, a in year_to_alias.items()})
        context['grades'] = grades
        context['test_structure'] = test_structure
        context['marks_structure'] = marks_structure
        context['survey_structure'] = survey_structure.get('structure', {})
        context['ap_structure'] = ap_structure
        context['sat_structure'] = sat_structure
        enabled_defs, enabled_cats = get_enabled_columns()
        context['column_categories'] = enabled_cats
        context['column_definitions'] = enabled_defs
        default_columns = get_saved_default_columns()
        context['default_columns'] = default_columns
        context['default_columns_json'] = json.dumps(default_columns)
        context['column_definitions_json'] = json.dumps(enabled_defs)
        
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

        # Resolve the project default school year for the initial page load
        _project_default_year = ''
        try:
            _cfg = get_project_config()
            _project_default_year = _cfg.default_school_year or ''
        except Exception:
            pass

        def get_param(name, default=''):
            # URL param takes precedence over saved query, which takes precedence over default
            return self.request.GET.get(name) or filters.get(name, default)

        # Current filter values
        context['search_query'] = get_param('search')
        if 'school_year' in self.request.GET:
            selected_year = self.request.GET.get('school_year')
        elif _project_default_year:
            selected_year = _project_default_year
        else:
            selected_year = filters.get('school_year', '')

        if not selected_year:
            selected_year = '__sy_this__'

        # Resolve selected_school_year to concrete school year if it is an alias
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



def load_students(request):
    """Load student data with dynamic test columns via HTMX."""
    with timed('load_students'):
        return _load_students(request)


def _load_students(request):
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

    # Resolve any relative column keys (e.g. test___sy_this___...) to real year keys
    selected_columns = [resolve_relative_column_key(c) for c in selected_columns]

    # ── AP "All Results" expansion ──────────────────────────────────────────
    # The `apall_<year>` umbrella expands into wide assessment/result pairs
    # (apall_<year>_1_SUBJECT, apall_<year>_1_PL, ...) — one pair per AP exam a
    # student has, ranked by exam name. Selecting it also unselects the
    # individual ap_<year>_*_PL columns. Subjects the user unchecked in the
    # picker are stored under the umbrella key as 'exclude:<subject>' values
    # and skipped during expansion.
    def _apall_year_key(col):
        if not isinstance(col, str) or not col.startswith('apall_'):
            return None
        parts = col[len('apall_'):].split('_')
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{parts[0]}_{parts[1]}"
        return None

    apall_years = []
    apall_max = {}
    for c in selected_columns:
        yk = _apall_year_key(c)
        if yk and yk.replace('_', '-') not in apall_years:
            apall_years.append(yk.replace('_', '-'))

    # Collapse any already-expanded apall keys (saved/previous selection) back
    # to their umbrella, and unselect the individual ap_<year>_*_PL columns.
    # Expansion into wide pairs happens later once the filtered population is
    # known, so we only emit pairs students actually have.
    if apall_years:
        new_selected = []
        for c in selected_columns:
            if isinstance(c, str) and c.startswith('apall_'):
                yk = _apall_year_key(c)
                if yk:
                    umbrella = f"apall_{yk}"
                    if umbrella not in new_selected:
                        new_selected.append(umbrella)
                    continue
            if isinstance(c, str) and c.startswith('ap_') and not c.startswith(('apaward_', 'apawards_all_')):
                tail = c[len('ap_'):]
                parts = tail.split('_')
                if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                    if f"{parts[0]}-{parts[1]}" in apall_years:
                        continue
            new_selected.append(c)
        selected_columns = new_selected

    # ── Marks "All Marks" expansion ─────────────────────────────────────────
    # The `markall_<year>_<subject>` umbrella key expands into wide course/marks
    # pairs (markall_<year>_<subject>_1_COURSE, ..._1_MARKS, ...) one pair per
    # course a student has for that subject. Marks are shown as "Q1: B, Q2: B".
    # Selecting it also unselects the individual mark_<year>_<subject>_* columns.
    from dataexploration.column_utils import parse_markall_key, markall_key as _markall_key

    def _markall_subject_actual(year, subject_norm):
        """Resolve the normalized subject name back to its real spelling."""
        try:
            marks_struct = get_mark_columns()
            subjects = marks_struct.get(year, {})
            for s in subjects:
                if _markall_key(year, s) == _markall_key(year, subject_norm):
                    return s
        except Exception:
            pass
        return subject_norm.replace('_', ' ')

    def _markall_course_sql_parts(subject, excludes):
        """SQL predicate + params restricting which courses participate.

        Homeroom courses (e.g. "Homeroom Gr 1", "IS Homeroom Gr KN-5") are
        administrative placeholder courses and are filtered out of the
        Elementary subject's "All Marks" view. User-deselected courses (from
        the course picker) are excluded here too so the whole wide-pair view
        stays consistent.
        """
        from .filter_sql import markall_course_filter_parts
        return markall_course_filter_parts(subject, excludes)

    markall_years = {}  # (year, subject_actual) -> umbrella key
    for c in selected_columns:
        parsed = parse_markall_key(c)
        if parsed:
            key_t = (parsed['year'], _markall_subject_actual(parsed['year'], parsed['subject_norm']))
            if key_t not in markall_years:
                markall_years[key_t] = _markall_key(parsed['year'], key_t[1])

    # Collapse any already-expanded markall keys (saved/previous selection) back to
    # their umbrella, and unselect the individual mark_<year>_<subject>_* columns.
    # Actual expansion into wide pairs happens later once the filtered student
    # population is known (so we only emit pairs students actually have).
    if markall_years:

        new_selected = []
        for c in selected_columns:
            # Unselect individual mark columns for expanded subjects
            if isinstance(c, str) and c.startswith('mark_') and not c.startswith('markall_'):
                tail = c[len('mark_'):]
                parts = tail.split('_')
                if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                    yr = f"{parts[0]}-{parts[1]}"
                    # recover the subject by matching the normalized subject prefix
                    norm_rest = '_'.join(parts[2:])
                    for (y, subj) in markall_years:
                        subj_norm = _markall_key(yr, subj)[len('markall_'):].split('_', 2)[2]
                        if y == yr and norm_rest.startswith(subj_norm + '_'):
                            break
                    else:
                        new_selected.append(c)
                        continue
                    continue
            # Collapse already-expanded keys back to the umbrella (re-expanded later)
            parsed = parse_markall_key(c)
            if parsed and parsed['kind'] is not None:
                key_t = (parsed['year'], _markall_subject_actual(parsed['year'], parsed['subject_norm']))
                umbrella = markall_years.get(key_t)
                if umbrella and umbrella not in new_selected:
                    new_selected.append(umbrella)
                continue
            new_selected.append(c)
        selected_columns = new_selected

    markall_max = {}

    # Extract saved filters (they may be nested under 'filters' key or at top level)
    saved_filters = saved_config.get('filters', {}) if isinstance(saved_config.get('filters'), dict) else {}
    
    def _parse_multi(param_val, fallback):
        """Parse a URL param that may be a JSON array string, a plain string, or a list from saved config."""
        if not param_val:
            return fallback
        if isinstance(param_val, list):
            return param_val if param_val else fallback
        # Try JSON array
        val_str = str(param_val).strip()
        if val_str.startswith('['):
            try:
                parsed = json.loads(val_str)
                if isinstance(parsed, list) and parsed:
                    return parsed
                return fallback
            except Exception:
                pass
        # Single value
        return val_str if val_str else fallback

    # Pasted ID list search (overrides regular search when present)
    search_ids = None
    search_ids_param = request.GET.get('search_ids', '')
    if search_ids_param:
        try:
            parsed_ids = json.loads(search_ids_param)
            if isinstance(parsed_ids, list):
                search_ids = [str(s).strip() for s in parsed_ids if str(s).strip()]
                if not search_ids:
                    search_ids = None
        except Exception:
            pass

    # Get filters - URL params take precedence over saved config
    search = request.GET.get('search', '') or saved_filters.get('search', '') or saved_config.get('search', '')
    
    _project_default_year = ''
    try:
        _cfg = get_project_config()
        _project_default_year = _cfg.default_school_year or ''
    except Exception:
        pass

    _sy_in_url = 'school_year' in request.GET
    if _sy_in_url:
        _sy_raw = request.GET.get('school_year', '')
        school_year = _parse_multi(_sy_raw, '')
    else:
        # A saved query's own school year wins over the project default, so
        # reopening a query still targets the year it was built for.
        _saved_school_year = _parse_multi(
            saved_filters.get('school_year', ''),
            _parse_multi(saved_config.get('school_year', ''), '')
        )
        if saved_query_id and _saved_school_year:
            school_year = _saved_school_year
        elif _project_default_year:
            school_year = _project_default_year
        else:
            school_year = _saved_school_year

    if not school_year:
        school_year = '__sy_this__'

    # Resolve school-year aliases to concrete year strings
    try:
        from dataexploration.school_year_utils import resolve_school_year
        if isinstance(school_year, list):
            school_year = [resolve_school_year(y) for y in school_year]
        elif school_year:
            school_year = resolve_school_year(school_year)
    except Exception:
        pass
    
    _gr_raw = request.GET.get('grade', '')
    grade = _parse_multi(
        _gr_raw,
        _parse_multi(saved_filters.get('grade', ''),
                     _parse_multi(saved_config.get('grade', ''), ''))
    )
    
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

    # Optional keyset cursor. Numbered pages remain supported. Cursor
    # pagination is used for default name order and other base-column sorts.
    cursor_raw = request.GET.get('cursor', '')
    cursor_value = decode_cursor(cursor_raw) if cursor_raw else None

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
    
    # Resolve any relative column keys in sort_by and filters keys
    if sort_by:
        sort_by = resolve_relative_column_key(sort_by)
    if isinstance(filters, dict):
        filters = {resolve_relative_column_key(k): v for k, v in filters.items()}

    # Drop saved filters on individual AP subject PL columns when their "All Results" umbrella is selected
    if isinstance(filters, dict) and apall_years:
        apall_year_set = set(apall_years)
        filters = {
            k: v for k, v in filters.items()
            if not (isinstance(k, str) and k.startswith('ap_') and not k.startswith('apaward_') and not k.startswith('apawards_all_') and not k.startswith('apall_'))
        }

    # Drop saved filters on individual mark columns when their "All Marks" umbrella is selected
    if isinstance(filters, dict) and markall_years:
        filters = {
            k: v for k, v in filters.items()
            if not (isinstance(k, str) and k.startswith('mark_') and not k.startswith('markall_'))
        }

    # Courses the user deselected from an "All Marks" umbrella. Stored under the
    # umbrella key as 'exclude:<course>' values; read before SQL filtering since
    # these entries only shape the wide-pair columns, never the row population.
    markall_excludes = extract_markall_course_excludes(filters)

    def _markall_course_excludes(year, subject):
        umbrella = markall_years.get((year, subject))
        if umbrella:
            return markall_excludes.get(umbrella, set())
        return set()

    # Subjects the user unchecked from an "All Results" umbrella. Stored under
    # the umbrella key as 'exclude:<subject>' values; like the marks course
    # excludes these only shape the wide-pair columns, never the row population.
    def _apall_subject_excludes(year):
        if not isinstance(filters, dict):
            return set()
        vals = filters.get(f"apall_{year.replace('-', '_')}")
        names = set()
        if isinstance(vals, list):
            for v in vals:
                s = str(v)
                if s.startswith('exclude:'):
                    names.add(s[len('exclude:'):])
        return names

    with QueryExecutor(analytics=True).cursor() as cursor:
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

            # Try cache first (sync-versioned so a ClickHouse re-sync invalidates)
            _sync_v_meta = cache.get('analytics:clickhouse_sync_version', 'legacy')
            test_cols_cache_key = f"test_cols:{_sync_v_meta}:{hashlib.md5(json.dumps(sorted(selected_test_cols)).encode()).hexdigest()}"
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
                    SELECT DISTINCT t.school_year, t.test_name, t.subject, COALESCE(t.test_window, '')
                    FROM tests t
                    WHERE t.school_year = ANY(%s) AND t.school_year IS NOT NULL AND t.test_name IS NOT NULL
                    """,
                    [years],
                )
            else:
                cursor.execute(
                    """
                    SELECT DISTINCT t.school_year, t.test_name, t.subject, ''
                    FROM tests t
                    WHERE t.school_year = ANY(%s) AND t.school_year IS NOT NULL AND t.test_name IS NOT NULL
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
                dfp_norm = f"test_{key_base}_DFP"

                for norm_key, metric in ((ss_norm, 'ss'), (pl_norm, 'pl'), (dfp_norm, 'dfp')):
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
                    if metric == 'dfp':
                        info['other_info_key'] = 'dfp'
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

        def _parse_year_from_mark_col(col_key):
            s = (col_key or '').replace('-', '_')
            if not s.startswith('mark_'):
                return None
            tail = s[len('mark_'):]
            if len(tail) >= 10 and tail[4] == '_' and tail[9] == '_':
                return tail[:9].replace('_', '-')
            parts = tail.split('_', 1)
            if parts:
                return parts[0].replace('_', '-')
            return None

        def _build_mark_columns_from_db(selected_mark_cols):
            """Map selected mark_* columns to student marks metadata."""
            if not selected_mark_cols:
                return [], {}

            _sync_v_meta = cache.get('analytics:clickhouse_sync_version', 'legacy')
            mark_cols_cache_key = f"mark_cols:{_sync_v_meta}:{hashlib.md5(json.dumps(sorted(selected_mark_cols)).encode()).hexdigest()}"
            cached = cache.get(mark_cols_cache_key)
            if cached:
                return cached

            normalized_to_original = {c.replace('-', '_'): c for c in selected_mark_cols}
            years = sorted({y for y in (_parse_year_from_mark_col(c) for c in selected_mark_cols) if y})
            if not years:
                return [], {}

            cursor.execute(
                """
                SELECT DISTINCT school_year, subject, course_name, mark_period
                FROM student_marks_list
                WHERE school_year = ANY(%s) AND school_year IS NOT NULL
                """,
                [years],
            )

            infos = []
            col_map = {}

            def _norm_part(v):
                return (v or '').replace(' ', '_').replace('-', '_')

            for (year, subject, course_name, mark_period) in cursor.fetchall():
                year_norm = _norm_part(year)
                subject_norm = _norm_part(subject)
                course_norm = _norm_part(course_name)
                period_norm = _norm_part(mark_period)

                key_base = f"{year_norm}_{subject_norm}_{course_norm}_{period_norm}"
                mark_norm = f"mark_{key_base}"

                original_key = normalized_to_original.get(mark_norm)
                if not original_key:
                    continue

                info = {
                    'col_name': original_key,
                    'year': year or '',
                    'subject': subject or '',
                    'course_name': course_name or '',
                    'mark_period': (mark_period or '').strip() or None,
                }
                infos.append(info)
                col_map[original_key] = info
                col_map[original_key.replace('-', '_')] = info

            result = (infos, col_map)
            cache.set(mark_cols_cache_key, result, 600)
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
        if search_ids:
            # Exact ID list match against ssid or student_id
            base_where_clauses.append("(cs.ssid = ANY(%s) OR cs.student_id = ANY(%s))")
            base_params.extend([search_ids, search_ids])
        elif search:
            # Handle search if it comes in as a list
            if isinstance(search, list):
                search = search[0] if search else ''

            if search:
                base_where_clauses.append("(cs.student_name ILIKE %s OR cs.ssid ILIKE %s OR cs.student_id ILIKE %s)")
                search_param = f'%{search}%'
                base_params.extend([search_param, search_param, search_param])
        
        _append_school_year_filter(base_where_clauses, base_params, school_year)
        
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

        # Apply active school context or enforce school permissions
        _apply_de_school_filter(base_where_clauses, base_params, request.user)

        active_role_name = getattr(getattr(request.user, 'active_role', None), 'name', None)
        if active_role_name == 'School Gr View':
            delegated_grades = getattr(request.user, 'grade_levels', None)
            if isinstance(delegated_grades, list) and delegated_grades:
                base_where_clauses.append("cs.grade = ANY(%s)")
                base_params.append(delegated_grades)

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

        # Separate base columns from test/mark/survey columns
        filter_keys = list(filters.keys()) if isinstance(filters, dict) else []
        selected_test_cols = list(set(
            [c for c in selected_columns if isinstance(c, str) and c.startswith('test_')] +
            [k for k in filter_keys if isinstance(k, str) and k.startswith('test_')]
        ))
        selected_mark_cols = list(set(
            [c for c in selected_columns if isinstance(c, str) and c.startswith('mark_') and not c.startswith('markall_')] +
            [k for k in filter_keys if isinstance(k, str) and k.startswith('mark_') and not k.startswith('markall_')]
        ))
        selected_survey_cols = [c for c in selected_columns if isinstance(c, str) and c.startswith('survey_')]
        selected_ap_cols = list(set(
            [c for c in selected_columns if isinstance(c, str) and c.startswith('ap_') and not c.startswith('apaward_') and not c.startswith('apawards_all_') and not c.startswith('apall_')] +
            [k for k in filter_keys if isinstance(k, str) and k.startswith('ap_') and not k.startswith('apaward_') and not k.startswith('apawards_all_') and not k.startswith('apall_')]
        ))
        selected_award_cols = list(set(
            [c for c in selected_columns if isinstance(c, str) and (c.startswith('apaward_') or c.startswith('apawards_all_'))] +
            [k for k in filter_keys if isinstance(k, str) and (k.startswith('apaward_') or k.startswith('apawards_all_'))]
        ))
        selected_sat_cols = list(set(
            [c for c in selected_columns if isinstance(c, str) and c.startswith('sat_')] +
            [k for k in filter_keys if isinstance(k, str) and k.startswith('sat_')]
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
        base_cols = [
            c for c in selected_columns
            if isinstance(c, str)
            and (not c.startswith(('test_', 'mark_', 'markall_', 'survey_', 'ap_', 'apaward_', 'apawards_all_', 'apall_', 'sat_')))
            and c in COLUMN_DEFINITIONS
            and c not in VIRTUAL_COLUMNS
        ]

        # Build test column metadata from DB to avoid parsing issues (e.g. multi-word subjects)
        test_cols_info, test_col_map = _build_test_columns_from_db(selected_test_cols)
        mark_cols_info, mark_col_map = _build_mark_columns_from_db(selected_mark_cols)
        ap_exam_infos = ap_award_infos = []
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

        # Determine if we need student_enroll join
        enroll_fields = {'ell', 'birth_date', 'us_enter_date', 'fep_date', 'migrant'}
        needs_enroll = (
            any(c in enroll_fields for c in selected_columns)
            or (isinstance(filters, dict) and any(k in enroll_fields for k in filters.keys()))
            or sort_by in enroll_fields
        )

        # LTEL lives on current_student (synced). The SIS `students` override
        # table is not in ClickHouse, so joining it fails analytics reads.
        needs_students = False

        needs_eht = (
             (isinstance(filters, dict) and 'elem_teacher_name' in filters.keys())
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

        # Column filters (applied to the main query)
        where_clauses = list(base_where_clauses)
        params = list(base_params)
        apply_column_filters(
            where_clauses, params, filters, test_col_map, mark_col_map,
            db_columns, enroll_columns, needs_enroll, needs_students, needs_eht,
            ap_exam_infos, ap_award_infos,
        )

        # Handle survey column filters (subquery since survey data is not in the main SQL table)
        survey_filters = {k: v for k, v in filters.items() if isinstance(k, str) and k.startswith('survey_') and v} if isinstance(filters, dict) else {}
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
                _sr_base_params = [_sr_meta['survey_type'], _sr_meta['school_year'], _sr_meta['window'], _sr_meta.get('grade_range', ''), _sr_meta['question_order']]
                if _sr_real_values:
                    _sr_conditions.append(f"cs.student_id IN ({_sr_base_query} AND sr.value = ANY(%s))")
                    params.extend(_sr_base_params + [_sr_real_values])
                if _sr_has_blank:
                    _sr_conditions.append(f"cs.student_id NOT IN ({_sr_base_query})")
                    params.extend(_sr_base_params)
                if _sr_conditions:
                    where_clauses.append(f"({' OR '.join(_sr_conditions)})")

        # Exclude school years hidden via Project Settings
        try:
            from landing.utils import get_disabled_school_years
            _disabled_years = get_disabled_school_years()
            if _disabled_years:
                where_clauses.append("cs.school_year NOT IN (%s)")
                params.append(_disabled_years)
        except Exception:
            pass

        where_sql = " AND ".join(where_clauses)
        
        # Count total with caching
        _sync_v = cache.get('analytics:clickhouse_sync_version', 'legacy')
        count_cache_key = f"dataexp:count:{_sync_v}:{hashlib.md5(json.dumps({'where': where_clauses, 'params': [str(p) for p in params], 'join': join_sql}, sort_keys=True).encode()).hexdigest()}"
        total = cache.get(count_cache_key)
        if total is None:
            with timed('dataexp_count'):
                count_query = f"""
                    SELECT COUNT(DISTINCT cs.ssid)
                    FROM current_student cs
                    {join_sql}
                    WHERE {where_sql}
                """
                cursor.execute(count_query, params)
                total = cursor.fetchone()[0]
                cache.set(count_cache_key, total, 300)  # Cache for 5 minutes

        # ── Marks "All Marks" expansion (filtered population) ──────────────
        # Only emit course/marks wide pairs up to the max number of courses the
        # *filtered* students actually have in that subject. If no filtered
        # student has a second course, only one pair is produced.
        if markall_years:
            for (year, subject), umbrella in markall_years.items():
                course_filter_sql, course_extra_params = _markall_course_sql_parts(
                    subject, _markall_course_excludes(year, subject)
                )
                markall_cache_key = (
                    f"dataexp:markall_max:{_sync_v}:"
                    + hashlib.md5(json.dumps({
                        'where': where_sql,
                        'params': [str(p) for p in params],
                        'join': join_sql,
                        'year': year,
                        'subject': subject,
                        'cf': course_filter_sql,
                        'cep': [str(p) for p in course_extra_params],
                    }, sort_keys=True).encode()).hexdigest()
                )
                max_val = cache.get(markall_cache_key)
                if max_val is None:
                    ch_query = f"""
                    SELECT COALESCE(MAX(cnt), 0)
                    FROM (
                        SELECT sm.ssid, COUNT(DISTINCT sm.course_name) AS cnt
                        FROM analytics_student_marks sm
                        INNER JOIN current_student cs ON sm.ssid = cs.ssid
                        {join_sql}
                        WHERE {where_sql}
                          AND sm.school_year = %s AND sm.subject = %s
                          {course_filter_sql}
                        GROUP BY sm.ssid
                    ) t
                    """
                    pg_query = ch_query.replace('analytics_student_marks', 'student_marks_list')
                    res = _fetchall_ch_or_pg(
                        ch_query,
                        params + [year, subject] + course_extra_params,
                        pg_sql=pg_query,
                    )
                    max_val = min(int(res[0][0] or 0), 10) if res else 0
                    cache.set(markall_cache_key, max_val, 300)
                markall_max[(year, subject)] = max_val

            new_selected = []
            for c in selected_columns:
                parsed = parse_markall_key(c)
                if parsed and parsed['kind'] is None:
                    year = parsed['year']
                    subject = _markall_subject_actual(year, parsed['subject_norm'])
                    n = markall_max.get((year, subject), 0)
                    for i in range(1, n + 1):
                        new_selected.append(f"{c}_{i}_COURSE")
                        new_selected.append(f"{c}_{i}_MARKS")
                    continue
                new_selected.append(c)
            selected_columns = new_selected

        # ── AP "All Results" expansion (filtered population) ───────────────
        # Emit assessment/result wide pairs up to the max number of AP exams
        # the *filtered* students actually have for the year (subjects the
        # user unchecked in the picker are skipped).
        if apall_years:
            for year in apall_years:
                excludes = _apall_subject_excludes(year)
                exclude_clause = ""
                exclude_params = []
                if excludes:
                    placeholders = ", ".join(["%s"] * len(excludes))
                    exclude_clause = f" AND e.name NOT IN ({placeholders})"
                    exclude_params = sorted(excludes)
                apall_cache_key = (
                    f"dataexp:apall_max:{_sync_v}:"
                    + hashlib.md5(json.dumps({
                        'where': where_sql,
                        'params': [str(p) for p in params],
                        'join': join_sql,
                        'year': year,
                        'excl': exclude_clause,
                        'exp': [str(p) for p in exclude_params],
                    }, sort_keys=True).encode()).hexdigest()
                )
                max_val = cache.get(apall_cache_key)
                if max_val is None:
                    ch_exclude_clause = (
                        f" AND r.exam_name NOT IN ({', '.join(['%s'] * len(excludes))})" if excludes else ""
                    )
                    ch_query = f"""
                    SELECT COALESCE(MAX(cnt), 0)
                    FROM (
                        SELECT r.ssid, COUNT(*) AS cnt
                        FROM analytics_ap_results r
                        INNER JOIN current_student cs ON r.ssid = cs.ssid
                        {join_sql}
                        WHERE {where_sql}
                          AND r.admin_year = %s
                          {ch_exclude_clause}
                        GROUP BY r.ssid
                    ) t
                    """
                    pg_query = f"""
                    SELECT COALESCE(MAX(cnt), 0)
                    FROM (
                        SELECT r.ssid, COUNT(*) AS cnt
                        FROM "dataLoader_apexamresult" r
                        INNER JOIN "dataLoader_apexamcode" e ON r.exam_id = e.code
                        INNER JOIN "dataLoader_apimportlog" il ON r.import_log_id = il.id
                        INNER JOIN current_student cs ON r.ssid = cs.ssid
                        {join_sql}
                        WHERE {where_sql}
                          AND r.admin_year = %s AND il.is_published = true
                          {exclude_clause}
                        GROUP BY r.ssid
                    ) t
                    """
                    res = _fetchall_ch_or_pg(
                        ch_query,
                        params + [year] + exclude_params,
                        pg_sql=pg_query,
                    )
                    max_val = min(int(res[0][0] or 0), 10) if res else 0
                    cache.set(apall_cache_key, max_val, 300)
                apall_max[year] = max_val

            new_selected = []
            for c in selected_columns:
                yk = _apall_year_key(c)
                if yk:
                    year = yk.replace('_', '-')
                    n = apall_max.get(year, 0)
                    for i in range(1, n + 1):
                        new_selected.append(f"apall_{yk}_{i}_SUBJECT")
                        new_selected.append(f"apall_{yk}_{i}_PL")
                    continue
                new_selected.append(c)
            selected_columns = new_selected

        # Copy params for later reuse
        filter_params = list(params)
        
        # Get paginated results
        offset = (page - 1) * limit
        
        # Always include ssid for joining
        if 'ssid' not in base_cols:
            base_cols.insert(0, 'ssid')

        if (test_cols_info or selected_survey_cols or mark_cols_info) and 'student_id' not in base_cols:
            base_cols.insert(0, 'student_id')

        if (mark_cols_info or 'elem_teacher_name' in base_cols) and 'stu_annual_key' not in base_cols:
            base_cols.insert(0, 'stu_annual_key')

        # Marks "All Marks" fetch needs stu_annual_key to join marks per course
        if markall_years and 'stu_annual_key' not in base_cols:
            base_cols.insert(0, 'stu_annual_key')
        # Keyset pagination needs a unique tie-breaker even when it is not displayed.
        if 'stu_annual_key' not in base_cols:
            base_cols.insert(0, 'stu_annual_key')
        if 'student_name' not in base_cols:
            base_cols.append('student_name')
        # Build base column SELECT with safe mapping
        base_col_selects = []
        base_col_expressions = {}
        for col in base_cols:
            expr, alias = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
            if expr and alias:
                base_col_expressions[col] = expr
                # Always alias: ClickHouse names `cs.ssid` as `cs.ssid`, not `ssid`.
                base_col_selects.append(f"{expr} AS {alias}")

        if not base_col_selects:
            base_col_selects.append("cs.ssid AS ssid") if 'ssid' in db_columns else base_col_selects.append("NULL AS ssid")

        base_col_sql = ", ".join(base_col_selects)

        # Sorting. ClickHouse rejects correlated ORDER BY subqueries that read
        # outer cs.ssid / cs.student_id, so dynamic sorts are LEFT JOINs.
        order_by_expr = "cs.student_name"
        sort_join_sql = ""

        if sort_by:
            expr = base_col_expressions.get(sort_by)
            if expr:
                order_by_expr = expr
            elif sort_by in mark_col_map:
                mark_info = mark_col_map.get(sort_by)
                period_clause = ""
                if mark_info.get('mark_period'):
                    period_clause = "AND sm.mark_period ILIKE %s"
                else:
                    period_clause = "AND (sm.mark_period IS NULL OR sm.mark_period = '')"
                sort_join_sql = (
                    " LEFT JOIN ("
                    " SELECT sm.ssid AS _k, any(sm.mark) AS _v"
                    " FROM analytics_student_marks sm"
                    " WHERE sm.school_year = %s"
                    " AND sm.subject ILIKE %s"
                    " AND sm.course_name ILIKE %s"
                    f" {period_clause}"
                    " GROUP BY sm.ssid"
                    " ) _sort ON _sort._k = cs.ssid"
                )
                order_by_expr = "_sort._v"
                params.extend([
                    mark_info['year'],
                    f"%{mark_info['subject']}%",
                    f"%{mark_info['course_name']}%",
                ])
                if mark_info.get('mark_period'):
                    params.append(mark_info['mark_period'])
            elif sort_by.startswith('survey_'):
                _sv_col_meta = get_survey_columns().get('col_meta', {})
                _sv_meta = _sv_col_meta.get(sort_by)
                if _sv_meta:
                    sort_join_sql = (
                        " LEFT JOIN ("
                        " SELECT student_id AS _k, any(value) AS _v"
                        " FROM analytics_survey_responses"
                        " WHERE survey_type = %s AND school_year = %s"
                        " AND testing_window = %s AND grade_range = %s"
                        " AND question_order = %s"
                        " GROUP BY student_id"
                        " ) _sort ON _sort._k = cs.student_id"
                    )
                    order_by_expr = "_sort._v"
                    params.extend([
                        _sv_meta['survey_type'],
                        _sv_meta['school_year'],
                        _sv_meta['window'],
                        _sv_meta.get('grade_range', ''),
                        _sv_meta['question_order'],
                    ])
            elif sort_by in test_col_map:
                test_info = test_col_map.get(sort_by)
                metric_col = test_info['metric']

                window_clause = ""
                window_params = []
                if test_info.get('test_window'):
                    window_clause = "AND lower(r.test_window) = lower(%s)"
                    window_params = [test_info['test_window']]

                extra_params = []
                if metric_col == 'ss':
                    select_expr = SS_SORT_SQL
                elif metric_col == 'pl':
                    select_expr = PL_SORT_SQL
                else:
                    select_expr = "(r.other_info ->> %s)"
                    extra_params = [test_info.get('other_info_key', '')]

                test_where = (
                    "r.school_year = %s AND lower(r.test_name) = lower(%s) "
                    f"AND lower(r.subject) = lower(%s) {window_clause}"
                )
                test_join_params = extra_params + [
                    test_info['year'],
                    test_info['test_name'],
                    test_info['subject'],
                    *window_params,
                ]
                sort_join_sql = (
                    " LEFT JOIN ("
                    f" SELECT r.ssid AS _k, any({select_expr}) AS _v"
                    " FROM analytics_student_results r"
                    f" WHERE {test_where}"
                    " AND r.ssid IS NOT NULL AND r.ssid != '' AND r.ssid != 'NaN'"
                    " GROUP BY r.ssid"
                    " ) _sort_ssid ON _sort_ssid._k = cs.ssid"
                    " LEFT JOIN ("
                    f" SELECT r.local_student_id AS _k, any({select_expr}) AS _v"
                    " FROM analytics_student_results r"
                    f" WHERE {test_where}"
                    " AND (r.ssid IS NULL OR r.ssid = '' OR r.ssid = 'NaN')"
                    " GROUP BY r.local_student_id"
                    " ) _sort_local ON _sort_local._k = cs.student_id"
                )
                order_by_expr = "coalesce(_sort_ssid._v, _sort_local._v)"
                params.extend(test_join_params)
                params.extend(test_join_params)
            elif sort_by.startswith('apall_'):
                apall_parsed = parse_apall_sort_key(sort_by)
                if apall_parsed and apall_parsed['index'] >= 1:
                    _rn = int(apall_parsed['index'])
                    _apall_year = apall_parsed['year']
                    # Rank only over the subjects still included in the "All
                    # Results" view, otherwise the sort slot (i-th exam) does not
                    # line up with the displayed pair.
                    _apall_excludes = _apall_subject_excludes(_apall_year)
                    _apall_exclude_clause = ""
                    _apall_exclude_params = []
                    if _apall_excludes:
                        _ph = ", ".join(["%s"] * len(_apall_excludes))
                        _apall_exclude_clause = f" AND exam_name NOT IN ({_ph})"
                        _apall_exclude_params = sorted(_apall_excludes)
                    if apall_parsed['field'] == 'SUBJECT':
                        _apall_select = "exam_name"
                        # ClickHouse LEFT JOIN fills non-matching rows with ''
                        # (not NULL), which would sort blanks first. Map the
                        # sentinel to NULL so NULLS LAST actually applies.
                        _apall_order = "nullIf(_sort._v, '')"
                    else:
                        _apall_select = (
                            "CAST(NULLIF(regexp_replace("
                            "NULLIF(btrim(exam_grade), ''), '[^0-9.]', '', 'g'), '') AS NUMERIC)"
                        )
                        _apall_order = "nullIf(_sort._v, 0)"
                    sort_join_sql = (
                        " LEFT JOIN ("
                        f" SELECT ssid AS _k, {_apall_select} AS _v"
                        " FROM ("
                        " SELECT ssid, exam_name, exam_grade,"
                        " row_number() OVER (PARTITION BY ssid ORDER BY exam_name) AS _rn"
                        " FROM analytics_ap_results"
                        f" WHERE admin_year = %s{_apall_exclude_clause}"
                        " ) t WHERE _rn = %s"
                        " ) _sort ON _sort._k = cs.ssid"
                    )
                    order_by_expr = _apall_order
                    params.extend([_apall_year] + _apall_exclude_params + [_rn])
            elif sort_by.startswith('apawards_all_'):
                _yr = sort_by[len('apawards_all_'):].replace('_', '-')
                sort_join_sql = (
                    " LEFT JOIN ("
                    " SELECT ssid AS _k, string_agg(award_description, ', ') AS _v"
                    " FROM analytics_ap_awards"
                    " WHERE award_year = %s"
                    " GROUP BY ssid"
                    " ) _sort ON _sort._k = cs.ssid"
                )
                order_by_expr = "nullIf(_sort._v, '')"
                params.append(_yr)
            elif sort_by.startswith('ap_') and not sort_by.startswith('apaward_'):
                ap_info = next((i for i in ap_exam_infos if i['col_name'] == sort_by), None)
                if ap_info:
                    sort_join_sql = (
                        " LEFT JOIN ("
                        " SELECT ssid AS _k,"
                        " any(CAST(NULLIF(regexp_replace(exam_grade, '[^0-9.]', '', 'g'), '') AS NUMERIC)) AS _v"
                        " FROM analytics_ap_results"
                        " WHERE admin_year = %s AND exam_name = %s"
                        " GROUP BY ssid"
                        " ) _sort ON _sort._k = cs.ssid"
                    )
                    # ClickHouse LEFT JOIN defaults numeric misses to 0, not NULL.
                    order_by_expr = "nullIf(_sort._v, 0)"
                    params.extend([ap_info['year'], ap_info['subject']])
            elif sort_by.startswith('apaward_'):
                ap_info = next((i for i in ap_award_infos if i['col_name'] == sort_by), None)
                if ap_info:
                    sort_join_sql = (
                        " LEFT JOIN ("
                        " SELECT ssid AS _k, any(award_description) AS _v"
                        " FROM analytics_ap_awards"
                        " WHERE award_year = %s AND award_description = %s"
                        " GROUP BY ssid"
                        " ) _sort ON _sort._k = cs.ssid"
                    )
                    order_by_expr = "nullIf(_sort._v, '')"
                    params.extend([ap_info['year'], ap_info['award']])
            elif sort_by.startswith('sat_'):
                sat_info = next((i for i in sat_infos if i['col_name'] == sort_by), None)
                if sat_info:
                    sat_db_field = sat_column_db_field(sort_by)
                    if sat_db_field:
                        sort_join_sql = (
                            " LEFT JOIN ("
                            f" SELECT ssid AS _k, {sat_db_field} AS _v"
                            " FROM analytics_sat_results"
                            " WHERE school_year = %s"
                            " ) _sort ON _sort._k = cs.ssid"
                        )
                        order_by_expr = "_sort._v"
                        params.append(sat_info['year'])

        sort_dir = 'desc' if sort_dir == 'desc' else 'asc'
        # Put blank/missing assessment values at the end for both directions.
        # Otherwise DESC on sparse columns (Initial ELPAC, AP slot N) fills the
        # first page with empty rows and looks like sorting is broken.
        is_dynamic_sort = bool(sort_by) and (
            sort_by in test_col_map
            or sort_by in mark_col_map
            or sort_by.startswith(('test_', 'mark_', 'survey_', 'ap_', 'apaward_', 'apawards_all_', 'apall_', 'sat_'))
        )
        order_nulls_sql = ' NULLS LAST' if is_dynamic_sort else ''
        # sort_join placeholders come before WHERE in the SQL, so they must
        # be prepended to the execute param list (not appended onto params).
        sort_params = params[len(filter_params):]
        page_where_sql = where_sql
        page_params = list(filter_params)
        expected_cursor_key = sort_by or 'student_name'
        use_keyset = bool(
            cursor_value
            and not is_dynamic_sort
            and cursor_value.get('k') == expected_cursor_key
        )
        if use_keyset:
            page_where_sql += keyset_clause(order_by_expr, sort_dir)
            page_params.extend([
                cursor_value['v'], cursor_value['v'], cursor_value['sak'],
            ])

        # Check Redis cache for sorted IDs
        # Include full params in key to ensure uniqueness for filters
        # Note: We rely on cache invalidation via TTL (5 mins) matching the count cache
        cache_key_components = {
            'sql_hash': hashlib.md5((where_sql + join_sql + sort_join_sql).encode()).hexdigest(),
            'params': [str(p) for p in list(filter_params) + list(sort_params)],
            'sync': cache.get('analytics:clickhouse_sync_version', 'legacy'),
            'sort': f"{sort_by}:{sort_dir}:nulls_last" if is_dynamic_sort else f"{sort_by}:{sort_dir}",
            'order': order_by_expr,
            'limit': limit,
            'offset': offset 
            , 'cursor': cursor_raw if cursor_value else ''
        }
        # Actually, for pagination optimization we should cache the FULL list of IDs for this filter+sort,
        # but fetching all IDs might be heavy. 
        # For now, let's cache the specific page result to speed up subsequent views of the same page/sort.
        
        page_cache_key = f"dataexp:page:{hashlib.md5(json.dumps(cache_key_components, sort_keys=True).encode()).hexdigest()}"
        cached_ssids = cache.get(page_cache_key)

        if cached_ssids:
            # HIT: Retrieve students by SSID from DB (unordered), then reorder in Python
            # This avoids the expensive JOIN/SORT query provided we just fetch by ID.
            
            # We must still fetch the data columns...
            # Construct a simpler query for retrieving known SSIDs
            placeholders = ",".join(["%s"] * len(cached_ssids))
            if not placeholders:
                 columns = []
                 students = []
            else:
                id_fetch_query = f"""
                SELECT {base_col_sql}
                FROM current_student cs
                {join_sql}
                WHERE cs.ssid IN ({placeholders})
                """
                # We need to recreate params ONLY for join columns (if any) + SSIDs
                # This is tricky because join_sql doesn't have params here (it joins on fields).
                # But wait, join_sql is just string.
                # However base_col_expressions might have params (unlikely for base columns).
                # To be safe, re-running the full query w/ cached result is not what we want.
                
                # Simpler approach: If we have SSIDs, we can just fetch those students.
                # But we need their dynamic columns too.
                # The `test_data` fetching below relies on `student_ids` or `student_keys` derived from `students`.
                
                # So:
                # 1. Fetch base row data for these SSIDs.
                # 2. Sort them in Python to match the cached order.
                # 3. Proceed to fetch Test/Mark data as usual.
                
                cached_exec = QueryExecutor(analytics=True)
                sy_clauses = []
                sy_params = []
                _append_school_year_filter(sy_clauses, sy_params, school_year)
                sy_sql = (' AND ' + ' AND '.join(sy_clauses)) if sy_clauses else ''
                cached_result = cached_exec.execute(
                    f"SELECT {base_col_sql} FROM current_student cs {join_sql} "
                    f"WHERE cs.ssid = ANY(%s){sy_sql}",
                    [cached_ssids, *sy_params],
                )
                cols = cached_result['columns']
                unordered_students = {}
                for row_dict in cached_result['rows']:
                    unordered_students[row_dict.get('ssid')] = row_dict
                
                # Reconstruct ordered list
                students = []
                for ssid in cached_ssids:
                    if ssid in unordered_students:
                        students.append(unordered_students[ssid])
            
            # Set columns for zip from the cached execution context (simulated)
            columns = [c for c in base_col_expressions] # Approximate, but we used the cursor above.
             
        else:
            # MISS: Run expensive sort query
            query = f"""
            SELECT {base_col_sql}
            FROM current_student cs
            {join_sql}
            {sort_join_sql}
            WHERE {page_where_sql}
            ORDER BY {order_by_expr} {sort_dir}{order_nulls_sql}, cs.stu_annual_key {sort_dir}
            LIMIT %s
            """
            query_params = list(sort_params) + list(page_params)
            query_params.append(limit)
            if not use_keyset:
                query = query.rstrip() + " OFFSET %s\n"
                query_params.append(offset)
            
            main_exec = QueryExecutor(analytics=True)
            main_result = main_exec.execute(query, query_params)
            empty_page = (not main_result.get('rows')) and total and offset < total
            if main_result.get('error') or empty_page:
                logger.error(
                    "Explorer page query failed: error=%s empty=%s",
                    main_result.get('error'), empty_page,
                )
                # ponytail: keep the list populated if the sort join fails
                query = f"""
                SELECT {base_col_sql}
                FROM current_student cs
                {join_sql}
                WHERE {where_sql}
                ORDER BY cs.student_name {sort_dir}, cs.stu_annual_key {sort_dir}
                LIMIT %s
                """
                query_params = list(filter_params)
                query_params.append(limit)
                if not use_keyset:
                    query = query.rstrip() + " OFFSET %s\n"
                    query_params.append(offset)
                main_result = main_exec.execute(query, query_params)
            columns = main_result['columns']
            students = main_result['rows']
            
            # Cache the SSIDs of this result page
            result_ssids = [s.get('ssid') for s in students if s.get('ssid')]
            if result_ssids:
                cache.set(page_cache_key, result_ssids, 300)

        # Proceed with Detail Fetching (Test/Marks) for the identified students
        student_ids = [s.get('student_id') for s in students if s.get('student_id')]
        student_keys = [s.get('stu_annual_key') for s in students if s.get('stu_annual_key')]

        enrich_students_tests_marks(students, test_cols_info, mark_cols_info)

        # Fetch Marks "All Marks" wide-pair data (markall_<year>_<subject>_<i>_COURSE / _MARKS)
        if markall_years and students:
            try:
                student_ssids_all = [str(s.get('ssid') or '').strip() for s in students if str(s.get('ssid') or '').strip()]
                student_ids_all = [str(s.get('student_id') or '').strip() for s in students if str(s.get('student_id') or '').strip()]
                if student_ssids_all or student_ids_all:
                    for (year, subject), umbrella in markall_years.items():
                        n = markall_max.get((year, subject), 0)
                        excludes = _markall_course_excludes(year, subject)
                        live_rows = _fetch_marks_live_from_alt(
                            student_ssids_all, student_ids_all, [year],
                        )
                        by_ssid, by_student_id = group_marks_by_ssid_course(
                            live_rows,
                            subject=subject,
                            course_filter_fn=markall_course_filter_fn(subject, excludes),
                        )
                        for student in students:
                            ssid_key = str(student.get('ssid') or '').strip()
                            sid_key = str(student.get('student_id') or '').strip()
                            courses = by_ssid.get(ssid_key) or by_student_id.get(sid_key, {})
                            for i in range(1, n + 1):
                                course_key = f"{umbrella}_{i}_COURSE"
                                marks_key = f"{umbrella}_{i}_MARKS"
                                if i <= len(courses):
                                    course_name = list(courses.keys())[i - 1]
                                    periods = sorted(
                                        courses[course_name],
                                        key=lambda x: (1, str(x[0])) if str(x[0] or '').lower() == 'live' else (0, str(x[0]))
                                    )
                                    marks_str = ', '.join(
                                        f"{p}: {m}" for p, m in periods if p and m
                                    )
                                    col_filters = filters.get(marks_key) if isinstance(filters, dict) else None
                                    if col_filters:
                                        sel_periods = [str(v).split(':', 1)[1] for v in col_filters if str(v).startswith('period:')]
                                        sel_marks = [str(v).split(':', 1)[1] for v in col_filters if str(v).startswith('mark:')]
                                        if sel_periods or sel_marks:
                                            kept = []
                                            for p, m in periods:
                                                if not p or not m:
                                                    continue
                                                if sel_periods and p not in sel_periods:
                                                    continue
                                                if sel_marks and m not in sel_marks:
                                                    continue
                                                kept.append(f"{p}: {m}")
                                            marks_str = ', '.join(kept)
                                    student[course_key] = course_name
                                    student[marks_key] = marks_str or '\u2014'
                                else:
                                    student[course_key] = '\u2014'
                                    student[marks_key] = '\u2014'
            except Exception:
                pass

        # Fetch AP (College Board) data in bulk for selected AP columns
        if (ap_exam_infos or ap_award_infos) and students:
            try:
                from dataviz.utils.data_loader import _fetch_ap_exam_data, _fetch_ap_award_data
                if ap_exam_infos:
                    _fetch_ap_exam_data(students, ap_exam_infos)
                if ap_award_infos:
                    _fetch_ap_award_data(students, ap_award_infos)
                # Ensure every student has an em-dash (not the raw column key) when
                # they have no AP result for that subject/year.
                for student in students:
                    for _info in ap_exam_infos + ap_award_infos:
                        student.setdefault(_info['col_name'], '\u2014')
            except Exception:
                pass

        # Fetch SAT (College Board) score/percentile data in bulk
        if sat_infos and students:
            try:
                from dataviz.utils.data_loader import _fetch_sat_data
                _fetch_sat_data(students, sat_infos)
            except Exception:
                pass

        # Fetch AP "All Results" wide-pair data (apall_<year>_<i>_SUBJECT / _PL)
        if apall_years and students:
            try:
                ssids = [str(s.get('ssid') or '').strip() for s in students if str(s.get('ssid') or '').strip()]
                if ssids:
                    from dataviz.utils.data_loader import _analytics_rows
                    for year in apall_years:
                        yk = year.replace('-', '_')
                        n = apall_max.get(year, 0)
                        excludes = _apall_subject_excludes(year)
                        ap_rows = _analytics_rows(
                            "SELECT ssid, exam_name, exam_grade FROM analytics_ap_results "
                            "WHERE ssid = ANY(%s) AND admin_year = %s "
                            "ORDER BY ssid, exam_name",
                            [ssids, year],
                        )
                        if ap_rows is None:
                            with connections['default'].cursor() as apall_cur:
                                apall_cur.execute(
                                    """
                                    SELECT r.ssid, e.name, r.exam_grade
                                    FROM "dataLoader_apexamresult" r
                                    INNER JOIN "dataLoader_apexamcode" e ON r.exam_id = e.code
                                    INNER JOIN "dataLoader_apimportlog" il ON r.import_log_id = il.id
                                    WHERE r.ssid = ANY(%s) AND r.admin_year = %s AND il.is_published = true
                                    ORDER BY r.ssid, e.name
                                    """,
                                    [ssids, year],
                                )
                                ap_rows = [
                                    {'ssid': r[0], 'exam_name': r[1], 'exam_grade': r[2]}
                                    for r in apall_cur.fetchall()
                                ]
                        by_ssid = {}
                        for row in ap_rows:
                            exam_name = row.get('exam_name')
                            if exam_name in excludes:
                                continue
                            by_ssid.setdefault(str(row.get('ssid') or '').strip(), []).append(
                                (exam_name, row.get('exam_grade'))
                            )
                        for student in students:
                            sid = str(student.get('ssid') or '').strip()
                            results = by_ssid.get(sid, [])
                            for i in range(1, n + 1):
                                subj_key = f"apall_{yk}_{i}_SUBJECT"
                                pl_key = f"apall_{yk}_{i}_PL"
                                if i <= len(results):
                                    student[subj_key] = results[i - 1][0]
                                    student[pl_key] = str(results[i - 1][1]).strip() if results[i - 1][1] else '\u2014'
                                else:
                                    student[subj_key] = '\u2014'
                                    student[pl_key] = '\u2014'
            except Exception:
                pass

        # Fetch survey data in bulk for selected survey columns
        survey_colors = {}  # Map column_key -> {'colors': {...}, 'scale_max': int}
        if selected_survey_cols and students:
            student_ids = [s.get('student_id') for s in students if s.get('student_id')]
            student_ssids = [s.get('ssid') for s in students if s.get('ssid')]
            if student_ids or student_ssids:
                survey_data = get_survey_data_for_students(student_ids=student_ids, ssids=student_ssids)
                
                # Get survey colors from survey_utils
                survey_info = get_survey_columns()
                survey_colors = survey_info.get('colors', {})
                
                # survey_data is {student_id / ssid: {col_key: value}}
                for student in students:
                    student_id = str(student.get('student_id') or '').strip()
                    ssid = str(student.get('ssid') or '').strip()
                    student_survey_data = (
                        survey_data.get(student_id)
                        or survey_data.get(ssid)
                        or {}
                    )
                    for col_key in selected_survey_cols:
                        student[col_key] = student_survey_data.get(col_key, '—')

        # Fast fetch elem_teacher_name if requested but not joined
        if 'elem_teacher_name' in base_cols and not needs_eht and student_keys:
            q_eht = """
                SELECT sl.stu_annual_key, regexp_replace(sl.staff_name, '^([^,]+),\\s*(\\w).*$', '\\1, \\2.')
                FROM schedule_list sl
                JOIN school sch ON sch.state_id = sl.state_school_id
                WHERE sl.stu_annual_key = ANY(%s)
                AND sl.period = '0'
                AND sch.school_group = 'Elementary School'
            """
            eht_map = {
                row[0]: row[1]
                for row in _fetchall_ch_or_pg(q_eht, [student_keys])
            }
            for student in students:
                key = student.get('stu_annual_key')
                if key:
                    student['elem_teacher_name'] = eht_map.get(key, '')

        # Ensure JSON serialization safety (e.g. Decimal fields like atendace_rate)
        for student in students:
            for key, value in list(student.items()):
                if isinstance(value, Decimal):
                    student[key] = float(value)
                elif isinstance(value, (datetime, date)):
                    student[key] = value.isoformat()

        # Build filter options for repeatable columns (with Redis caching)
        _filter_opts_t0 = time.perf_counter()
        filter_options = {}
        option_tasks = []

        # Add filter options for survey columns based on their response scale
        if selected_survey_cols:
            _survey_all_colors = survey_colors or get_survey_columns().get('colors', {})
            for _sv_col_key in selected_survey_cols:
                _sv_color_info = _survey_all_colors.get(_sv_col_key, {})
                _sv_scale_max = _sv_color_info.get('scale_max', 5)
                filter_options[_sv_col_key] = list(range(1, _sv_scale_max + 1))

        if total > 0:
            excluded_filters = {'ssid', 'student_id', 'student_name'}
            always_include = {'school'}
            
            base_filter_state = {
                'search': search or '',
                'school_year': school_year or '',
                'grade': grade or '',
                'status': status or '',
            }

            def _normalize_filter_value(value):
                if isinstance(value, (list, tuple, set)):
                    return sorted([str(v) for v in value if v is not None])
                return value

            # Create cache key based on active filters (excluding current column)
            def _get_filter_cache_key(column, active_filters):
                normalized_filters = {}
                if isinstance(active_filters, dict):
                    for key, val in active_filters.items():
                        normalized_filters[key] = _normalize_filter_value(val)

                payload = {
                    'base': base_filter_state,
                    'filters': normalized_filters or "no_filters",
                    'sync': cache.get('analytics:clickhouse_sync_version', 'legacy'),
                }
                filter_str = json.dumps(payload, sort_keys=True)
                return f"filter_opts:{column}:{hashlib.md5(filter_str.encode()).hexdigest()}"
            
            for col in base_cols:
                if col in excluded_filters:
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
                
                # Cache miss - queue the query (executed concurrently below)
                option_where = list(base_where_clauses)
                option_params = list(base_params)
                apply_column_filters(option_where, option_params, other_filters, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll, needs_students, False, ap_exam_infos, ap_award_infos)
                option_where_sql = " AND ".join(option_where) if option_where else "1=1"

                json_key = json_list_key_for_column(col)
                if json_key:
                    unnest_expr = json_list_unnest_select_expr(json_key, 'cs')
                    options_query = f"""
                        SELECT DISTINCT v FROM (
                            SELECT {unnest_expr} AS v
                            FROM current_student cs
                            {join_sql}
                            WHERE {option_where_sql}
                        ) _json_list_opts
                        WHERE v IS NOT NULL AND BTRIM(v) != ''
                        ORDER BY v ASC
                        LIMIT 100
                    """
                    option_tasks.append((col, cache_key, _make_json_option_task(options_query, option_params)))
                    continue

                expr = base_col_expressions.get(col)
                if not expr:
                    continue

                limit_clause = "" if col in always_include else "LIMIT 31"

                options_query = f"""
                    SELECT {expr}
                    FROM current_student cs
                    {join_sql}
                    WHERE {option_where_sql}
                    GROUP BY {expr}
                    ORDER BY {expr} ASC NULLS FIRST
                    {limit_clause}
                """
                option_tasks.append((
                    col, cache_key,
                    _make_base_option_task(options_query, option_params, col, always_include),
                ))

            # Add filter options for test PL columns, dfp, and other_info columns (with caching)
            for test_info in test_cols_info:
                if test_info['metric'] not in {'pl', 'oi', 'dfp'}:
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
                apply_column_filters(option_where, option_params, other_filters, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll, False, False, ap_exam_infos, ap_award_infos)
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
                    INNER JOIN current_student cs ON {RESULTS_STUDENT_MATCH_SQL}
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
                    options_params = option_params + [
                        test_info['year'],
                        test_info['test_name'],
                        test_info['subject'],
                        *window_params,
                    ]
                else:
                    options_query = f"""
                    SELECT (r.other_info ->> %s) AS v
                    FROM results r
                    INNER JOIN tests t ON r.test_group_key = t.test_group_key
                    INNER JOIN current_student cs ON {RESULTS_STUDENT_MATCH_SQL}
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
                    options_params = [
                        other_key,
                        *option_params,
                        test_info['year'],
                        test_info['test_name'],
                        test_info['subject'],
                        *window_params,
                    ]

                # Check if there are any students with NO result record (Blank).
                # Anti-join: ClickHouse rejects correlated EXISTS that read outer cs.ssid.
                check_blank_query = f"""
                    SELECT 1
                    FROM current_student cs
                    {join_sql}
                    LEFT JOIN (
                        SELECT r.ssid, r.local_student_id
                        FROM results r
                        INNER JOIN tests t ON r.test_group_key = t.test_group_key
                        WHERE r.school_year = %s
                          AND t.test_name ILIKE %s
                          AND t.subject ILIKE %s
                          {window_clause}
                    ) _r ON (
                        (_r.ssid IS NOT NULL AND _r.ssid != '' AND _r.ssid != 'NaN'
                         AND cs.ssid IS NOT NULL AND cs.ssid != '' AND _r.ssid = cs.ssid)
                        OR ((_r.ssid IS NULL OR _r.ssid = '' OR _r.ssid = 'NaN')
                            AND _r.local_student_id = cs.student_id)
                    )
                    WHERE {option_where_sql}
                      AND _r.ssid IS NULL AND _r.local_student_id IS NULL
                    LIMIT 1
                """
                check_params = [
                    test_info['year'],
                    test_info['test_name'],
                    test_info['subject'],
                    *window_params,
                ] + option_params
                option_tasks.append((
                    col_key, cache_key,
                    _make_options_with_blank_task(
                        options_query, options_params, check_blank_query, check_params),
                ))

            # Add filter options for mark columns
            for mark_info in mark_cols_info:
                col_key = mark_info['col_name']

                if isinstance(filters, dict) and col_key in filters:
                    other_filters = {k: v for k, v in filters.items() if k != col_key}
                else:
                    other_filters = filters

                cache_key = _get_filter_cache_key(col_key, other_filters)
                cached_values = cache.get(cache_key)
                if cached_values is not None:
                    filter_options[col_key] = cached_values
                    continue

                option_where = list(base_where_clauses)
                option_params = list(base_params)
                apply_column_filters(option_where, option_params, other_filters, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll, False, False, ap_exam_infos, ap_award_infos)
                option_where_sql = " AND ".join(option_where) if option_where else "1=1"

                period_clause = ""
                period_params = []
                if mark_info.get('mark_period'):
                    period_clause = "AND sm.mark_period ILIKE %s"
                    period_params = [mark_info['mark_period']]
                else:
                    period_clause = "AND (sm.mark_period IS NULL OR sm.mark_period = '')"

                options_query = f"""
                    SELECT sm.mark AS v
                    FROM student_marks_list sm
                    INNER JOIN current_student cs ON sm.ssid = cs.ssid
                    {join_sql}
                    WHERE {option_where_sql}
                      AND sm.school_year = %s
                      AND sm.subject ILIKE %s
                      AND sm.course_name ILIKE %s
                      {period_clause}
                    GROUP BY v
                    ORDER BY v ASC NULLS FIRST
                    LIMIT 30
                """

                option_tasks.append((
                    col_key, cache_key,
                    _make_options_task(options_query, option_params + [
                        mark_info['year'],
                        f"%{mark_info['subject']}%",
                        f"%{mark_info['course_name']}%",
                        *period_params,
                    ]),
                ))

            # Add filter options for Marks "All Marks" wide-pair columns
            for (year, subject), umbrella in markall_years.items():
                n = markall_max.get((year, subject), 0)
                course_filter_sql, course_extra_params = _markall_course_sql_parts(
                    subject, _markall_course_excludes(year, subject)
                )
                for i in range(1, n + 1):
                    course_col = f"{umbrella}_{i}_COURSE"
                    marks_col = f"{umbrella}_{i}_MARKS"

                    for col_key, is_marks in ((course_col, False), (marks_col, True)):
                        if isinstance(filters, dict) and col_key in filters:
                            other_filters = {k: v for k, v in filters.items() if k != col_key}
                        else:
                            other_filters = filters

                        cache_key = _get_filter_cache_key(col_key, other_filters)
                        cached_values = cache.get(cache_key)
                        if cached_values is not None:
                            filter_options[col_key] = cached_values
                            continue

                        option_where = list(base_where_clauses)
                        option_params = list(base_params)
                        apply_column_filters(option_where, option_params, other_filters, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll, False, False, ap_exam_infos, ap_award_infos)
                        option_where_sql = " AND ".join(option_where) if option_where else "1=1"

                        if is_marks:
                            # Distinct marking periods + marks (prefixed).
                            period_query = f"""
                                SELECT DISTINCT CONCAT('period:', sm.mark_period) AS v
                                FROM student_marks_list sm
                                INNER JOIN current_student cs ON sm.ssid = cs.ssid
                                {join_sql}
                                WHERE {option_where_sql}
                                  AND sm.school_year = %s AND sm.subject = %s
                                  {course_filter_sql}
                                  AND sm.mark_period IS NOT NULL AND sm.mark_period <> ''
                                ORDER BY v ASC NULLS FIRST
                                LIMIT 50
                            """
                            mark_query = f"""
                                SELECT DISTINCT CONCAT('mark:', sm.mark) AS v
                                FROM student_marks_list sm
                                INNER JOIN current_student cs ON sm.ssid = cs.ssid
                                {join_sql}
                                WHERE {option_where_sql}
                                  AND sm.school_year = %s AND sm.subject = %s
                                  {course_filter_sql}
                                  AND sm.mark IS NOT NULL AND sm.mark <> ''
                                ORDER BY v ASC NULLS FIRST
                                LIMIT 50
                            """
                            option_tasks.append((
                                col_key, cache_key,
                                _make_markall_marks_task(
                                    period_query, mark_query,
                                    option_params + [year, subject] + course_extra_params),
                            ))
                        else:
                            # Distinct course names
                            options_query = f"""
                                SELECT DISTINCT sm.course_name AS v
                                FROM student_marks_list sm
                                INNER JOIN current_student cs ON sm.ssid = cs.ssid
                                {join_sql}
                                WHERE {option_where_sql}
                                  AND sm.school_year = %s AND sm.subject = %s
                                  {course_filter_sql}
                                  AND sm.course_name IS NOT NULL AND sm.course_name <> ''
                                ORDER BY v ASC NULLS FIRST
                                LIMIT 50
                            """
                            option_tasks.append((
                                col_key, cache_key,
                                _make_options_task(
                                    options_query,
                                    option_params + [year, subject] + course_extra_params,
                                    use_pg=True),
                            ))

            # Add filter options for AP "All Results" wide-pair columns
            for year in apall_years:
                yk = year.replace('-', '_')
                n = apall_max.get(year, 0)
                excludes = _apall_subject_excludes(year)
                exclude_clause = ""
                exclude_params = []
                if excludes:
                    placeholders = ", ".join(["%s"] * len(excludes))
                    exclude_clause = f" AND e.name NOT IN ({placeholders})"
                    exclude_params = sorted(excludes)
                for i in range(1, n + 1):
                    for col_key, field in (
                        (f"apall_{yk}_{i}_SUBJECT", "exam_name"),
                        (f"apall_{yk}_{i}_PL", "exam_grade"),
                    ):
                        if isinstance(filters, dict) and col_key in filters:
                            other_filters = {k: v for k, v in filters.items() if k != col_key}
                        else:
                            other_filters = filters

                        cache_key = _get_filter_cache_key(col_key, other_filters)
                        cached_values = cache.get(cache_key)
                        if cached_values is not None:
                            filter_options[col_key] = cached_values
                            continue

                        option_where = list(base_where_clauses)
                        option_params = list(base_params)
                        apply_column_filters(option_where, option_params, other_filters, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll, False, False, ap_exam_infos, ap_award_infos)
                        option_where_sql = " AND ".join(option_where) if option_where else "1=1"

                        rank_subquery = f"""
                            SELECT a.ssid, e.name AS exam_name, a.exam_grade,
                                   row_number() OVER (PARTITION BY a.ssid ORDER BY e.name) AS rn
                            FROM "dataLoader_apexamresult" a
                            INNER JOIN "dataLoader_apexamcode" e ON a.exam_id = e.code
                            INNER JOIN "dataLoader_apimportlog" il ON a.import_log_id = il.id
                            WHERE a.admin_year = %s AND il.is_published = true
                              {exclude_clause}
                        """

                        options_query = f"""
                            SELECT DISTINCT t.{field} AS v
                            FROM ({rank_subquery}) t
                            INNER JOIN current_student cs ON t.ssid = cs.ssid
                            {join_sql}
                            WHERE t.rn = %s
                              AND {option_where_sql}
                              AND t.{field} IS NOT NULL AND t.{field} <> ''
                            ORDER BY v ASC NULLS FIRST
                            LIMIT 50
                        """
                        options_params = [year] + exclude_params + [i] + option_params

                        # Blank when a filtered student has no i-th exam (or a blank field)
                        check_blank_query = f"""
                            SELECT 1
                            FROM current_student cs
                            {join_sql}
                            WHERE {option_where_sql}
                            AND NOT EXISTS (
                                SELECT 1 FROM ({rank_subquery}) t
                                WHERE t.ssid = cs.ssid AND t.rn = %s
                                  AND t.{field} IS NOT NULL AND t.{field} <> ''
                            )
                            LIMIT 1
                        """
                        check_params = option_params + [year] + exclude_params + [i]
                        option_tasks.append((
                            col_key, cache_key,
                            _make_options_with_blank_task(
                                options_query, options_params,
                                check_blank_query, check_params, use_pg=True),
                        ))

            # Add filter options for AP exam grades (PL columns)
            for ap_info in ap_exam_infos:
                col_key = ap_info['col_name']

                if isinstance(filters, dict) and col_key in filters:
                    other_filters = {k: v for k, v in filters.items() if k != col_key}
                else:
                    other_filters = filters

                cache_key = _get_filter_cache_key(col_key, other_filters)
                cached_values = cache.get(cache_key)
                if cached_values is not None:
                    filter_options[col_key] = cached_values
                    continue

                option_where = list(base_where_clauses)
                option_params = list(base_params)
                apply_column_filters(option_where, option_params, other_filters, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll, False, False, ap_exam_infos, ap_award_infos)
                option_where_sql = " AND ".join(option_where) if option_where else "1=1"

                options_query = f"""
                    SELECT DISTINCT a.exam_grade AS v
                    FROM "dataLoader_apexamresult" a
                    INNER JOIN "dataLoader_apexamcode" e ON a.exam_id = e.code
                    INNER JOIN "dataLoader_apimportlog" il ON a.import_log_id = il.id
                    INNER JOIN current_student cs ON a.ssid = cs.ssid
                    {join_sql}
                    WHERE {option_where_sql}
                      AND a.admin_year = %s
                      AND e.name = %s
                      AND il.is_published = true
                      AND a.exam_grade IS NOT NULL AND a.exam_grade <> ''
                    ORDER BY v ASC NULLS FIRST
                    LIMIT 30
                """
                options_params = option_params + [
                    ap_info['year'],
                    ap_info['subject'],
                ]

                # Check if there are students with NO AP result for this subject/year (Blank)
                check_blank_query = f"""
                    SELECT 1
                    FROM current_student cs
                    {join_sql}
                    WHERE {option_where_sql}
                    AND NOT EXISTS (
                        SELECT 1 FROM "dataLoader_apexamresult" a
                        INNER JOIN "dataLoader_apexamcode" e ON a.exam_id = e.code
                        INNER JOIN "dataLoader_apimportlog" il ON a.import_log_id = il.id
                        WHERE a.ssid = cs.ssid
                          AND a.admin_year = %s
                          AND e.name = %s
                          AND il.is_published = true
                    )
                    LIMIT 1
                """
                option_tasks.append((
                    col_key, cache_key,
                    _make_options_with_blank_task(
                        options_query, options_params,
                        check_blank_query, options_params),
                ))

            # Add filter options for AP award columns
            for ap_info in ap_award_infos:
                col_key = ap_info['col_name']

                if isinstance(filters, dict) and col_key in filters:
                    other_filters = {k: v for k, v in filters.items() if k != col_key}
                else:
                    other_filters = filters

                cache_key = _get_filter_cache_key(col_key, other_filters)
                cached_values = cache.get(cache_key)
                if cached_values is not None:
                    filter_options[col_key] = cached_values
                    continue

                option_where = list(base_where_clauses)
                option_params = list(base_params)
                apply_column_filters(option_where, option_params, other_filters, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll, False, False, ap_exam_infos, ap_award_infos)
                option_where_sql = " AND ".join(option_where) if option_where else "1=1"

                if ap_info['award'] == '__ALL__':
                    # Combined "All Awards" column: distinct award descriptions for the year
                    options_query = f"""
                        SELECT DISTINCT t.description AS v
                        FROM "dataLoader_apstudentaward" a
                        INNER JOIN "dataLoader_apawardtype" t ON a.award_type_id = t.code
                        INNER JOIN "dataLoader_apimportlog" il ON a.import_log_id = il.id
                        INNER JOIN current_student cs ON a.ssid = cs.ssid
                        {join_sql}
                        WHERE {option_where_sql}
                          AND a.award_year = %s
                          AND il.is_published = true
                        ORDER BY v ASC NULLS FIRST
                        LIMIT 30
                    """
                    option_tasks.append((
                        col_key, cache_key,
                        _make_options_task(options_query, option_params + [ap_info['year']]),
                    ))
                else:
                    # Individual award column: single award description
                    options_query = f"""
                        SELECT DISTINCT t.description AS v
                        FROM "dataLoader_apstudentaward" a
                        INNER JOIN "dataLoader_apawardtype" t ON a.award_type_id = t.code
                        INNER JOIN "dataLoader_apimportlog" il ON a.import_log_id = il.id
                        INNER JOIN current_student cs ON a.ssid = cs.ssid
                        {join_sql}
                        WHERE {option_where_sql}
                          AND a.award_year = %s
                          AND t.description = %s
                          AND il.is_published = true
                        ORDER BY v ASC NULLS FIRST
                        LIMIT 30
                    """
                    options_params = option_params + [ap_info['year'], ap_info['award']]

                    # Check for students with NO award record for this year (Blank)
                    check_blank_query = f"""
                        SELECT 1
                        FROM current_student cs
                        {join_sql}
                        WHERE {option_where_sql}
                        AND NOT EXISTS (
                            SELECT 1 FROM "dataLoader_apstudentaward" a
                            INNER JOIN "dataLoader_apimportlog" il ON a.import_log_id = il.id
                            WHERE a.ssid = cs.ssid
                              AND a.award_year = %s
                              AND il.is_published = true
                        )
                        LIMIT 1
                    """
                    option_tasks.append((
                        col_key, cache_key,
                        _make_options_with_blank_task(
                            options_query, options_params,
                            check_blank_query, option_params + [ap_info['year']]),
                    ))

            # Execute all queued columns concurrently and merge the results.
            for col_key, cache_key, values in _run_option_tasks(option_tasks):
                if values:
                    filter_options[col_key] = values
                    cache.set(cache_key, values, 300)

    logger.info(
        'request_id=%s op=dataexp_filter_options columns=%s elapsed_ms=%.1f',
        current_request_id(), len(filter_options),
        (time.perf_counter() - _filter_opts_t0) * 1000,
    )

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
    # Always merge survey column definitions (full question text for column headers)
    dynamic_definitions.update(get_survey_columns().get('definitions', {}))
    if test_cols_info:
        for info in test_cols_info:
            metric_map = {'ss': 'Scale Score', 'pl': 'Perf Lvl', 'dfp': 'DFP', 'oi': info.get('other_info_key', 'Info')}
            metric_label = metric_map.get(info['metric'], info['metric'])
            display_test = _get_display_name(info['test_name'])
            display_subject = _get_display_name(info['subject'])
            label = f"{display_test} {display_subject} ({info['year']}) {metric_label}"
            if info.get('test_window'):
                label += f"\n{info['test_window']}"
            dynamic_definitions[info['col_name']] = label

    if mark_cols_info:
        for info in mark_cols_info:
            period = info.get('mark_period') or 'Unknown'
            label = f"{info['course_name']} ({info['subject']}) {period}\n{info['year']}"
            dynamic_definitions[info['col_name']] = label

    # Definitions for the AP "All Results" wide-pair columns. Only number the
    # sets when a student can have more than one AP exam that year.
    for year in apall_years:
        yk = year.replace('-', '_')
        n = apall_max.get(year, 0)
        for i in range(1, n + 1):
            suffix = f" {i}" if n > 1 else ""
            dynamic_definitions[f"apall_{yk}_{i}_SUBJECT"] = f"AP Assessment{suffix}\n{year}"
            dynamic_definitions[f"apall_{yk}_{i}_PL"] = f"AP Results{suffix}\n{year}"

    # Definitions for the Marks "All Marks" wide-pair columns
    for (year, subject), umbrella in markall_years.items():
        n = markall_max.get((year, subject), 0)
        for i in range(1, n + 1):
            dynamic_definitions[f"{umbrella}_{i}_COURSE"] = f"Course {i} ({subject})\n{year}"
            dynamic_definitions[f"{umbrella}_{i}_MARKS"] = f"Marks {i} ({subject})\n{year}"

    if request.GET.get('format') == 'json':
        next_cursor = ''
        if students and not is_dynamic_sort:
            next_cursor = cursor_from_row(
                students[-1], sort_by or 'student_name', sort_dir,
            )
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
            'school_year': _serialize_context_filter(school_year),
            'grade': _serialize_context_filter(grade),
            'status': _serialize_context_filter(status),
            'sort_by': sort_by,
            'sort_dir': sort_dir,
            'next_cursor': next_cursor,
            'survey_colors': survey_colors,
        })

    context = {
        'students': students,
        'selected_columns': selected_columns,
        'total': total,
        'page': page,
        'total_pages': total_pages,
        'column_definitions': dynamic_definitions,
        'survey_colors': survey_colors,
    }

    return render(request, 'dataexploration/partials/student_table.html', context)


def warm_data_explorer_default_cache():
    """Fill Redis for default Data Explorer queries and filter options after ClickHouse sync."""
    warmed = 0
    try:
        from django.test import RequestFactory
        from django.contrib.auth import get_user_model
        User = get_user_model()
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            admin_user = User.objects.first()
        if not admin_user:
            return 0

        factory = RequestFactory()
        req = factory.get('/dataexploration/api/students/', {'format': 'json', 'page': '1'})
        req.user = admin_user
        resp = _load_students(req)
        if resp.status_code == 200:
            warmed += 1
    except Exception:
        logger.exception("warm_data_explorer_default_cache failed")
    return warmed

