"""CSV export functionality for student data."""

from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse
from django.db import connections
import json
import csv
import hashlib

from ..models import SavedQuery, StudentList
from ..catalog_columns import (
    COURSE_PROGRAM_DEFINITIONS,
    apply_course_program_columns,
    apply_course_program_filters_for_clickhouse,
    course_program_columns_enabled,
    is_course_program_column,
    split_course_program_filters,
)
from ..constants import COLUMN_DEFINITIONS, VIRTUAL_COLUMNS
from ..column_utils import (
    _get_display_name,
    export_csv_cell,
    enrich_students_tests_marks,
    _fetch_marks_live_from_alt,
    group_marks_by_ssid_course,
    markall_course_filter_fn,
    parse_markall_key,
    markall_key as _markall_key,
    parse_sat_column_key,
)
from ..utils import can_user_view_query, get_user_folders, _apply_de_school_filter
from .schedule_filter_sql import parse_schedule_marks_filters, apply_schedule_marks_filters
from .student_list_views import _append_school_year_filter, _fetchall_ch_or_pg
from .filter_sql import apply_column_filters, extract_markall_course_picker, markall_course_filter_parts
from .student_data_utils import (
    get_current_student_columns,
    get_student_enroll_columns,
)
from .column_management import (
    get_saved_default_columns,
    get_column_expression,
)
from ..survey_utils import get_survey_columns, get_survey_data_for_students
from dataexploration.school_year_utils import resolve_relative_column_key
from clickhouse_utils.timing import timed_fn


def _parse_multi(param_val, fallback):
    """Parse a URL parameter that may be a plain string or a JSON-encoded list."""
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

@login_required
@timed_fn('export_students_csv')
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
    else:
        # A saved query's own school year wins over the project default, so
        # exporting a saved query still targets the year it was built for.
        _saved_school_year = saved_filters.get('school_year', '') or saved_config.get('school_year', '')
        if saved_query_id and _saved_school_year:
            school_year = _saved_school_year
        elif _project_default_year:
            school_year = _project_default_year
        else:
            school_year = _saved_school_year

    # Resolve school-year aliases (e.g. '__sy_this__') to concrete year strings
    try:
        from dataexploration.school_year_utils import resolve_school_year
        if school_year:
            if isinstance(school_year, list):
                school_year = [resolve_school_year(y) for y in school_year]
            else:
                school_year = resolve_school_year(school_year)
    except Exception:
        pass
    grade = _parse_multi(
        request.GET.get('grade', ''),
        _parse_multi(saved_filters.get('grade', ''), _parse_multi(saved_config.get('grade', ''), '')),
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
            search_ids = None

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
        filters = {
            k: v for k, v in saved_filters.items()
            if k not in [
                'search', 'school_year', 'grade', 'status',
                'schedule_period', 'schedule_subject', 'schedule_course_name', 'schedule_staff',
            ]
        }

    schedule_marks_filters = parse_schedule_marks_filters(
        request, saved_filters, column_filters=filters, user=request.user,
    )

    if sort_by:
        sort_by = resolve_relative_column_key(sort_by)
    if isinstance(filters, dict):
        filters = {resolve_relative_column_key(k): v for k, v in filters.items()}

    # ── Marks "All Marks" expansion setup ──────────────────────────────────
    # Any markall umbrella key (or already-expanded pair keys) collapses back to
    # the umbrella. Actual expansion into wide course/marks pairs happens inside
    # the stream once the filtered population is known. Selecting it also drops
    # the individual mark_<year>_<subject>_* columns for the same subjects.
    def _markall_subject_actual(year, subject_norm):
        try:
            from ..column_utils import get_mark_columns
            marks_struct = get_mark_columns()
            for subject in marks_struct.get(year, {}):
                if _markall_key(year, subject) == _markall_key(year, subject_norm):
                    return subject
        except Exception:
            pass
        return subject_norm.replace('_', ' ')

    markall_years = {}  # (year, subject_actual) -> umbrella key
    for c in selected_columns:
        if not isinstance(c, str) or not c.startswith('markall_'):
            continue
        parsed = parse_markall_key(c)
        if parsed:
            key_t = (parsed['year'], _markall_subject_actual(parsed['year'], parsed['subject_norm']))
            markall_years.setdefault(key_t, _markall_key(*key_t))

    if markall_years:
        new_selected = []
        for c in selected_columns:
            # Unselect individual mark columns for expanded subjects
            if isinstance(c, str) and c.startswith('mark_') and not c.startswith('markall_'):
                tail = c[len('mark_'):]
                parts = tail.split('_')
                if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                    yr = f"{parts[0]}-{parts[1]}"
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
            if isinstance(c, str) and c.startswith('markall_'):
                parsed = parse_markall_key(c)
                if parsed and parsed['kind'] is not None:
                    umbrella = markall_years.get(
                        (parsed['year'], _markall_subject_actual(parsed['year'], parsed['subject_norm']))
                    )
                    if umbrella and umbrella not in new_selected:
                        new_selected.append(umbrella)
                    continue
            new_selected.append(c)
        selected_columns = new_selected

    markall_pickers = extract_markall_course_picker(filters)

    def _markall_course_picker_for_umbrella(umbrella):
        return markall_pickers.get(umbrella, {'mode': 'all', 'courses': set()})

    # ── AP "All Results" expansion setup ───────────────────────────────────
    # The apall_<year> umbrella (or already-expanded pairs) collapses back to
    # the umbrella; expansion into assessment/result wide pairs happens inside
    # the stream once the filtered population is known.
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
        [c for c in selected_columns if isinstance(c, str) and c.startswith('mark_') and not c.startswith('markall_')] +
        [k for k in filter_keys if isinstance(k, str) and k.startswith('mark_') and not k.startswith('markall_')]
    ))
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
    selected_survey_cols = [
        c for c in selected_columns if isinstance(c, str) and c.startswith('survey_')
    ]
    selected_course_prog_cols = list(set(
        [c for c in selected_columns if is_course_program_column(c)]
        + [k for k in filter_keys if is_course_program_column(k)]
    ))
    base_cols = [
        c for c in selected_columns
        if isinstance(c, str)
        and (not c.startswith(('test_', 'mark_', 'markall_', 'survey_', 'ap_', 'apaward_', 'apawards_all_', 'apall_', 'sat_', 'course_prog_')))
        and c in COLUMN_DEFINITIONS
        and c not in VIRTUAL_COLUMNS
    ]

    enroll_fields = {'ell', 'birth_date', 'us_enter_date', 'fep_date', 'migrant'}
    needs_enroll = (
        any(c in enroll_fields for c in selected_columns)
        or (isinstance(filters, dict) and any(k in enroll_fields for k in filters.keys()))
        or sort_by in enroll_fields
    )

    # Determine if we need students join (for ltel overrides)
    needs_students = False

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
            # Local working copy (the markall expansion below re-binds it).
            export_cols = list(selected_columns)
            with QueryExecutor(analytics=True).cursor() as cursor:
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

                ap_exam_infos = []
                ap_award_infos = []
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

                # Prepare Dynamic Headers
                dynamic_labels = COLUMN_DEFINITIONS.copy()
                if course_program_columns_enabled():
                    dynamic_labels.update(COURSE_PROGRAM_DEFINITIONS)
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

                for info in ap_exam_infos:
                    dynamic_labels[info['col_name']] = f"{info['subject']} Performance Level {info['year']}"
                for info in ap_award_infos:
                    if info.get('award') == '__ALL__':
                        dynamic_labels[info['col_name']] = f"All Awards {info['year']}"
                    else:
                        dynamic_labels[info['col_name']] = f"{info['award']} {info['year']}"

                # Build Where Clauses
                base_where_clauses = ["1=1"]
                base_params = []

                # Local copies for streaming scope
                search_value = search
                search_ids_value = search_ids
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
                            search_ids_value = None
                            school_year_value = None
                            grade_value = None
                            status_value = None
                            filters_value = {}

                search_ids_value = search_ids
                if search_ids_value:
                    base_where_clauses.append("(cs.ssid = ANY(%s) OR cs.student_id = ANY(%s))")
                    base_params.extend([search_ids_value, search_ids_value])
                elif search_value:
                    base_where_clauses.append("(cs.student_name ILIKE %s OR cs.ssid ILIKE %s OR cs.student_id ILIKE %s)")
                    base_params.extend([f'%{search_value}%'] * 3)
                _append_school_year_filter(base_where_clauses, base_params, school_year_value)
                if grade_value:
                    if isinstance(grade_value, list):
                        if len(grade_value) == 1:
                            base_where_clauses.append("cs.grade = %s")
                            base_params.append(grade_value[0])
                        elif len(grade_value) > 1:
                            base_where_clauses.append("cs.grade = ANY(%s)")
                            base_params.append(grade_value)
                    else:
                        base_where_clauses.append("cs.grade = %s")
                        base_params.append(grade_value)
                if status_value:
                    if isinstance(status_value, list):
                        if len(status_value) == 1:
                            base_where_clauses.append("cs.status = %s")
                            base_params.append(status_value[0])
                        elif len(status_value) > 1:
                            base_where_clauses.append("cs.status = ANY(%s)")
                            base_params.append(status_value)
                    else:
                        base_where_clauses.append("cs.status = %s")
                        base_params.append(status_value)

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
                cp_filters, filters_without_cp = split_course_program_filters(filters_value)
                apply_column_filters(
                    where_clauses, params, filters_without_cp, test_col_map, mark_col_map,
                    db_columns, enroll_columns, needs_enroll, needs_students, needs_eht,
                    ap_exam_infos, ap_award_infos,
                )
                apply_course_program_filters_for_clickhouse(
                    where_clauses, params, cp_filters, base_where_clauses, base_params,
                )

                survey_filters = {
                    k: v for k, v in (filters_value or {}).items()
                    if isinstance(k, str) and k.startswith('survey_') and v
                }
                if survey_filters:
                    from ..survey_utils import get_survey_columns
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
                            _sr_meta['survey_type'], _sr_meta['school_year'], _sr_meta['window'],
                            _sr_meta.get('grade_range', ''), _sr_meta['question_order'],
                        ]
                        if _sr_real_values:
                            _sr_conditions.append(f"cs.student_id IN ({_sr_base_query} AND sr.value = ANY(%s))")
                            params.extend(_sr_base_params + [_sr_real_values])
                        if _sr_has_blank:
                            _sr_conditions.append(f"cs.student_id NOT IN ({_sr_base_query})")
                            params.extend(_sr_base_params)
                        if _sr_conditions:
                            where_clauses.append(f"({' OR '.join(_sr_conditions)})")

                where_sql = " AND ".join(where_clauses)

                # ── Marks "All Marks" expansion (filtered population) ──────
                # Emit one COURSE/MARKS wide pair per course the filtered
                # students actually have in the subject (respecting the courses
                # the user deselected). Mirrors load_students.
                markall_max = {}
                if markall_years:
                    with connections['default'].cursor() as markall_cursor:
                        for (year, subject), umbrella in markall_years.items():
                            course_filter_sql, course_extra_params = markall_course_filter_parts(
                                subject, _markall_course_picker_for_umbrella(umbrella)
                            )
                            markall_cursor.execute(
                                f"""
                                SELECT COALESCE(MAX(cnt), 0)
                                FROM (
                                    SELECT sm.ssid, COUNT(DISTINCT sm.course_name) AS cnt
                                    FROM student_marks_list sm
                                    INNER JOIN current_student cs ON sm.ssid = cs.ssid
                                    {join_sql}
                                    WHERE {where_sql}
                                      AND sm.school_year = %s AND sm.subject = %s
                                      {course_filter_sql}
                                    GROUP BY sm.ssid
                                ) t
                                """,
                                params + [year, subject] + course_extra_params,
                            )
                            markall_max[(year, subject)] = min(int(markall_cursor.fetchone()[0] or 0), 10)

                    new_selected = []
                    for c in export_cols:
                        parsed = parse_markall_key(c) if (isinstance(c, str) and c.startswith('markall_')) else None
                        if parsed and parsed['kind'] is None:
                            year = parsed['year']
                            subject = _markall_subject_actual(year, parsed['subject_norm'])
                            n = markall_max.get((year, subject), 0)
                            for i in range(1, n + 1):
                                new_selected.append(f"{c}_{i}_COURSE")
                                new_selected.append(f"{c}_{i}_MARKS")
                            continue
                        new_selected.append(c)
                    export_cols = new_selected

                # Definitions for the Marks "All Marks" wide-pair columns
                for (year, subject), umbrella in markall_years.items():
                    n = markall_max.get((year, subject), 0)
                    for i in range(1, n + 1):
                        dynamic_labels[f"{umbrella}_{i}_COURSE"] = f"Course {i} ({subject}) {year}"
                        dynamic_labels[f"{umbrella}_{i}_MARKS"] = f"Marks {i} ({subject}) {year}"

                # ── AP "All Results" expansion (filtered population) ───────
                apall_max = {}
                if apall_years:
                    with connections['default'].cursor() as apall_cursor:
                        for year in apall_years:
                            excludes = _apall_subject_excludes(year)
                            exclude_clause = ""
                            exclude_params = []
                            if excludes:
                                placeholders = ", ".join(["%s"] * len(excludes))
                                exclude_clause = f" AND e.name NOT IN ({placeholders})"
                                exclude_params = sorted(excludes)
                            apall_cursor.execute(
                                f"""
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
                                """,
                                params + [year] + exclude_params,
                            )
                            apall_max[year] = min(int(apall_cursor.fetchone()[0] or 0), 10)

                    new_selected = []
                    for c in export_cols:
                        yk = _apall_year_key(c)
                        if yk:
                            year = yk.replace('_', '-')
                            n = apall_max.get(year, 0)
                            for i in range(1, n + 1):
                                new_selected.append(f"apall_{yk}_{i}_SUBJECT")
                                new_selected.append(f"apall_{yk}_{i}_PL")
                            continue
                        new_selected.append(c)
                    export_cols = new_selected

                # Definitions for the AP "All Results" wide-pair columns
                for year in apall_years:
                    yk = year.replace('-', '_')
                    n = apall_max.get(year, 0)
                    for i in range(1, n + 1):
                        suffix = f" {i}" if n > 1 else ""
                        dynamic_labels[f"apall_{yk}_{i}_SUBJECT"] = f"AP Assessment{suffix} {year}"
                        dynamic_labels[f"apall_{yk}_{i}_PL"] = f"AP Results{suffix} {year}"

                # Definitions for survey columns
                if selected_survey_cols:
                    dynamic_labels.update(get_survey_columns().get('definitions', {}))

                # Yield Header (UTF-8 BOM so Excel opens the file as UTF-8)
                header_labels = [dynamic_labels.get(col, col) for col in export_cols]
                yield '\ufeff'
                yield writer.writerow(header_labels)
                logger.info(f"CSV Export: Header yielded, starting data export with {len(export_cols)} columns")

                # Fetch Loop
                chunk_size = 2000
                offset = 0
                
                # Ensure we fetch minimal required columns plus sorting
                query_base_cols = list(base_cols)
                if 'ssid' not in query_base_cols: query_base_cols.insert(0, 'ssid')
                if (test_cols_info or selected_mark_cols or markall_years or selected_survey_cols) and 'student_id' not in query_base_cols: query_base_cols.insert(0, 'student_id')
                if (selected_mark_cols or markall_years or selected_course_prog_cols) and 'stu_annual_key' not in query_base_cols:
                    query_base_cols.insert(0, 'stu_annual_key')
                if selected_course_prog_cols and 'school_year' not in query_base_cols:
                    query_base_cols.insert(0, 'school_year')
                
                base_col_selects = []
                base_col_expressions = {}
                for col in query_base_cols:
                    expr, alias = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
                    if expr and alias:
                        base_col_expressions[col] = expr
                        base_col_selects.append(f"{expr} AS {alias}")
                
                if not base_col_selects:
                    base_col_selects.append("cs.ssid AS ssid")

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
                        ORDER BY {order_by_expr} {dir_sql}, cs.stu_annual_key {dir_sql}
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
                    enrich_students_tests_marks(
                        chunk_students, test_cols_info, mark_cols_info, blank='',
                    )

                    if selected_survey_cols:
                        try:
                            chunk_stu_ids = [s.get('student_id') for s in chunk_students if s.get('student_id')]
                            chunk_ssids = [s.get('ssid') for s in chunk_students if s.get('ssid')]
                            if chunk_stu_ids or chunk_ssids:
                                chunk_survey_data = get_survey_data_for_students(student_ids=chunk_stu_ids, ssids=chunk_ssids)
                                for student in chunk_students:
                                    student_id = str(student.get('student_id') or '').strip()
                                    ssid = str(student.get('ssid') or '').strip()
                                    student_survey_data = (
                                        chunk_survey_data.get(student_id)
                                        or chunk_survey_data.get(ssid)
                                        or {}
                                    )
                                    for col_key in selected_survey_cols:
                                        student[col_key] = student_survey_data.get(col_key, '')
                            else:
                                for student in chunk_students:
                                    for col_key in selected_survey_cols:
                                        student[col_key] = ''
                        except Exception:
                            pass

                    if selected_course_prog_cols:
                        apply_course_program_columns(chunk_students, selected_course_prog_cols)

                    if sat_infos:
                        try:
                            from dataviz.utils.data_loader import _fetch_sat_data
                            _fetch_sat_data(chunk_students, sat_infos)
                        except Exception:
                            pass

                    # Fetch AP (College Board) exam + award values for this chunk
                    if ap_exam_infos or ap_award_infos:
                        try:
                            from dataviz.utils.data_loader import (
                                _fetch_ap_exam_data,
                                _fetch_ap_award_data,
                            )
                            if ap_exam_infos:
                                _fetch_ap_exam_data(chunk_students, ap_exam_infos)
                            if ap_award_infos:
                                _fetch_ap_award_data(chunk_students, ap_award_infos)
                            for _student in chunk_students:
                                for _info in ap_exam_infos + ap_award_infos:
                                    _student.setdefault(_info['col_name'], '')
                        except Exception:
                            pass

                    # Fetch AP "All Results" wide-pair data for this chunk
                    if apall_years:
                        try:
                            chunk_ssids = [
                                str(s.get('ssid') or '').strip()
                                for s in chunk_students if str(s.get('ssid') or '').strip()
                            ]
                            if chunk_ssids:
                                from dataviz.utils.data_loader import _analytics_rows
                                for year in apall_years:
                                    yk = year.replace('-', '_')
                                    n = apall_max.get(year, 0)
                                    excludes = _apall_subject_excludes(year)
                                    ap_rows = _analytics_rows(
                                        "SELECT ssid, exam_name, exam_grade FROM analytics_ap_results "
                                        "WHERE ssid = ANY(%s) AND admin_year = %s "
                                        "ORDER BY ssid, exam_name",
                                        [chunk_ssids, year],
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
                                                [chunk_ssids, year],
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
                                    for student in chunk_students:
                                        sid = str(student.get('ssid') or '').strip()
                                        results = by_ssid.get(sid, [])
                                        for i in range(1, n + 1):
                                            subj_key = f"apall_{yk}_{i}_SUBJECT"
                                            pl_key = f"apall_{yk}_{i}_PL"
                                            if i <= len(results):
                                                student[subj_key] = results[i - 1][0]
                                                student[pl_key] = str(results[i - 1][1]).strip() if results[i - 1][1] else ''
                                            else:
                                                student[subj_key] = ''
                                                student[pl_key] = ''
                        except Exception:
                            pass

                    # Fetch Marks "All Marks" wide-pair data for this chunk
                    if markall_years:
                        try:
                            chunk_ssids = [str(s.get('ssid') or '').strip() for s in chunk_students if str(s.get('ssid') or '').strip()]
                            chunk_ids = [str(s.get('student_id') or '').strip() for s in chunk_students if str(s.get('student_id') or '').strip()]
                            if chunk_ssids or chunk_ids:
                                for (year, subject), umbrella in markall_years.items():
                                    n = markall_max.get((year, subject), 0)
                                    picker = _markall_course_picker_for_umbrella(umbrella)
                                    live_rows = _fetch_marks_live_from_alt(chunk_ssids, chunk_ids, [year])
                                    by_ssid, by_student_id = group_marks_by_ssid_course(
                                        live_rows,
                                        subject=subject,
                                        course_filter_fn=markall_course_filter_fn(subject, picker),
                                    )
                                    for student in chunk_students:
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
                                                # If this pair column is filtered by marking period
                                                # and/or mark, only those entries are shown — mirror
                                                # what load_students renders in the table.
                                                col_filters = filters_value.get(marks_key) if isinstance(filters_value, dict) else None
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
                                                    else:
                                                        marks_str = ', '.join(
                                                            f"{p}: {m}" for p, m in periods if p and m
                                                        )
                                                else:
                                                    marks_str = ', '.join(
                                                        f"{p}: {m}" for p, m in periods if p and m
                                                    )
                                                student[course_key] = course_name
                                                student[marks_key] = marks_str
                                            else:
                                                student[course_key] = ''
                                                student[marks_key] = ''
                        except Exception:
                            pass

                    # Yield Rows
                    for student in chunk_students:
                        row = []
                        for col in export_cols:
                            row.append(export_csv_cell(student.get(col)))
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
    response = StreamingHttpResponse(stream_csv(), content_type="text/csv; charset=utf-8")
    response['Content-Disposition'] = 'attachment; filename="students_export.csv"'
    logger.info(f"CSV Export: Returning response")
    return response

