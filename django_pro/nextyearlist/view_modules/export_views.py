"""CSV export for Next Year List.

Aligned with dataexploration export logic for consistency.
"""
from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse
from django.db import connections
from django.core.cache import cache
import json
import csv
import hashlib
import logging

from dataexploration.models import SavedQuery
from dataexploration.view_modules.student_data_utils import get_current_student_columns, get_student_enroll_columns
from dataexploration.column_utils import _get_display_name
from ..constants import COLUMN_DEFINITIONS
from ..utils import can_user_view_query
from ..view_modules.column_management import get_saved_default_columns, get_column_expression
from ..view_modules.student_list_views import _is_unrestricted, _apply_nyl_school_filter
from users.decorators import page_access_required

logger = logging.getLogger(__name__)


@login_required
@page_access_required('nextyearlist')
def export_students_csv(request):
    """Export filtered next-year student data as a streaming CSV.

    Logic aligned with dataexploration export for consistency.
    """
    logger.info(f"NYL CSV Export: Function called, user={request.user.username}")

    saved_query_id = request.GET.get('saved_query')
    saved_config = {}
    if saved_query_id:
        try:
            query_obj = SavedQuery.objects.get(pk=saved_query_id)
            if can_user_view_query(request.user, query_obj):
                saved_config = query_obj.config
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
        except Exception:
            filters = saved_filters.copy()
    else:
        filters = {k: v for k, v in saved_filters.items() if k not in ['search', 'school_year', 'grade', 'status']}

    # Helper for column filters (aligned with dataexploration export)
    def _apply_column_filters(where_clauses, params, filters_to_apply, test_col_map, db_columns, enroll_columns, needs_enroll, needs_students=False, needs_eht=False):
        if not isinstance(filters_to_apply, dict):
            return
        import re as _re
        for col, values in filters_to_apply.items():
            if not values:
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

            # Handle nextyearlist-specific columns
            if col == 'atendace_rate':
                expr = 'cs.attendance_rate' if 'attendance_rate' in db_columns else None
                if expr:
                    conditions = []
                    for val in values:
                        val_str = str(val).strip()
                        if not val_str:
                            conditions.append(f"({expr} IS NULL)")
                            continue
                        m = _re.match(r'^([<>]=?|=)?\s*(\d+(?:\.\d+)?)$', val_str)
                        if m:
                            op = m.group(1) or '='
                            conditions.append(f"{expr} {op} %s")
                            params.append(m.group(2))
                            continue
                        m2 = _re.match(r'^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)$', val_str)
                        if m2:
                            conditions.append(f"{expr} BETWEEN %s AND %s")
                            params.extend(list(m2.groups()))
                            continue
                        conditions.append(f"{expr} = %s")
                        params.append(val)
                    if conditions:
                        where_clauses.append(f"({' OR '.join(conditions)})")
                continue

            # Nextyearlist-specific column resolution
            expr_col = f"cs.{col}" if col in db_columns else None
            if col in {'next_year_school_state_id', 'next_year_school_name'}:
                expr_col = f"nyl.{col}"
            elif col == 'math_selection':
                expr_col = "mr.math_selection"
            elif col in enroll_columns:
                expr_col = f"se.{col}"

            if not expr_col:
                # Try generic column expression
                gen_expr, _ = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
                if gen_expr:
                    expr_col = gen_expr

            if not expr_col:
                continue

            conditions = []
            if real_values:
                conditions.append(f"{expr_col} = ANY(%s)")
                params.append(real_values)
            if has_blank:
                if col in {'birth_date', 'us_enter_date', 'fep_date'}:
                    conditions.append(f"{expr_col} IS NULL")
                else:
                    conditions.append(f"({expr_col} IS NULL OR {expr_col} = '')")

            if conditions:
                where_clauses.append(f"({' OR '.join(conditions)})")

    db_columns = get_current_student_columns()
    enroll_columns = get_student_enroll_columns()

    selected_test_cols = [c for c in selected_columns if isinstance(c, str) and c.startswith('test_')]
    selected_mark_cols = [c for c in selected_columns if isinstance(c, str) and c.startswith('mark_')]
    base_cols = [c for c in selected_columns if isinstance(c, str) and not c.startswith(('test_', 'mark_')) and c in COLUMN_DEFINITIONS]

    enroll_fields = {'ell', 'birth_date', 'us_enter_date', 'fep_date', 'migrant'}
    needs_enroll = any(c in enroll_fields for c in selected_columns) or (isinstance(filters, dict) and any(k in enroll_fields for k in filters)) or sort_by in enroll_fields
    needs_students = any(c in {'ltel'} for c in selected_columns) or (isinstance(filters, dict) and 'ltel' in filters) or sort_by == 'ltel'
    needs_eht = (isinstance(filters, dict) and 'elem_teacher_name' in filters) or sort_by == 'elem_teacher_name'

    # Handle student_list filter — overrides all other filters
    student_list_id = request.GET.get('student_list')
    if student_list_id:
        try:
            slq = SavedQuery.objects.get(pk=student_list_id, query_type='student_list')
            if can_user_view_query(request.user, slq):
                list_ssids = slq.config.get('student_ids') or []
                normalized_ssids = [str(s).strip() for s in list_ssids if str(s).strip()]
                if normalized_ssids:
                    # List defines membership — override all filters
                    search = None
                    school_year = None
                    grade = None
                    status = None
                    filters = {}
        except (SavedQuery.DoesNotExist, Exception):
            normalized_ssids = []
    else:
        normalized_ssids = None

    where_clauses = ["1=1"]
    params = []

    if normalized_ssids is not None:
        where_clauses.append("cs.ssid = ANY(%s)")
        params.append(normalized_ssids)

    if search:
        where_clauses.append("(cs.student_name ILIKE %s OR cs.ssid ILIKE %s OR cs.student_id ILIKE %s)")
        sp = f'%{search}%'
        params.extend([sp, sp, sp])
    if school_year:
        if isinstance(school_year, list) and len(school_year) > 1:
            where_clauses.append("cs.school_year = ANY(%s)")
            params.append(school_year)
        else:
            where_clauses.append("cs.school_year = %s")
            params.append(school_year[0] if isinstance(school_year, list) else school_year)
    if grade:
        if isinstance(grade, list) and len(grade) > 1:
            where_clauses.append("cs.grade = ANY(%s)")
            params.append(grade)
        else:
            where_clauses.append("cs.grade = %s")
            params.append(grade[0] if isinstance(grade, list) else grade)
    if status:
        if isinstance(status, list) and len(status) > 1:
            where_clauses.append("cs.status = ANY(%s)")
            params.append(status)
        else:
            where_clauses.append("cs.status = %s")
            params.append(status[0] if isinstance(status, list) else status)

    _apply_nyl_school_filter(where_clauses, params, request.user)

    # NOTE: Column filters including test/mark filters are applied inside csv_rows()
    # after test_col_map is built, matching dataexploration logic

    if hasattr(request.user, 'get_accessible_student_keys'):
        accessible_keys = request.user.get_accessible_student_keys()
        if accessible_keys is not None:
            if not accessible_keys:
                where_clauses.append("1=0")
            else:
                where_clauses.append("cs.stu_annual_key = ANY(%s)")
                params.append(accessible_keys)

    join_sql = ""
    if needs_enroll:
        join_sql += (
            " LEFT JOIN (SELECT DISTINCT ON (student_id, school_year) * FROM student_enroll"
            " ORDER BY student_id, school_year, enter_date DESC NULLS LAST, leave_date DESC NULLS LAST"
            ") se ON se.student_id = cs.student_id AND se.school_year = cs.school_year"
        )
    if needs_eht:
        join_sql += " LEFT JOIN elementary_homeroom_teacher eht ON eht.stu_annual_key = cs.stu_annual_key"

    if 'ssid' not in base_cols:
        base_cols.insert(0, 'ssid')
    if selected_test_cols and 'student_id' not in base_cols:
        base_cols.insert(0, 'student_id')
    if selected_mark_cols and 'stu_annual_key' not in base_cols:
        base_cols.insert(0, 'stu_annual_key')

    where_sql = " AND ".join(where_clauses)
    order_by_expr = "cs.student_name"
    if sort_by:
        if sort_by in {'next_year_school_state_id', 'next_year_school_name'}:
            order_by_expr = f"nyl.{sort_by}"
        elif sort_by == 'math_selection':
            order_by_expr = "mr.math_selection"
        elif sort_by in db_columns:
            order_by_expr = f"cs.{sort_by}"
    sort_dir_sql = 'DESC' if sort_dir == 'desc' else 'ASC'

    def _np(v):
        return (v or '').replace(' ', '_').replace('-', '_')

    def _parse_year_from_test_col(col_key):
        s = (col_key or '').replace('-', '_')
        if not s.startswith('test_'):
            return None
        tail = s[len('test_'):]
        if len(tail) >= 10 and tail[4] == '_' and tail[9] == '_':
            return tail[:9].replace('_', '-')
        parts = tail.split('_', 1)
        return parts[0].replace('_', '-') if parts else None

    def _parse_year_from_mark_col(col_key):
        s = (col_key or '').replace('-', '_')
        if not s.startswith('mark_'):
            return None
        tail = s[len('mark_'):]
        if len(tail) >= 10 and tail[4] == '_' and tail[9] == '_':
            return tail[:9].replace('_', '-')
        parts = tail.split('_', 1)
        return parts[0].replace('_', '-') if parts else None

    def csv_rows():
        try:
            with connections['default'].cursor() as cursor:
                # Build base column selects
                base_col_selects = []
                base_col_expressions = {}
                for col in base_cols:
                    expr, alias = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
                    if expr and alias:
                        base_col_expressions[col] = expr
                        base_col_selects.append(f"{expr} AS {alias}" if expr != f"cs.{alias}" else expr)

                # Build test column metadata (aligned with dataexploration)
                test_cols_info = []
                test_col_map = {}
                if selected_test_cols:
                    normalized_to_original = {c.replace('-', '_'): c for c in selected_test_cols}
                    years = sorted({y for y in (_parse_year_from_test_col(c) for c in selected_test_cols) if y})
                    if years:
                        cursor.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'tests' AND column_name = 'test_window'")
                        has_tw = cursor.fetchone() is not None
                        if has_tw:
                            cursor.execute(
                                "SELECT DISTINCT r.school_year, t.test_name, t.subject, COALESCE(t.test_window, '')"
                                " FROM results r INNER JOIN tests t ON r.test_group_key = t.test_group_key"
                                " WHERE r.school_year = ANY(%s) AND r.school_year IS NOT NULL AND t.test_name IS NOT NULL",
                                [years]
                            )
                        else:
                            cursor.execute(
                                "SELECT DISTINCT r.school_year, t.test_name, t.subject, ''"
                                " FROM results r INNER JOIN tests t ON r.test_group_key = t.test_group_key"
                                " WHERE r.school_year = ANY(%s) AND r.school_year IS NOT NULL AND t.test_name IS NOT NULL",
                                [years]
                            )
                        for (yr, test_name, subject, test_window) in cursor.fetchall():
                            win_norm = _np(test_window).strip('_')
                            key_base = f"{_np(yr)}_{_np(test_name)}_{_np(subject)}_{win_norm}" if win_norm else f"{_np(yr)}_{_np(test_name)}_{_np(subject)}"
                            win_value = (test_window or '').strip() or None
                            for metric, suffix in (('ss', 'SS'), ('pl', 'PL')):
                                norm_key = f"test_{key_base}_{suffix}"
                                ok = normalized_to_original.get(norm_key)
                                if not ok:
                                    continue
                                info = {
                                    'col_name': ok, 'year': yr, 'test_name': test_name or '',
                                    'subject': subject or '', 'test_window': win_value, 'metric': metric
                                }
                                test_cols_info.append(info)
                                test_col_map[ok] = info
                                test_col_map[ok.replace('-', '_')] = info

                        # Other Info (OI) columns support (aligned with dataexploration)
                        want_other = any('_OI_' in k for k in normalized_to_original.keys())
                        if want_other:
                            if has_tw:
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
                                yr_norm = _np(yr)
                                tn_norm = _np(test_name)
                                sub_norm = _np(subject)
                                win_norm = _np(test_window).strip('_')

                                if win_norm:
                                    key_base = f"{yr_norm}_{tn_norm}_{sub_norm}_{win_norm}"
                                    win_value = (test_window or '').strip() or None
                                else:
                                    key_base = f"{yr_norm}_{tn_norm}_{sub_norm}"
                                    win_value = None

                                raw_key_str = str(raw_key)
                                oi_norm = _np(raw_key_str)
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
                                test_cols_info.append(info)
                                test_col_map[original_key] = info
                                test_col_map[original_key.replace('-', '_')] = info

                # Build mark column metadata
                mark_cols_info = []
                mark_col_map = {}
                if selected_mark_cols:
                    normalized_to_original = {c.replace('-', '_'): c for c in selected_mark_cols}
                    years = sorted({y for y in (_parse_year_from_mark_col(c) for c in selected_mark_cols) if y})
                    if years:
                        cursor.execute(
                            "SELECT DISTINCT school_year, subject, course_name, mark_period"
                            " FROM student_marks_list WHERE school_year = ANY(%s) AND school_year IS NOT NULL",
                            [years]
                        )
                        for (year, subject, course_name, mark_period) in cursor.fetchall():
                            key_base = f"{_np(year)}_{_np(subject)}_{_np(course_name)}_{_np(mark_period)}"
                            ok = normalized_to_original.get(f"mark_{key_base}")
                            if not ok:
                                continue
                            info = {
                                'col_name': ok, 'year': year or '', 'subject': subject or '',
                                'course_name': course_name or '', 'mark_period': (mark_period or '').strip() or None
                            }
                            mark_cols_info.append(info)
                            mark_col_map[ok] = info
                            mark_col_map[ok.replace('-', '_')] = info

                # Build dynamic column headers using _get_display_name (aligned with dataexploration)
                dynamic_labels = COLUMN_DEFINITIONS.copy()
                for info in test_cols_info:
                    metric_map = {'ss': 'Scale Score', 'pl': 'Perf Lvl', 'oi': info.get('other_info_key', 'Info')}
                    metric_label = metric_map.get(info['metric'], info['metric'])
                    display_test = _get_display_name(info['test_name'])
                    display_subject = _get_display_name(info['subject'])
                    label = f"{display_test} {display_subject} ({info['year']}) {metric_label}"
                    if info.get('test_window'):
                        label += f" {info['test_window']}"
                    dynamic_labels[info['col_name']] = label

                for info in mark_cols_info:
                    period = info.get('mark_period') or 'Unknown'
                    label = f"{info['course_name']} ({info['subject']}) {period} {info['year']}"
                    dynamic_labels[info['col_name']] = label

                # Yield Header
                header_labels = [dynamic_labels.get(col, col) for col in selected_columns]
                yield header_labels
                logger.info(f"NYL CSV Export: Header yielded, {len(selected_columns)} columns")

                # Apply column filters (now that test_col_map is built)
                local_where_clauses = list(where_clauses)
                local_params = list(params)
                _apply_column_filters(
                    local_where_clauses, local_params, filters, test_col_map,
                    db_columns, enroll_columns, needs_enroll, needs_students, needs_eht
                )
                final_where_sql = " AND ".join(local_where_clauses)

                # Build SELECT subqueries for test and mark columns
                select_params = []
                test_selects = []
                for ti in test_cols_info:
                    metric_col = ti['metric']
                    if metric_col in {'ss', 'pl'}:
                        val_expr = f"r.{metric_col}"
                    else:
                        val_expr = "(r.other_info ->> %s)"
                        select_params.append(ti.get('other_info_key', ''))
                    window_clause = "AND t.test_window ILIKE %s " if ti.get('test_window') else ""
                    test_selects.append(
                        f"(SELECT {val_expr} FROM results r INNER JOIN tests t ON r.test_group_key = t.test_group_key"
                        f" WHERE r.local_student_id = cs.student_id AND r.school_year = %s"
                        f" AND t.test_name ILIKE %s AND t.subject ILIKE %s {window_clause}LIMIT 1)"
                        f" AS \"{ti['col_name']}\""
                    )
                    select_params += [ti['year'], f"%{ti['test_name']}%", f"%{ti['subject']}%"]
                    if ti.get('test_window'):
                        select_params.append(ti['test_window'])

                mark_selects = []
                for mi in mark_cols_info:
                    period_clause = "AND sm.mark_period ILIKE %s " if mi.get('mark_period') else "AND (sm.mark_period IS NULL OR sm.mark_period = '') "
                    mark_selects.append(
                        f"(SELECT sm.mark FROM student_marks_list sm WHERE sm.stu_annual_key = cs.stu_annual_key"
                        f" AND sm.school_year = %s AND sm.subject ILIKE %s AND sm.course_name ILIKE %s {period_clause}LIMIT 1)"
                        f" AS \"{mi['col_name']}\""
                    )
                    select_params += [mi['year'], f"%{mi['subject']}%", f"%{mi['course_name']}%"]
                    if mi.get('mark_period'):
                        select_params.append(mi['mark_period'])

                all_selects = ", ".join(base_col_selects) if base_col_selects else "cs.ssid"
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
                    f" WHERE {final_where_sql}"
                    f" ORDER BY {order_by_expr} {sort_dir_sql} NULLS LAST"
                )
                cursor.execute(query, select_params + local_params)

                # Build column order map to reorder raw DB row into selected_columns order
                db_col_names = [d[0] for d in cursor.description]
                col_index = {name: i for i, name in enumerate(db_col_names)}

                # Map each selected column to its DB position
                col_positions = []
                for col in selected_columns:
                    pos = col_index.get(col) if col in col_index else col_index.get(col.replace('-', '_'))
                    col_positions.append(pos)

                row_count = 0
                for row in cursor:
                    row_count += 1
                    yield [row[p] if p is not None else '' for p in col_positions]

                logger.info(f"NYL CSV Export: Completed, {row_count} rows exported")

        except Exception as e:
            import traceback
            logger.error(f"NYL CSV Export Error: {str(e)}")
            logger.error(traceback.format_exc())
            yield [f"Error exporting data: {str(e)}"]

    class Echo:
        def write(self, value):
            return value

    writer = csv.writer(Echo())

    def streaming_content():
        for row in csv_rows():
            yield writer.writerow(row)

    logger.info(f"NYL CSV Export: Creating StreamingHttpResponse with {len(selected_columns)} columns")
    response = StreamingHttpResponse(streaming_content(), content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="next_year_list.csv"'
    return response
