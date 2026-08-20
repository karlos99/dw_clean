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

from clickhouse_utils.executor import QueryExecutor
from ..models import SavedQuery, StudentList
from studata.models import CurrentStudent
from ..column_utils import (
    get_test_columns, get_mark_columns, get_ap_columns, _get_display_name,
    RESULTS_STUDENT_MATCH_SQL, RESULTS_BULK_MATCH_SQL, RESULTS_BLANK_SSID_SQL,
    assign_result_value_maps, lookup_result_value,
)
from ..survey_utils import get_survey_columns, get_survey_data_for_students
from ..constants import COLUMN_DEFINITIONS, COLUMN_CATEGORIES
from ..utils import can_user_view_query, get_user_folders, is_unrestricted as _is_user_unrestricted
from .student_data_utils import (
    get_current_student_columns,
    get_student_enroll_columns,
)
from .column_management import (
    get_saved_default_columns,
    get_column_expression,
)
from ..school_year_utils import get_school_year_choices, get_year_labels_map, get_year_alias_map, resolve_relative_column_key
from landing.utils import get_project_config

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


def _normalize_filter_param(col, value):
    """Coerce filter values to native Python types for typed columns."""
    if value in (None, ''):
        return value

    if col in {'birth_date', 'us_enter_date', 'fep_date'}:
        if isinstance(value, (datetime, date)):
            return value
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return value
            try:
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError:
                try:
                    parsed = datetime.combine(date.fromisoformat(value), datetime.min.time())
                except ValueError:
                    return value
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed

    return value


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
        all_years = list(test_structure.keys()) + list(marks_structure.keys()) + list(ap_structure.keys())
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
    # The `apall_<year>` umbrella key expands into wide subject/PL pairs
    # (apall_<year>_1_SUBJECT, apall_<year>_1_PL, ...) one pair per result.
    # Selecting it also unselects the individual ap_<year>_*_PL columns.
    def _apall_year_key(col):
        if not isinstance(col, str) or not col.startswith('apall_'):
            return None
        parts = col[len('apall_'):].split('_')
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{parts[0]}_{parts[1]}"
        return None

    apall_years = []
    for c in selected_columns:
        yk = _apall_year_key(c)
        if yk and yk.replace('_', '-') not in apall_years:
            apall_years.append(yk.replace('_', '-'))

    apall_max = {}
    if apall_years:
        with connections['default'].cursor() as apall_cursor:
            for year in apall_years:
                apall_cursor.execute(
                    """
                    SELECT COALESCE(MAX(cnt), 0)
                    FROM (
                        SELECT r.ssid, COUNT(*) AS cnt
                        FROM "dataLoader_apexamresult" r
                        JOIN "dataLoader_apimportlog" il ON r.import_log_id = il.id
                        WHERE r.admin_year = %s AND il.is_published = true
                        GROUP BY r.ssid
                    ) t
                    """,
                    [year],
                )
                apall_max[year] = min(int(apall_cursor.fetchone()[0] or 0), 10)

        new_selected = []
        for c in selected_columns:
            # Unselect individual AP subject PL columns for expanded years
            if isinstance(c, str) and c.startswith('ap_') and not c.startswith('apaward_') and not c.startswith('apawards_all_') and not c.startswith('apall_'):
                tail = c[3:]
                parts = tail.split('_')
                if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                    yr = f"{parts[0]}-{parts[1]}"
                    if yr in apall_years:
                        continue
            # Expand umbrella / already-expanded keys into wide pairs
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

    def _markall_excluded_course_sql():
        """SQL predicate excluding Homeroom courses for the Elementary subject.

        Homeroom courses (e.g. "Homeroom Gr 1", "IS Homeroom Gr KN-5") are
        administrative placeholder courses and are filtered out of the
        Elementary subject's "All Marks" view.
        """
        return "(sm.course_name NOT ILIKE '%%Homeroom%%')"

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
    elif _project_default_year:
        school_year = _project_default_year
    else:
        school_year = _parse_multi(
            saved_filters.get('school_year', ''),
            _parse_multi(saved_config.get('school_year', ''), '')
        )

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

    def _apply_column_filters(where_clauses, params, filters_to_apply, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll, needs_students=False, needs_eht=False):
        """Append SQL filter clauses + params for the provided filters dict."""
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
                        if not val_str: # Blank
                            conditions.append(f"({expr} IS NULL)")
                            continue
                        
                        # Check for operators: <, <=, >, >=, = followed by number
                        match = re.match(r'^([<>]=?|=)?\s*(\d+(?:\.\d+)?)$', val_str)
                        if match:
                            op = match.group(1) or '='
                            num = match.group(2)
                            conditions.append(f"{expr} {op} %s")
                            params.append(num)
                            continue
                        
                        # Check for range: num - num
                        match_range = re.match(r'^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)$', val_str)
                        if match_range:
                            low, high = match_range.groups()
                            conditions.append(f"{expr} BETWEEN %s AND %s")
                            params.extend([low, high])
                            continue
                        
                        # Fallback to equality
                        conditions.append(f"{expr} = %s")
                        params.append(val)
                    
                    if conditions:
                        where_clauses.append(f"({' OR '.join(conditions)})")
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
                base_params = [
                    test_info['year'],
                    test_info['test_name'],
                    test_info['subject'],
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
                    blank_subconditions.append(f"(cs.student_id NOT IN ({id_subquery_base}) AND cs.ssid NOT IN ({ssid_subquery_base}))")
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

            if col.startswith('mark_'):
                mark_info = mark_col_map.get(col) or mark_col_map.get(col.replace('-', '_'))
                if not mark_info:
                    continue

                conditions = []

                mark_subquery_base = (
                    "SELECT sm.stu_annual_key FROM student_marks_list sm "
                    "WHERE sm.school_year = %s "
                    "AND sm.subject ILIKE %s "
                    "AND sm.course_name ILIKE %s "
                )
                mark_base_params = [
                    mark_info['year'],
                    f"%{mark_info['subject']}%",
                    f"%{mark_info['course_name']}%",
                ]

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

            # AP exam performance-level columns (ap_<year>_<subject>_PL)
            if col.startswith('ap_') and not col.startswith('apaward_') and not col.startswith('apawards_all_'):
                ap_info = next((i for i in ap_exam_infos if i['col_name'] == col), None)
                if not ap_info:
                    continue
                year = ap_info['year']
                subject = ap_info['subject']
                sub_base = (
                    "SELECT a.ssid FROM \"dataLoader_apexamresult\" a "
                    "INNER JOIN \"dataLoader_apexamcode\" e ON a.exam_id = e.code "
                    "INNER JOIN \"dataLoader_apimportlog\" il ON a.import_log_id = il.id "
                    "WHERE a.admin_year = %s AND e.name = %s AND il.is_published = true "
                )
                sub_params = [year, subject]
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

            # Marks "All Marks" columns (markall_<year>_<subject>_<i>_COURSE / _MARKS)
            if col.startswith('markall_'):
                parsed = parse_markall_key(col)
                if not parsed:
                    continue
                year = parsed['year']
                subject = _markall_subject_actual(year, parsed['subject_norm'])
                is_marks_col = parsed['kind'] == 'MARKS'
                course_filter_sql = ""
                if subject == 'Elementary':
                    course_filter_sql = " AND " + _markall_excluded_course_sql()
                sub_base = (
                    "SELECT sm.stu_annual_key FROM student_marks_list sm "
                    "WHERE sm.school_year = %s AND sm.subject = %s "
                    f"{course_filter_sql}"
                )
                sub_params = [year, subject]
                conditions = []
                if real_values:
                    if is_marks_col:
                        # Filter by individual marking-period and mark selections.
                        # Values arrive prefixed ("period:Q1", "mark:B"). Selecting a
                        # period AND a mark overlaps (intersects): students must have a
                        # mark row matching any selected period AND any selected mark.
                        periods = [str(v).split(':', 1)[1] for v in real_values if str(v).startswith('period:')]
                        marks = [str(v).split(':', 1)[1] for v in real_values if str(v).startswith('mark:')]
                        if periods and marks:
                            val_clause = sub_base + "AND sm.mark_period = ANY(%s) AND sm.mark = ANY(%s)"
                            conditions.append(f"cs.stu_annual_key IN ({val_clause})")
                            params.extend(sub_params + [periods, marks])
                        elif periods:
                            val_clause = sub_base + "AND sm.mark_period = ANY(%s)"
                            conditions.append(f"cs.stu_annual_key IN ({val_clause})")
                            params.extend(sub_params + [periods])
                        elif marks:
                            val_clause = sub_base + "AND sm.mark = ANY(%s)"
                            conditions.append(f"cs.stu_annual_key IN ({val_clause})")
                            params.extend(sub_params + [marks])
                    else:
                        # COURSE column filter: match course name
                        val_clause = sub_base + "AND sm.course_name = ANY(%s)"
                        conditions.append(f"cs.stu_annual_key IN ({val_clause})")
                        params.extend(sub_params + [real_values])
                if has_blank:
                    blank_clause = sub_base + "AND sm.course_name IS NULL"
                    conditions.append(f"cs.stu_annual_key NOT IN ({sub_base})")
                    params.extend(sub_params + sub_params)
                if conditions:
                    where_clauses.append(f"({' OR '.join(conditions)})")
                continue

            # Standard columns
            expr, _ = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
            if expr:
                 conditions = []
                 if real_values:
                      normalized_values = [_normalize_filter_param(col, v) for v in real_values]
                      conditions.append(f"{expr} = ANY(%s)")
                      params.append(normalized_values)
                 if has_blank:
                      # Check if date (only IS NULL) or string (IS NULL or Empty)
                      if col in {'birth_date', 'us_enter_date', 'fep_date'}:
                           conditions.append(f"{expr} IS NULL")
                      else:
                           conditions.append(f"({expr} IS NULL OR {expr} = '')")
                 
                 if conditions:
                      where_clauses.append(f"({' OR '.join(conditions)})")

    with QueryExecutor().cursor() as cursor:
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

            mark_cols_cache_key = f"mark_cols:{hashlib.md5(json.dumps(sorted(selected_mark_cols)).encode()).hexdigest()}"
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

        # Check if user is unrestricted (Project Admin OR District Role)
        is_unrestricted = _is_user_unrestricted(request.user)

        # Apply active school context or enforce school permissions
        if hasattr(request.user, 'active_school') and request.user.active_school:
             # User explicitly selected a school (which is validated by middleware to be in their allowed list)
             base_where_clauses.append("cs.school_state_id = %s")
             base_params.append(request.user.active_school.state_id)
        else:
             if not is_unrestricted:
                 # No specific school selected, so limit to ALL assigned schools
                 if hasattr(request.user, 'schools') and isinstance(request.user.schools, list) and request.user.schools:
                      base_where_clauses.append("cs.school_state_id = ANY(%s)")
                      base_params.append(request.user.schools)
                 else:
                      # Non-admin user with no assigned schools sees nothing
                      base_where_clauses.append("1=0")

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
        base_cols = [
            c for c in selected_columns
            if isinstance(c, str)
            and (not c.startswith(('test_', 'mark_', 'markall_', 'survey_', 'ap_', 'apaward_', 'apawards_all_', 'apall_')))
            and c in COLUMN_DEFINITIONS
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

        # Determine if we need students join (for ltel overrides)
        students_fields = {'ltel'}
        needs_students = (
            any(c in students_fields for c in selected_columns)
            or (isinstance(filters, dict) and any(k in students_fields for k in filters.keys()))
            or sort_by in students_fields
        )

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
        _apply_column_filters(where_clauses, params, filters, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)

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
        count_cache_key = f"dataexp:count:{hashlib.md5(json.dumps({'where': where_clauses, 'params': [str(p) for p in params], 'join': join_sql}, sort_keys=True).encode()).hexdigest()}"
        total = cache.get(count_cache_key)
        if total is None:
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
            with connections['default'].cursor() as markall_cursor:
                for (year, subject), umbrella in markall_years.items():
                    course_filter_sql = ""
                    if subject == 'Elementary':
                        course_filter_sql = "AND " + _markall_excluded_course_sql()
                    markall_cursor.execute(
                        f"""
                        SELECT COALESCE(MAX(cnt), 0)
                        FROM (
                            SELECT sm.stu_annual_key, COUNT(DISTINCT sm.course_name) AS cnt
                            FROM student_marks_list sm
                            INNER JOIN current_student cs ON sm.stu_annual_key = cs.stu_annual_key
                            {join_sql}
                            WHERE {where_sql}
                              AND sm.school_year = %s AND sm.subject = %s
                              {course_filter_sql}
                            GROUP BY sm.stu_annual_key
                        ) t
                        """,
                        params + [year, subject],
                    )
                    markall_max[(year, subject)] = min(int(markall_cursor.fetchone()[0] or 0), 10)

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

        # Copy params for later reuse
        filter_params = list(params)
        
        # Get paginated results
        offset = (page - 1) * limit
        
        # Always include ssid for joining
        if 'ssid' not in base_cols:
            base_cols.insert(0, 'ssid')

        if (test_cols_info or selected_survey_cols) and 'student_id' not in base_cols:
            base_cols.insert(0, 'student_id')

        if (mark_cols_info or 'elem_teacher_name' in base_cols) and 'stu_annual_key' not in base_cols:
            base_cols.insert(0, 'stu_annual_key')

        # Marks "All Marks" fetch needs stu_annual_key to join marks per course
        if markall_years and 'stu_annual_key' not in base_cols:
            base_cols.insert(0, 'stu_annual_key')        # Build base column SELECT with safe mapping
        base_col_selects = []
        base_col_expressions = {}
        for col in base_cols:
            expr, alias = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
            if expr and alias:
                base_col_expressions[col] = expr
                if expr != f"cs.{alias}":
                    base_col_selects.append(f"{expr} AS {alias}")
                else:
                    base_col_selects.append(expr)

        if not base_col_selects:
            base_col_selects.append("cs.ssid") if 'ssid' in db_columns else base_col_selects.append("NULL AS ssid")

        base_col_sql = ", ".join(base_col_selects)

        # Sorting (Optimized with Redis Caching)
        order_by_expr = "cs.student_name"
        need_joined_sort = False
        
        if sort_by:
            expr = base_col_expressions.get(sort_by)
            if expr:
                order_by_expr = expr
            elif sort_by in mark_col_map:
                # Keep subquery for marks (less frequent)
                mark_info = mark_col_map.get(sort_by)
                period_clause = ""
                if mark_info.get('mark_period'):
                    period_clause = "AND sm.mark_period ILIKE %s"
                else:
                    period_clause = "AND (sm.mark_period IS NULL OR sm.mark_period = '')"

                order_by_expr = (
                    "(SELECT sm.mark FROM student_marks_list sm "
                    "WHERE sm.stu_annual_key = cs.stu_annual_key "
                    "AND sm.school_year = %s "
                    "AND sm.subject ILIKE %s "
                    "AND sm.course_name ILIKE %s "
                    f"{period_clause} "
                    "LIMIT 1)"
                )
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
                    order_by_expr = (
                        "(SELECT sr.value FROM \"dataLoader_surveyresponse\" sr "
                        "INNER JOIN \"dataLoader_surveyquestion\" sq ON sr.question_id = sq.id "
                        "INNER JOIN \"dataLoader_surveydefinition\" sd ON sq.survey_definition_id = sd.id "
                        "INNER JOIN \"dataLoader_importlog\" il ON sr.import_log_id = il.id "
                        "WHERE sr.student_id = cs.student_id "
                        "AND sd.survey_type = %s AND sd.school_year = %s "
                        "AND sd.testing_window = %s AND sd.grade_range = %s "
                        "AND sq.\"order\" = %s "
                        "AND il.ready_for_exploration = true "
                        "AND il.status = 'COMPLETED' "
                        "LIMIT 1)"
                    )
                    params.extend([
                        _sv_meta['survey_type'],
                        _sv_meta['school_year'],
                        _sv_meta['window'],
                        _sv_meta.get('grade_range', ''),
                        _sv_meta['question_order'],
                    ])
            elif sort_by in test_col_map:
                # Use subquery for now, but we'll cache the result ids
                test_info = test_col_map.get(sort_by)
                metric_col = test_info['metric']
                
                window_clause = ""
                window_params = []
                if test_info.get('test_window'):
                    window_clause = "AND t.test_window ILIKE %s"
                    window_params = [test_info['test_window']]
                
                select_expr = ""
                extra_params = []
                
                if metric_col == 'ss':
                     select_expr = "CAST(NULLIF(regexp_replace(r.ss, '[^0-9.]', '', 'g'), '') AS NUMERIC)"
                elif metric_col == 'pl':
                     select_expr = "CASE WHEN r.pl ~ '^[0-9]+$' THEN LPAD(r.pl, 10, '0') ELSE r.pl END"
                else:
                    select_expr = "(r.other_info ->> %s)"
                    extra_params = [test_info.get('other_info_key', '')]

                order_by_expr = (
                    f"(SELECT {select_expr} FROM results r "
                    "INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                    f"WHERE {RESULTS_STUDENT_MATCH_SQL} "
                    "AND r.school_year = %s "
                    "AND t.test_name ILIKE %s "
                    "AND t.subject ILIKE %s "
                    f"{window_clause} "
                    "LIMIT 1)"
                )
                params.extend(extra_params)
                params.extend([
                    test_info['year'],
                    test_info['test_name'],
                    test_info['subject'],
                ])
                params.extend(window_params)
            elif sort_by.startswith('apawards_all_'):
                # Combined "All Awards" column for a year — sort by the joined list.
                # Use PG dialect (string_agg) so the Postgres fallback works;
                # the QueryExecutor translator converts it to ClickHouse syntax
                # (arrayStringConcat(groupArray(...))) when ClickHouse is used.
                _yr = sort_by[len('apawards_all_'):].replace('_', '-')
                order_by_expr = (
                    "(SELECT string_agg(t.description, ', ') "
                    "FROM \"dataLoader_apstudentaward\" a "
                    "INNER JOIN \"dataLoader_apawardtype\" t ON a.award_type_id = t.code "
                    "INNER JOIN \"dataLoader_apimportlog\" il ON a.import_log_id = il.id "
                    "WHERE a.ssid = cs.ssid AND a.award_year = %s AND il.is_published = true)"
                )
                params.append(_yr)
            elif sort_by.startswith('ap_') and not sort_by.startswith('apaward_'):
                ap_info = next((i for i in ap_exam_infos if i['col_name'] == sort_by), None)
                if ap_info:
                    order_by_expr = (
                        "(SELECT CAST(NULLIF(regexp_replace(r.exam_grade, '[^0-9.]', '', 'g'), '') AS NUMERIC) "
                        "FROM \"dataLoader_apexamresult\" r "
                        "INNER JOIN \"dataLoader_apexamcode\" e ON r.exam_id = e.code "
                        "INNER JOIN \"dataLoader_apimportlog\" il ON r.import_log_id = il.id "
                        "WHERE r.ssid = cs.ssid AND r.admin_year = %s AND e.name = %s "
                        "AND il.is_published = true "
                        "LIMIT 1)"
                    )
                    params.extend([ap_info['year'], ap_info['subject']])
            elif sort_by.startswith('apaward_'):
                ap_info = next((i for i in ap_award_infos if i['col_name'] == sort_by), None)
                if ap_info:
                    order_by_expr = (
                        "(SELECT t.description FROM \"dataLoader_apstudentaward\" a "
                        "INNER JOIN \"dataLoader_apawardtype\" t ON a.award_type_id = t.code "
                        "INNER JOIN \"dataLoader_apimportlog\" il ON a.import_log_id = il.id "
                        "WHERE a.ssid = cs.ssid AND a.award_year = %s AND t.description = %s "
                        "AND il.is_published = true "
                        "LIMIT 1)"
                    )
                    params.extend([ap_info['year'], ap_info['award']])

        sort_dir = 'desc' if sort_dir == 'desc' else 'asc'

        # Check Redis cache for sorted IDs
        # Include full params in key to ensure uniqueness for filters
        # Note: We rely on cache invalidation via TTL (5 mins) matching the count cache
        cache_key_components = {
            'sql_hash': hashlib.md5((where_sql + join_sql).encode()).hexdigest(),
            'params': [str(p) for p in params], # params includes where + sort params
            'sort': f"{sort_by}:{sort_dir}",
            'limit': limit,
            'offset': offset 
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
                
                cached_exec = QueryExecutor()
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
            WHERE {where_sql}
            ORDER BY {order_by_expr} {sort_dir}
            LIMIT %s OFFSET %s
            """
            
            query_params = list(params)
            query_params.extend([limit, offset])
            
            main_exec = QueryExecutor()
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
        
        _fetch_cursor = QueryExecutor().cursor()
        
        # Fetch test data in bulk for selected test columns
        if test_cols_info and student_ids:
            student_ssids = [s.get('ssid') for s in students if s.get('ssid')]
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
                    SELECT /*+ IndexScan(r) */ r.local_student_id, r.ssid, {select_expr}
                    FROM results r
                    INNER JOIN tests t ON r.test_group_key = t.test_group_key
                    WHERE {RESULTS_BULK_MATCH_SQL}
                      AND r.school_year = %s
                      AND t.test_name ILIKE %s
                      AND t.subject ILIKE %s
                      {window_clause}
                """
                _fetch_cursor.execute(query, [
                    *extra_params,
                    student_ssids,
                    student_ids,
                    test_info['year'],
                    test_info['test_name'],
                    test_info['subject'],
                    *window_params,
                ])
                
                test_data_by_id, test_data_by_ssid = assign_result_value_maps(
                    _fetch_cursor.fetchall()
                )
                
                # Add test data to each student
                for student in students:
                    student[test_info['col_name']] = lookup_result_value(
                        student, test_data_by_id, test_data_by_ssid,
                    )

        # Fetch mark data in bulk for selected mark columns
        if mark_cols_info and student_keys:
            for mark_info in mark_cols_info:
                period_clause = ""
                period_params = []
                if mark_info.get('mark_period'):
                    period_clause = "AND sm.mark_period ILIKE %s"
                    period_params = [mark_info['mark_period']]
                else:
                    period_clause = "AND (sm.mark_period IS NULL OR sm.mark_period = '')"

                query = f"""
                    SELECT /*+ IndexScan(sm stu_annual_key) */ sm.stu_annual_key, sm.mark
                    FROM student_marks_list sm
                    WHERE sm.stu_annual_key = ANY(%s)
                      AND sm.school_year = %s
                      AND sm.subject ILIKE %s
                      AND sm.course_name ILIKE %s
                      {period_clause}
                """
                _fetch_cursor.execute(query, [
                    student_keys,
                    mark_info['year'],
                    f"%{mark_info['subject']}%",
                    f"%{mark_info['course_name']}%",
                    *period_params,
                ])

                mark_data = {row[0]: row[1] for row in _fetch_cursor.fetchall()}

                for student in students:
                    student_key = student.get('stu_annual_key')
                    student[mark_info['col_name']] = mark_data.get(student_key, '—')

        # Fetch Marks "All Marks" wide-pair data (markall_<year>_<subject>_<i>_COURSE / _MARKS)
        if markall_years and students:
            try:
                student_keys_all = [str(s.get('stu_annual_key') or '').strip() for s in students if str(s.get('stu_annual_key') or '').strip()]
                if student_keys_all:
                    with connections['default'].cursor() as markall_cur:
                        for (year, subject), umbrella in markall_years.items():
                            n = markall_max.get((year, subject), 0)
                            course_filter_sql = ""
                            if subject == 'Elementary':
                                course_filter_sql = " AND " + _markall_excluded_course_sql()
                            markall_cur.execute(
                                f"""
                                SELECT stu_annual_key, course_name, mark_period, mark
                                FROM student_marks_list sm
                                WHERE stu_annual_key = ANY(%s) AND school_year = %s AND subject = %s
                                  {course_filter_sql}
                                ORDER BY stu_annual_key, course_name, mark_period
                                """,
                                [student_keys_all, year, subject],
                            )
                            by_key = {}
                            for stu_key, course, period, mark in markall_cur.fetchall():
                                by_key.setdefault(str(stu_key).strip(), {}).setdefault(course, []).append((period, mark))
                            for student in students:
                                stu_key = str(student.get('stu_annual_key') or '').strip()
                                courses = by_key.get(stu_key, {})
                                for i in range(1, n + 1):
                                    course_key = f"{umbrella}_{i}_COURSE"
                                    marks_key = f"{umbrella}_{i}_MARKS"
                                    if i <= len(courses):
                                        course_name = list(courses.keys())[i - 1]
                                        periods = courses[course_name]
                                        marks_str = ', '.join(
                                            f"{p}: {m}" for p, m in periods if p and m
                                        )
                                        # If this MARKS column has an active filter, only
                                        # keep the matching period:mark entries in the cell.
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

        # Fetch AP "All Results" wide-pair data (apall_<year>_<i>_SUBJECT / _PL)
        if apall_years and students:
            try:
                ssids = [str(s.get('ssid') or '').strip() for s in students if str(s.get('ssid') or '').strip()]
                if ssids:
                    with connections['default'].cursor() as apall_cur:
                        for year in apall_years:
                            yk = year.replace('-', '_')
                            n = apall_max.get(year, 0)
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
                            by_ssid = {}
                            for ssid, subject, grade in apall_cur.fetchall():
                                by_ssid.setdefault(str(ssid).strip(), []).append((subject, grade))
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
            if student_ids:
                # Use the school_year filter if set, otherwise None for all years
                survey_year = school_year[0] if isinstance(school_year, list) and school_year else school_year
                survey_data = get_survey_data_for_students(student_ids, survey_year)
                
                # Get survey colors from survey_utils
                survey_info = get_survey_columns()
                survey_colors = survey_info.get('colors', {})
                
                # survey_data is {student_id: {col_key: value}}
                for student in students:
                    student_id = student.get('student_id')
                    student_survey_data = survey_data.get(student_id, {})
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
            _fetch_cursor.execute(q_eht, [student_keys])
            eht_map = {row[0]: row[1] for row in _fetch_cursor.fetchall()}
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
        filter_options = {}

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
                }
                filter_str = json.dumps(payload, sort_keys=True)
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
                _apply_column_filters(option_where, option_params, other_filters, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll, needs_students)
                option_where_sql = " AND ".join(option_where) if option_where else "1=1"

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
                cursor.execute(options_query, option_params)
                rows = cursor.fetchall()
                num_rows = len(rows)

                if num_rows <= 1 or (num_rows > 30 and col not in always_include):
                    continue

                values = [("" if row[0] is None else row[0]) for row in rows]
                if col not in always_include:
                    values = values[:30]

                if values:
                    filter_options[col] = values
                    # Cache for 5 minutes
                    cache.set(cache_key, values, 300)

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
                _apply_column_filters(option_where, option_params, other_filters, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll)
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
                    cursor.execute(options_query, option_params + [
                        test_info['year'],
                        test_info['test_name'],
                        test_info['subject'],
                        *window_params,
                    ])
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
                    cursor.execute(options_query, [
                        other_key,
                        *option_params,
                        test_info['year'],
                        test_info['test_name'],
                        test_info['subject'],
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
                        WHERE {RESULTS_STUDENT_MATCH_SQL}
                        AND r.school_year = %s
                        AND t.test_name ILIKE %s
                        AND t.subject ILIKE %s
                        {window_clause}
                    )
                    LIMIT 1
                """
                check_params = option_params + [
                    test_info['year'],
                    test_info['test_name'],
                    test_info['subject'],
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
                _apply_column_filters(option_where, option_params, other_filters, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll)
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
                    INNER JOIN current_student cs ON sm.stu_annual_key = cs.stu_annual_key
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

                cursor.execute(options_query, option_params + [
                    mark_info['year'],
                    f"%{mark_info['subject']}%",
                    f"%{mark_info['course_name']}%",
                    *period_params,
                ])

                values = []
                for row in cursor.fetchall():
                    val = row[0]
                    if val is None:
                        val = ""
                    if val not in values:
                        values.append(val)

                if values:
                    filter_options[col_key] = values
                    cache.set(cache_key, values, 300)

            # Add filter options for Marks "All Marks" wide-pair columns
            for (year, subject), umbrella in markall_years.items():
                n = markall_max.get((year, subject), 0)
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
                        _apply_column_filters(option_where, option_params, other_filters, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll)
                        option_where_sql = " AND ".join(option_where) if option_where else "1=1"

                        if is_marks:
                            # Distinct "period: mark" pairs
                            course_filter_sql = ""
                            if subject == 'Elementary':
                                course_filter_sql = " AND " + _markall_excluded_course_sql()
                            # Distinct marking periods (prefixed) — individual selections
                            period_query = f"""
                                SELECT DISTINCT CONCAT('period:', sm.mark_period) AS v
                                FROM student_marks_list sm
                                INNER JOIN current_student cs ON sm.stu_annual_key = cs.stu_annual_key
                                {join_sql}
                                WHERE {option_where_sql}
                                  AND sm.school_year = %s AND sm.subject = %s
                                  {course_filter_sql}
                                  AND sm.mark_period IS NOT NULL AND sm.mark_period <> ''
                                ORDER BY v ASC NULLS FIRST
                                LIMIT 50
                            """
                            cursor.execute(period_query, option_params + [year, subject])
                            period_values = []
                            for row in cursor.fetchall():
                                v = row[0]
                                if v is not None and v not in period_values:
                                    period_values.append(v)

                            # Distinct marks (prefixed) — individual selections
                            mark_query = f"""
                                SELECT DISTINCT CONCAT('mark:', sm.mark) AS v
                                FROM student_marks_list sm
                                INNER JOIN current_student cs ON sm.stu_annual_key = cs.stu_annual_key
                                {join_sql}
                                WHERE {option_where_sql}
                                  AND sm.school_year = %s AND sm.subject = %s
                                  {course_filter_sql}
                                  AND sm.mark IS NOT NULL AND sm.mark <> ''
                                ORDER BY v ASC NULLS FIRST
                                LIMIT 50
                            """
                            cursor.execute(mark_query, option_params + [year, subject])
                            mark_values = []
                            for row in cursor.fetchall():
                                v = row[0]
                                if v is not None and v not in mark_values:
                                    mark_values.append(v)

                            values = period_values + mark_values
                            if values:
                                filter_options[col_key] = values
                                cache.set(cache_key, values, 300)
                        else:
                            # Distinct course names
                            course_filter_sql = ""
                            if subject == 'Elementary':
                                course_filter_sql = " AND " + _markall_excluded_course_sql()
                            options_query = f"""
                                SELECT DISTINCT sm.course_name AS v
                                FROM student_marks_list sm
                                INNER JOIN current_student cs ON sm.stu_annual_key = cs.stu_annual_key
                                {join_sql}
                                WHERE {option_where_sql}
                                  AND sm.school_year = %s AND sm.subject = %s
                                  {course_filter_sql}
                                  AND sm.course_name IS NOT NULL AND sm.course_name <> ''
                                ORDER BY v ASC NULLS FIRST
                                LIMIT 50
                            """
                            cursor.execute(options_query, option_params + [year, subject])

                            values = []
                            for row in cursor.fetchall():
                                val = row[0]
                                if val is None:
                                    val = ""
                                if val not in values:
                                    values.append(val)
                            if values:
                                filter_options[col_key] = values
                                cache.set(cache_key, values, 300)

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
                _apply_column_filters(option_where, option_params, other_filters, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll)
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
                cursor.execute(options_query, option_params + [
                    ap_info['year'],
                    ap_info['subject'],
                ])

                values = []
                for row in cursor.fetchall():
                    val = row[0]
                    if val is None:
                        val = ""
                    if val not in values:
                        values.append(val)

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
                cursor.execute(check_blank_query, option_params + [
                    ap_info['year'],
                    ap_info['subject'],
                ])
                if cursor.fetchone():
                    if "" not in values:
                        values.insert(0, "")

                if values:
                    filter_options[col_key] = values
                    cache.set(cache_key, values, 300)

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
                _apply_column_filters(option_where, option_params, other_filters, test_col_map, mark_col_map, db_columns, enroll_columns, needs_enroll)
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
                    cursor.execute(options_query, option_params + [ap_info['year']])
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
                    cursor.execute(options_query, option_params + [ap_info['year'], ap_info['award']])

                values = []
                for row in cursor.fetchall():
                    val = row[0]
                    if val is None:
                        val = ""
                    if val not in values:
                        values.append(val)

                if ap_info['award'] != '__ALL__':
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
                    cursor.execute(check_blank_query, option_params + [ap_info['year']])
                    if cursor.fetchone():
                        if "" not in values:
                            values.insert(0, "")

                if values:
                    filter_options[col_key] = values
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

    # Definitions for the AP "All Results" wide-pair columns
    for year in apall_years:
        yk = year.replace('-', '_')
        n = apall_max.get(year, 0)
        for i in range(1, n + 1):
            dynamic_definitions[f"apall_{yk}_{i}_SUBJECT"] = f"AP Subject {i}\n{year}"
            dynamic_definitions[f"apall_{yk}_{i}_PL"] = f"AP PL {i}\n{year}"

    # Definitions for the Marks "All Marks" wide-pair columns
    for (year, subject), umbrella in markall_years.items():
        n = markall_max.get((year, subject), 0)
        for i in range(1, n + 1):
            dynamic_definitions[f"{umbrella}_{i}_COURSE"] = f"Course {i} ({subject})\n{year}"
            dynamic_definitions[f"{umbrella}_{i}_MARKS"] = f"Marks {i} ({subject})\n{year}"

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
            'school_year': _serialize_context_filter(school_year),
            'grade': _serialize_context_filter(grade),
            'status': _serialize_context_filter(status),
            'sort_by': sort_by,
            'sort_dir': sort_dir,
            'survey_colors': survey_colors,
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
        'column_definitions': dynamic_definitions,
        'survey_colors': survey_colors,
    }

    return render(request, 'dataexploration/partials/student_table.html', context)


