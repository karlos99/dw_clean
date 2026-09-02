"""Top-level build_option() entry point."""
import json
import random
import re

import polars as pl

try:
    from dataexploration.school_year_utils import resolve_relative_column_key
except Exception:  # pragma: no cover
    def resolve_relative_column_key(col_key: str) -> str:
        return col_key

from .constants import *  # noqa: F401,F403
from .common import *  # noqa: F401,F403
from .charts import *  # noqa: F401,F403
from .tables import *  # noqa: F401,F403
from .progress_growth import *  # noqa: F401,F403
from .assessment_compare import *  # noqa: F401,F403

def build_option(rows: list, col_defs: dict, cfg: dict) -> dict:
    """Build an ECharts 6 option dict from raw rows + column definitions + chart config."""
    if not rows:
        return _message_option('No data loaded', cfg)

    chart_type   = cfg.get('chart_type', 'bar')
    raw_rows = list(rows) if chart_type == 'progress_growth' else None
    legacy_bar_defaults = {
        'column': {'bar_layout': 'grouped', 'bar_orientation': 'vertical', 'bar_radial': False},
        'grouped_bar': {'bar_layout': 'grouped', 'bar_orientation': 'vertical', 'bar_radial': False},
        'stacked_bar': {'bar_layout': 'stacked', 'bar_orientation': 'vertical', 'bar_radial': False},
    }
    if chart_type in legacy_bar_defaults:
        merged_cfg = dict(cfg)
        for key, value in legacy_bar_defaults[chart_type].items():
            if merged_cfg.get(key) in (None, ''):
                merged_cfg[key] = value
        cfg = merged_cfg
        chart_type = 'bar'
    elif chart_type == 'bar':
        merged_cfg = dict(cfg)
        merged_cfg.setdefault('bar_layout', 'grouped')
        merged_cfg.setdefault('bar_orientation', 'vertical')
        merged_cfg.setdefault('bar_radial', False)
        cfg = merged_cfg
    x_col        = cfg.get('x_col', '')
    x_cols_raw   = cfg.get('x_cols', [])
    if isinstance(x_col, str) and ',' in x_col:
        x_cols_raw = [c.strip() for c in x_col.split(',') if c.strip()]
    y_col        = cfg.get('y_col', '')
    y_cols_raw   = cfg.get('y_cols', [])
    stack_col    = cfg.get('stack_col', '')
    group_col    = cfg.get('group_col', '')
    compare_pl_cols = cfg.get('compare_pl_cols', [])
    size_col     = cfg.get('size_col', '')
    agg          = str(cfg.get('agg', 'count') or 'count').lower()
    if agg not in {'count', 'sum', 'avg', 'min', 'max', 'median', 'count_unique', 'count_occurrence'}:
        agg = 'count'
    _raw_decimals = cfg.get('agg_decimals')
    agg_decimals  = int(_raw_decimals) if _raw_decimals is not None else None
    # Dates (date_series) extra fields
    ds_date_col = cfg.get('ds_date_col', '')
    ds_lines_raw = cfg.get('ds_lines', [])
    ds_lines = ds_lines_raw if isinstance(ds_lines_raw, list) else []
    # Progress Growth extra fields
    pg_before_col = cfg.get('pg_before_col', '')
    pg_after_col  = cfg.get('pg_after_col', '')
    pl_scale_raw = cfg.get('pl_scale', [])
    pl_scale = []
    if isinstance(pl_scale_raw, list):
        pl_scale = pl_scale_raw
    elif isinstance(pl_scale_raw, str) and pl_scale_raw.strip():
        val_str = pl_scale_raw.strip()
        if val_str.startswith('['):
            try:
                pl_scale = json.loads(val_str)
            except Exception:
                pl_scale = [v.strip() for v in val_str.split(',') if v.strip()]
        else:
            pl_scale = [v.strip() for v in val_str.split(',') if v.strip()]
    pg_display    = cfg.get('pg_display', 'stacked_bar') or 'stacked_bar'
    # Summary Table extra fields
    st_row_col    = cfg.get('st_row_col', '')
    st_col_col    = cfg.get('st_col_col', '')
    st_col_mode   = cfg.get('st_col_mode', 'values')  # 'values' or 'columns'
    st_show_row_totals = cfg.get('st_show_row_totals', True)
    st_show_col_totals = cfg.get('st_show_col_totals', True)
    # Dynamic Summary Table extra fields
    st_row_cols    = cfg.get('dyn_row_cols')
    if st_row_cols is None:
        st_row_cols = cfg.get('st_row_cols', [])
    st_col_cols    = cfg.get('dyn_col_cols')
    if st_col_cols is None:
        st_col_cols = cfg.get('st_col_cols', [])
    st_agg_func    = cfg.get('dyn_agg_func')
    if st_agg_func is None:
        st_agg_func = cfg.get('st_agg_func', 'count')
    st_value_col   = cfg.get('dyn_value_col')
    if st_value_col is None:
        st_value_col = cfg.get('st_value_col', '')
    _st_raw_decimals = cfg.get('dyn_agg_decimals')
    if _st_raw_decimals is None:
        _st_raw_decimals = cfg.get('st_agg_decimals')
    st_agg_decimals = int(_st_raw_decimals) if _st_raw_decimals is not None else None

    df = rows_to_df(rows)

    # Progress growth always uses the full query — drill and matrix need every row.
    _row_limit = cfg.get('limit')
    if _row_limit and _row_limit > 0 and chart_type != 'progress_growth':
        df = df.head(_row_limit)

    def _norm_col_name(c):
        return str(c).replace('-', '_').lower()

    col_map = {_norm_col_name(c): c for c in df.columns}

    def _resolve_cfg_col(col):
        if not isinstance(col, str):
            return col
        norm_col = _norm_col_name(col)
        if norm_col in col_map:
            return col_map[norm_col]
        resolved = resolve_relative_column_key(col)
        norm_resolved = _norm_col_name(resolved)
        if norm_resolved in col_map:
            return col_map[norm_resolved]
        return col

    # Resolve relative-year config keys against the loaded dataframe columns.
    x_col = _resolve_cfg_col(x_col)
    y_col = _resolve_cfg_col(y_col)
    stack_col = _resolve_cfg_col(stack_col)
    group_col = _resolve_cfg_col(group_col)
    size_col = _resolve_cfg_col(size_col)
    pg_before_col = _resolve_cfg_col(pg_before_col)
    pg_after_col = _resolve_cfg_col(pg_after_col)
    ds_date_col = _resolve_cfg_col(ds_date_col)
    _resolved_ds_lines = []
    for _ds_line in ds_lines:
        _l = dict(_ds_line) if isinstance(_ds_line, dict) else {}
        if _l.get('value_col'):
            _l['value_col'] = _resolve_cfg_col(_l['value_col'])
        if isinstance(_l.get('filters'), dict):
            _l['filters'] = {_resolve_cfg_col(k): v for k, v in _l['filters'].items()}
        _resolved_ds_lines.append(_l)
    ds_lines = _resolved_ds_lines
    st_row_col = _resolve_cfg_col(st_row_col)
    st_col_col = _resolve_cfg_col(st_col_col)
    st_row_cols = [_resolve_cfg_col(c) for c in st_row_cols] if isinstance(st_row_cols, list) else []
    st_col_cols = [_resolve_cfg_col(c) for c in st_col_cols] if isinstance(st_col_cols, list) else []
    st_value_col = _resolve_cfg_col(st_value_col) if st_value_col else ''
    x_cols_raw = [_resolve_cfg_col(c) for c in x_cols_raw] if isinstance(x_cols_raw, list) else x_cols_raw
    y_cols_raw = [_resolve_cfg_col(c) for c in y_cols_raw] if isinstance(y_cols_raw, list) else y_cols_raw
    compare_pl_cols = [_resolve_cfg_col(c) for c in compare_pl_cols] if isinstance(compare_pl_cols, list) else compare_pl_cols

    _dyn_calcs = cfg.get('dyn_calculations') or []
    if isinstance(_dyn_calcs, list):
        _resolved_dyn = []
        for _calc in _dyn_calcs:
            if not isinstance(_calc, dict):
                _resolved_dyn.append(_calc)
                continue
            _c = dict(_calc)
            if _c.get('value_col'):
                _c['value_col'] = _resolve_cfg_col(_c['value_col'])
            def _res_filt(_f):
                if not isinstance(_f, dict):
                    return _f
                _ff = dict(_f)
                if _ff.get('col'):
                    _ff['col'] = _resolve_cfg_col(_ff['col'])
                return _ff
            if isinstance(_c.get('filter'), dict):
                _c['filter'] = _res_filt(_c['filter'])
            if isinstance(_c.get('filters'), list):
                _c['filters'] = [_res_filt(_f) for _f in _c['filters']]
            _resolved_dyn.append(_c)
        cfg = dict(cfg)
        cfg['dyn_calculations'] = _resolved_dyn

    # Composite x-axis
    x_cols = [c for c in x_cols_raw if isinstance(c, str) and c in df.columns] if isinstance(x_cols_raw, list) else []
    if len(x_cols) > 1:
        df = df.with_columns(
            pl.concat_str(
                [pl.col(c).fill_null('(Blank)').cast(pl.Utf8) for c in x_cols],
                separator=' · ',
            ).alias('__x_comp__')
        )
        x_col = '__x_comp__'
    elif len(x_cols) == 1 and not x_col:
        x_col = x_cols[0]

    x_col       = x_col       if x_col       in df.columns else ''
    y_col       = y_col       if y_col       in df.columns else ''
    stack_col   = stack_col   if stack_col   in df.columns else ''
    group_col   = group_col   if group_col   in df.columns else ''
    size_col    = size_col    if size_col    in df.columns else ''
    y_cols    = [c for c in y_cols_raw if isinstance(c, str) and c in df.columns] if isinstance(y_cols_raw, list) else []

    # Meta melt
    trigger = _META_DIM_COLS | _META_SCORE_COLS
    if x_col in trigger or stack_col in trigger or y_col in trigger or any(c in trigger for c in y_cols):
        df, y_col, y_cols = _maybe_melt_for_meta(df, x_col, stack_col, y_col, y_cols, col_defs)
        x_col       = x_col       if x_col       in df.columns else ''
        y_col       = y_col       if y_col       in df.columns else ''
        stack_col   = stack_col   if stack_col   in df.columns else ''
        group_col   = group_col   if group_col   in df.columns else ''
        size_col    = size_col    if size_col    in df.columns else ''
        y_cols    = [c for c in y_cols if c in df.columns]

    # Progress growth classifies from PL columns — never drop rows for blank x/stack.
    if chart_type != 'progress_growth':
        _BLANK_VALUES = frozenset({'', '-', '\u2014', '\u2013', 'N/A', 'n/a', 'None', 'null', 'NULL', 'nan', 'NAN', 'NaN'})
        for _col in (stack_col, group_col, x_col):
            if _col and _col in df.columns:
                df = df.drop_nulls(subset=[_col])
                if not is_numeric_column(df, _col):
                    df = df.filter(
                        ~pl.col(_col).cast(pl.Utf8).str.strip_chars().is_in(list(_BLANK_VALUES))
                    )
    if df_is_empty(df):
        return _message_option('All rows filtered out (no valid data)', cfg)

    # Unique per-field x values for alias UI (grade, year, … — not "K · 2024").
    _alias_src_cols = x_cols if len(x_cols) > 1 else ([x_col] if x_col else [])
    x_alias_keys: list[str] = []
    _alias_seen: set[str] = set()
    for _ac in _alias_src_cols:
        if _ac not in df.columns:
            continue
        for _av in col_unique_str(df, _ac):
            if _av not in _alias_seen:
                _alias_seen.add(_av)
                x_alias_keys.append(_av)

    labels = {
        'x': (' · '.join([_label(c, col_defs) for c in x_cols]) if len(x_cols) > 1
              else (_META_LABEL_MAP.get(x_col) or _label(x_col, col_defs)) if x_col else ''),
        'y': (_META_LABEL_MAP.get(y_col) or _label(y_col, col_defs)) if y_col else ('Count' if agg == 'count' else ''),
        's': (_META_LABEL_MAP.get(stack_col) or _label(stack_col, col_defs)) if stack_col else '',
    }

    stacked = cfg.get('stacking_mode', 'normal') == 'percent' or chart_type in ('stacked_bar', 'stacked_area') or (chart_type == 'line' and cfg.get('line_layout') == 'stacked')
    smooth  = chart_type in ('spline', 'areaspline') or (chart_type == 'line' and bool(cfg.get('smooth', False)))

    builders = {
        'bar':          lambda: _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, False, col_defs, group_col),
        'column':       lambda: _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, False, col_defs, group_col),
        'grouped_bar':  lambda: _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, False, col_defs, group_col),
        'stacked_bar':  lambda: _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, True,  col_defs, group_col),
        'waterfall':    lambda: _waterfall_option(df, x_col, y_col, agg, cfg, labels),
        'line':         lambda: _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, bool(cfg.get('line_fill', False)), (cfg.get('line_layout') == 'stacked'), y_cols, col_defs, bool(cfg.get('smooth', False))),
        'date_series':  lambda: _date_series_option(df, ds_date_col, ds_lines, cfg, labels, col_defs),
        'spline':       lambda: _parliament_option(df, x_col, y_col, agg, cfg, col_defs),
        'parliament':   lambda: _parliament_option(df, x_col, y_col, agg, cfg, col_defs),
        'area':         lambda: _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, True,  False, y_cols, col_defs),
        'areaspline':   lambda: _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, True,  False, y_cols, col_defs, smooth=True),
        'stacked_area': lambda: _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, True,  True,  y_cols, col_defs),
        'pie':          lambda: _pie_option(df, x_col, y_col, agg, cfg, donut=False),
        'donut':        lambda: _pie_option(df, x_col, y_col, agg, cfg, donut=True),
        'scatter':      lambda: _scatter_option(df, x_col, y_col, stack_col, size_col, cfg, labels, bubble=False),
        'jitter':       lambda: _jitter_option(df, x_col, y_col, stack_col, size_col, cfg, labels),
        'bubble':       lambda: _scatter_option(df, x_col, y_col, stack_col, size_col, cfg, labels, bubble=True),
        'heatmap':      lambda: _heatmap_option(df, x_col, y_col, agg, cfg, labels),
        'histogram':    lambda: _histogram_option(df, x_col, stack_col, cfg, labels),
        'box':          lambda: _boxplot_option(df, x_col, y_col, cfg, labels),
        'funnel':       lambda: _funnel_option(df, x_col, y_col, agg, cfg),
        'treemap':      lambda: _treemap_option(df, x_col, y_col, agg, cfg),
        'radar':        lambda: _radar_option(df, x_col, y_col, agg, cfg),
        'gauge':        lambda: _gauge_option(df, y_col, agg, cfg, labels),
        'assessment_compare': lambda: _assessment_compare_option(df, x_col, compare_pl_cols, cfg, labels, col_defs),
        'progress_growth': lambda: _progress_growth_option(
            df, x_col, pg_before_col, pg_after_col, pl_scale, pg_display, cfg, labels,
            raw_rows=raw_rows,
        ),
        'summary_table':   lambda: _summary_table_option(df, st_row_col, st_col_col, cfg, col_defs),
        'dynamic_summary_table': lambda: _dynamic_summary_table_option(df, st_row_cols, st_col_cols, st_agg_func, st_value_col, st_agg_decimals, cfg, col_defs),
    }

    try:
        option = builders.get(chart_type, builders['bar'])()
    except Exception as exc:
        option = _message_option(f'Error rendering {chart_type}: {exc}', cfg, error=True)

    # Apply colour mapping (user overrides + semantic defaults) to all chart types
    _apply_color_map(option, cfg)

    if option.get('__type__') == 'table':
        if not option.get('__category_names__'):
            rows = option.get('rows') or []
            option['__category_names__'] = [str(r[0]) for r in rows if r]
        option['title_text'] = (cfg.get('chart_title') or cfg.get('title') or '').strip()
        option['title_show'] = _chart_title_shown(cfg)
        option['subtitle_text'] = (cfg.get('chart_subtitle') or '').strip()
        option['subtitle_show'] = _chart_subtitle_shown(cfg)
        try:
            option['title_font_size'] = int(cfg.get('title_font_size', 16) or 16)
        except (TypeError, ValueError):
            option['title_font_size'] = 16

    # Alias category-axis display labels (bar / category charts). Raw values stay
    # in __category_names__ for drill-through.
    _apply_x_value_map(option, cfg)
    option['__x_alias_keys__'] = x_alias_keys

    # Alias summary/dynamic-table column headers. Raw values stay in
    # col_header_raw / _drillMeta.colHeaders for drill-through.
    _apply_col_header_aliases(option, cfg)

    # Apply category-axis label overrides (rotation, font size, truncation)
    x_label_style = _x_axis_label_style(cfg)
    
    # Secondary category axis label style (outer group categories).
    # When true-centered via graphic bands, keep tick labels hidden.
    _center_groups = (
        cfg.get('center_group_labels', True) is not False
        and bool(option.get('__group_label_bands__'))
    )
    sec_label_style = {
        'show': (not _center_groups) and (cfg.get('show_categories', True) is not False),
        'fontSize': int(cfg.get('compare_cat_font_size', 12) or 12),
        'color': _theme(cfg)['text']
    }
    sec_rotation = int(cfg.get('compare_cat_rotation', 0) or 0)
    if sec_rotation:
        sec_label_style['rotate'] = sec_rotation
    else:
        sec_label_style['rotate'] = 0

    category_axis = (option.get('dv') or {}).get('category_axis', 'x')
    axis_targets = []
    if category_axis == 'y':
        axis_targets.append(option.get('yAxis'))
    elif category_axis == 'angle':
        axis_targets.append(option.get('angleAxis'))
    elif category_axis == 'radius':
        axis_targets.append(option.get('radiusAxis'))
    else:
        axis_targets.append(option.get('xAxis'))

    for axes in axis_targets:
        if axes is None:
            continue
        if isinstance(axes, list):
            for i, axis in enumerate(axes):
                if isinstance(axis, dict):
                    if i == 0:
                        axis.setdefault('axisLabel', {}).update(x_label_style)
                    else:
                        axis.setdefault('axisLabel', {}).update(sec_label_style)
        elif isinstance(axes, dict):
            axes.setdefault('axisLabel', {}).update(x_label_style)

    # Apply value-axis min/max overrides
    y_min = cfg.get('y_axis_min')
    y_max = cfg.get('y_axis_max')
    if y_min is not None or y_max is not None:
        value_axis = (option.get('radiusAxis') if category_axis == 'angle'
                      else option.get('angleAxis') if category_axis == 'radius'
                      else option.get('xAxis') if category_axis == 'y'
                      else option.get('yAxis'))
        targets = value_axis if isinstance(value_axis, list) else ([value_axis] if isinstance(value_axis, dict) else [])
        for ya in targets:
            if isinstance(ya, dict) and ya.get('type') == 'value':
                if y_min not in (None, ''):
                    try: ya['min'] = float(y_min)
                    except (TypeError, ValueError): pass
                if y_max not in (None, ''):
                    try: ya['max'] = float(y_max)
                    except (TypeError, ValueError): pass

    # Apply bar category gap (legacy config / interactive editor)
    bar_gap = cfg.get('bar_category_gap')
    if bar_gap not in (None, ''):
        for s in option.get('series', []):
            if isinstance(s, dict) and s.get('type') == 'bar':
                s['barCategoryGap'] = str(bar_gap) + '%'

    # Apply category gap (Cat. gap) — smooth % gap between categories on the
    # base category axis.  This is the ONLY smooth native gap control in
    # ECharts; between-group spacing is handled separately via spacer columns
    # (group_gap_size) so it never spaces the bars inside a group.
    _raw_gg = cfg.get('group_gap')
    if _raw_gg not in (None, ''):
        try:
            gg = float(_raw_gg)
        except (TypeError, ValueError):
            gg = None
        if gg is not None and gg > 0:
            for s in option.get('series', []):
                if isinstance(s, dict) and s.get('type') == 'bar':
                    s['barCategoryGap'] = f'{gg:g}%'

    # Apply label distance
    label_dist = cfg.get('label_distance')
    if label_dist not in (None, ''):
        try:
            ld = int(label_dist)
            for s in option.get('series', []):
                if isinstance(s, dict) and isinstance(s.get('label'), dict):
                    s['label']['distance'] = ld
        except (TypeError, ValueError):
            pass

    # Attach series names so the preview template can expose them
    series_names = [s.get('name', '') for s in option.get('series', []) if s.get('name')]
    option['__series_names__'] = series_names

    return option

# Expose private names to sibling ``import *`` (PEP 328).
__all__ = [n for n in list(globals()) if not n.startswith('__')]
