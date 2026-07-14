"""Column statistics view for Next Year List."""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db import connections
from django.core.cache import cache
import json
import hashlib
import statistics as stats_module

from dataexploration.view_modules.student_data_utils import (
    get_current_student_columns,
    get_student_enroll_columns,
)
from ..view_modules.column_management import get_column_expression
from ..view_modules.student_list_views import _is_unrestricted, _apply_nyl_school_filter
from users.decorators import page_access_required


def _np(v):
    """Normalise a string to a column-key-safe form."""
    return (v or '').replace(' ', '_').replace('-', '_')


def _parse_test_year(col_key):
    s = col_key.replace('-', '_')
    if not s.startswith('test_'):
        return None
    tail = s[len('test_'):]
    if len(tail) >= 10 and tail[4] == '_' and tail[9] == '_':
        return tail[:9].replace('_', '-')
    parts = tail.split('_', 1)
    return parts[0].replace('_', '-') if parts else None


def _parse_mark_year(col_key):
    s = col_key.replace('-', '_')
    if not s.startswith('mark_'):
        return None
    tail = s[len('mark_'):]
    if len(tail) >= 10 and tail[4] == '_' and tail[9] == '_':
        return tail[:9].replace('_', '-')
    parts = tail.split('_', 1)
    return parts[0].replace('_', '-') if parts else None


@login_required
@page_access_required('nextyearlist')
def get_column_statistics(request):
    """Return value distribution statistics for specific columns."""
    target_columns_json = request.GET.get('target_columns', '')
    try:
        target_columns = json.loads(target_columns_json) if target_columns_json else []
    except Exception:
        target_columns = []
    if not target_columns:
        return JsonResponse({'columns': {}})

    search = request.GET.get('search', '')
    school_year = request.GET.get('school_year', '')
    grade = request.GET.get('grade', '')
    status_param = request.GET.get('status')
    status = status_param if status_param is not None else 'Active'
    filters_json = request.GET.get('filters', '')
    try:
        filters = json.loads(filters_json) if filters_json else {}
    except Exception:
        filters = {}

    db_columns = get_current_student_columns()
    enroll_columns = get_student_enroll_columns()

    all_cols = set(target_columns) | set(filters.keys())
    enroll_fields = {'ell', 'birth_date', 'us_enter_date', 'fep_date', 'migrant'}
    needs_enroll = any(c in enroll_fields for c in all_cols)
    needs_students = 'ltel' in all_cols
    needs_eht = 'elem_teacher_name' in all_cols

    join_sql = ''
    if needs_enroll:
        join_sql += (
            ' LEFT JOIN (SELECT DISTINCT ON (student_id, school_year) * FROM student_enroll'
            ' ORDER BY student_id, school_year, enter_date DESC NULLS LAST, leave_date DESC NULLS LAST'
            ') se ON se.student_id = cs.student_id AND se.school_year = cs.school_year'
        )
    if needs_students:
        join_sql += ' LEFT JOIN students s ON s.student_annual_key = cs.stu_annual_key'
        join_sql += (
            ' LEFT JOIN (SELECT DISTINCT ON (state_student_number) * FROM students'
            ' ORDER BY state_student_number, school_year DESC NULLS LAST, enter_date DESC NULLS LAST, leave_date DESC NULLS LAST'
            ') sssid ON sssid.state_student_number = cs.ssid'
        )
    if needs_eht:
        join_sql += ' LEFT JOIN elementary_homeroom_teacher eht ON eht.stu_annual_key = cs.stu_annual_key'

    NYL_JOIN = (
        ' LEFT JOIN nextyearlist_placement nyl ON nyl.ssid = cs.ssid AND nyl.school_year = cs.school_year'
        ' LEFT JOIN mathr_recommendation mr ON mr.ssid = cs.ssid AND mr.school_year = cs.school_year'
    )

    base_where_clauses = ['1=1']
    base_params = []

    if search:
        sp = f'%{search}%'
        base_where_clauses.append('(cs.student_name ILIKE %s OR cs.ssid ILIKE %s OR cs.student_id ILIKE %s)')
        base_params.extend([sp, sp, sp])
    if school_year:
        base_where_clauses.append('cs.school_year = %s')
        base_params.append(school_year)
    if grade:
        base_where_clauses.append('cs.grade = %s')
        base_params.append(grade)
    if status:
        base_where_clauses.append('cs.status = %s')
        base_params.append(status)

    _apply_nyl_school_filter(base_where_clauses, base_params, request.user)

    if not _is_unrestricted(request.user) and hasattr(request.user, 'grade_levels'):
        gls = request.user.grade_levels
        if isinstance(gls, list) and gls:
            grade_restricted_roles = {'School Gr View', 'School View', 'School Admin'}
            current_role = None
            if hasattr(request.user, 'active_role') and request.user.active_role:
                current_role = request.user.active_role.name
            elif request.user.roles.count() == 1:
                current_role = request.user.roles.first().name
            if current_role in grade_restricted_roles:
                base_where_clauses.append('cs.grade = ANY(%s)')
                base_params.append(gls)

    if hasattr(request.user, 'get_accessible_student_keys'):
        accessible_keys = request.user.get_accessible_student_keys()
        if accessible_keys is not None:
            if not accessible_keys:
                base_where_clauses.append('1=0')
            else:
                base_where_clauses.append('cs.stu_annual_key = ANY(%s)')
                base_params.append(accessible_keys)

    where_sql = ' AND '.join(base_where_clauses)
    results_data = {}

    with connections['default'].cursor() as cursor:
        for col in target_columns:
            query = None
            q_params = list(base_params)
            is_string = False

            if col.startswith('test_'):
                yr = _parse_test_year(col)
                if not yr:
                    continue
                norm_col = col.replace('-', '_')
                if norm_col.upper().endswith('_DFP'):
                    metric = 'dfp'
                elif norm_col.upper().endswith('_SS'):
                    metric = 'ss'
                elif norm_col.upper().endswith('_PL'):
                    metric = 'pl'
                else:
                    metric = 'oi'

                cursor.execute(
                    'SELECT 1 FROM information_schema.columns'
                    " WHERE table_name='tests' AND column_name='test_window'"
                )
                has_tw = cursor.fetchone() is not None
                if has_tw:
                    cursor.execute(
                        "SELECT DISTINCT r.school_year, t.test_name, t.subject, COALESCE(t.test_window,'')"
                        ' FROM results r INNER JOIN tests t ON r.test_group_key = t.test_group_key'
                        ' WHERE r.school_year = %s',
                        [yr],
                    )
                else:
                    cursor.execute(
                        "SELECT DISTINCT r.school_year, t.test_name, t.subject, ''"
                        ' FROM results r INNER JOIN tests t ON r.test_group_key = t.test_group_key'
                        ' WHERE r.school_year = %s',
                        [yr],
                    )

                test_info = None
                for (row_yr, test_name, subject, test_window) in cursor.fetchall():
                    win_norm = _np(test_window).strip('_')
                    key_base = (f'{_np(row_yr)}_{_np(test_name)}_{_np(subject)}_{win_norm}'
                                if win_norm else f'{_np(row_yr)}_{_np(test_name)}_{_np(subject)}')
                    candidate = f'test_{key_base}_{metric.upper()}'
                    if candidate.replace('-', '_') == norm_col:
                        test_info = {
                            'year': row_yr,
                            'test_name': test_name,
                            'subject': subject,
                            'test_window': (test_window or '').strip() or None,
                            'metric': metric,
                        }
                        if metric == 'dfp':
                            test_info['other_info_key'] = 'dfp'
                        break

                if not test_info:
                    continue

                oi_key = test_info.get('other_info_key', '')
                val_expr = f'r.{metric}' if metric in ('ss', 'pl') else f"(r.other_info ->> '{oi_key}')"
                win_cl = 'AND t.test_window ILIKE %s' if test_info.get('test_window') else ''
                win_p = [test_info['test_window']] if test_info.get('test_window') else []

                query = (
                    f'SELECT {val_expr} AS val, COUNT(*) AS cnt FROM results r'
                    f' INNER JOIN tests t ON r.test_group_key = t.test_group_key'
                    f' WHERE r.local_student_id IN ('
                    f'  SELECT cs.student_id FROM current_student cs {NYL_JOIN} {join_sql} WHERE {where_sql}'
                    f' )'
                    f' AND r.school_year = %s AND t.test_name ILIKE %s AND t.subject ILIKE %s {win_cl}'
                    f' AND {val_expr} IS NOT NULL'
                    f' GROUP BY val ORDER BY val LIMIT 50'
                )
                q_params = list(base_params) + [
                    test_info['year'], test_info['test_name'], test_info['subject']
                ] + win_p
                is_string = (metric == 'pl')

            elif col.startswith('mark_'):
                yr = _parse_mark_year(col)
                if not yr:
                    continue
                norm_col = col.replace('-', '_')
                cursor.execute(
                    'SELECT DISTINCT school_year, subject, course_name, mark_period'
                    ' FROM student_marks_list WHERE school_year = %s',
                    [yr],
                )
                mark_info = None
                for (row_yr, subject, course_name, mark_period) in cursor.fetchall():
                    key_base = f'{_np(row_yr)}_{_np(subject)}_{_np(course_name)}_{_np(mark_period)}'
                    if f'mark_{key_base}' == norm_col:
                        mark_info = {
                            'year': row_yr,
                            'subject': subject,
                            'course_name': course_name,
                            'mark_period': (mark_period or '').strip() or None,
                        }
                        break

                if not mark_info:
                    continue

                per_cl = 'AND sm.mark_period ILIKE %s' if mark_info.get('mark_period') else "AND (sm.mark_period IS NULL OR sm.mark_period = '')"
                per_p = [mark_info['mark_period']] if mark_info.get('mark_period') else []

                query = (
                    f'SELECT sm.mark AS val, COUNT(*) AS cnt FROM student_marks_list sm'
                    f' WHERE sm.stu_annual_key IN ('
                    f'  SELECT cs.stu_annual_key FROM current_student cs {NYL_JOIN} {join_sql} WHERE {where_sql}'
                    f' )'
                    f' AND sm.school_year = %s AND sm.subject ILIKE %s AND sm.course_name ILIKE %s {per_cl}'
                    f' AND sm.mark IS NOT NULL'
                    f' GROUP BY val ORDER BY val LIMIT 50'
                )
                q_params = list(base_params) + [
                    mark_info['year'], f"%{mark_info['subject']}%", f"%{mark_info['course_name']}%"
                ] + per_p
                is_string = True

            else:
                expr, _ = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
                if not expr:
                    continue
                null_check = f'AND {expr} IS NOT NULL'
                if col not in {'birth_date', 'us_enter_date', 'fep_date'}:
                    null_check += f" AND {expr} != ''"
                query = (
                    f'SELECT {expr} AS val, COUNT(*) AS cnt'
                    f' FROM current_student cs {NYL_JOIN} {join_sql}'
                    f' WHERE {where_sql} {null_check}'
                    f' GROUP BY val ORDER BY val ASC NULLS FIRST LIMIT 50'
                )
                q_params = list(base_params)
                is_string = False

            if not query:
                continue

            try:
                cursor.execute(query, q_params)
                rows = cursor.fetchall()
            except Exception:
                continue

            total_count = 0
            distribution = []
            numeric_values = []
            numeric_count = 0

            for val, cnt in rows:
                total_count += cnt
                if is_string and val is not None:
                    val = str(val)
                is_num = False
                if val is not None and not is_string:
                    try:
                        fval = float(val)
                        numeric_values.extend([fval] * cnt)
                        numeric_count += cnt
                        is_num = True
                    except (ValueError, TypeError):
                        pass
                distribution.append({'value': val, 'count': cnt, '_n': is_num})

            for item in distribution:
                item['percent'] = round(item['count'] / total_count * 100, 1) if total_count else 0
                del item['_n']

            stats = {
                'count': total_count,
                'unique': len(distribution),
                'avg': None,
                'median': None,
                'distribution': distribution,
            }
            if not is_string and total_count > 0 and numeric_count / total_count > 0.9 and numeric_values:
                distribution.sort(key=lambda x: float(x['value']) if x['value'] is not None else float('-inf'))
                stats['avg'] = round(stats_module.mean(numeric_values), 2)
                stats['median'] = round(stats_module.median(numeric_values), 2)

            results_data[col] = stats

    return JsonResponse({'columns': results_data})

