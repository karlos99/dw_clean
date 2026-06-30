"""Chart API views: preview, save, detail, list partial."""
import hashlib
import json
import traceback

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST, require_GET

from dataviz.models import SavedChart
from dataviz.utils.chart_builder import build_option

from ..mixins import is_project_admin, FORBIDDEN


_CONFIG_COL_KEYS = frozenset({
    'x_col', 'y_col', 'stack_col', 'series_col', 'group_col',
    'pg_before_col', 'pg_after_col', 'st_row_col', 'st_col_col',
    'st_row_cols', 'st_col_cols', 'st_value_col',
    'sort_col', 'compare_pl_cols',
})


def _remap_config_cols(config: dict, column_mappings: dict | None) -> dict:
    """Remap 'table.column' column references in chart config to their clean aliases.

    Needed for backward-compat: charts saved before alias support stored raw
    table-prefixed names (e.g. x_col='current_student.grade') while the rows
    now carry aliased keys ('grade'). The remapping makes both old and new
    configs work without a data migration.
    """
    if not column_mappings or not config:
        return config

    def _remap(val):
        if isinstance(val, str):
            parts = [p.strip() for p in val.split(',') if p.strip()]
            remapped = [column_mappings.get(p, p) for p in parts]
            return ','.join(remapped)
        if isinstance(val, list):
            return [column_mappings.get(v, v) if isinstance(v, str) else v for v in val]
        return val

    return {k: (_remap(v) if k in _CONFIG_COL_KEYS else v) for k, v in config.items()}


def _augment_rows_with_assessment_cols(rows: list, needed_keys: list, user) -> list:
    """Fetch test_*/mark_* columns via the data explorer and merge into rows.

    Used when a dataset chart references assessment columns not returned by the
    dataset SQL (i.e. the dataset doesn't include the results/tests JOIN).  The
    data explorer already knows how to resolve these keys to pl/ss from results
    and test metadata from tests — this reuses that exact mechanism.
    """
    if not rows or not needed_keys:
        return rows
    from dataviz.utils.data_loader import run_column_query

    id_key = None
    ids = []
    for candidate in ('stu_annual_key', 'ssid', 'student_id'):
        ids = [str(r.get(candidate) or '').strip() for r in rows if r.get(candidate)]
        if ids:
            id_key = candidate
            ids = list(set(ids))
            break
    if not ids:
        return rows

    explorer_filters: dict = {id_key: ids}
    years = list({str(r.get('school_year') or '').strip() for r in rows if r.get('school_year')})
    if years:
        explorer_filters['school_year'] = years

    fetch_cols = ['stu_annual_key', 'ssid', 'student_id', 'school_year'] + needed_keys
    exp_result = run_column_query(fetch_cols, explorer_filters, user=None)
    lookup = {str(r.get(id_key) or '').strip(): r for r in exp_result.get('rows', []) if r.get(id_key)}

    for row in rows:
        match = str(row.get(id_key) or '').strip()
        if match and match in lookup:
            exp_row = lookup[match]
            for k in needed_keys:
                if k not in row or row[k] is None:
                    row[k] = exp_row.get(k)
    return rows


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

    # Parse assessment_compare filters
    def _parse_json_list(key):
        raw = post.get(key, '')
        try:
            return json.loads(raw) if raw else []
        except (json.JSONDecodeError, ValueError):
            return []

    ac_include_cats = _parse_json_list('ac_include_cats')
    ac_include_pl   = _parse_json_list('ac_include_pl')
    ac_col_filters_raw = post.get('ac_col_filters', '')
    try:
        ac_col_filters = json.loads(ac_col_filters_raw) if ac_col_filters_raw else {}
    except (json.JSONDecodeError, ValueError):
        ac_col_filters = {}

    # Parse pl_scale (JSON array or comma-separated string)
    pls_raw = post.get('pl_scale', '')
    try:
        if pls_raw and pls_raw.startswith('['):
            pl_scale = json.loads(pls_raw)
        else:
            pl_scale = [v.strip() for v in pls_raw.split(',') if v.strip()]
    except (json.JSONDecodeError, ValueError):
        pl_scale = []

    def _parse_int(val, default=0):
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def _parse_float(val, default=None):
        try:
            return float(val) if val else default
        except (ValueError, TypeError):
            return default

    def _parse_pos(val):
        if not val: return None
        if val in ('center', 'middle', 'left', 'right', 'top', 'bottom'):
            return val
        return _parse_int(val, None)

    return {
        'chart_type':        post.get('chart_type', 'bar'),
        'x_col':             post.get('x_col', ''),
        'y_col':             post.get('y_col', ''),
        'series_col':        post.get('series_col', ''),
        'stack_col':         post.get('series_col', ''),
        'group_col':         post.get('group_col', ''),
        'compare_pl_cols':   compare_pl_cols,
        'ac_include_cats':   ac_include_cats,
        'ac_include_pl':     ac_include_pl,
        'ac_col_filters':    ac_col_filters,
        'agg':               post.get('agg_func', 'count'),
        'agg_decimals':      _parse_int(post.get('agg_decimals'), None),
        # Progress Growth fields
        'pg_before_col':     post.get('pg_before_col', ''),
        'pg_after_col':      post.get('pg_after_col', ''),
        'pl_scale':          pl_scale,
        'pg_display':        post.get('pg_display', 'stacked_bar'),
        # Summary Table fields
        'st_row_col':        post.get('st_row_col', ''),
        'st_col_col':        post.get('st_col_col', ''),
        'st_col_mode':       post.get('st_col_mode', 'values'),
        'st_show_row_totals': post.get('st_show_row_totals', '1') == '1',
        'st_show_col_totals': post.get('st_show_col_totals', '1') == '1',
        # Dynamic Summary Table fields
        'st_row_cols':       _parse_json_list('st_row_cols'),
        'st_col_cols':       _parse_json_list('st_col_cols'),
        'st_agg_func':       post.get('st_agg_func', 'count'),
        'st_value_col':      post.get('st_value_col', ''),
        'st_agg_decimals':   _parse_int(post.get('st_agg_decimals'), None),
        'sort_col':          post.get('sort_col', ''),
        'sort_dir':          post.get('sort_dir', 'asc'),
        'limit':             _parse_int(post.get('limit'), None),
        # Visual options
        'palette':           post.get('palette', ''),
        'legend_pos':        legend_pos if legend_pos else ('bottom' if show_legend else 'hidden'),
        'legend_item_gap':   _parse_int(post.get('legend_item_gap'),  10),
        'legend_font_size':  _parse_int(post.get('legend_font_size'), 11),
        'legend_icon_size':  _parse_int(post.get('legend_icon_size'), 10),
        'legend_offset':     _parse_int(post.get('legend_offset'),     0),
        'legend_left':       _parse_pos(post.get('legend_left')),
        'legend_top':        _parse_pos(post.get('legend_top')),
        'data_labels':       bool(post.get('show_labels')),
        'label_position':    post.get('label_position', 'top'),
        'label_font_size':   _parse_int(post.get('label_font_size'), 10),
        'background':        post.get('background', 'white'),
        'opacity':           float(post.get('opacity', 0.9) or 0.9),
        'bar_border_radius': _parse_int(post.get('bar_border_radius'), 0),
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
        'show_categories':   bool(post.get('show_categories', '1')),
        'show_axis_titles':  bool(post.get('show_axis_titles', '1')),
        'animation':         bool(post.get('animation', '1')),
        'show_tooltip':           bool(post.get('show_tooltip', '1')),
        'tooltip_show_pct':       bool(post.get('tooltip_show_pct', '1')),
        'tooltip_show_count':     bool(post.get('tooltip_show_count', '1')),
        'tooltip_value_pct':      post.get('tooltip_value_pct') == '1',
        'show_percent':           bool(post.get('show_percent')),
        'show_percent_symbol':    bool(post.get('show_percent_symbol', '1')),
        'percent_decimals':  _parse_int(post.get('percent_decimals'), 1),
        'category_order':    _parse_json_list('category_order'),
        'bar_layout':       post.get('bar_layout', 'grouped'),
        'bar_orientation':  post.get('bar_orientation', 'vertical'),
        'bar_radial':       bool(post.get('bar_radial')),
        'bar_polar':        bool(post.get('bar_polar')),
        'line_layout':      post.get('line_layout', 'standard'),
        'line_fill':        bool(post.get('line_fill')),
        'compare_gap':       _parse_int(post.get('compare_gap'), 30),
        'compare_cat_font_size': _parse_int(post.get('compare_cat_font_size'), 12),
        'compare_cat_rotation':  _parse_int(post.get('compare_cat_rotation'), 0),
        # Axis & layout
        'x_label_rotation':  _parse_int(post.get('x_label_rotation'), 0),
        'x_label_font_size': _parse_int(post.get('x_label_font_size'), 11),
        'x_label_max_length': _parse_int(post.get('x_label_max_length'), 0),
        'force_show_all_labels': bool(post.get('force_show_all_labels')),
        'bar_category_gap':  post.get('bar_category_gap') or None,
        'y_axis_min':        post.get('y_axis_min') or None,
        'y_axis_max':        post.get('y_axis_max') or None,
        # Grid + title overrides (from interactive editor)
        'grid_left':   _parse_int(post.get('grid_left'),   None),
        'grid_right':  _parse_int(post.get('grid_right'),  None),
        'grid_top':    _parse_int(post.get('grid_top'),    None),
        'grid_bottom': _parse_int(post.get('grid_bottom'), None),
        'title_left':     _parse_pos(post.get('title_left')),
        'title_top':      _parse_pos(post.get('title_top')),
        'title_font_size': _parse_int(post.get('title_font_size'), 16),
        'label_distance':   _parse_int(post.get('label_distance'),   None),
        'chart_title':      post.get('chart_title', '').strip(),
        'container_width':  _parse_int(post.get('container_width'),  None),
        'container_height': _parse_int(post.get('container_height'), None),
        'parliament_dot_scale': _parse_float(post.get('parliament_dot_scale'), None),
        'parliament_shape':     post.get('parliament_shape', 'semi-circle'),
        'parliament_rows':      _parse_int(post.get('parliament_rows'), None) if post.get('parliament_rows') else None,
        'parliament_show_ss':   bool(post.get('parliament_show_ss')),
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
                option.pop('__category_names__', None)
                query_columns = list(body['rows'][0].keys()) if body['rows'] else []
                return render(request, 'dataviz/partials/chart_preview.html', {
                    'option_json': json.dumps(option), 'chart_id': 'preview',
                    'query_columns_json': json.dumps(query_columns),
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

    # ── Preview data cache: reuse query result across visual config tweaks ──
    # Cache keyed by data source config; expires in 2 minutes so fresh data
    # is fetched periodically while still avoiding repeated queries during
    # rapid config iteration.
    _sel_cols = None
    if source_type == 'columns':
        if 'application/json' in content_type:
            _sel_raw = body.get('selected_columns', '[]')
        else:
            _sel_raw = request.POST.get('selected_columns', '[]')
        try:
            _sel_cols = json.loads(_sel_raw) if isinstance(_sel_raw, str) else _sel_raw
        except (json.JSONDecodeError, ValueError):
            _sel_cols = []
    preview_source_hash = hashlib.md5(json.dumps({
        'st': source_type,
        'sid': source_id,
        'ssql': source_sql,
        'sf': source_filters,
        'sc': _sel_cols,
    }, sort_keys=True).encode()).hexdigest()[:12]
    preview_data_key = f'preview_data:{preview_source_hash}'
    preview_cached = cache.get(preview_data_key)

    # Run the query (skip if cached data from a recent preview)
    from dataviz.utils.data_loader import run_saved_query, run_raw_sql, run_column_query
    if preview_cached is not None:
        result = preview_cached
    elif source_type == 'sql':
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
    elif source_type == 'dataset':
        if not source_id:
            return _error('Select a dataset first')
        from dataviz.models import Dataset
        from dataviz.utils.compiler import (
            compile_dataset_to_sql, execute_compiled_query_aliased,
            resolve_dataset_filters, generate_column_aliases,
        )
        try:
            ds = Dataset.objects.get(pk=source_id)
        except Dataset.DoesNotExist:
            return _error('Dataset not found')
        # Fall back to auto-generated aliases when the dataset has no saved mappings.
        col_mappings = ds.column_mappings or generate_column_aliases(ds.columns or [], {})
        active_tables = {ds.base_table} | {j['table'] for j in (ds.joins or []) if j.get('table')}
        resolved_filters = resolve_dataset_filters(source_filters or {}, ds, active_tables)
        # Parse limit from config if present to avoid returning too many records from query
        limit_val = cfg.get('limit')
        try:
            limit_val = int(limit_val) if limit_val is not None and str(limit_val).strip() != '' else 150000
        except (ValueError, TypeError):
            limit_val = 150000
        sql, params = compile_dataset_to_sql(ds, user=None, interactive_filters=resolved_filters, limit=limit_val)
        res = execute_compiled_query_aliased(sql, params, col_mappings)
        if res.get('error'):
            return _error(res['error'])
        aliased_rows = res.get('rows', [])
        # Fetch test_*/mark_* columns via data explorer for any alias that wasn't
        # returned by the dataset SQL (dataset without a results/tests JOIN).
        _missing_assessment = [
            v for v in col_mappings.values()
            if v and (v.startswith('test_') or v.startswith('mark_'))
            and (not aliased_rows or v not in aliased_rows[0])
        ]
        if _missing_assessment:
            aliased_rows = _augment_rows_with_assessment_cols(aliased_rows, _missing_assessment, None)
        cfg = _remap_config_cols(cfg, col_mappings)
        result = {'rows': aliased_rows, 'columns': res.get('columns', []), 'row_count': res.get('row_count', 0)}
    else:
        if not source_id:
            return _error('Select a data source first')
        result = run_saved_query(source_id, source_filters, request.user, apply_user_filters=False)

    # Cache preview data for 2 min so rapid config iteration reuses the query
    if preview_cached is None and not result.get('error') and result.get('rows'):
        cache.set(preview_data_key, result, timeout=120)

    if result.get('error'):
        return _error(result['error'])

    rows = result.get('rows', [])
    if not rows:
        return _error('Query returned no data')

    try:
        option = build_option(rows, {}, cfg)
        # Extract series names for the stack-order UI, then strip the internal key
        series_names    = option.pop('__series_names__', [])
        category_names  = option.pop('__category_names__', [])
        ac_avail_cats   = option.pop('__ac_avail_cats__', [])
        ac_avail_pl     = option.pop('__ac_avail_pl__', [])
        query_columns = list(rows[0].keys()) if rows else (result.get('columns', []))
        return render(request, 'dataviz/partials/chart_preview.html', {
            'option_json':        json.dumps(option),
            'series_names_json':  json.dumps(series_names),
            'category_names_json': json.dumps(category_names),
            'ac_avail_cats_json': json.dumps(ac_avail_cats),
            'ac_avail_pl_json':   json.dumps(ac_avail_pl),
            'query_columns_json': json.dumps(query_columns),
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
        school_filter_mode = body.get('school_filter_mode', 'enrolled')
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
        school_filter_mode = request.POST.get('school_filter_mode', 'enrolled')

    if not name:
        msg = 'Chart name is required'
        if is_htmx:
            return HttpResponse(f'<div class="text-red-600 text-sm py-2">{msg}</div>')
        return JsonResponse({'error': msg}, status=400)

    if chart_id:
        chart = get_object_or_404(SavedChart, pk=chart_id)
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
    chart.school_filter_mode       = school_filter_mode
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
    """GET a chart's full dict; PUT to update description; DELETE to remove it."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    chart = get_object_or_404(SavedChart, pk=chart_id)

    if request.method == 'GET':
        return JsonResponse({'chart': chart.to_dict()})
    if request.method == 'PUT':
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        if 'description' in body:
            chart.description = body['description']
            chart.save(update_fields=['description'])
            return JsonResponse({'success': True, 'description': chart.description})
        return JsonResponse({'error': 'No updatable fields provided'}, status=400)
    if request.method == 'DELETE':
        chart.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@login_required
@require_POST
def chart_move_api(request, chart_id):
    """Move a chart to a different folder by updating its name prefix."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    chart = get_object_or_404(SavedChart, pk=chart_id)
    try:
        body = json.loads(request.body)
        new_folder = body.get('folder', '').strip().strip('/')
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    # Extract current display name (part after last slash)
    display_name = chart.name.split('/')[-1].strip()
    
    if new_folder:
        chart.name = f"{new_folder} / {display_name}"
    else:
        chart.name = display_name
    
    chart.save()
    return JsonResponse({'chart': chart.to_dict(), 'success': True})


@login_required
@require_POST
def chart_duplicate_api(request, chart_id):
    """Duplicate a saved chart."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    chart = get_object_or_404(SavedChart, pk=chart_id)
    
    # Clone the object
    chart.pk = None
    chart.id = None
    chart.name = f"{chart.name} (Copy)"
    chart.save()
    
    return JsonResponse({'chart': chart.to_dict(), 'success': True})


@login_required
@require_GET
def chart_render_api(request, chart_id):
    """
    Return a rendered chart partial for a saved chart_id.
    Runs the underlying query and builds the ECharts option.
    Used by page/dashboard view to lazy-load chart data.

    Caching: rendered HTML is cached in Redis keyed by
      chart_id + chart.updated_at + sorted pf_* filter params
    so that repeat views (same filters, any user) return in <10 ms.
    The cache automatically busts when the chart config is saved
    (updated_at changes) or when the 5-minute TTL expires.
    """
    from dataviz.utils.data_loader import run_saved_query, run_raw_sql, run_column_query

    chart = get_object_or_404(SavedChart, pk=chart_id)

    # ── Build cache key ────────────────────────────────────────────
    # Only pf_* params affect query results — source/source_id/source_name
    # are breadcrumb-only and must NOT be part of the key.
    pf_params = sorted(
        (k, v)
        for k in request.GET
        if k.startswith('pf_')
        for v in request.GET.getlist(k)
        if v
    )
    pf_hash = hashlib.md5(str(pf_params).encode()).hexdigest()[:12]
    # updated_at as an int-second timestamp busts the key on every chart save
    updated_ts = int(chart.updated_at.timestamp())

    # ── Two-layer cache: data cache (5 min) + render cache (1 hour) ──
    # Layer 1 – Data cache: raw rows keyed by source + filters
    sel_cols_hash = ""
    if chart.source_type == SavedChart.SOURCE_COLUMNS:
        sel_cols_hash = hashlib.md5(json.dumps(chart.config.get('selected_columns', []), sort_keys=True).encode()).hexdigest()[:8]
    
    source_part = f'{chart.source_type}:{chart.source_id or "_"}:{chart.source_sql or "_"}:{sel_cols_hash}:{pf_hash}:{updated_ts}'
    data_cache_key = f'chart_data:{source_part}'
    data_cached = cache.get(data_cache_key)
    if data_cached is not None:
        result = data_cached
    else:
        result = None

    # Layer 2 – Render cache: rendered HTML keyed by chart config + data hash
    if result is not None:
        data_hash = hashlib.md5(json.dumps(result.get('rows',[])[:1], sort_keys=True).encode()).hexdigest()[:12]
    else:
        data_hash = 'raw'
    config_hash = hashlib.md5(json.dumps(chart.config, sort_keys=True).encode()).hexdigest()[:12]
    render_cache_key = f'chart_render:{chart_id}:{updated_ts}:{data_hash}:{config_hash}:{pf_hash}'
    etag = f'W/"{render_cache_key[-16:]}"'

    # Support conditional requests (If-None-Match)
    if request.headers.get('If-None-Match') == etag:
        resp = HttpResponse(status=304)
        resp['ETag'] = etag
        resp['Cache-Control'] = 'private, no-cache, no-store, must-revalidate, max-age=0'
        return resp

    cached_html = cache.get(render_cache_key)
    if cached_html is not None:
        resp = HttpResponse(cached_html, content_type='text/html')
        resp['X-Chart-Cache'] = 'HIT'
        resp['ETag'] = etag
        resp['Cache-Control'] = 'private, no-cache, no-store, must-revalidate, max-age=0'
        return resp

    # ── Cache miss: run the full pipeline ─────────────────────────
    chart_cfg = chart.config  # may be overridden below for dataset source
    try:
        if result is not None:
            # Data cache hit — skip SQL, jump to rendering
            pass
        elif chart.source_type == SavedChart.SOURCE_SQL:
            result = run_raw_sql(chart.source_sql)
        elif chart.source_type == SavedChart.SOURCE_COLUMNS:
            selected_columns = chart.config.get('selected_columns', [])
            merged_filters = dict(chart.source_filters or {})
            for key in request.GET:
                if key.startswith('pf_'):
                    vals = [v for v in request.GET.getlist(key) if v]
                    if vals:
                        merged_filters[key[3:]] = vals if len(vals) > 1 else vals[0]
            result = run_column_query(selected_columns, merged_filters)
        elif chart.source_type == SavedChart.SOURCE_DATASET:
            from dataviz.models import Dataset
            from dataviz.utils.compiler import (
                compile_dataset_to_sql, execute_compiled_query_aliased,
                resolve_dataset_filters, generate_column_aliases,
            )
            try:
                ds = Dataset.objects.get(pk=chart.source_id)
            except Dataset.DoesNotExist:
                result = {'error': 'Dataset not found', 'rows': [], 'columns': []}
            else:
                # Dataset-specific data cache — key includes dataset updated_at
                # so schema changes automatically bust stale cached rows.
                col_mappings = ds.column_mappings or generate_column_aliases(ds.columns or [], {})
                raw_filters = dict(chart.source_filters or {})
                for key in request.GET:
                    if key.startswith('pf_'):
                        vals = [v for v in request.GET.getlist(key) if v]
                        if vals:
                            raw_filters[key[3:]] = vals if len(vals) > 1 else vals[0]
                active_tables = {ds.base_table} | {j['table'] for j in (ds.joins or []) if j.get('table')}
                resolved_filters = resolve_dataset_filters(raw_filters, ds, active_tables)
                limit_val = chart_cfg.get('limit')
                try:
                    limit_val = int(limit_val) if limit_val is not None and str(limit_val).strip() != '' else 150000
                except (ValueError, TypeError):
                    limit_val = 150000
                ds_updated = int(ds.updated_at.timestamp())
                ds_filter_hash = hashlib.md5(json.dumps(resolved_filters, sort_keys=True).encode()).hexdigest()[:12]
                ds_cache_key = f'chart_data:ds:{chart.source_id}:{ds_updated}:{limit_val}:{ds_filter_hash}'
                ds_cached = cache.get(ds_cache_key)
                if ds_cached is not None:
                    result = ds_cached
                    chart_cfg = _remap_config_cols(chart.config, col_mappings)
                else:
                    sql, params = compile_dataset_to_sql(ds, user=None, interactive_filters=resolved_filters, limit=limit_val)
                    res = execute_compiled_query_aliased(sql, params, col_mappings)
                    if res.get('error'):
                        result = {'error': res['error'], 'rows': [], 'columns': []}
                    else:
                        result = {'rows': res.get('rows', []), 'columns': res.get('columns', []), 'row_count': res.get('row_count', 0)}
                        if result.get('rows'):
                            cache.set(ds_cache_key, result, timeout=1800)
                    chart_cfg = _remap_config_cols(chart.config, col_mappings)
                
                # After data is retrieved (either from cache or fresh query), augment with assessment cols if needed.
                # This must be done outside the dataset cache because different charts on the same dataset 
                # might require different assessment columns.
                if not result.get('error') and result.get('rows'):
                    aliased_rows = result['rows']
                    _missing_assessment = [
                        v for v in col_mappings.values()
                        if v and (v.startswith('test_') or v.startswith('mark_'))
                        and (not aliased_rows or v not in aliased_rows[0])
                    ]
                    if _missing_assessment:
                        aliased_rows = _augment_rows_with_assessment_cols(aliased_rows, _missing_assessment, None)
                        result['rows'] = aliased_rows

        else:
            merged_filters = dict(chart.source_filters or {})
            for key in request.GET:
                if key.startswith('pf_'):
                    vals = [v for v in request.GET.getlist(key) if v]
                    if vals:
                        merged_filters[key[3:]] = vals if len(vals) > 1 else vals[0]
            result = run_saved_query(chart.source_id, merged_filters, request.user, apply_user_filters=False)

        if result.get('error'):
            option_json = '{}'
            error = result['error']
        else:
            # Save data cache (30 min TTL — confirmed acceptable staleness window)
            if data_cached is None and result.get('rows'):
                cache.set(data_cache_key, result, timeout=1800)
            option = build_option(result.get('rows', []), chart.column_defs, chart_cfg)
            option.pop('__series_names__', None)
            option_json = json.dumps(option)
            error = None
    except Exception as exc:
        option_json = '{}'
        error = str(exc)

    ctx = {
        'option_json':       option_json,
        'chart_id':          chart.pk,
        'drill_enabled':     '1',
        'drill_source':      request.GET.get('source', ''),
        'drill_source_id':   request.GET.get('source_id', ''),
        'drill_source_name': request.GET.get('source_name', ''),
        'error':             error,
    }
    html = render_to_string('dataviz/partials/chart_preview.html', ctx, request=request)

    # Cache only successful renders (no error); render cache = 1 hour
    if not error:
        if result is not None:
            data_hash = hashlib.md5(json.dumps(result.get('rows',[])[:1], sort_keys=True).encode()).hexdigest()[:12]
        else:
            data_hash = 'raw'
        render_cache_key = f'chart_render:{chart_id}:{updated_ts}:{data_hash}:{config_hash}:{pf_hash}'
        etag = f'W/"{render_cache_key[-16:]}"'
        cache.set(render_cache_key, html, timeout=3600)

    resp = HttpResponse(html, content_type='text/html')
    resp['X-Chart-Cache'] = 'MISS'
    resp['ETag'] = etag
    resp['Cache-Control'] = 'private, no-cache, no-store, must-revalidate, max-age=0'
    return resp


@login_required
@require_POST
def chart_save_and_attach_api(request):
    """Save a chart AND attach it as a section to a target page in one call.

    Accepts JSON: the full chart save payload plus:
      - target_page_id: int — the DataVizPage to attach to
      - target_row_id: int (optional) — the DataVizPageRow to place the section in
      - section_gs_w: int (default 6), section_gs_h: int (default 4)
      - section_show_title: bool (default True)
    """
    if not is_project_admin(request.user):
        return FORBIDDEN

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    target_page_id = body.get('target_page_id')
    if not target_page_id:
        return JsonResponse({'error': 'target_page_id is required'}, status=400)

    from dataviz.models import SavedChart, DataVizPage, DataVizPageRow, DataVizPageSection

    try:
        page = DataVizPage.objects.get(pk=target_page_id)
    except DataVizPage.DoesNotExist:
        return JsonResponse({'error': 'Page not found'}, status=404)

    # ── Save the chart (reuse existing save logic) ──
    name = (body.get('name') or 'Untitled').strip()
    chart_id = body.get('id')
    chart_type = body.get('chart_type', 'bar')
    config = body.get('config', {})
    source_type = body.get('source_type', SavedChart.SOURCE_QUERY)
    source_id = body.get('source_id')
    source_sql = body.get('source_sql', '')
    source_filters = body.get('source_filters', {})
    description = body.get('description', '')
    drill_columns = body.get('drill_through_columns', [])
    drill_fm = body.get('drill_through_filter_map', {})
    school_filter_mode = body.get('school_filter_mode', 'enrolled')

    if not name:
        return JsonResponse({'error': 'Chart name is required'}, status=400)

    if chart_id:
        chart = get_object_or_404(SavedChart, pk=chart_id)
    else:
        chart = SavedChart(owner=request.user)

    chart.name = name
    chart.description = description
    chart.chart_type = chart_type
    chart.config = config
    chart.source_type = source_type
    chart.source_id = source_id or None
    chart.source_sql = source_sql
    chart.source_filters = source_filters
    chart.school_filter_mode = school_filter_mode
    chart.drill_through_columns = drill_columns
    chart.drill_through_filter_map = drill_fm

    if source_type == 'columns':
        sel_cols = body.get('selected_columns', [])
        chart.config['selected_columns'] = sel_cols if isinstance(sel_cols, list) else []

    chart.save()

    # ── Attach chart to the target page ──
    target_row_id = body.get('target_row_id')
    if target_row_id:
        try:
            row = DataVizPageRow.objects.get(pk=target_row_id, page=page)
        except DataVizPageRow.DoesNotExist:
            row = None
    else:
        row = None

    # If no row specified or invalid, use the first row of the page, creating one if needed
    if not row:
        row = page.rows.order_by('position').first()
        if not row:
            row = DataVizPageRow.objects.create(
                page=page,
                label='',
                position=0,
            )

    # Determine position for the new section
    last_pos = DataVizPageSection.objects.filter(row=row).order_by('-position').first()
    new_position = (last_pos.position + 1) if last_pos else 0

    section = DataVizPageSection.objects.create(
        page=page,
        row=row,
        element_type='chart',
        chart=chart,
        position=new_position,
        show_title=body.get('section_show_title', True),
        gs_w=body.get('section_gs_w', 6),
        gs_h=body.get('section_gs_h', 4),
    )

    return JsonResponse({
        'chart': chart.to_dict(),
        'section': section.to_dict(),
        'page': page.to_dict(include_sections=False),
        'success': True,
    })


@login_required
@require_POST
def chart_recommend_api(request):
    """Recommend chart types based on selected column names.

    Accepts JSON: { columns: [...], column_definitions: {...} }
    Returns: { recommendations: [{ chart_type, label, icon, reason, score }] }
    """
    if not is_project_admin(request.user):
        return FORBIDDEN

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    columns = body.get('columns', [])
    column_defs = body.get('column_definitions', {})

    from dataviz.utils.chart_builder import recommend_chart_types
    recommendations = recommend_chart_types(columns, column_defs)

    return JsonResponse({'recommendations': recommendations, 'success': True})


@login_required
@require_POST
def chart_describe_api(request):
    """Generate a plain-English description of a chart configuration.

    Accepts JSON: { chart_config: {...}, column_definitions: {...} }
    Returns: { description: '...' }
    """
    if not is_project_admin(request.user):
        return FORBIDDEN

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    cfg = body.get('chart_config', {})
    column_defs = body.get('column_definitions', {})

    from dataviz.utils.chart_builder import auto_describe_chart
    description = auto_describe_chart(cfg, column_defs)

    return JsonResponse({'description': description, 'success': True})


@login_required
@require_GET
def chart_list_partial(request):
    """HTMX-friendly: renders a partial list of saved charts for the chart picker modal."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    q = request.GET.get('q', '').strip()
    charts = SavedChart.objects.all().order_by('name')
    if q:
        charts = charts.filter(name__icontains=q)
    else:
        charts = charts[:200]

    # Build a full tree structure
    root = {'folders': {}, 'charts': []}
    for chart in charts:
        parts = [p.strip() for p in chart.name.split('/')]
        curr = root
        for part in parts[:-1]:
            if part not in curr['folders']:
                curr['folders'][part] = {'folders': {}, 'charts': []}
            curr = curr['folders'][part]
        
        chart.display_name = parts[-1]
        curr['charts'].append(chart)

    return render(request, 'dataviz/partials/chart_picker_list.html', {
        'tree': root,
        'q': q,
    })
