"""Chart API views: preview, save, detail, list partial."""
import json
import traceback

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_GET

from dataviz.models import SavedChart
from dataviz.utils.chart_builder import build_option

from ..mixins import is_project_admin, FORBIDDEN


def _cfg_from_post(post):
    """Extract chart config dict from form-encoded POST data.

    Maps HTML form field names to the keys expected by
    ``chart_builder.build_option()``.
    """
    show_legend = bool(post.get('show_legend'))
    legend_pos  = post.get('legend_pos', '')

    # Parse color_map JSON
    cm_raw = post.get('color_map', '')
    try:
        color_map = json.loads(cm_raw) if cm_raw else {}
    except (json.JSONDecodeError, ValueError):
        color_map = {}

    # Parse stack_order JSON
    so_raw = post.get('stack_order', '')
    try:
        stack_order = json.loads(so_raw) if so_raw else []
    except (json.JSONDecodeError, ValueError):
        stack_order = []

    # Parse compare_pl_cols JSON
    cpc_raw = post.get('compare_pl_cols', '')
    try:
        compare_pl_cols = json.loads(cpc_raw) if cpc_raw else []
    except (json.JSONDecodeError, ValueError):
        compare_pl_cols = []

    return {
        'chart_type':        post.get('chart_type', 'bar'),
        'x_col':             post.get('x_col', ''),
        'y_col':             post.get('y_col', ''),
        'series_col':        post.get('series_col', ''),
        'stack_col':         post.get('series_col', ''),
        'compare_pl_cols':   compare_pl_cols,
        'agg':               post.get('agg_func', 'count'),
        'sort_col':          post.get('sort_col', ''),
        'sort_dir':          post.get('sort_dir', 'asc'),
        'limit':             int(post.get('limit', 500) or 500),
        # Visual options
        'palette':           post.get('palette', ''),
        'legend_pos':        legend_pos if legend_pos else ('bottom' if show_legend else 'hidden'),
        'data_labels':       bool(post.get('show_labels')),
        'label_position':    post.get('label_position', 'top'),
        'background':        post.get('background', 'white'),
        'opacity':           float(post.get('opacity', 0.9) or 0.9),
        'bar_border_radius': int(post.get('bar_border_radius', 0) or 0),
        'show_points':       bool(post.get('show_points')),
        'smooth':            bool(post.get('smooth')),
        'color_map':         color_map,
        # Stack order
        'stack_order':       stack_order,
        # Element toggles
        'show_zoom':         bool(post.get('show_zoom', '1')),
        'show_toolbox':      bool(post.get('show_toolbox', '1')),
        'show_grid':         bool(post.get('show_grid', '1')),
        'show_axis_labels':  bool(post.get('show_axis_labels', '1')),
        'animation':         bool(post.get('animation', '1')),
        'show_tooltip':      bool(post.get('show_tooltip', '1')),
        'show_percent':      bool(post.get('show_percent')),
        'compare_gap':       int(post.get('compare_gap') if post.get('compare_gap') not in (None, '') else 30),
        'compare_cat_font_size': int(post.get('compare_cat_font_size', 12) or 12),
        'compare_cat_rotation':  int(post.get('compare_cat_rotation') if post.get('compare_cat_rotation') not in (None, '') else 0),
    }


@login_required
@require_POST
def chart_preview_api(request):
    """
    Build chart preview.

    Accepts:
      - JSON with rows + chart_config (direct render)
      - JSON / form with source ref + config (query → render)
    Returns ``dataviz/partials/chart_preview.html``.
    """
    if not is_project_admin(request.user):
        return FORBIDDEN

    is_htmx = bool(request.headers.get('HX-Request'))
    content_type = request.content_type or ''

    def _error(msg):
        if is_htmx:
            return render(request, 'dataviz/partials/chart_preview.html', {'error': msg})
        return JsonResponse({'error': msg}, status=400)

    if 'application/json' in content_type:
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return _error('Invalid JSON')
        # Legacy: rows passed directly
        if 'rows' in body:
            cfg = body.get('chart_config', {})
            if not cfg.get('chart_type'):
                return _error('chart_type is required')
            try:
                option = build_option(body['rows'], body.get('column_definitions', {}), cfg)
                option.pop('__series_names__', None)
                return render(request, 'dataviz/partials/chart_preview.html', {
                    'option_json': json.dumps(option), 'chart_id': 'preview',
                })
            except Exception as exc:
                return _error(str(exc))
        source_type = body.get('source_type', 'query')
        source_id   = body.get('source_id') or body.get('saved_query_id')
        source_sql  = body.get('source_sql', '')
        cfg = body.get('config', body)
        if not cfg.get('chart_type'):
            cfg['chart_type'] = body.get('chart_type', 'bar')
    else:
        source_type = request.POST.get('source_type', 'query')
        source_id   = request.POST.get('saved_query_id') or request.POST.get('source_id')
        source_sql  = request.POST.get('raw_sql', '')
        cfg = _cfg_from_post(request.POST)

    if not cfg.get('chart_type'):
        return _error('Select a chart type first')

    # If editing a saved chart, capture its id so drill-through works in preview
    _chart_id = None
    if 'application/json' in content_type:
        _chart_id = body.get('id') or body.get('chart_id')
    else:
        _chart_id = request.POST.get('chart_id') or None
    # Coerce to int; fall back to 'preview' for new/unsaved charts
    try:
        _chart_id = int(_chart_id)
    except (TypeError, ValueError):
        _chart_id = None

    # Extract source_filters from the request (chart builder sends JSON-encoded filters)
    source_filters = {}
    if 'application/json' in content_type:
        source_filters = body.get('source_filters', {})
    else:
        sf_raw = request.POST.get('source_filters', '')
        if sf_raw:
            try:
                source_filters = json.loads(sf_raw)
            except (json.JSONDecodeError, ValueError):
                pass

    # Run the query
    from dataviz.utils.data_loader import run_saved_query, run_raw_sql, run_column_query
    if source_type == 'sql':
        result = run_raw_sql(source_sql)
    elif source_type == 'columns':
        # Column Picker source: selected_columns come from POST or JSON
        if 'application/json' in content_type:
            sel_cols_raw = body.get('selected_columns', '[]')
        else:
            sel_cols_raw = request.POST.get('selected_columns', '[]')
        try:
            selected_columns = json.loads(sel_cols_raw) if isinstance(sel_cols_raw, str) else sel_cols_raw
        except (json.JSONDecodeError, ValueError):
            selected_columns = []
        if not selected_columns:
            return _error('Select at least one column in the Explorer')
        result = run_column_query(selected_columns, source_filters)
    else:
        if not source_id:
            return _error('Select a data source first')
        result = run_saved_query(source_id, source_filters, request.user)

    if result.get('error'):
        return _error(result['error'])

    rows = result.get('rows', [])
    if not rows:
        return _error('Query returned no data')

    try:
        option = build_option(rows, {}, cfg)
        # Extract series names for the stack-order UI, then strip the internal key
        series_names = option.pop('__series_names__', [])
        return render(request, 'dataviz/partials/chart_preview.html', {
            'option_json':        json.dumps(option),
            'series_names_json':  json.dumps(series_names),
            'chart_id':           _chart_id or 'preview',
            'drill_enabled':      '1' if _chart_id else '0',
        })
    except Exception as exc:
        return _error(f'Chart error: {exc}')


@login_required
def chart_save_api(request):
    """Create or update a SavedChart. Accepts POST JSON or form-encoded data."""
    if not is_project_admin(request.user):
        return FORBIDDEN
    if request.method not in ('POST', 'PUT'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    is_htmx = bool(request.headers.get('HX-Request'))
    content_type = request.content_type or ''

    if 'application/json' in content_type:
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        name           = (body.get('name') or '').strip()
        chart_id       = body.get('id')
        chart_type     = body.get('chart_type', 'bar')
        config         = body.get('config', {})
        source_type    = body.get('source_type', SavedChart.SOURCE_QUERY)
        source_id      = body.get('source_id')
        source_sql     = body.get('source_sql', '')
        source_filters = body.get('source_filters', {})
        description    = body.get('description', '')
        drill_columns  = body.get('drill_through_columns', [])
        drill_fm       = body.get('drill_through_filter_map', {})
    else:
        name           = request.POST.get('name', '').strip()
        chart_id       = request.POST.get('chart_id') or None
        chart_type     = request.POST.get('chart_type', 'bar')
        source_type    = request.POST.get('source_type', 'query')
        source_id      = request.POST.get('saved_query_id') or request.POST.get('source_id')
        source_sql     = request.POST.get('raw_sql', '')
        description    = request.POST.get('description', '')
        config         = _cfg_from_post(request.POST)
        sf_raw         = request.POST.get('source_filters', '')
        try:
            source_filters = json.loads(sf_raw) if sf_raw else {}
        except (json.JSONDecodeError, ValueError):
            source_filters = {}
        dt_str         = request.POST.get('drill_through_columns_str', '')
        drill_columns  = [c.strip() for c in dt_str.split(',') if c.strip()] if dt_str else []
        drill_fm       = {}

    if not name:
        msg = 'Chart name is required'
        if is_htmx:
            return HttpResponse(f'<div class="text-red-600 text-sm py-2">{msg}</div>')
        return JsonResponse({'error': msg}, status=400)

    if chart_id:
        chart = get_object_or_404(SavedChart, pk=chart_id, owner=request.user)
    else:
        chart = SavedChart(owner=request.user)

    chart.name                     = name
    chart.description              = description
    chart.chart_type               = chart_type
    chart.config                   = config
    chart.source_type              = source_type
    chart.source_id                = source_id or None
    chart.source_sql               = source_sql
    chart.source_filters           = source_filters
    chart.drill_through_columns    = drill_columns
    chart.drill_through_filter_map = drill_fm

    # For Column Picker source, store selected columns inside config
    if source_type == 'columns':
        if 'application/json' in content_type:
            sel_raw = body.get('selected_columns', '[]')
        else:
            sel_raw = request.POST.get('selected_columns', '[]')
        try:
            sel_cols = json.loads(sel_raw) if isinstance(sel_raw, str) else sel_raw
        except (json.JSONDecodeError, ValueError):
            sel_cols = []
        chart.config['selected_columns'] = sel_cols

    chart.save()

    if is_htmx:
        return HttpResponse(
            f'<div class="bg-green-50 border border-green-200 text-green-700 '
            f'text-sm rounded-lg p-3 flex items-center gap-2">'
            f'<svg class="w-4 h-4 shrink-0" fill="currentColor" viewBox="0 0 20 20">'
            f'<path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.857-9.809'
            f'a.75.75 0 00-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 10-1.06 1.06l2.5 '
            f'2.5a.75.75 0 001.137-.089l4-5.5z" clip-rule="evenodd"/></svg>'
            f'<span>Chart &ldquo;{chart.name}&rdquo; saved!</span>'
            f'<a href="{reverse("dataviz:chart_edit", args=[chart.pk])}" '
            f'class="underline ml-auto text-green-800">Edit</a></div>'
        )

    return JsonResponse({'chart': chart.to_dict(), 'success': True})


@login_required
def chart_detail_api(request, chart_id):
    """GET a chart's full dict; DELETE to remove it."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    chart = get_object_or_404(SavedChart, pk=chart_id, owner=request.user)

    if request.method == 'GET':
        return JsonResponse({'chart': chart.to_dict()})
    if request.method == 'DELETE':
        chart.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@login_required
@require_GET
def chart_render_api(request, chart_id):
    """
    Return a rendered chart partial for a saved chart_id.
    Runs the underlying query and builds the ECharts option.
    Used by page/dashboard view to lazy-load chart data.
    """
    from dataviz.utils.data_loader import run_saved_query, run_raw_sql, run_column_query

    chart = get_object_or_404(SavedChart, pk=chart_id)

    try:
        if chart.source_type == SavedChart.SOURCE_SQL:
            result = run_raw_sql(chart.source_sql)
        elif chart.source_type == SavedChart.SOURCE_COLUMNS:
            # Column Picker source: selected columns stored in config['selected_columns']
            selected_columns = chart.config.get('selected_columns', [])
            # Merge saved chart filters with page-level pf_* filters
            merged_filters = dict(chart.source_filters or {})
            for key in request.GET:
                if key.startswith('pf_'):
                    vals = request.GET.getlist(key)
                    vals = [v for v in vals if v]
                    if vals:
                        col = key[3:]
                        merged_filters[col] = vals if len(vals) > 1 else vals[0]
            result = run_column_query(selected_columns, merged_filters)
        else:
            # Merge saved chart filters with page-level filters from URL (pf_* params)
            merged_filters = dict(chart.source_filters or {})
            for key in request.GET:
                if key.startswith('pf_'):
                    vals = request.GET.getlist(key)
                    vals = [v for v in vals if v]
                    if vals:
                        col = key[3:]  # strip 'pf_' prefix
                        merged_filters[col] = vals if len(vals) > 1 else vals[0]
            result = run_saved_query(chart.source_id, merged_filters, request.user)

        if result.get('error'):
            option_json = '{}'
            error = result['error']
        else:
            option = build_option(result.get('rows', []), chart.column_defs, chart.config)
            option.pop('__series_names__', None)
            option_json = json.dumps(option)
            error = None
    except Exception as exc:
        option_json = '{}'
        error = str(exc)

    # Drill-through is enabled for ALL saved charts.  If no explicit columns
    # were configured we fall back to a sensible default set.
    # Source context for breadcrumbs (forwarded from the page/dashboard template)
    return render(request, 'dataviz/partials/chart_preview.html', {
        'option_json':       option_json,
        'chart_id':          chart.pk,
        'drill_enabled':     '1',
        'drill_source':      request.GET.get('source', ''),
        'drill_source_id':   request.GET.get('source_id', ''),
        'drill_source_name': request.GET.get('source_name', ''),
        'error':             error,
    })


@login_required
@require_GET
def chart_list_partial(request):
    """HTMX-friendly: renders a partial list of saved charts for the chart picker modal."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    q = request.GET.get('q', '').strip()
    charts = SavedChart.objects.filter(owner=request.user).order_by('name')
    if q:
        charts = charts.filter(name__icontains=q)

    return render(request, 'dataviz/partials/chart_picker_list.html', {'charts': charts})
