"""Drill-through table view — shows filtered student data after clicking a chart."""
import json

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from dataviz.models import SavedChart
from dataviz.utils.drill_through import (
    build_drill_filter, execute_drill_query, get_all_available_columns,
)
from dataexploration.constants import COLUMN_DEFINITIONS


def _col_label(key):
    """Human-readable label for a column key.

    Static columns are looked up from COLUMN_DEFINITIONS.
    Dynamic test/mark columns use their last segment (e.g. 'BOY_PL') plus a
    cleaned prefix, e.g. 'Dibbles BOY PL'.
    """
    # Sentinel keys used internally by chart builders
    if key == 'pg_cat':
        return 'Progress Category'
    col_map = dict(COLUMN_DEFINITIONS) if isinstance(COLUMN_DEFINITIONS, (list, tuple)) else dict(COLUMN_DEFINITIONS)
    if key in col_map:
        return col_map[key]
    # Prettify dynamic test/mark column keys:
    #   test_2025_2026_Dibbles_Dibbles_BOY_PL  →  Dibbles BOY PL
    #   mark_2025_2026_ELA_Q1                   →  ELA Q1
    parts = key.split('_')
    if len(parts) > 3 and parts[0] in ('test', 'mark'):
        # skip prefix + year portions (test, yyyy, yyyy) then join the rest
        return ' '.join(parts[3:]).replace('  ', ' ')
    return key.replace('_', ' ').title()


def _parse_column_filters(request):
    """Extract per-column filter values from query params.

    Columns arrive as ``cf_<column>=val1&cf_<column>=val2``.
    Returns dict like { 'grade': ['K', '1'], 'race': ['W'] }.
    """
    result = {}
    for key in request.GET:
        if key.startswith('cf_'):
            col = key[3:]
            vals = request.GET.getlist(key)
            if vals:
                result[col] = vals
    return result


def _build_breadcrumbs(request):
    """Build a list of breadcrumb dicts [{label, url}] from source params."""
    source = request.GET.get('source', '')
    source_id = request.GET.get('source_id', '')
    source_name = request.GET.get('source_name', '')

    crumbs = [{'label': 'DataViz', 'url': '/dataviz/'}]

    if source == 'page' and source_id:
        label = source_name or 'Page'
        crumbs.append({'label': label, 'url': f'/dataviz/pages/{source_id}/'})
    elif source == 'dashboard' and source_id:
        # Try to resolve dashboard name
        label = source_name or 'Dashboard'
        crumbs.append({'label': label, 'url': f'/dataviz/dashboards/{source_id}/edit/'})
    elif source == 'chart_view' and source_id:
        label = source_name or 'Chart'
        crumbs.append({'label': label, 'url': f'/dataviz/charts/{source_id}/view/'})

    crumbs.append({'label': 'Drill-Through', 'url': None})
    return crumbs


@login_required
def drill_table(request, chart_id):
    """
    Full-page drill-through table.

    Query params forwarded from the ECharts click event:
      x_val        — value of the x-axis category clicked
      series_val   — series name (stack column value) if applicable
      source       — 'page' | 'dashboard' | 'chart_view' (for breadcrumbs)
      source_id    — slug or id of the originating page/dashboard/chart
      source_name  — human-readable name for the breadcrumb
      sort_by      — column key to sort by
      sort_dir     — 'asc' | 'desc'
      cols         — comma-separated visible column keys (empty = all)
      cf_<col>     — per-column filter values (repeatable)
      skip_drill_<col> — '1' to remove a chart-derived filter
    """
    chart = get_object_or_404(SavedChart, pk=chart_id)

    # Build drill filters from URL params
    params = {
        'x_val':      request.GET.get('x_val', ''),
        'series_val': request.GET.get('series_val', ''),
        'drill_col':  request.GET.get('drill_col', ''),
        'before_val': request.GET.get('before_val', ''),
        'after_val':  request.GET.get('after_val', ''),
        'st_row_val': request.GET.get('st_row_val', ''),
        'st_col_val': request.GET.get('st_col_val', ''),
    }
    raw_drill_filters = build_drill_filter(chart, params)

    # Merge page-level filters (pf_* query params)
    page_filters = {}
    for key in request.GET:
        if key.startswith('pf_'):
            vals = request.GET.getlist(key)
            vals = [v for v in vals if v]
            if vals:
                page_filters[key[3:]] = vals if len(vals) > 1 else vals[0]

    # Allow the user to remove individual drill filters
    active_drill_filters = {}
    removed_drill_keys = set()
    for k, v in raw_drill_filters.items():
        if request.GET.get(f'skip_drill_{k}') == '1':
            removed_drill_keys.add(k)
        else:
            active_drill_filters[k] = v

    # Merge page-level filters into active drill filters
    active_drill_filters.update(page_filters)

    # Per-column header filters
    column_filters = _parse_column_filters(request)

    # Sorting
    sort_by = request.GET.get('sort_by', '')
    sort_dir = request.GET.get('sort_dir', 'asc')

    # Column selection: comma-separated list, empty = all
    cols_param = request.GET.get('cols', '')
    selected_columns = [c for c in cols_param.split(',') if c] if cols_param else []

    # Determine which columns are "extra" — requested but not in the original
    # query result set.  We'll ask execute_drill_query to fetch them.
    # The full universe of addable columns for the sidebar:
    available_columns = get_all_available_columns(chart, request.user)

    page = int(request.GET.get('page', 1))
    per_page = 50

    # Ensure any dynamic columns used in drill filters (e.g. test_*_PL columns
    # from assessment_compare) are included in extra_columns so the data_loader
    # actually fetches them.
    extra_cols = list(selected_columns) if selected_columns else []
    for fk in active_drill_filters:
        if fk.startswith(('test_', 'mark_')) and fk not in extra_cols:
            extra_cols.append(fk)

    result = execute_drill_query(
        chart, active_drill_filters, columns=selected_columns, user=request.user,
        page=page, per_page=per_page,
        sort_by=sort_by, sort_dir=sort_dir,
        column_filters=column_filters,
        extra_columns=extra_cols or None,
    )

    # Columns that the query produced (may be restricted by selection)
    query_columns = result['columns'] or []
    all_columns = result.get('all_columns', query_columns)

    # Merge available_columns into the universe (sidebar needs both query cols
    # and all addable cols)
    universe_set = set(all_columns)
    universe = list(all_columns)
    for c in available_columns:
        if c not in universe_set:
            universe.append(c)
            universe_set.add(c)

    # Build column label map (include universe so sidebar labels work)
    col_labels = {c: _col_label(c) for c in universe}

    # Human-readable drill filter labels (for both active and removed)
    drill_filters_display = []
    for k, v in raw_drill_filters.items():
        drill_filters_display.append({
            'key': k,
            'label': _col_label(k),
            'value': v,
            'active': k not in removed_drill_keys,
        })

    # Active column-level filters for display
    col_filters_display = []
    for k, vals in column_filters.items():
        col_filters_display.append({
            'key': k,
            'label': _col_label(k),
            'values': vals,
        })

    breadcrumbs = _build_breadcrumbs(request)

    # Build display items for page-level filters
    page_filters_display = []
    for k, v in page_filters.items():
        display_val = ', '.join(v) if isinstance(v, list) else v
        page_filters_display.append({
            'key': k,
            'label': _col_label(k),
            'value': display_val,
        })

    return render(request, 'dataviz/drill/table.html', {
        'chart':              chart,
        'rows':               result['rows'],
        'columns':            query_columns,
        'all_columns':        all_columns,
        'all_columns_json':   json.dumps(all_columns),
        'universe':           universe,
        'universe_json':      json.dumps(universe),
        'selected_columns':   selected_columns,
        'selected_columns_json': json.dumps(selected_columns),
        'col_labels':         col_labels,
        'col_labels_json':    json.dumps(col_labels),
        'total_rows':         result['total_rows'],
        'page':               result['page'],
        'per_page':           result['per_page'],
        'has_next':           (result['page'] * result['per_page']) < result['total_rows'],
        'has_prev':           result['page'] > 1,
        'error':              result.get('error'),
        'drill_filters':      drill_filters_display,
        'page_filters':       page_filters_display,
        'col_filters':        col_filters_display,
        'column_filters_raw': column_filters,
        'x_val':              params.get('x_val', ''),
        'series_val':         params.get('series_val', ''),
        'sort_by':            sort_by,
        'sort_dir':           sort_dir,
        'breadcrumbs':        breadcrumbs,
        'query_params':       request.GET.urlencode(),
        'removed_drill_keys': list(removed_drill_keys),
    })

