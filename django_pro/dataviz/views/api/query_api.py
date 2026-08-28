"""Query execution API views: run saved queries, SQL lab, schema browser."""
import json
import re

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from dataviz.utils.data_loader import run_raw_sql, run_saved_query
from dataexploration.view_modules.column_management import (
    JSON_LIST_COLUMNS,
    JSON_SCALAR_COLUMNS,
    append_json_list_filter,
    json_list_unnest_select_expr,
    json_scalar_expr,
)

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
def query_preview_api(request, query_id):
    """Return first 5 rows + column list for a SavedQuery without full execution.

    GET /dataviz/api/queries/<id>/preview/
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

    from dataviz.utils.data_loader import run_saved_query_preview
    result = run_saved_query_preview(sq)
    if result.get('error'):
        return JsonResponse({'error': result['error']}, status=400)

    return JsonResponse({
        'columns': result.get('columns', []),
        'rows': result.get('rows', []),
        'row_count': result.get('row_count', 0),
        'success': True,
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
    'school_year', 'grade', 'school', 'gender', 'race', 'race_list', 'dsusd_fep', 'ell', 'sed',
    'foster', 'homeless', 'sped', 'gate', 'in_504', 'dli', 'ltel',
    'status', 'suspension_indicator', 'atendace_rate',
    'birth_date', 'us_enter_date', 'fep_date',
])

# Enroll-dependent fields need a LEFT JOIN to student_enroll.
_ENROLL_FILTER_EXPRS = {
    'ell': 'COALESCE(cs.ell, se.ell_status)',
    'birth_date': 'se.birth_date',
    'us_enter_date': 'se.us_enter_date',
    'fep_date': 'se.fep_date',
    'migrant': 'se.migrant',
}


def _is_date_filter_col(col: str) -> bool:
    return col.endswith('_date') or col.endswith('_at') or col == 'date' or 'date' in col


def _is_numeric_filter_col(col: str) -> bool:
    return col == 'atendace_rate' or col.endswith('_SS')


def _append_range_filter(where_parts: list, params: list, expr: str, fval: dict) -> None:
    """Append include/exclude range bounds to a WHERE clause."""
    gte = fval.get('gte')
    lte = fval.get('lte')
    exclude = fval.get('exclude')
    if exclude:
        conds = []
        if gte:
            conds.append(f'{expr} < %s')
            params.append(gte)
        if lte:
            conds.append(f'{expr} > %s')
            params.append(lte)
        if conds:
            where_parts.append(f"({' OR '.join(conds)} OR {expr} IS NULL)")
    else:
        if gte:
            where_parts.append(f'{expr} >= %s')
            params.append(gte)
        if lte:
            where_parts.append(f'{expr} <= %s')
            params.append(lte)

_ENROLL_JOIN = (
    " LEFT JOIN ("
    " SELECT DISTINCT ON (student_id, school_year) *"
    " FROM student_enroll"
    " ORDER BY student_id, school_year, enter_date DESC NULLS LAST, leave_date DESC NULLS LAST"
    ") se ON se.student_id = cs.student_id AND se.school_year = cs.school_year"
)


def _full_test_pl_label(info: dict) -> str:
    """Build the complete test-name label for a test_*_PL column from its DB metadata.

    Uses the raw test_name/subject/test_window fetched from the DB for *this*
    request (via ``_build_test_columns_from_db``) rather than reconstructing a
    name by parsing the sanitized column key, which can mangle multi-word test
    names and skips the ``_get_display_name`` spelling/casing normalization.
    """
    from dataexploration.column_utils import _get_display_name
    parts = [
        _get_display_name((info.get('test_name') or '').strip()),
        _get_display_name((info.get('subject') or '').strip()),
    ]
    if info.get('test_window'):
        parts.append(info['test_window'])
    return ' '.join(p for p in parts if p) + ' PL'


def _full_mark_label(info: dict) -> str:
    """Build the complete label for a mark_ column from its DB metadata."""
    course = (info.get('course_name') or '').strip()
    subject = (info.get('subject') or '').strip()
    period = (info.get('mark_period') or '').strip()
    label = course or 'Mark'
    if subject:
        label += f' ({subject})'
    if period:
        label += f' {period}'
    return label


def _inject_school_year_aliases(options: dict, labels: dict) -> None:
    """Prepend relative alias options to the school_year filter if present."""
    sy_key = None
    for k in options:
        if k == 'school_year' or (isinstance(k, str) and k.lower().endswith('.school_year')):
            sy_key = k
            break
    if sy_key is None:
        return
    from dataexploration.constants import (
        SY_THIS_YEAR, SY_LAST_YEAR, SY_TWO_PRIOR,
        SY_THREE_PRIOR, SY_FOUR_PRIOR, SY_FIVE_PRIOR,
    )
    alias_entries = [
        (SY_THIS_YEAR, 'This School Year'),
        (SY_LAST_YEAR, 'Last School Year'),
        (SY_TWO_PRIOR, 'Two Years Prior'),
        (SY_THREE_PRIOR, 'Three Years Prior'),
        (SY_FOUR_PRIOR, 'Four Years Prior'),
        (SY_FIVE_PRIOR, 'Five Years Prior'),
    ]
    # Prepend aliases so they appear at the top of the list
    existing = options[sy_key]
    alias_set = {a for a, _ in alias_entries}
    options[sy_key] = [a for a, _ in alias_entries] + [y for y in existing if y not in alias_set]
    # Store per-value labels under a special key for the frontend
    labels['__school_year_alias_labels__'] = {a: lbl for a, lbl in alias_entries}


_RELATIVE_YEAR_LABELS = [
    'This School Year', 'Last School Year', 'Two Years Prior',
    'Three Years Prior', 'Four Years Prior', 'Five Years Prior',
]


def _inject_relative_year_labels(options: dict) -> None:
    """Ensure all six relative-year labels are available as filter options.

    Dataset charts filter by a ``relative_label`` column (from the
    relative_year table). Only the labels for years that actually have data
    appear from the query, so inject the full set to future-proof the options
    when the data rolls over to a different current year.
    """
    for key in list(options):
        if key == 'relative_label' or (isinstance(key, str) and key.endswith('.relative_label')):
            existing = options[key]
            merged = _RELATIVE_YEAR_LABELS + [v for v in existing if v not in _RELATIVE_YEAR_LABELS]
            options[key] = merged
    return


def _build_where_clause(filters_dict: dict, exclude_col: str = None) -> tuple[list[str], list]:
    """Helper to build WHERE clause parts and parameters for scoping filter options.
    
    Excluding `exclude_col` from the active filters allows the options for `exclude_col`
    itself to stay populated with all other context-valid values.
    """
    where_parts = []
    params = []
    _SAFE_COL_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
    _enroll_keys = set(_ENROLL_FILTER_EXPRS.keys())

    for fkey, fval in filters_dict.items():
        if fkey == exclude_col:
            continue
        if fkey == 'search' or fkey.startswith(('test_', 'mark_')):
            continue
        if fkey not in _FILTERABLE_COLUMNS:
            continue
        
        # Determine SQL expression for the column
        json_key = JSON_LIST_COLUMNS.get(fkey)
        if json_key:
            vals = fval if isinstance(fval, list) else ([fval] if fval else [])
            append_json_list_filter(where_parts, params, vals, json_key)
            continue
        if fkey in _enroll_keys:
            expr = _ENROLL_FILTER_EXPRS[fkey]
        elif fkey in JSON_SCALAR_COLUMNS:
            expr = json_scalar_expr(fkey)
        elif _SAFE_COL_RE.match(fkey):
            expr = f'cs.{fkey}'
        else:
            continue

        if not fval:
            continue

        if isinstance(fval, dict):
            _append_range_filter(where_parts, params, expr, fval)
            continue

        # Handle list/multi-select values
        if isinstance(fval, list):
            vals = [v for v in fval if v]
            if not vals:
                continue
            has_blank = '(Blank)' in vals
            others = [v for v in vals if v != '(Blank)']
            parts = []
            if others:
                ph = ', '.join(['%s'] * len(others))
                parts.append(f'{expr} IN ({ph})')
                params.extend(others)
            if has_blank:
                parts.append(f"({expr} IS NULL OR {expr} = '' OR {expr} = '—')")
            if len(parts) > 1:
                where_parts.append(f"({' OR '.join(parts)})")
            elif parts:
                where_parts.append(parts[0])
        else:
            if fval == '(Blank)':
                where_parts.append(f"({expr} IS NULL OR {expr} = '' OR {expr} = '—')")
            else:
                where_parts.append(f'{expr} = %s')
                params.append(fval)

    return where_parts, params


@login_required
def query_filter_options_api(request, query_id):
    """Return distinct values for every filterable column available in a query.

    Response: ``{ filter_options: { grade: ['K','1',...], school: [...], ... } }``

    Accepts optional ?current_filters=<JSON> to scope values by active filters (cascading).

    Includes:
    - Base demographic columns from ``current_student`` (grade, school, …)
    - Assessment PL columns (``test_*_PL``) — categorical performance levels
    - Mark columns (``mark_*``) — categorical grade/mark values

    Excludes:
    - SS columns (``test_*_SS``) — numeric scale scores, high cardinality

    Results are cached for 5 minutes per query config hash.
    """
    if not is_project_admin(request.user):
        return FORBIDDEN

    from dataexploration.models import SavedQuery
    from dataexploration.utils import can_user_view_query
    from clickhouse_utils.executor import QueryExecutor
    from hashlib import md5

    try:
        sq = SavedQuery.objects.get(pk=query_id)
    except SavedQuery.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    if not can_user_view_query(request.user, sq):
        return JsonResponse({'error': 'Access denied'}, status=403)

    # Parse optional cascading filter context
    current_filters_raw = request.GET.get('current_filters', '')
    current_filters = {}
    if current_filters_raw:
        try:
            current_filters = json.loads(current_filters_raw)
        except (json.JSONDecodeError, ValueError):
            pass

    cache_key_parts = f'dv_filter_opts:{query_id}'
    if current_filters:
        filter_hash = md5(json.dumps(current_filters, sort_keys=True).encode()).hexdigest()[:8]
        cache_key_parts += f':{filter_hash}'
    cached_resp = cache.get(cache_key_parts)
    if cached_resp is not None:
        return JsonResponse(cached_resp)

    config  = sq.config or {}
    columns = config.get('columns', [])
    filters = config.get('filters', {}) if isinstance(config.get('filters'), dict) else {}

    # Keep track of original keys to map resolved values back
    resolved_to_original = {}
    try:
        from dataexploration.school_year_utils import resolve_relative_column_key
        for c in columns:
            resolved = resolve_relative_column_key(c)
            resolved_to_original[resolved] = c
    except Exception:
        resolved_to_original = {c: c for c in columns}

    resolved_columns = list(resolved_to_original.keys())
    # Always include all demographic filter columns — same as explorer mode.
    filter_cols = sorted(_FILTERABLE_COLUMNS)
    pl_cols     = [c for c in resolved_columns if c.startswith('test_') and c.endswith('_PL')]
    mark_cols   = [c for c in resolved_columns if c.startswith('mark_')]

    if not filter_cols and not pl_cols and not mark_cols:
        return JsonResponse({'filter_options': {}})

    # Merge config level filters and cascading current_filters
    merged_filters = {}
    if isinstance(config.get('filters'), dict):
        merged_filters.update(config['filters'])
    if isinstance(current_filters, dict):
        merged_filters.update(current_filters)

    # Resolve any relative column keys and school_year alias values in merged_filters
    try:
        from dataexploration.school_year_utils import resolve_relative_column_key, resolve_school_year
        merged_filters = {resolve_relative_column_key(k): v for k, v in merged_filters.items()}
        if 'school_year' in merged_filters:
            sy_val = merged_filters['school_year']
            if isinstance(sy_val, list):
                merged_filters['school_year'] = [resolve_school_year(v) for v in sy_val]
            else:
                merged_filters['school_year'] = resolve_school_year(sy_val)
    except Exception:
        pass

    # Do we need the student_enroll JOIN?
    needs_enroll = (
        any(c in _ENROLL_FILTER_EXPRS for c in filter_cols) or
        any(c in _ENROLL_FILTER_EXPRS for c in merged_filters.keys())
    )
    join_sql  = _ENROLL_JOIN if needs_enroll else ''

    # ── Build comprehensive demographic filters for PL / mark scoping ──────────
    # These replicate the same filter logic as _run_saved_query_sql so that PL
    # and mark filter options are scoped by the same demographic context.
    demo_where_parts, demo_params = _build_where_clause(merged_filters)
    demo_where_sql = ('WHERE ' + ' AND '.join(demo_where_parts)) if demo_where_parts else ''
    # cs join fragment for scoping PL / mark queries against current_student
    _CS_JOIN = ' INNER JOIN current_student cs ON cs.student_id = r.local_student_id AND cs.school_year = r.school_year'
    _CS_JOIN_MARK = ' INNER JOIN current_student cs ON cs.stu_annual_key = sm.stu_annual_key'

    options: dict[str, list] = {}
    labels: dict[str, str] = {}
    test_col_map: dict[str, dict] = {}
    mark_col_map: dict[str, dict] = {}
    try:
        with QueryExecutor(analytics=True).cursor() as cursor:
            # ── Base demographic columns from current_student ──
            if filter_cols:
                union_parts = []
                union_params: list = []
                for col in filter_cols:
                    json_key = JSON_LIST_COLUMNS.get(col)
                    if json_key:
                        col_where_parts, col_params = _build_where_clause(merged_filters, exclude_col=col)
                        col_where_sql = 'WHERE ' + ' AND '.join(col_where_parts) if col_where_parts else ''
                        unnest = json_list_unnest_select_expr(json_key, 'cs')
                        union_parts.append(
                            f"(SELECT DISTINCT v AS val, %s AS col_name FROM ("
                            f" SELECT {unnest} AS v FROM current_student cs{join_sql} {col_where_sql}"
                            f") _jl WHERE v IS NOT NULL AND v != '' ORDER BY val)"
                        )
                        union_params.append(col)
                        union_params.extend(col_params)
                        continue

                    expr = (
                        json_scalar_expr(col) if col in JSON_SCALAR_COLUMNS
                        else _ENROLL_FILTER_EXPRS.get(col, f'cs.{col}')
                    )
                    
                    # Build dynamic cascading WHERE clause excluding the current column itself
                    col_where_parts, col_params = _build_where_clause(merged_filters, exclude_col=col)
                    is_numeric = _is_numeric_filter_col(col)
                    is_date = _is_date_filter_col(col)
                    col_where_sql = 'WHERE ' + ' AND '.join(col_where_parts) if col_where_parts else ''
                    if is_numeric or is_date:
                        col_where_parts.append(f"{expr} IS NOT NULL")
                        col_where_sql = 'WHERE ' + ' AND '.join(col_where_parts)
                        cast_expr = f'CAST({expr} AS VARCHAR)'
                        union_parts.append(
                            f"(SELECT CAST(MIN({expr}) AS VARCHAR) AS val, %s AS col_name "
                            f"FROM current_student cs{join_sql} "
                            f"{col_where_sql})"
                        )
                        union_params.append(col)
                        union_params.extend(col_params)
                        union_parts.append(
                            f"(SELECT CAST(MAX({expr}) AS VARCHAR) AS val, %s AS col_name "
                            f"FROM current_student cs{join_sql} "
                            f"{col_where_sql})"
                        )
                        union_params.append(col)
                        union_params.extend(col_params)
                    else:
                        col_where_parts.append(f"{expr} IS NOT NULL AND {expr} != ''")
                        col_where_sql = 'WHERE ' + ' AND '.join(col_where_parts)
                        union_parts.append(
                            f"(SELECT DISTINCT {expr} AS val, %s AS col_name "
                            f"FROM current_student cs{join_sql} "
                            f"{col_where_sql} "
                            f"ORDER BY val)"
                        )
                        union_params.append(col)
                        union_params.extend(col_params)
                union_sql = ' UNION ALL '.join(union_parts)
                cursor.execute(union_sql, union_params)
                for row in cursor.fetchall():
                    val, col_name = row[0], row[1]
                    options.setdefault(col_name, []).append(str(val))

            # ── Assessment PL columns (test_*_PL) ──
            if pl_cols:
                from dataviz.utils.data_loader import _build_test_columns_from_db
                _infos, test_col_map = _build_test_columns_from_db(cursor, pl_cols)

                # Collect tested school codes from results for all PL columns
                tested_school_codes: dict[str, str] = {}  # code → display name
                for col in pl_cols:
                    info = test_col_map.get(col)
                    if not info or info.get('metric') != 'pl':
                        continue
                    win_clause = ''
                    win_params = []
                    if info.get('test_window'):
                        win_clause = 'AND t.test_window ILIKE %s'
                        win_params = [info['test_window']]

                    # PL values — scoped by demographic filters via current_student join
                    if demo_where_sql:
                        pl_sql = (
                            f"SELECT DISTINCT r.pl AS val "
                            f"FROM results r "
                            f"INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                            f"{_CS_JOIN} "
                            f"{demo_where_sql} "
                            f"AND r.school_year = %s "
                            f"AND t.test_name ILIKE %s AND t.subject ILIKE %s "
                            f"{win_clause} "
                            f"AND r.pl IS NOT NULL AND r.pl != '' "
                            f"ORDER BY val"
                        )
                        pl_params = [*demo_params, info['year'],
                                     info['test_name'],
                                     info['subject'],
                                     *win_params]
                    else:
                        pl_sql = (
                            f"SELECT DISTINCT r.pl AS val "
                            f"FROM results r "
                            f"INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                            f"WHERE r.school_year = %s "
                            f"AND t.test_name ILIKE %s AND t.subject ILIKE %s "
                            f"{win_clause} "
                            f"AND r.pl IS NOT NULL AND r.pl != '' "
                            f"ORDER BY val"
                        )
                        pl_params = [info['year'],
                                     info['test_name'],
                                     info['subject'],
                                     *win_params]
                    cursor.execute(pl_sql, pl_params)
                    vals = [str(row[0]) for row in cursor.fetchall()]
                    if vals:
                        options[resolved_to_original.get(col, col)] = vals

                    # Gather school_codes from results for this assessment
                    if demo_where_sql:
                        ts_sql = (
                            "SELECT DISTINCT r.school_code, "
                            "  COALESCE(sch.short_name, sch.school_name, r.school_code) AS school_label "
                            "FROM results r "
                            "INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                            f"{_CS_JOIN} "
                            "LEFT JOIN school sch ON sch.assessment_school_code = r.school_code "
                            f"{demo_where_sql} "
                            "AND r.school_year = %s "
                            "AND t.test_name ILIKE %s AND t.subject ILIKE %s "
                            f"{win_clause} "
                            "AND r.school_code IS NOT NULL AND r.school_code != '' "
                            "ORDER BY school_label"
                        )
                        ts_params = [*demo_params, info['year'],
                                     info['test_name'],
                                     info['subject'],
                                     *win_params]
                    else:
                        ts_sql = (
                            "SELECT DISTINCT r.school_code, "
                            "  COALESCE(sch.short_name, sch.school_name, r.school_code) AS school_label "
                            "FROM results r "
                            "INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                            "LEFT JOIN school sch ON sch.assessment_school_code = r.school_code "
                            "WHERE r.school_year = %s "
                            "AND t.test_name ILIKE %s AND t.subject ILIKE %s "
                            f"{win_clause} "
                            "AND r.school_code IS NOT NULL AND r.school_code != '' "
                            "ORDER BY school_label"
                        )
                        ts_params = [info['year'],
                                     info['test_name'],
                                     info['subject'],
                                     *win_params]
                    cursor.execute(ts_sql, ts_params)
                    for code, label in cursor.fetchall():
                        if code:
                            tested_school_codes[str(code)] = str(label) if label else str(code)

                if tested_school_codes:
                    options['tested_school'] = list(tested_school_codes.keys())
                    # Store display labels under special key for the frontend
                    labels['__tested_school_labels__'] = tested_school_codes

            # ── Mark columns (mark_*) ──
            if mark_cols:
                from dataviz.utils.data_loader import _build_mark_columns_from_db
                _infos, mark_col_map = _build_mark_columns_from_db(cursor, mark_cols)
                for col in mark_cols:
                    info = mark_col_map.get(col)
                    if not info:
                        continue
                    per_clause = ''
                    per_params = []
                    if info.get('mark_period'):
                        per_clause = 'AND sm.mark_period ILIKE %s'
                        per_params = [info['mark_period']]
                    else:
                        per_clause = "AND (sm.mark_period IS NULL OR sm.mark_period = '')"

                    if demo_where_sql:
                        mark_sql = (
                            f"SELECT DISTINCT sm.mark AS val "
                            f"FROM student_marks_list sm "
                            f"{_CS_JOIN_MARK} "
                            f"{demo_where_sql} "
                            f"AND sm.school_year = %s "
                            f"AND sm.subject ILIKE %s AND sm.course_name ILIKE %s "
                            f"{per_clause} "
                            f"AND sm.mark IS NOT NULL AND sm.mark != '' "
                            f"ORDER BY val"
                        )
                        mark_params = [*demo_params, info['year'],
                                       f"%{info['subject']}%",
                                       f"%{info['course_name']}%",
                                       *per_params]
                    else:
                        mark_sql = (
                            f"SELECT DISTINCT sm.mark AS val "
                            f"FROM student_marks_list sm "
                            f"WHERE sm.school_year = %s "
                            f"AND sm.subject ILIKE %s AND sm.course_name ILIKE %s "
                            f"{per_clause} "
                            f"AND sm.mark IS NOT NULL AND sm.mark != '' "
                            f"ORDER BY val"
                        )
                        mark_params = [info['year'],
                                       f"%{info['subject']}%",
                                       f"%{info['course_name']}%",
                                       *per_params]
                    cursor.execute(mark_sql, mark_params)
                    vals = [str(row[0]) for row in cursor.fetchall()]
                    if vals:
                        options[resolved_to_original.get(col, col)] = vals

    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)

    # Strip blank / placeholder values that aren't useful as filters
    _JUNK = frozenset({'', '-', '—', '–', 'N/A', 'n/a', 'None', 'null', 'NULL', '@ERR'})
    for col in list(options):
        if col == 'tested_school':
            options[col] = [v for v in options[col] if v.strip() not in _JUNK]
        else:
            options[col] = [v for v in options[col] if v.strip() not in _JUNK]
        if not options[col]:
            del options[col]

    # Build human-readable labels for the filter columns
    # Use the saved query's col_defs first (always populated), then COLUMN_DEFINITIONS
    col_defs = config.get('col_defs', {})

    # tested_school display labels may have been collected per-code
    tested_school_labels = labels.pop('__tested_school_labels__', {})

    for col in options:
        defn = col_defs.get(col)
        if defn:
            labels[col] = str(defn).split('\n')[0].strip()
        elif col == 'tested_school':
            labels[col] = 'Tested School'
        elif col in _FILTERABLE_COLUMNS:
            labels[col] = col.replace('_', ' ').title()
        elif col.startswith('test_') and col.endswith('_PL'):
            key_for_parsing = col
            if '__sy_' in col:
                try:
                    from dataexploration.school_year_utils import resolve_relative_column_key
                    key_for_parsing = resolve_relative_column_key(col)
                except Exception:
                    pass
            info = test_col_map.get(key_for_parsing) or test_col_map.get(col)
            if info:
                labels[col] = _full_test_pl_label(info)
            else:
                # Fallback: reconstruct from the sanitized column key.
                # test_2025_2026_Dibbles_Dibbles_BOY_PL → Dibbles Dibbles BOY PL
                inner = key_for_parsing[5:-3]  # strip "test_" and "_PL"
                parts = [p for p in inner.split('_') if p]
                # Skip the year portion (first two parts like 2025_2026)
                name_parts = parts[2:] if len(parts) > 2 else parts
                labels[col] = ' '.join(name_parts) + ' PL'
        elif col.startswith('mark_'):
            key_for_parsing = col
            if '__sy_' in col:
                try:
                    from dataexploration.school_year_utils import resolve_relative_column_key
                    key_for_parsing = resolve_relative_column_key(col)
                except Exception:
                    pass
            info = mark_col_map.get(key_for_parsing) or mark_col_map.get(col)
            if info:
                labels[col] = _full_mark_label(info)
            else:
                inner = key_for_parsing[5:]  # strip "mark_"
                parts = [p for p in inner.split('_') if p]
                name_parts = parts[2:] if len(parts) > 2 else parts
                labels[col] = ' '.join(name_parts)
        else:
            labels[col] = col.replace('_', ' ').title()

    _inject_school_year_aliases(options, labels)
    if tested_school_labels:
        labels['__tested_school_labels__'] = tested_school_labels
    resp_data = {'filter_options': options, 'filter_labels': labels}
    cache.set(cache_key_parts, resp_data, 300)
    return JsonResponse(resp_data)


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
    from dataexploration.column_utils import (
        get_test_columns, get_mark_columns, get_ap_columns,
        invert_test_to_assessment_first, invert_marks_to_assessment_first,
    )
    from dataexploration.school_year_utils import get_year_labels_map, get_year_alias_map

    test_structure = get_test_columns()
    mark_structure = get_mark_columns()
    ap_structure = get_ap_columns()
    all_years = list(test_structure.keys()) + list(mark_structure.keys()) + list(ap_structure.keys())

    # Build assessment-first views for the new assessment-centric picker UI
    assessment_structure = invert_test_to_assessment_first(test_structure)
    mark_assessment_structure = invert_marks_to_assessment_first(mark_structure)

    return JsonResponse({
        'categories':              COLUMN_CATEGORIES,
        'definitions':             COLUMN_DEFINITIONS,
        'test_structure':          test_structure,
        'mark_structure':          mark_structure,
        'ap_structure':            ap_structure,
        'assessment_structure':    assessment_structure,
        'mark_assessment_structure': mark_assessment_structure,
        'year_labels':             get_year_labels_map(all_years),
        'year_alias_map':          get_year_alias_map(),
    })


@login_required
def column_filter_options_api(request):
    """Return distinct filter values for a set of selected columns (no SavedQuery needed).

    Accepts POST JSON: ``{ "columns": ["grade", "school", "test_..._PL", ...],
                          "filters": {"school_year": "2024_2025", "school": [...], ...} }``

    When ``filters`` is provided, PL and mark filter values are scoped by those
    demographic filters (joined through ``current_student``).  Base column filter
    options are always unfiltered (all distinct values).
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

    req_filters = body.get('filters', {}) or {}

    # Cache based on column set + active filters (5-minute TTL)
    from hashlib import md5
    cache_key = 'dv_col_filter_opts:' + md5(
        json.dumps({'cols': sorted(columns), 'filters': req_filters}, sort_keys=True).encode()
    ).hexdigest()[:14]
    cached = cache.get(cache_key)
    if cached is not None:
        return JsonResponse(cached)

    # Keep track of original keys to map resolved values back
    resolved_to_original = {}
    try:
        from dataexploration.school_year_utils import resolve_relative_column_key
        for c in columns:
            resolved = resolve_relative_column_key(c)
            resolved_to_original[resolved] = c
    except Exception:
        resolved_to_original = {c: c for c in columns}

    resolved_columns = list(resolved_to_original.keys())
    # Always include ALL demographic filter columns — the explorer source always
    # reads from current_student so all demographics are available regardless of
    # which specific columns the user picked in the explorer.
    filter_cols = list(_FILTERABLE_COLUMNS)
    pl_cols     = [c for c in resolved_columns if c.startswith('test_') and c.endswith('_PL')]
    mark_cols   = [c for c in resolved_columns if c.startswith('mark_')]

    if not filter_cols and not pl_cols and not mark_cols:
        return JsonResponse({'filter_options': {}, 'filter_labels': {}})

    # Do we need the student_enroll JOIN?
    merged_filters = req_filters

    # Resolve any relative column keys and school_year alias values in merged_filters
    try:
        from dataexploration.school_year_utils import resolve_relative_column_key, resolve_school_year
        merged_filters = {resolve_relative_column_key(k): v for k, v in merged_filters.items()}
        if 'school_year' in merged_filters:
            sy_val = merged_filters['school_year']
            if isinstance(sy_val, list):
                merged_filters['school_year'] = [resolve_school_year(v) for v in sy_val]
            else:
                merged_filters['school_year'] = resolve_school_year(sy_val)
    except Exception:
        pass

    needs_enroll = (
        any(c in _ENROLL_FILTER_EXPRS for c in filter_cols) or
        any(c in _ENROLL_FILTER_EXPRS for c in merged_filters.keys())
    )
    join_sql = _ENROLL_JOIN if needs_enroll else ''

    # ── Build demographic filter context from optional request filters ─────────
    demo_where_parts, demo_params = _build_where_clause(merged_filters)
    demo_where_sql = ('WHERE ' + ' AND '.join(demo_where_parts)) if demo_where_parts else ''
    _CS_JOIN = ' INNER JOIN current_student cs ON cs.student_id = r.local_student_id AND cs.school_year = r.school_year'
    _CS_JOIN_MARK = ' INNER JOIN current_student cs ON cs.stu_annual_key = sm.stu_annual_key'

    options: dict[str, list] = {}
    test_col_map: dict[str, dict] = {}
    mark_col_map: dict[str, dict] = {}
    from clickhouse_utils.executor import QueryExecutor

    try:
        with QueryExecutor(analytics=True).cursor() as cursor:
            if filter_cols:
                union_parts = []
                union_params: list = []
                for col in filter_cols:
                    json_key = JSON_LIST_COLUMNS.get(col)
                    if json_key:
                        col_where_parts, col_params = _build_where_clause(merged_filters, exclude_col=col)
                        col_where_sql = 'WHERE ' + ' AND '.join(col_where_parts) if col_where_parts else ''
                        unnest = json_list_unnest_select_expr(json_key, 'cs')
                        union_parts.append(
                            f"(SELECT DISTINCT v AS val, %s AS col_name FROM ("
                            f" SELECT {unnest} AS v FROM current_student cs{join_sql} {col_where_sql}"
                            f") _jl WHERE v IS NOT NULL AND v != '' ORDER BY val)"
                        )
                        union_params.append(col)
                        union_params.extend(col_params)
                        continue

                    expr = (
                        json_scalar_expr(col) if col in JSON_SCALAR_COLUMNS
                        else _ENROLL_FILTER_EXPRS.get(col, f'cs.{col}')
                    )
                    col_where_parts, col_params = _build_where_clause(merged_filters, exclude_col=col)
                    is_numeric = _is_numeric_filter_col(col)
                    is_date = _is_date_filter_col(col)
                    if is_numeric or is_date:
                        col_where_parts.append(f"{expr} IS NOT NULL")
                        col_where_sql = 'WHERE ' + ' AND '.join(col_where_parts)
                        union_parts.append(
                            f"(SELECT CAST(MIN({expr}) AS VARCHAR) AS val, %s AS col_name "
                            f"FROM current_student cs{join_sql} "
                            f"{col_where_sql})"
                        )
                        union_params.append(col)
                        union_params.extend(col_params)
                        union_parts.append(
                            f"(SELECT CAST(MAX({expr}) AS VARCHAR) AS val, %s AS col_name "
                            f"FROM current_student cs{join_sql} "
                            f"{col_where_sql})"
                        )
                        union_params.append(col)
                        union_params.extend(col_params)
                    else:
                        col_where_parts.append(f"{expr} IS NOT NULL AND {expr} != ''")
                        col_where_sql = 'WHERE ' + ' AND '.join(col_where_parts)
                        union_parts.append(
                            f"(SELECT DISTINCT {expr} AS val, %s AS col_name "
                            f"FROM current_student cs{join_sql} "
                            f"{col_where_sql} "
                            f"ORDER BY val)"
                        )
                        union_params.append(col)
                        union_params.extend(col_params)
                union_sql = ' UNION ALL '.join(union_parts)
                cursor.execute(union_sql, union_params)
                for row in cursor.fetchall():
                    val, col_name = row[0], row[1]
                    options.setdefault(col_name, []).append(str(val))

            if pl_cols:
                from dataviz.utils.data_loader import _build_test_columns_from_db
                _infos, test_col_map = _build_test_columns_from_db(cursor, pl_cols)
                tested_school_codes: dict[str, str] = {}
                for col in pl_cols:
                    info = test_col_map.get(col)
                    if not info or info.get('metric') != 'pl':
                        continue
                    win_clause, win_params = '', []
                    if info.get('test_window'):
                        win_clause = 'AND t.test_window ILIKE %s'
                        win_params = [info['test_window']]

                    if demo_where_sql:
                        pl_sql = (
                            f"SELECT DISTINCT r.pl AS val "
                            f"FROM results r "
                            f"INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                            f"{_CS_JOIN} "
                            f"{demo_where_sql} "
                            f"AND r.school_year = %s "
                            f"AND t.test_name ILIKE %s AND t.subject ILIKE %s "
                            f"{win_clause} "
                            f"AND r.pl IS NOT NULL AND r.pl != '' "
                            f"ORDER BY val"
                        )
                        pl_params = [*demo_params, info['year'],
                                     info['test_name'],
                                     info['subject'],
                                     *win_params]
                    else:
                        pl_sql = (
                            f"SELECT DISTINCT r.pl AS val "
                            f"FROM results r "
                            f"INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                            f"WHERE r.school_year = %s "
                            f"AND t.test_name ILIKE %s AND t.subject ILIKE %s "
                            f"{win_clause} "
                            f"AND r.pl IS NOT NULL AND r.pl != '' "
                            f"ORDER BY val"
                        )
                        pl_params = [info['year'],
                                     info['test_name'],
                                     info['subject'],
                                     *win_params]
                    cursor.execute(pl_sql, pl_params)
                    vals = [str(row[0]) for row in cursor.fetchall()]
                    if vals:
                        options[resolved_to_original.get(col, col)] = vals

                    if demo_where_sql:
                        ts_sql = (
                            "SELECT DISTINCT r.school_code, "
                            "  COALESCE(sch.short_name, sch.school_name, r.school_code) AS school_label "
                            "FROM results r "
                            "INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                            f"{_CS_JOIN} "
                            "LEFT JOIN school sch ON sch.assessment_school_code = r.school_code "
                            f"{demo_where_sql} "
                            "AND r.school_year = %s "
                            "AND t.test_name ILIKE %s AND t.subject ILIKE %s "
                            f"{win_clause} "
                            "AND r.school_code IS NOT NULL AND r.school_code != '' "
                            "ORDER BY school_label"
                        )
                        ts_params = [*demo_params, info['year'],
                                     info['test_name'],
                                     info['subject'],
                                     *win_params]
                    else:
                        ts_sql = (
                            "SELECT DISTINCT r.school_code, "
                            "  COALESCE(sch.short_name, sch.school_name, r.school_code) AS school_label "
                            "FROM results r "
                            "INNER JOIN tests t ON r.test_group_key = t.test_group_key "
                            "LEFT JOIN school sch ON sch.assessment_school_code = r.school_code "
                            "WHERE r.school_year = %s "
                            "AND t.test_name ILIKE %s AND t.subject ILIKE %s "
                            f"{win_clause} "
                            "AND r.school_code IS NOT NULL AND r.school_code != '' "
                            "ORDER BY school_label"
                        )
                        ts_params = [info['year'],
                                     info['test_name'],
                                     info['subject'],
                                     *win_params]
                    cursor.execute(ts_sql, ts_params)
                    for code, label in cursor.fetchall():
                        if code:
                            tested_school_codes[str(code)] = str(label) if label else str(code)

                if tested_school_codes:
                    options['tested_school'] = list(tested_school_codes.keys())

            if mark_cols:
                from dataviz.utils.data_loader import _build_mark_columns_from_db
                _infos, mark_col_map = _build_mark_columns_from_db(cursor, mark_cols)
                for col in mark_cols:
                    info = mark_col_map.get(col)
                    if not info:
                        continue
                    per_clause, per_params = '', []
                    if info.get('mark_period'):
                        per_clause = 'AND sm.mark_period ILIKE %s'
                        per_params = [info['mark_period']]
                    else:
                        per_clause = "AND (sm.mark_period IS NULL OR sm.mark_period = '')"

                    if demo_where_sql:
                        mark_sql = (
                            f"SELECT DISTINCT sm.mark AS val "
                            f"FROM student_marks_list sm "
                            f"{_CS_JOIN_MARK} "
                            f"{demo_where_sql} "
                            f"AND sm.school_year = %s "
                            f"AND sm.subject ILIKE %s AND sm.course_name ILIKE %s "
                            f"{per_clause} "
                            f"AND sm.mark IS NOT NULL AND sm.mark != '' "
                            f"ORDER BY val"
                        )
                        mark_params = [*demo_params, info['year'],
                                       f"%{info['subject']}%",
                                       f"%{info['course_name']}%",
                                       *per_params]
                    else:
                        mark_sql = (
                            f"SELECT DISTINCT sm.mark AS val "
                            f"FROM student_marks_list sm "
                            f"WHERE sm.school_year = %s "
                            f"AND sm.subject ILIKE %s AND sm.course_name ILIKE %s "
                            f"{per_clause} "
                            f"AND sm.mark IS NOT NULL AND sm.mark != '' "
                            f"ORDER BY val"
                        )
                        mark_params = [info['year'],
                                       f"%{info['subject']}%",
                                       f"%{info['course_name']}%",
                                       *per_params]
                    cursor.execute(mark_sql, mark_params)
                    vals = [str(row[0]) for row in cursor.fetchall()]
                    if vals:
                        options[resolved_to_original.get(col, col)] = vals

    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)

    _JUNK = frozenset({'', '-', '—', '–', 'N/A', 'n/a', 'None', 'null', 'NULL', '@ERR'})
    for col in list(options):
        options[col] = [v for v in options[col] if v.strip() not in _JUNK]
        if not options[col]:
            del options[col]

    from dataexploration.constants import COLUMN_DEFINITIONS
    labels: dict[str, str] = {}
    # tested_school_codes populated inside the pl_cols block above (local scope)
    _ts_codes: dict[str, str] = locals().get('tested_school_codes', {})
    for col in options:
        defn = COLUMN_DEFINITIONS.get(col)
        if defn:
            labels[col] = str(defn).split('\n')[0].strip()
        elif col == 'tested_school':
            labels[col] = 'Tested School'
        elif col in _FILTERABLE_COLUMNS:
            labels[col] = col.replace('_', ' ').title()
        elif col.startswith('test_') and col.endswith('_PL'):
            key_for_parsing = col
            if '__sy_' in col:
                try:
                    from dataexploration.school_year_utils import resolve_relative_column_key
                    key_for_parsing = resolve_relative_column_key(col)
                except Exception:
                    pass
            info = test_col_map.get(key_for_parsing) or test_col_map.get(col)
            if info:
                labels[col] = _full_test_pl_label(info)
            else:
                inner = key_for_parsing[5:-3]
                parts = [p for p in inner.split('_') if p]
                name_parts = parts[2:] if len(parts) > 2 else parts
                labels[col] = ' '.join(name_parts) + ' PL'
        elif col.startswith('mark_'):
            key_for_parsing = col
            if '__sy_' in col:
                try:
                    from dataexploration.school_year_utils import resolve_relative_column_key
                    key_for_parsing = resolve_relative_column_key(col)
                except Exception:
                    pass
            info = mark_col_map.get(key_for_parsing) or mark_col_map.get(col)
            if info:
                labels[col] = _full_mark_label(info)
            else:
                inner = key_for_parsing[5:]
                parts = [p for p in inner.split('_') if p]
                name_parts = parts[2:] if len(parts) > 2 else parts
                labels[col] = ' '.join(name_parts)
        else:
            labels[col] = col.replace('_', ' ').title()

    _inject_school_year_aliases(options, labels)
    if _ts_codes:
        labels['__tested_school_labels__'] = _ts_codes
    resp_data = {'filter_options': options, 'filter_labels': labels}
    cache.set(cache_key, resp_data, 300)
    return JsonResponse(resp_data)
