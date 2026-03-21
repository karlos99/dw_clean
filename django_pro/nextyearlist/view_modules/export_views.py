"""CSV export for Next Year List."""
from django.contrib.auth.decorators import login_required
from django.http import StreamingHttpResponse
from django.db import connections
from django.core.cache import cache
import json
import csv
import hashlib

from dataexploration.models import SavedQuery
from dataexploration.view_modules.student_data_utils import get_current_student_columns, get_student_enroll_columns
from ..constants import COLUMN_DEFINITIONS
from ..utils import can_user_view_query
from ..view_modules.column_management import get_saved_default_columns, get_column_expression
from ..view_modules.student_list_views import _is_unrestricted, _apply_nyl_school_filter
from users.decorators import page_access_required


@login_required
@page_access_required('nextyearlist')
def export_students_csv(request):
    """Export filtered next-year student data as a streaming CSV."""

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

    db_columns = get_current_student_columns()
    enroll_columns = get_student_enroll_columns()

    selected_test_cols = [c for c in selected_columns if isinstance(c, str) and c.startswith('test_')]
    selected_mark_cols = [c for c in selected_columns if isinstance(c, str) and c.startswith('mark_')]
    base_cols = [c for c in selected_columns if isinstance(c, str) and not c.startswith(('test_', 'mark_')) and c in COLUMN_DEFINITIONS]

    enroll_fields = {'ell', 'birth_date', 'us_enter_date', 'fep_date', 'migrant'}
    needs_enroll = any(c in enroll_fields for c in selected_columns) or (isinstance(filters, dict) and any(k in enroll_fields for k in filters)) or sort_by in enroll_fields
    needs_students = any(c in {'ltel'} for c in selected_columns) or (isinstance(filters, dict) and 'ltel' in filters) or sort_by == 'ltel'
    needs_eht = (isinstance(filters, dict) and 'elem_teacher_name' in filters) or sort_by == 'elem_teacher_name'

    where_clauses = ["1=1"]
    params = []

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

    col_selects = []
    col_headers = []
    for col in base_cols:
        expr, alias = get_column_expression(col, db_columns, enroll_columns, needs_enroll, needs_students, needs_eht)
        if expr and alias:
            col_selects.append(f"{expr} AS {alias}" if expr != f"cs.{alias}" else expr)
            col_headers.append(COLUMN_DEFINITIONS.get(col, col))

    where_sql = " AND ".join(where_clauses)
    order_by_expr = "cs.student_name"
    if sort_by:
        if sort_by in {'next_year_school_state_id', 'next_year_school_name'}:
            order_by_expr = f"nyl.{sort_by}"
        elif sort_by == 'math_selection':
            order_by_expr = "mr.math_selection"
        elif sort_by in db_columns:
            order_by_expr = f"cs.{sort_by}"
    sort_dir = 'DESC' if sort_dir == 'desc' else 'ASC'

    select_sql = ", ".join(col_selects) if col_selects else "cs.ssid"
    query = (
        f"SELECT {select_sql}"
        f" FROM nextyearlist_placement nyl"
        f" INNER JOIN current_student cs ON cs.ssid = nyl.ssid AND cs.school_year = nyl.school_year"
        f" LEFT JOIN mathr_recommendation mr ON mr.ssid = nyl.ssid AND mr.school_year = nyl.school_year"
        f" {join_sql}"
        f" WHERE {where_sql}"
        f" ORDER BY {order_by_expr} {sort_dir} NULLS LAST"
    )

    def csv_rows():
        yield col_headers
        with connections['default'].cursor() as cursor:
            cursor.execute(query, params)
            for row in cursor:
                yield list(row)

    class Echo:
        def write(self, value):
            return value

    writer = csv.writer(Echo())

    def streaming_content():
        for row in csv_rows():
            yield writer.writerow(row)

    response = StreamingHttpResponse(streaming_content(), content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="next_year_list.csv"'
    return response
