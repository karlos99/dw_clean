"""Query execution API views: run saved queries, SQL lab, schema browser."""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from dataviz.utils.data_loader import run_raw_sql, run_saved_query

from ..mixins import is_project_admin, FORBIDDEN


@login_required
@require_POST
def query_run_api(request):
    """
    Execute a query (saved or visual drag-drop) and return rows + columns.

    Accepts both:
      - JSON body: { source_type, source_id, source_sql, extra_filters }
      - Form data: saved_query_id=<int>  (sent by HTMX select change)
    """
    if not is_project_admin(request.user):
        return FORBIDDEN

    content_type = request.content_type or ''
    if 'application/json' in content_type:
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        source_type   = body.get('source_type', 'query')
        extra_filters = body.get('extra_filters', {})
        source_id     = body.get('source_id')
        source_sql    = body.get('source_sql', '').strip()
    else:
        # Form-encoded (HTMX select hx-include)
        source_type   = request.POST.get('source_type', 'query')
        extra_filters = {}
        source_id     = request.POST.get('source_id') or request.POST.get('saved_query_id')
        source_sql    = request.POST.get('source_sql', '').strip()

    is_htmx = bool(request.headers.get('HX-Request'))

    if source_type == 'sql':
        result = run_raw_sql(source_sql)
        columns = result.get('columns', [])
        rows    = result.get('rows', [])
        if result.get('error'):
            if is_htmx:
                from django.shortcuts import render
                return render(request, 'dataviz/partials/query_columns.html', {
                    'columns': [], 'row_count': 0, 'error': result['error'],
                })
            return JsonResponse({'error': result['error']}, status=400)
    else:
        # Saved query path
        if not source_id:
            # Empty select from chart builder — return empty partial, not 400
            if is_htmx:
                from django.shortcuts import render
                return render(request, 'dataviz/partials/query_columns.html', {
                    'columns': [], 'row_count': None,
                })
            return JsonResponse({'error': 'source_id required for saved query'}, status=400)

        # HTMX fast path: chart builder only needs column names, not full query execution
        if is_htmx:
            from django.shortcuts import render
            from dataexploration.models import SavedQuery
            from dataexploration.utils import can_user_view_query
            try:
                sq = SavedQuery.objects.get(pk=source_id)
                if not can_user_view_query(request.user, sq):
                    return render(request, 'dataviz/partials/query_columns.html', {
                        'columns': [], 'row_count': None, 'error': 'Access denied',
                    })
                all_cols = sq.config.get('columns', [])
                columns = [c for c in all_cols if c and isinstance(c, str)]
                if not columns:
                    columns = ['student_id', 'student_name', 'grade', 'school', 'school_year']
            except SavedQuery.DoesNotExist:
                columns = []
            return render(request, 'dataviz/partials/query_columns.html', {
                'columns': columns, 'row_count': None,
            })

        # JSON API path: execute the full query
        result = run_saved_query(source_id, extra_filters, request.user)
        if result.get('error'):
            return JsonResponse({'error': result['error']}, status=400)
        columns = result.get('columns', [])
        rows    = result.get('rows', [])

    return JsonResponse({
        'rows':      rows,
        'columns':   columns,
        'row_count': len(rows),
        'success':   True,
    })


@login_required
@require_POST
def sql_lab_api(request):
    """Execute a read-only raw SQL query (SQL Lab mode). Accepts JSON or form data."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    content_type = request.content_type or ''
    if 'application/json' in content_type:
        try:
            body = json.loads(request.body)
            sql = body.get('sql', '') or body.get('source_sql', '')
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
    else:
        # Form-encoded from HTMX hx-include on the textarea
        sql = request.POST.get('raw_sql', '') or request.POST.get('sql', '')

    result = run_raw_sql(sql)
    if result.get('error'):
        if request.headers.get('HX-Request'):
            from django.shortcuts import render
            return render(request, 'dataviz/partials/query_columns.html', {
                'columns': [], 'row_count': 0, 'error': result['error'],
            })
        return JsonResponse({'error': result['error']}, status=400)

    columns  = result.get('columns', [])
    rows     = result.get('rows', [])

    if request.headers.get('HX-Request'):
        from django.shortcuts import render
        return render(request, 'dataviz/partials/query_columns.html', {
            'columns':   columns,
            'row_count': len(rows),
        })

    return JsonResponse({
        'columns':   columns,
        'col_types': result.get('col_types', {}),
        'rows':      rows,
        'row_count': result.get('row_count', 0),
        'has_more':  result.get('has_more', False),
        'success':   True,
    })


@login_required
def query_columns_api(request, query_id):
    """Return column names for a SavedQuery config (no query execution).

    Returns all columns stored in the query config (base, test_*, mark_*).
    ``run_saved_query`` now supports fetching data for all column types.
    """
    if not is_project_admin(request.user):
        return FORBIDDEN
    from dataexploration.models import SavedQuery
    from dataexploration.utils import can_user_view_query
    try:
        sq = SavedQuery.objects.get(pk=query_id)
    except SavedQuery.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    if not can_user_view_query(request.user, sq):
        return JsonResponse({'error': 'Access denied'}, status=403)

    all_cols = sq.config.get('columns', [])
    valid = [c for c in all_cols if c and isinstance(c, str)]
    if not valid:
        valid = ['student_id', 'student_name', 'grade', 'school', 'school_year']
    return JsonResponse({'columns': valid})


# ── Filter options for chart builder ──────────────────────────────────────────

# Columns that make sense as discrete multi-select filters in the chart builder.
# Excludes unique / high-cardinality identifiers.
_FILTERABLE_COLUMNS = frozenset([
    'school_year', 'grade', 'school', 'gender', 'race', 'ell', 'sed',
    'foster', 'homeless', 'sped', 'gate', 'in_504', 'dli', 'ltel',
    'status', 'suspension_indicator',
])

# Enroll-dependent fields need a LEFT JOIN to student_enroll.
_ENROLL_FILTER_EXPRS = {
    'ell': 'COALESCE(cs.ell, se.ell_status)',
}

_ENROLL_JOIN = (
    " LEFT JOIN ("
    " SELECT DISTINCT ON (student_id, school_year) *"
    " FROM student_enroll"
    " ORDER BY student_id, school_year, enter_date DESC NULLS LAST, leave_date DESC NULLS LAST"
    ") se ON se.student_id = cs.student_id AND se.school_year = cs.school_year"
)


@login_required
def query_filter_options_api(request, query_id):
    """Return distinct values for every filterable column available in a query.

    Response: ``{ filter_options: { grade: ['K','1',...], school: [...], ... } }``

    Includes:
    - Base demographic columns from ``current_student`` (grade, school, …)
    - Assessment PL columns (``test_*_PL``) — categorical performance levels
    - Mark columns (``mark_*``) — categorical grade/mark values

    Excludes:
    - SS columns (``test_*_SS``) — numeric scale scores, high cardinality
    """
    if not is_project_admin(request.user):
        return FORBIDDEN

    from dataexploration.models import SavedQuery
    from dataexploration.utils import can_user_view_query
    from django.db import connection

    try:
        sq = SavedQuery.objects.get(pk=query_id)
    except SavedQuery.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    if not can_user_view_query(request.user, sq):
        return JsonResponse({'error': 'Access denied'}, status=403)

    config  = sq.config or {}
    columns = config.get('columns', [])
    filters = config.get('filters', {}) if isinstance(config.get('filters'), dict) else {}

    # Separate columns into base demographic, test PL, and mark columns
    filter_cols = [c for c in columns if c in _FILTERABLE_COLUMNS]
    pl_cols     = [c for c in columns if c.startswith('test_') and c.endswith('_PL')]
    mark_cols   = [c for c in columns if c.startswith('mark_')]

    if not filter_cols and not pl_cols and not mark_cols:
        return JsonResponse({'filter_options': {}})

    # Do we need the student_enroll JOIN?
    needs_enroll = any(c in _ENROLL_FILTER_EXPRS for c in filter_cols)

    # Build a minimal WHERE clause from the query's own school_year filter
    where_parts, params = [], []
    sy = filters.get('school_year')
    if sy:
        vals = sy if isinstance(sy, list) else [sy]
        vals = [v for v in vals if v]
        if vals:
            where_parts.append('cs.school_year IN (%s)' % ', '.join(['%s'] * len(vals)))
            params.extend(vals)

    where_sql = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''
    join_sql  = _ENROLL_JOIN if needs_enroll else ''

    options: dict[str, list] = {}
    try:
        with connection.cursor() as cursor:
            # ── Base demographic columns from current_student ──
            for col in filter_cols:
                expr = _ENROLL_FILTER_EXPRS.get(col, f'cs.{col}')
                sql = (
                    f"SELECT DISTINCT {expr} AS val "
                    f"FROM current_student cs{join_sql} "
                    f"{where_sql} "
                    f"AND {expr} IS NOT NULL AND {expr} != '' "
                    f"ORDER BY val LIMIT 100"
                ) if where_sql else (
                    f"SELECT DISTINCT {expr} AS val "
                    f"FROM current_student cs{join_sql} "
                    f"WHERE {expr} IS NOT NULL AND {expr} != '' "
                    f"ORDER BY val LIMIT 100"
                )
                cursor.execute(sql, params)
                options[col] = [str(row[0]) for row in cursor.fetchall()]

            # ── Assessment PL columns (test_*_PL) ──
            if pl_cols:
                from dataviz.utils.data_loader import _build_test_columns_from_db
                _infos, col_map = _build_test_columns_from_db(cursor, pl_cols)
                for col in pl_cols:
                    info = col_map.get(col)
                    if not info or info.get('metric') != 'pl':
                        continue
                    win_clause = ''
                    win_params = []
                    if info.get('test_window'):
                        win_clause = 'AND t.test_window ILIKE %s'
                        win_params = [info['test_window']]
                    cursor.execute(
                        f"SELECT DISTINCT r.pl AS val "
                        f"FROM results r "
                        f"INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                        f"WHERE r.school_year = %s "
                        f"AND t.test_name ILIKE %s AND t.subject ILIKE %s "
                        f"{win_clause} "
                        f"AND r.pl IS NOT NULL AND r.pl != '' "
                        f"ORDER BY val LIMIT 30",
                        [info['year'],
                         f"%{info['test_name']}%",
                         f"%{info['subject']}%",
                         *win_params],
                    )
                    vals = [str(row[0]) for row in cursor.fetchall()]
                    if vals:
                        options[col] = vals

            # ── Mark columns (mark_*) ──
            if mark_cols:
                from dataviz.utils.data_loader import _build_mark_columns_from_db
                _infos, col_map = _build_mark_columns_from_db(cursor, mark_cols)
                for col in mark_cols:
                    info = col_map.get(col)
                    if not info:
                        continue
                    per_clause = ''
                    per_params = []
                    if info.get('mark_period'):
                        per_clause = 'AND sm.mark_period ILIKE %s'
                        per_params = [info['mark_period']]
                    else:
                        per_clause = "AND (sm.mark_period IS NULL OR sm.mark_period = '')"
                    cursor.execute(
                        f"SELECT DISTINCT sm.mark AS val "
                        f"FROM student_marks_list sm "
                        f"WHERE sm.school_year = %s "
                        f"AND sm.subject ILIKE %s AND sm.course_name ILIKE %s "
                        f"{per_clause} "
                        f"AND sm.mark IS NOT NULL AND sm.mark != '' "
                        f"ORDER BY val LIMIT 30",
                        [info['year'],
                         f"%{info['subject']}%",
                         f"%{info['course_name']}%",
                         *per_params],
                    )
                    vals = [str(row[0]) for row in cursor.fetchall()]
                    if vals:
                        options[col] = vals

    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)

    # Strip blank / placeholder values that aren't useful as filters
    _JUNK = frozenset({'', '-', '—', '–', 'N/A', 'n/a', 'None', 'null', 'NULL', '@ERR'})
    for col in list(options):
        options[col] = [v for v in options[col] if v.strip() not in _JUNK]
        if not options[col]:
            del options[col]

    # Build human-readable labels for the filter columns
    # Use the saved query's col_defs first (always populated), then COLUMN_DEFINITIONS
    col_defs = config.get('col_defs', {})
    labels: dict[str, str] = {}
    for col in options:
        defn = col_defs.get(col)
        if defn:
            labels[col] = str(defn).split('\n')[0].strip()
        elif col in _FILTERABLE_COLUMNS:
            labels[col] = col.replace('_', ' ').title()
        elif col.startswith('test_') and col.endswith('_PL'):
            # Build a readable label from the column key:
            # test_2025_2026_Dibbles_Dibbles_BOY_PL → Dibbles Dibbles BOY PL
            inner = col[5:-3]  # strip "test_" and "_PL"
            parts = inner.split('_')
            # Skip the year portion (first two parts like 2025_2026)
            name_parts = parts[2:] if len(parts) > 2 else parts
            labels[col] = ' '.join(name_parts) + ' PL'
        elif col.startswith('mark_'):
            inner = col[5:]  # strip "mark_"
            parts = inner.split('_')
            name_parts = parts[2:] if len(parts) > 2 else parts
            labels[col] = ' '.join(name_parts)
        else:
            labels[col] = col.replace('_', ' ').title()

    return JsonResponse({'filter_options': options, 'filter_labels': labels})


@login_required
def sql_schema_api(request):
    """Return public tables/views for the SQL Lab schema browser."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT table_name, table_type
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type IN ('BASE TABLE', 'VIEW')
                ORDER BY table_type, table_name
            """)
            tables = [{'name': r[0], 'type': r[1]} for r in cursor.fetchall()]
        return JsonResponse({'tables': tables})
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)


# ── Column Picker (Explorer) API ─────────────────────────────────────────────

@login_required
def column_structures_api(request):
    """Return static column categories + dynamic test/mark tree structures.

    Used by the chart builder "Explorer" source type to populate the column picker.
    """
    if not is_project_admin(request.user):
        return FORBIDDEN

    from dataexploration.constants import COLUMN_DEFINITIONS, COLUMN_CATEGORIES
    from dataexploration.column_utils import get_test_columns, get_mark_columns

    test_structure = get_test_columns()
    mark_structure = get_mark_columns()

    return JsonResponse({
        'categories':     COLUMN_CATEGORIES,
        'definitions':    COLUMN_DEFINITIONS,
        'test_structure':  test_structure,
        'mark_structure':  mark_structure,
    })


@login_required
def column_filter_options_api(request):
    """Return distinct filter values for a set of selected columns (no SavedQuery needed).

    Accepts POST JSON: ``{ "columns": ["grade", "school", "test_..._PL", ...] }``
    Returns same shape as ``query_filter_options_api``.
    """
    if not is_project_admin(request.user):
        return FORBIDDEN

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    columns = body.get('columns', [])
    if not columns:
        return JsonResponse({'filter_options': {}, 'filter_labels': {}})

    filter_cols = [c for c in columns if c in _FILTERABLE_COLUMNS]
    pl_cols     = [c for c in columns if c.startswith('test_') and c.endswith('_PL')]
    mark_cols   = [c for c in columns if c.startswith('mark_')]

    if not filter_cols and not pl_cols and not mark_cols:
        return JsonResponse({'filter_options': {}, 'filter_labels': {}})

    needs_enroll = any(c in _ENROLL_FILTER_EXPRS for c in filter_cols)
    join_sql = _ENROLL_JOIN if needs_enroll else ''

    options: dict[str, list] = {}
    from django.db import connection

    try:
        with connection.cursor() as cursor:
            for col in filter_cols:
                expr = _ENROLL_FILTER_EXPRS.get(col, f'cs.{col}')
                sql = (
                    f"SELECT DISTINCT {expr} AS val "
                    f"FROM current_student cs{join_sql} "
                    f"WHERE {expr} IS NOT NULL AND {expr} != '' "
                    f"ORDER BY val LIMIT 100"
                )
                cursor.execute(sql)
                options[col] = [str(row[0]) for row in cursor.fetchall()]

            if pl_cols:
                from dataviz.utils.data_loader import _build_test_columns_from_db
                _infos, col_map = _build_test_columns_from_db(cursor, pl_cols)
                for col in pl_cols:
                    info = col_map.get(col)
                    if not info or info.get('metric') != 'pl':
                        continue
                    win_clause, win_params = '', []
                    if info.get('test_window'):
                        win_clause = 'AND t.test_window ILIKE %s'
                        win_params = [info['test_window']]
                    cursor.execute(
                        f"SELECT DISTINCT r.pl AS val "
                        f"FROM results r "
                        f"INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                        f"WHERE r.school_year = %s "
                        f"AND t.test_name ILIKE %s AND t.subject ILIKE %s "
                        f"{win_clause} "
                        f"AND r.pl IS NOT NULL AND r.pl != '' "
                        f"ORDER BY val LIMIT 30",
                        [info['year'],
                         f"%{info['test_name']}%",
                         f"%{info['subject']}%",
                         *win_params],
                    )
                    vals = [str(row[0]) for row in cursor.fetchall()]
                    if vals:
                        options[col] = vals

            if mark_cols:
                from dataviz.utils.data_loader import _build_mark_columns_from_db
                _infos, col_map = _build_mark_columns_from_db(cursor, mark_cols)
                for col in mark_cols:
                    info = col_map.get(col)
                    if not info:
                        continue
                    per_clause, per_params = '', []
                    if info.get('mark_period'):
                        per_clause = 'AND sm.mark_period ILIKE %s'
                        per_params = [info['mark_period']]
                    else:
                        per_clause = "AND (sm.mark_period IS NULL OR sm.mark_period = '')"
                    cursor.execute(
                        f"SELECT DISTINCT sm.mark AS val "
                        f"FROM student_marks_list sm "
                        f"WHERE sm.school_year = %s "
                        f"AND sm.subject ILIKE %s AND sm.course_name ILIKE %s "
                        f"{per_clause} "
                        f"AND sm.mark IS NOT NULL AND sm.mark != '' "
                        f"ORDER BY val LIMIT 30",
                        [info['year'],
                         f"%{info['subject']}%",
                         f"%{info['course_name']}%",
                         *per_params],
                    )
                    vals = [str(row[0]) for row in cursor.fetchall()]
                    if vals:
                        options[col] = vals

    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)

    _JUNK = frozenset({'', '-', '—', '–', 'N/A', 'n/a', 'None', 'null', 'NULL', '@ERR'})
    for col in list(options):
        options[col] = [v for v in options[col] if v.strip() not in _JUNK]
        if not options[col]:
            del options[col]

    from dataexploration.constants import COLUMN_DEFINITIONS
    labels: dict[str, str] = {}
    for col in options:
        defn = COLUMN_DEFINITIONS.get(col)
        if defn:
            labels[col] = str(defn).split('\n')[0].strip()
        elif col in _FILTERABLE_COLUMNS:
            labels[col] = col.replace('_', ' ').title()
        elif col.startswith('test_') and col.endswith('_PL'):
            inner = col[5:-3]
            parts = inner.split('_')
            name_parts = parts[2:] if len(parts) > 2 else parts
            labels[col] = ' '.join(name_parts) + ' PL'
        elif col.startswith('mark_'):
            inner = col[5:]
            parts = inner.split('_')
            name_parts = parts[2:] if len(parts) > 2 else parts
            labels[col] = ' '.join(name_parts)
        else:
            labels[col] = col.replace('_', ' ').title()

    return JsonResponse({'filter_options': options, 'filter_labels': labels})
