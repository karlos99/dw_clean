"""Drill-through HTMX API views."""
import csv
import io
import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, StreamingHttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render

from dataviz.models import SavedChart
from dataviz.utils.drill_through import (
    build_drill_filter, execute_drill_query, get_column_distinct_values,
    get_all_available_columns,
)
from dataviz.views.drill_views import _col_label, _parse_column_filters

MAX_PER_PAGE = 5000


@login_required
def drill_rows_api(request, chart_id):
    """
    Return paginated drill-through rows as an HTMX partial.

    Accepts all the same params as the main drill_table view so that
    HTMX requests can carry sort/filter/column state:
      x_val, series_val, page, per_page, sort_by, sort_dir,
      cols (comma-separated), cf_<col> (per-column filters),
      skip_drill_<col>=1 (removed chart filters)
    """
    chart = get_object_or_404(SavedChart, pk=chart_id)
    params = request.GET

    # Drill filters (with removal support)
    raw_drill = build_drill_filter(chart, params)
    drill_filters = {}
    for k, v in raw_drill.items():
        if params.get(f'skip_drill_{k}') != '1':
            drill_filters[k] = v

    # Merge page-level filters (pf_* query params)
    for key in params:
        if key.startswith('pf_'):
            vals = params.getlist(key)
            vals = [v for v in vals if v]
            if vals:
                drill_filters[key[3:]] = vals if len(vals) > 1 else vals[0]

    page_num = int(params.get('page', 1))
    per_page = min(int(params.get('per_page', 50)), MAX_PER_PAGE)
    sort_by = params.get('sort_by', '')
    sort_dir = params.get('sort_dir', 'asc')

    # Column selection
    cols_param = params.get('cols', '')
    if cols_param:
        selected_columns = [c for c in cols_param.split(',') if c]
    else:
        selected_columns = chart.drill_through_columns or []
    selected_columns = [c.split('.')[-1] for c in selected_columns]

    # Per-column filters
    column_filters = _parse_column_filters(request)

    # Ensure dynamic columns from drill filters are fetched
    extra_cols = list(selected_columns) if selected_columns else []
    for fk in drill_filters:
        if fk.startswith(('test_', 'mark_')) and fk not in extra_cols:
            extra_cols.append(fk)

    result = execute_drill_query(
        chart, drill_filters, columns=selected_columns, user=request.user,
        page=page_num, per_page=per_page,
        sort_by=sort_by, sort_dir=sort_dir,
        column_filters=column_filters,
        extra_columns=extra_cols or None,
    )

    query_columns = [c.split('.')[-1] for c in result.get('columns', [])]
    all_columns = [c.split('.')[-1] for c in result.get('all_columns', query_columns)]
    col_labels = {c.split('.')[-1]: _col_label(c) for c in all_columns}

    context = {
        'chart': chart,
        'rows': result.get('rows', []),
        'columns': query_columns,
        'all_columns': all_columns,
        'col_labels': col_labels,
        'total_rows': result.get('total_rows', 0),
        'page': result.get('page', 1),
        'per_page': result.get('per_page', 50),
        'error': result.get('error', ''),
        'x_val': params.get('x_val', ''),
        'series_val': params.get('series_val', ''),
        'sort_by': sort_by,
        'sort_dir': sort_dir,
    }

    if request.headers.get('HX-Request'):
        return render(request, 'dataviz/partials/drill_rows.html', context)
    return JsonResponse({k: v for k, v in context.items() if k not in ('rows', 'columns')})


@login_required
def drill_columns_api(request, chart_id):
    """
    POST with {columns: [col1, col2, ...]} to persist column selection in session.
    Returns the column selector partial via HTMX OOB swap.
    """
    chart = get_object_or_404(SavedChart, pk=chart_id)

    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
        selected = body.get('columns', [])
    except (json.JSONDecodeError, ValueError):
        selected = request.POST.getlist('columns')

    session_key = f'drill_cols_{chart_id}'
    request.session[session_key] = [str(c) for c in selected]
    request.session.modified = True

    # return HTMX partial that refreshes the column selector
    all_cols = chart.drill_through_columns or []
    context = {
        'chart': chart,
        'all_columns': all_cols,
        'selected_columns': request.session[session_key],
    }
    if request.headers.get('HX-Request'):
        return render(request, 'dataviz/partials/drill_column_selector.html', context)
    return JsonResponse({'selected': selected, 'success': True})


@login_required
def drill_filter_values_api(request, chart_id):
    """
    Return distinct values for a column in the drill query.
    GET params: column, x_val, series_val
    Returns JSON: { values: [...] }
    """
    chart = get_object_or_404(SavedChart, pk=chart_id)
    column = request.GET.get('column', '')
    if not column:
        return JsonResponse({'values': []})

    params = {
        'x_val': request.GET.get('x_val', ''),
        'series_val': request.GET.get('series_val', ''),
    }
    raw_drill = build_drill_filter(chart, params)
    # Use active drill filters only
    drill_filters = {}
    for k, v in raw_drill.items():
        if request.GET.get(f'skip_drill_{k}') != '1':
            drill_filters[k] = v

    # Merge page-level filters
    for key in request.GET:
        if key.startswith('pf_'):
            vals = request.GET.getlist(key)
            vals = [v for v in vals if v]
            if vals:
                drill_filters[key[3:]] = vals if len(vals) > 1 else vals[0]

    values = get_column_distinct_values(chart, drill_filters, column, request.user)
    return JsonResponse({'values': values})


@login_required
def drill_export_csv_api(request, chart_id):
    """
    Export the current drill-through data as a CSV file.
    Accepts the same filter/sort/column params as drill_rows_api but fetches
    ALL rows (no pagination) using streaming to avoid memory exhaustion.
    """
    chart = get_object_or_404(SavedChart, pk=chart_id)
    params = request.GET

    # Drill filters (with removal support)
    raw_drill = build_drill_filter(chart, params)
    drill_filters = {}
    for k, v in raw_drill.items():
        if params.get(f'skip_drill_{k}') != '1':
            drill_filters[k] = v

    # Merge page-level filters
    for key in params:
        if key.startswith('pf_'):
            vals = params.getlist(key)
            vals = [v for v in vals if v]
            if vals:
                drill_filters[key[3:]] = vals if len(vals) > 1 else vals[0]

    sort_by = params.get('sort_by', '')
    sort_dir = params.get('sort_dir', 'asc')

    cols_param = params.get('cols', '')
    if cols_param:
        selected_columns = [c for c in cols_param.split(',') if c]
    else:
        selected_columns = chart.drill_through_columns or []
    selected_columns = [c.split('.')[-1] for c in selected_columns]

    column_filters = _parse_column_filters(request)

    # Fetch all rows in chunks to avoid loading everything into memory
    CHUNK_SIZE = 2000
    safe_name = chart.name.replace(' ', '_').replace('/', '_')[:50]

    def stream_csv():
        first_chunk = True
        page = 1
        while True:
            result = execute_drill_query(
                chart, drill_filters, columns=selected_columns, user=request.user,
                page=page, per_page=CHUNK_SIZE,
                sort_by=sort_by, sort_dir=sort_dir,
                column_filters=column_filters,
                extra_columns=selected_columns or None,
            )
            rows = result.get('rows', [])
            cols = result.get('columns', [])

            if not rows:
                break

            if first_chunk:
                yield ','.join([_col_label(c) for c in cols]) + '\n'
                first_chunk = False

            for row in rows:
                yield ','.join(
                    f'"{str(row.get(c, "")).replace(chr(34), chr(34)+chr(34))}"'
                    for c in cols
                ) + '\n'

            if len(rows) < CHUNK_SIZE:
                break
            page += 1

    response = StreamingHttpResponse(stream_csv(), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="drill_{safe_name}.csv"'
    return response


@login_required
def drill_available_columns_api(request, chart_id):
    """
    Return the full universe of columns that can be added to the drill table.
    Used by the sidebar to show columns not already in the query.
    """
    chart = get_object_or_404(SavedChart, pk=chart_id)
    available = get_all_available_columns(chart, request.user)
    labels = {c: _col_label(c) for c in available}
    return JsonResponse({'columns': available, 'labels': labels})
