"""Schedule filter SQL for Data Exploration list/export/stats (schedule_list)."""

from __future__ import annotations

import hashlib
import json

from django.core.cache import cache
from django.db import connection

from ..utils import (
    SCHEDULE_FILTER_MODE_DISTRICT,
    SCHEDULE_FILTER_MODE_SCHOOL,
    SCHEDULE_FILTER_MODE_SCHOOL_ELEM,
    SCHEDULE_FILTER_MODE_TEACHER,
    SCHEDULE_FILTER_MODE_TEACHER_ELEM,
    get_user_roles,
    is_unrestricted,
    schedule_filter_bar_visible,
    schedule_filter_ui_mode,
    single_school_state_id_from_column_filters,
)


def _resolve_scope_state_school_id(user, column_filters=None):
    if getattr(user, 'active_school', None):
        return user.active_school.state_id
    scoped = single_school_state_id_from_column_filters(column_filters)
    if scoped:
        return scoped
    return None


def _school_scope_sql(alias, user, params, column_filters=None):
    """Return SQL fragment AND ... for state_school_id on ``alias``."""
    override = _resolve_scope_state_school_id(user, column_filters)
    if override:
        params.append(override)
        return f" AND {alias}.state_school_id = %s"
    if is_unrestricted(user):
        return ''
    schools = getattr(user, 'schools', None)
    if isinstance(schools, list) and schools:
        params.append(schools)
        return f" AND {alias}.state_school_id = ANY(%s)"
    params.append('__none__')
    return f" AND {alias}.state_school_id = %s"


def _teacher_staff_sql(alias, user, params):
    roles = get_user_roles(user)
    if 'Teacher' not in roles:
        return ''
    staff_id = getattr(user, 'staff_id', None) or ''
    if not staff_id:
        return f" AND 1=0"
    params.append(staff_id)
    return f" AND {alias}.staff_employee_id = %s"


def _append_school_year_clauses(alias, clauses, params, school_year):
    """Add school_year predicates for uncorrelated subqueries (ClickHouse-safe)."""
    if isinstance(school_year, list):
        if len(school_year) == 1:
            clauses.append(f"{alias}.school_year = %s")
            params.append(school_year[0])
        elif len(school_year) > 1:
            clauses.append(f"{alias}.school_year = ANY(%s)")
            params.append(school_year)
    elif school_year:
        clauses.append(f"{alias}.school_year = %s")
        params.append(school_year)


def _scope_clause_parts(alias, user, params, column_filters=None):
    """Return list of AND-free SQL fragments for school/teacher scope."""
    parts = []
    scope_sql = _school_scope_sql(alias, user, params, column_filters)
    if scope_sql:
        parts.append(scope_sql.strip().removeprefix('AND').strip())
    teacher_sql = _teacher_staff_sql(alias, user, params)
    if teacher_sql:
        parts.append(teacher_sql.strip().removeprefix('AND').strip())
    return parts


def _current_student_scope_clauses(user, params, column_filters=None):
    """WHERE fragments for current_student (school scope, ClickHouse-safe subqueries)."""
    clauses = []
    override = _resolve_scope_state_school_id(user, column_filters)
    if override:
        clauses.append("cs.school_state_id = %s")
        params.append(override)
        return clauses
    if is_unrestricted(user):
        return clauses
    if getattr(user, 'active_school', None):
        clauses.append("cs.school_state_id = %s")
        params.append(user.active_school.state_id)
    elif isinstance(getattr(user, 'schools', None), list) and user.schools:
        clauses.append("cs.school_state_id = ANY(%s)")
        params.append(user.schools)
    else:
        clauses.append("1=0")
    return clauses


def _schedule_student_year_scope_sql(user, school_year, params, column_filters=None, status=None):
    """
    Restrict schedule_list rows to students present in current_student for the
    selected school year (matches the main list's year slice).
    """
    cs_clauses = []
    cs_params = []
    _append_school_year_clauses('cs', cs_clauses, cs_params, school_year)
    status_vals = _normalize_multi_value(status)
    if len(status_vals) == 1:
        cs_clauses.append("cs.status = %s")
        cs_params.append(status_vals[0])
    elif len(status_vals) > 1:
        cs_clauses.append("cs.status = ANY(%s)")
        cs_params.append(status_vals)
    cs_clauses.extend(_current_student_scope_clauses(user, cs_params, column_filters))
    if not cs_clauses:
        return ""
    sql = (
        " AND sl.stu_annual_key IN ("
        "SELECT cs.stu_annual_key FROM current_student cs WHERE "
        + " AND ".join(cs_clauses)
        + ")"
    )
    params.extend(cs_params)
    return sql


def _normalize_multi_value(raw):
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(v).strip() for v in raw if str(v).strip()]
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith('['):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except Exception:
            pass
    if ',' in text:
        return [p.strip() for p in text.split(',') if p.strip()]
    return [text]


def _append_in_clause(column, values, clauses, params):
    values = _normalize_multi_value(values)
    if not values:
        return
    if len(values) == 1:
        clauses.append(f"{column} = %s")
        params.append(values[0])
    else:
        clauses.append(f"{column} = ANY(%s)")
        params.append(values)


def _schedule_match_subquery(
    user,
    school_year,
    period=None,
    subject=None,
    course_name=None,
    staff=None,
    column_filters=None,
    roles=None,
):
    """Uncorrelated subquery: students matching schedule row filters."""
    roles = roles or get_user_roles(user)
    params = []
    clauses = ["sl.schedule_status = 'Active'"]
    _append_school_year_clauses('sl', clauses, params, school_year)
    clauses.extend(_scope_clause_parts('sl', user, params, column_filters))
    _append_in_clause('sl.period', period, clauses, params)
    _append_in_clause('sl.course_subject', subject, clauses, params)
    _append_in_clause('sl.course_name', course_name, clauses, params)
    if _normalize_multi_value(staff) and 'Teacher' not in roles:
        _append_in_clause('sl.staff_employee_id', staff, clauses, params)
    sql = (
        "SELECT sl.stu_annual_key FROM schedule_list sl WHERE "
        + " AND ".join(clauses)
    )
    return sql, params


def _read_multi_param(request, key, saved_filters):
    if key in request.GET:
        vals = request.GET.getlist(key)
        if len(vals) > 1:
            return _normalize_multi_value(vals)
        return _normalize_multi_value(request.GET.get(key))
    if key not in saved_filters:
        return []
    return _normalize_multi_value(saved_filters.get(key))


def _restrict_schedule_filters(user, schedule_filters, column_filters=None):
    """Drop schedule fields this user is not allowed to filter on."""
    mode = schedule_filter_ui_mode(user, column_filters)
    if mode == SCHEDULE_FILTER_MODE_TEACHER:
        schedule_filters['schedule_subject'] = []
        schedule_filters['schedule_staff'] = []
    elif mode == SCHEDULE_FILTER_MODE_TEACHER_ELEM:
        schedule_filters['schedule_period'] = []
        schedule_filters['schedule_subject'] = []
        schedule_filters['schedule_staff'] = []
    elif mode == SCHEDULE_FILTER_MODE_SCHOOL_ELEM:
        schedule_filters['schedule_period'] = []
    return schedule_filters


def _sl_period_sql_suffix(periods, ui_mode):
    periods = _normalize_multi_value(periods)
    if not periods:
        return '', []
    if ui_mode not in (
        SCHEDULE_FILTER_MODE_DISTRICT,
        SCHEDULE_FILTER_MODE_SCHOOL,
        SCHEDULE_FILTER_MODE_TEACHER,
    ):
        return '', []
    if len(periods) == 1:
        return " AND sl.period = %s", [periods[0]]
    return " AND sl.period = ANY(%s)", [periods]


def parse_schedule_marks_filters(request, saved_filters=None, column_filters=None, user=None):
    """Read schedule filter GET params with saved-query fallback (multi-value aware)."""
    saved_filters = saved_filters or {}
    parsed = {
        'schedule_period': _read_multi_param(request, 'schedule_period', saved_filters),
        'schedule_subject': _read_multi_param(request, 'schedule_subject', saved_filters),
        'schedule_course_name': _read_multi_param(request, 'schedule_course_name', saved_filters),
        'schedule_staff': _read_multi_param(request, 'schedule_staff', saved_filters),
    }
    if user:
        _restrict_schedule_filters(user, parsed, column_filters)
    return parsed


def _schedule_filters_active(schedule_filters):
    return any(_normalize_multi_value(v) for v in (schedule_filters or {}).values())


def apply_schedule_marks_filters(where_clauses, params, user, schedule_filters, school_year, column_filters=None):
    """Append IN-subquery predicates from schedule_list (ClickHouse-safe)."""
    schedule_filters = dict(schedule_filters or {})
    _restrict_schedule_filters(user, schedule_filters, column_filters)
    if not _schedule_filters_active(schedule_filters):
        return
    if not schedule_filter_bar_visible(user, column_filters, school_year):
        return
    roles = get_user_roles(user)
    sub_sql, sub_params = _schedule_match_subquery(
        user,
        school_year,
        period=schedule_filters.get('schedule_period'),
        subject=schedule_filters.get('schedule_subject'),
        course_name=schedule_filters.get('schedule_course_name'),
        staff=schedule_filters.get('schedule_staff'),
        column_filters=column_filters,
        roles=roles,
    )
    where_clauses.append(f"cs.stu_annual_key IN ({sub_sql})")
    params.extend(sub_params)


def _cache_key(prefix, user, school_year, period, subject, course_name, column_filters=None, status=None):
    active = getattr(getattr(user, 'active_school', None), 'state_id', None)
    scoped = single_school_state_id_from_column_filters(column_filters)
    schools = getattr(user, 'schools', None) if isinstance(getattr(user, 'schools', None), list) else []
    staff = getattr(user, 'staff_id', None) or ''
    raw = json.dumps({
        'sy': school_year,
        'period': _normalize_multi_value(period),
        'subject': _normalize_multi_value(subject),
        'course': _normalize_multi_value(course_name),
        'status': _normalize_multi_value(status),
        'active': active,
        'scoped': scoped,
        'schools': schools,
        'staff': staff,
        'uid': user.id,
    }, sort_keys=True)
    return f"dataexp:sched_filt:{prefix}:{hashlib.md5(raw.encode()).hexdigest()}"


def fetch_schedule_filter_options(
    user,
    school_year,
    period=None,
    subject=None,
    course_name=None,
    column_filters=None,
    status=None,
):
    """Return {periods, subjects, courses, staff} for cascading dropdowns."""
    periods = _normalize_multi_value(period)
    subjects = _normalize_multi_value(subject)
    courses = _normalize_multi_value(course_name)
    ui_mode = schedule_filter_ui_mode(user, column_filters)

    if not schedule_filter_bar_visible(user, column_filters, school_year):
        return {'periods': [], 'subjects': [], 'courses': [], 'staff': []}

    cache_key = _cache_key(
        'opts', user, school_year, periods, subjects, courses, column_filters, status,
    )
    cache_key = f"{cache_key}:{ui_mode}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = {'periods': [], 'subjects': [], 'courses': [], 'staff': []}

    with connection.cursor() as cursor:
        if ui_mode in (
            SCHEDULE_FILTER_MODE_DISTRICT,
            SCHEDULE_FILTER_MODE_SCHOOL,
            SCHEDULE_FILTER_MODE_TEACHER,
        ):
            p_params = [school_year]
            p_sql = (
                "SELECT DISTINCT sl.period FROM schedule_list sl "
                "WHERE sl.school_year = %s AND sl.schedule_status = 'Active' "
                "AND sl.period IS NOT NULL AND sl.period <> ''"
            )
            p_sql += _school_scope_sql('sl', user, p_params, column_filters)
            p_sql += _teacher_staff_sql('sl', user, p_params)
            p_sql += _schedule_student_year_scope_sql(
                user, school_year, p_params, column_filters, status,
            )
            p_sql += " ORDER BY sl.period"
            cursor.execute(p_sql, p_params)
            result['periods'] = [r[0] for r in cursor.fetchall() if r[0]]

        if ui_mode in (
            SCHEDULE_FILTER_MODE_DISTRICT,
            SCHEDULE_FILTER_MODE_SCHOOL,
            SCHEDULE_FILTER_MODE_SCHOOL_ELEM,
        ):
            s_params = [school_year]
            s_sql = (
                "SELECT DISTINCT sl.course_subject FROM schedule_list sl "
                "WHERE sl.school_year = %s AND sl.schedule_status = 'Active' "
                "AND sl.course_subject IS NOT NULL AND sl.course_subject <> ''"
            )
            s_sql += _school_scope_sql('sl', user, s_params, column_filters)
            s_sql += _teacher_staff_sql('sl', user, s_params)
            period_sql, period_params = _sl_period_sql_suffix(periods, ui_mode)
            s_sql += period_sql
            s_params.extend(period_params)
            s_sql += _schedule_student_year_scope_sql(
                user, school_year, s_params, column_filters, status,
            )
            s_sql += " ORDER BY sl.course_subject"
            cursor.execute(s_sql, s_params)
            result['subjects'] = [r[0] for r in cursor.fetchall() if r[0]]

        load_courses = ui_mode in (
            SCHEDULE_FILTER_MODE_DISTRICT,
            SCHEDULE_FILTER_MODE_SCHOOL,
            SCHEDULE_FILTER_MODE_SCHOOL_ELEM,
            SCHEDULE_FILTER_MODE_TEACHER,
            SCHEDULE_FILTER_MODE_TEACHER_ELEM,
        )
        if load_courses:
            c_params = [school_year]
            c_sql = (
                "SELECT DISTINCT sl.course_name FROM schedule_list sl "
                "WHERE sl.school_year = %s AND sl.schedule_status = 'Active' "
                "AND sl.course_name IS NOT NULL AND sl.course_name <> ''"
            )
            if ui_mode in (
                SCHEDULE_FILTER_MODE_DISTRICT,
                SCHEDULE_FILTER_MODE_SCHOOL,
                SCHEDULE_FILTER_MODE_SCHOOL_ELEM,
            ) and subjects:
                if len(subjects) == 1:
                    c_sql += " AND sl.course_subject = %s"
                    c_params.append(subjects[0])
                else:
                    c_sql += " AND sl.course_subject = ANY(%s)"
                    c_params.append(subjects)
            c_sql += _school_scope_sql('sl', user, c_params, column_filters)
            c_sql += _teacher_staff_sql('sl', user, c_params)
            period_sql, period_params = _sl_period_sql_suffix(periods, ui_mode)
            c_sql += period_sql
            c_params.extend(period_params)
            c_sql += _schedule_student_year_scope_sql(
                user, school_year, c_params, column_filters, status,
            )
            c_sql += " ORDER BY sl.course_name"
            cursor.execute(c_sql, c_params)
            result['courses'] = [r[0] for r in cursor.fetchall() if r[0]]

        if ui_mode in (
            SCHEDULE_FILTER_MODE_DISTRICT,
            SCHEDULE_FILTER_MODE_SCHOOL,
            SCHEDULE_FILTER_MODE_SCHOOL_ELEM,
        ):
            st_params = [school_year]
            st_sql = (
                "SELECT sl.staff_employee_id, MAX(sl.staff_name) AS staff_name "
                "FROM schedule_list sl "
                "WHERE sl.school_year = %s AND sl.schedule_status = 'Active' "
                "AND sl.staff_employee_id IS NOT NULL AND sl.staff_employee_id <> ''"
            )
            if subjects:
                if len(subjects) == 1:
                    st_sql += " AND sl.course_subject = %s"
                    st_params.append(subjects[0])
                else:
                    st_sql += " AND sl.course_subject = ANY(%s)"
                    st_params.append(subjects)
            if courses:
                if len(courses) == 1:
                    st_sql += " AND sl.course_name = %s"
                    st_params.append(courses[0])
                else:
                    st_sql += " AND sl.course_name = ANY(%s)"
                    st_params.append(courses)
            st_sql += _school_scope_sql('sl', user, st_params, column_filters)
            period_sql, period_params = _sl_period_sql_suffix(periods, ui_mode)
            st_sql += period_sql
            st_params.extend(period_params)
            st_sql += _schedule_student_year_scope_sql(
                user, school_year, st_params, column_filters, status,
            )
            st_sql += " GROUP BY sl.staff_employee_id ORDER BY staff_name"
            cursor.execute(st_sql, st_params)
            result['staff'] = [
                {'id': r[0], 'name': r[1] or r[0]}
                for r in cursor.fetchall()
                if r[0]
            ]

    cache.set(cache_key, result, 300)
    return result
