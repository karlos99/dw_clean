"""Standard chart option builders (bar, line, pie, scatter, etc.)."""
import json
import random
import re

import pandas as pd

try:
    from dataexploration.school_year_utils import resolve_relative_column_key
except Exception:  # pragma: no cover
    def resolve_relative_column_key(col_key: str) -> str:
        return col_key

from .constants import *  # noqa: F401,F403
from .common import *  # noqa: F401,F403

def _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, stacked, col_defs, group_col=''):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)
    option = _base_option(cfg)
    ax = _axis_style(cfg)
    x_lbl = _x_axis_label_style(cfg)
    opacity = float(cfg.get('opacity', 0.9) or 0.9)
    border_radius = int(cfg.get('bar_border_radius', 0) or 0)
    label_pos = cfg.get('label_position', 'top') or 'top'
    show_percent = bool(cfg.get('show_percent', False))
    percent_decimals = _percent_decimals(cfg)
    percent_suffix = _percent_suffix(cfg)
    bar_layout = (cfg.get('bar_layout') or ('stacked' if stacked else 'grouped')).lower()
    is_stacked = stacked or bar_layout == 'stacked'
    orientation = (cfg.get('bar_orientation') or 'vertical').lower()
    radial = bool(cfg.get('bar_radial', False))
    polar  = bool(cfg.get('bar_polar', False)) and not radial  # polar: radiusAxis=category, angleAxis=value
    valid_y = [c for c in (y_cols or []) if c in df.columns]
    if len(valid_y) == 1 and not y_col:
        y_col = valid_y[0]

    def make_series(name, data, stk=None):
        label_cfg = {
            'show': bool(cfg.get('data_labels', False)),
            'position': label_pos,
            'fontSize': int(cfg.get('label_font_size', 10) or 10),
        }
        if show_percent:
            label_cfg['__formatter_js__'] = (
                f"function(p){{var v=Number(p.value||0);return v.toFixed({percent_decimals})+'{percent_suffix}';}}"
            )
        s = {
            'name': name, 'type': 'bar',
            'stack': 'total' if stk else None,
            'label': label_cfg,
            'itemStyle': {'opacity': opacity},
            'data': data,
        }
        if radial or polar:
            s['coordinateSystem'] = 'polar'
            if label_pos == 'top':
                s['label']['position'] = 'outside'
        if border_radius:
            if radial or polar:
                s['itemStyle']['borderRadius'] = border_radius
            elif orientation == 'horizontal':
                s['itemStyle']['borderRadius'] = [0, border_radius, border_radius, 0]
            else:
                s['itemStyle']['borderRadius'] = [border_radius, border_radius, 0, 0]
        return s

    def update_axes(categories, series, y_axis_name):
        _update_chart_axes(option, categories, series, y_axis_name, labels, cfg, ax, x_lbl, orientation, radial, polar)

    if len(valid_y) > 1:
        categories = sorted(df[x_col].fillna('(Blank)').astype(str).unique().tolist())
        series_value_maps = {}
        series = []
        for mc in valid_y:
            col_agg = agg if agg in ('count_unique', 'count_occurrence') or (pd.api.types.is_numeric_dtype(df[mc]) and agg != 'count') else 'count'
            adf = _agg(df, x_col, mc, col_agg, cfg=cfg)
            vm = dict(zip(adf[x_col].astype(str), adf['_value']))
            series_value_maps[_label(mc, col_defs)] = vm
            series.append(make_series(_label(mc, col_defs), [float(vm.get(xv, 0) or 0) for xv in categories], is_stacked))
        if series_value_maps:
            total_value_map = {cat: sum(float(vm.get(cat, 0) or 0) for vm in series_value_maps.values()) for cat in categories}
            ordered_categories = _ordered_categories(categories, cfg, total_value_map)
            for s in series:
                vm = series_value_maps.get(s['name'], {})
                s['data'] = [float(vm.get(cat, 0) or 0) for cat in ordered_categories]
            categories = ordered_categories
        update_axes(categories, series, 'Value')
    elif group_col and group_col in df.columns and stack_col and stack_col in df.columns:
        # Grouped + stacked: x_col = inner category, group_col = outer group, stack_col = stack within group
        gc = [x_col, group_col, stack_col]
        df_gs = df.dropna(subset=[c for c in gc if c in df.columns])
        if agg in ('sum', 'avg', 'min', 'max', 'median') and y_col and y_col in df_gs.columns:
            df_gs[y_col] = pd.to_numeric(df_gs[y_col], errors='coerce')
            adf = df_gs.dropna(subset=[y_col]).groupby(gc, dropna=True)[y_col].agg(
                {'sum': 'sum', 'avg': 'mean', 'min': 'min', 'max': 'max', 'median': 'median'}[agg]
            ).reset_index().rename(columns={y_col: '_value'})
        elif agg == 'count_unique' and y_col and y_col in df_gs.columns:
            adf = df_gs.dropna(subset=[y_col]).groupby(gc, dropna=True)[y_col].nunique().reset_index().rename(columns={y_col: '_value'})
        elif agg == 'count_occurrence' and y_col and y_col in df_gs.columns:
            adf = df_gs.dropna(subset=[y_col]).groupby(gc, dropna=True)[y_col].count().reset_index().rename(columns={y_col: '_value'})
        else:
            adf = df_gs.groupby(gc, dropna=True).size().reset_index(name='_value')

        categories = sorted(adf[x_col].astype(str).unique().tolist())
        groups = sorted(adf[group_col].astype(str).unique().tolist())
        stacks = _ordered_stacks(adf[stack_col].astype(str).unique().tolist(), cfg)

        raw_vals = {}
        for _, row in adf.iterrows():
            raw_vals[(str(row[x_col]), str(row[group_col]), str(row[stack_col]))] = float(row['_value'] or 0)

        cat_totals_per_group = {
            (xv, grp): sum(raw_vals.get((xv, grp, sv), 0) for sv in stacks)
            for xv in categories for grp in groups
        }
        cat_totals = {xv: sum(raw_vals.get((xv, grp, sv), 0) for grp in groups for sv in stacks) for xv in categories}
        categories = _ordered_categories(categories, cfg, cat_totals)

        series = []
        for grp in groups:
            for sv in reversed(stacks):
                if show_percent:
                    data = [
                        round(raw_vals.get((xv, grp, sv), 0) / cat_totals_per_group.get((xv, grp), 1) * 100, 1)
                        if cat_totals_per_group.get((xv, grp), 0) > 0 else 0.0
                        for xv in categories
                    ]
                else:
                    data = [raw_vals.get((xv, grp, sv), 0) for xv in categories]
                s = make_series(f'{grp} — {sv}', data)
                s['stack'] = grp  # distinct per group → stacked within group, grouped across groups
                series.append(s)

        y_axis_name = '%' if show_percent else (labels['y'] or 'Count')
        y_axis = {'type': 'value', 'name': y_axis_name, **ax}
        if show_percent:
            y_axis['max'] = 100
        update_axes(categories, series, y_axis_name)
        if show_percent:
            if radial:
                option['radiusAxis']['max'] = 100
            elif polar:
                option['angleAxis']['max'] = 100
            elif orientation == 'horizontal':
                option['xAxis']['max'] = 100
            else:
                option['yAxis']['max'] = 100
        option['tooltip'] = {'show': cfg.get('show_tooltip', True) is not False,
                             'trigger': 'axis', 'axisPointer': {'type': 'shadow'}}

    elif stack_col and stack_col in df.columns:
        adf = _agg(df, x_col, y_col, agg, stack_col, cfg=cfg)
        categories = sorted(adf[x_col].astype(str).unique().tolist())
        stacks = _ordered_stacks(adf[stack_col].astype(str).unique().tolist(), cfg)

        # Build raw count/value lookup: {(cat, stack): value}
        raw_vals = {}
        for sv in stacks:
            sub = adf[adf[stack_col].astype(str) == sv]
            vm = dict(zip(sub[x_col].astype(str), sub['_value']))
            for xv in categories:
                raw_vals[(xv, sv)] = float(vm.get(xv, 0) or 0)

        # Compute totals per category — always, so tooltip shows count + % regardless of show_percent
        cat_totals = {xv: sum(raw_vals.get((xv, sv), 0) for sv in stacks) for xv in categories}
        categories = _ordered_categories(categories, cfg, cat_totals)

        series = []
        for sv in reversed(stacks):
            if show_percent:
                data = [round(raw_vals.get((xv, sv), 0) / cat_totals[xv] * 100, 1)
                        if cat_totals.get(xv, 0) > 0 else 0.0
                        for xv in categories]
            else:
                data = [raw_vals.get((xv, sv), 0) for xv in categories]
            series.append(make_series(sv, data, is_stacked))

        y_axis_name = '%' if show_percent else (labels['y'] or 'Count')
        y_axis = {'type': 'value', 'name': y_axis_name, **ax}
        if show_percent:
            y_axis['max'] = 100

        # Build tooltip — always shows raw count + % + (n=total), regardless of show_percent display mode
        raw_lookup = {sv: {xv: raw_vals.get((xv, sv), 0) for xv in categories} for sv in stacks}
        totals_lookup = {xv: cat_totals.get(xv, 0) for xv in categories}
        tooltip_js = _rich_tooltip_js(categories, raw_lookup, totals_lookup, percent_decimals, percent_suffix,
                                      show_pct=_tooltip_show_pct(cfg), show_count=_tooltip_show_count(cfg),
                                      value_pct_suffix='%' if _tooltip_value_pct(cfg) else '')
        update_axes(categories, series, y_axis_name)
        if show_percent:
            if radial:
                option['radiusAxis']['max'] = 100
            elif polar:
                option['angleAxis']['max'] = 100
            elif orientation == 'horizontal':
                option['xAxis']['max'] = 100
            else:
                option['yAxis']['max'] = 100
        option['tooltip'] = {'show': cfg.get('show_tooltip', True) is not False,
                             'trigger': 'axis', 'axisPointer': {'type': 'shadow'},
                             '__formatter_js__': tooltip_js}
    else:
        adf = _agg(df, x_col, y_col, agg, cfg=cfg).sort_values('_value', ascending=False)
        value_map = dict(zip(adf[x_col].astype(str), adf['_value']))
        ordered_categories = _ordered_categories(adf[x_col].astype(str).tolist(), cfg, value_map)
        ordered_values = [float(value_map.get(cat, 0) or 0) for cat in ordered_categories]
        series_name = labels['y'] or 'Value'
        update_axes(
            ordered_categories,
            [make_series(series_name, ordered_values)],
            _axis_name(labels['y'] or 'Count', cfg),
        )
        # Single series: tooltip shows value + % of grand total
        grand_total = sum(ordered_values)
        option['tooltip'] = {'show': cfg.get('show_tooltip', True) is not False,
                             'trigger': 'axis', 'axisPointer': {'type': 'shadow'},
                             '__formatter_js__': _inline_pct_tooltip_js(percent_decimals, percent_suffix, grand_total,
                                                                         show_pct=_tooltip_show_pct(cfg), show_count=_tooltip_show_count(cfg),
                                                                         value_pct_suffix='%' if _tooltip_value_pct(cfg) else '',
                                                                         categories=ordered_categories)}
    option['__category_names__'] = categories if 'categories' in locals() else ordered_categories
    if (radial or polar) and isinstance(option.get('tooltip'), dict):
        option['tooltip']['axisPointer'] = {'type': 'line'}
    if not radial and not polar:
        _add_zoom(option, cfg, axis='y' if orientation == 'horizontal' else 'x')
    return option


def _parliament_option(df, x_col, y_col, agg, cfg, col_defs=None):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)
    
    # 1. Locate the corresponding Scale Score (SS) column
    ss_col = None
    x_meta = _parse_col_meta(x_col, col_defs or {})
    if x_meta and x_meta.get('col_type') == 'test':
        for c in df.columns:
            c_meta = _parse_col_meta(c, col_defs or {})
            if c_meta and c_meta.get('col_type') == 'test':
                if (c_meta.get('assessment') == x_meta.get('assessment') and
                    c_meta.get('year') == x_meta.get('year') and
                    c_meta.get('window') == x_meta.get('window') and
                    (c.endswith('_SS') or 'ss' in c.lower() or 'scale score' in (col_defs or {}).get(c, '').lower())):
                    ss_col = c
                    break
    if not ss_col:
        # Fallback to standard conventions
        for candidate in [x_col.replace('_PL', '_SS'), x_col.replace('_pl', '_ss'), 'ss']:
            if candidate in df.columns:
                ss_col = candidate
                break

    # 2. Sort the student rows by SS (ascending, lowest to highest)
    df_sorted = df.copy()
    if ss_col:
        # Create a numeric column for sorting, coercing invalid values to NaN
        df_sorted['_ss_numeric'] = pd.to_numeric(df_sorted[ss_col], errors='coerce')
        # Sort ascending, placing NaNs first (so they are on the left-most / lowest side)
        df_sorted = df_sorted.sort_values(by='_ss_numeric', ascending=True, na_position='first')
    else:
        # Fallback: sort by Category or Student Name
        sort_by_cols = []
        if x_col in df_sorted.columns:
            sort_by_cols.append(x_col)
        if 'student_name' in df_sorted.columns:
            sort_by_cols.append('student_name')
        if sort_by_cols:
            df_sorted = df_sorted.sort_values(by=sort_by_cols, ascending=True)

    # 3. Get categories and value counts for sorting the categories list
    value_counts = df_sorted[x_col].dropna().astype(str).value_counts()
    value_map = value_counts.to_dict()
    categories = _ordered_categories(list(value_map.keys()), cfg, value_map)

    # 4. Total dots N = number of students
    N = len(df_sorted)
    if N <= 0:
        return _message_option('No data (total dots is 0)', cfg)

    import math
    # Calculate rows (R) and concentric semicircular radii
    custom_rows = cfg.get('parliament_rows')
    if custom_rows is not None:
        try:
            R = max(1, min(20, int(custom_rows)))
        except (ValueError, TypeError):
            R = max(3, min(15, int(round(math.sqrt(N) / 2.0))))
    else:
        R = max(3, min(15, int(round(math.sqrt(N) / 2.0))))

    R_min = 100.0
    R_max = 220.0
    
    if R > 1:
        radii = [R_min + i * (R_max - R_min) / (R - 1) for i in range(R)]
    else:
        radii = [R_min]
        
    # Distribute dots proportional to row radii
    S = sum(radii)
    assigned = [int(round(N * r / S)) for r in radii]
    diff = N - sum(assigned)
    while diff != 0:
        step = 1 if diff > 0 else -1
        # Adjust starting from outer rows (largest radii)
        for idx in sorted(range(len(radii)), key=lambda k: radii[k], reverse=True):
            if diff == 0:
                break
            assigned[idx] += step
            diff -= step

    # Get shape configuration
    shape = cfg.get('parliament_shape', 'semi-circle')
    if shape not in ('semi-circle', 'horseshoe', 'circle'):
        shape = 'semi-circle'

    # Generate seats coordinates
    coords = []
    phi = 0.05 * math.pi
    for i, r in enumerate(radii):
        num_seats = assigned[i]
        if num_seats <= 0:
            continue
        if num_seats == 1:
            angles = [math.pi / 2.0]
        else:
            if shape == 'circle':
                # Full circle layout starting at pi and wrapping around counter-clockwise
                angles = [math.pi - j * 2.0 * math.pi / num_seats for j in range(num_seats)]
            elif shape == 'horseshoe':
                # Horseshoe spans from 1.25*pi down to -0.25*pi (270 degrees)
                start_angle = 1.25 * math.pi - phi
                end_angle = -0.25 * math.pi + phi
                span = start_angle - end_angle
                angles = [start_angle - j * span / (num_seats - 1) for j in range(num_seats)]
            else: # semi-circle
                start_angle = math.pi - phi
                end_angle = phi
                span = start_angle - end_angle
                angles = [start_angle - j * span / (num_seats - 1) for j in range(num_seats)]
                
        for theta in angles:
            x = round(r * math.cos(theta), 2)
            y = round(r * math.sin(theta), 2)
            coords.append((theta, x, y))

    # Sort coordinates by angle theta from left to right (decreasing theta) to group categories side-by-side
    coords.sort(key=lambda t: -t[0])
    sorted_coords = [(cx, cy) for _, cx, cy in coords]

    # Map each student row to one of the generated coordinates
    # Adjust N in case generated coordinates differ slightly from N due to rounding edge cases
    N = min(len(df_sorted), len(sorted_coords))
    
    student_data_by_cat = {cat: [] for cat in categories}
    
    def _clean_val(val):
        if pd.isna(val):
            return None
        if isinstance(val, (int, float)):
            if float(val).is_integer():
                return int(val)
            return float(val)
        val_str = str(val).strip()
        try:
            if '.' in val_str:
                return float(val_str)
            else:
                return int(val_str)
        except ValueError:
            return val_str

    for i in range(N):
        row = df_sorted.iloc[i]
        cat = str(row[x_col])
        if cat not in student_data_by_cat:
            student_data_by_cat[cat] = []
            
        cx, cy = sorted_coords[i]
        
        student_name = row.get('student_name', '')
        if pd.isna(student_name):
            student_name = 'Student'
            
        pl_val = row.get(x_col, '')
        if pd.isna(pl_val):
            pl_val = ''
            
        ss_val = _clean_val(row.get(ss_col, '')) if ss_col else None
            
        student_data_by_cat[cat].append({
            'value': [cx, cy],
            'student_name': str(student_name),
            'pl': str(pl_val),
            'ss': ss_val
        })

    # Determine point size dynamically based on N so it fits beautifully
    if N < 50:
        pt_size = 14
    elif N < 150:
        pt_size = 10
    elif N < 400:
        pt_size = 8
    elif N < 800:
        pt_size = 6
    else:
        pt_size = 4

    # Apply custom dot size scaling multiplier if provided
    dot_scale = cfg.get('parliament_dot_scale')
    if dot_scale is not None:
        try:
            scale_val = max(0.2, min(5.0, float(dot_scale)))
            pt_size = int(round(pt_size * scale_val))
        except (ValueError, TypeError):
            pass

    # Assign seats to categories sequentially
    series = []
    for cat in categories:
        cat_data = student_data_by_cat.get(cat, [])
        if not cat_data:
            continue
        
        series.append({
            'name': cat,
            'type': 'scatter',
            'symbolSize': pt_size,
            'data': cat_data,
            'clip': False,
            'itemStyle': {
                'opacity': 0.85,
                'shadowBlur': 2,
                'shadowColor': 'rgba(0,0,0,0.15)'
            }
        })

    # Set coordinate boundaries based on layout shape (expanded margins to prevent clipping)
    if shape == 'circle':
        x_min, x_max = -260, 260
        y_min, y_max = -260, 260
    elif shape == 'horseshoe':
        x_min, x_max = -260, 260
        y_min, y_max = -190, 260
    else: # semi-circle
        x_min, x_max = -260, 260
        y_min, y_max = -20, 260

    show_legend = cfg.get('legend_pos', 'bottom') != 'hidden'
    title_text = cfg.get('chart_title', '') or cfg.get('title', '')

    option = _base_option(cfg, axis_trigger=False)
    option['__is_parliament__'] = True
    option['__parliament_shape__'] = shape
    option['grid'] = {
        'left': 40,
        'right': 40,
        'top': 60 if title_text else 20,
        'bottom': 60 if show_legend else 20,
        'containLabel': False
    }
    option['xAxis'] = {'show': False, 'type': 'value', 'min': x_min, 'max': x_max}
    option['yAxis'] = {'show': False, 'type': 'value', 'min': y_min, 'max': y_max}
    option['series'] = series
    
    # Custom tooltip
    show_ss = cfg.get('parliament_show_ss', False) is True
    tooltip_js = (
        "function(params) {"
        "  var data = params.data;"
        "  var marker = params.marker || '';"
        "  var studentName = data.student_name || 'Student';"
        "  var pl = data.pl || params.seriesName || '';"
        f"  var showSs = {'true' if show_ss else 'false'};"
        "  var ss = data.ss;"
        "  var tooltip = '<b>' + studentName + '</b><br/>' + marker + ' ' + pl;"
        "  if (showSs && ss !== undefined && ss !== null && ss !== '') {"
        "    tooltip += ' (SS: ' + ss + ')';"
        "  }"
        "  return tooltip;"
        "}"
    )
    option['tooltip'] = {'show': True, 'trigger': 'item', '__formatter_js__': tooltip_js}
    option['__category_names__'] = categories
    return option


def _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, fill, stacked, y_cols=None, col_defs=None, smooth=False):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)
    date_bucket = (cfg.get('x_date_bucket') or '').strip().lower()
    if date_bucket and x_col in df.columns:
        df = df.assign(**{x_col: _bucket_date_column(df[x_col], date_bucket)})
    option = _base_option(cfg)
    ax = _axis_style(cfg)
    x_lbl = _x_axis_label_style(cfg)
    opacity = float(cfg.get('opacity', 0.9) or 0.9)
    smooth = smooth or bool(cfg.get('smooth', False))
    show_pts = bool(cfg.get('show_points', False))
    valid_y = [c for c in (y_cols or []) if c in df.columns]
    if len(valid_y) == 1 and not y_col:
        y_col = valid_y[0]

    def make_series(name, data, stk=None):
        label_cfg = {
            'show': bool(cfg.get('data_labels', False)),
            'position': cfg.get('label_position', 'top') or 'top',
            'fontSize': int(cfg.get('label_font_size', 10) or 10),
        }
        return {
            'name': name, 'type': 'line', 'smooth': smooth,
            'stack': 'total' if stk else None,
            'areaStyle': {} if fill else None,
            'showSymbol': show_pts, 'symbolSize': 6 if show_pts else 0,
            'lineStyle': {'opacity': opacity}, 'itemStyle': {'opacity': opacity},
            'label': label_cfg,
            'data': data,
        }

    percent_decimals = _percent_decimals(cfg)
    percent_suffix = _percent_suffix(cfg)

    if len(valid_y) > 1:
        categories = sorted(df[x_col].fillna('(Blank)').astype(str).unique().tolist())
        series_value_maps = {}
        series = []
        for mc in valid_y:
            col_agg = agg if pd.api.types.is_numeric_dtype(df[mc]) and agg != 'count' else 'count'
            adf = _agg(df, x_col, mc, col_agg, cfg=cfg)
            vm = dict(zip(adf[x_col].astype(str), adf['_value']))
            series_name = _label(mc, col_defs or {})
            series_value_maps[series_name] = vm
            series.append(make_series(series_name, [float(vm.get(xv, 0) or 0) for xv in categories], stacked))
        total_value_map = {cat: sum(float(vm.get(cat, 0) or 0) for vm in series_value_maps.values()) for cat in categories}
        categories = _ordered_categories(categories, cfg, total_value_map)
        for s in series:
            vm = series_value_maps.get(s['name'], {})
            s['data'] = [float(vm.get(cat, 0) or 0) for cat in categories]
        totals_lookup = {cat: total_value_map.get(cat, 0) for cat in categories}
        raw_lookup = {sn: {cat: float(vm.get(cat, 0) or 0) for cat in categories} for sn, vm in series_value_maps.items()}
        _update_chart_axes(option, categories, series, 'Value', labels, cfg, ax, x_lbl)
        option.update({
                       'tooltip': {'show': cfg.get('show_tooltip', True) is not False, 'trigger': 'axis', 'axisPointer': {'type': 'shadow'},
                                   '__formatter_js__': _rich_tooltip_js(categories, raw_lookup, totals_lookup, percent_decimals, percent_suffix,
                                                                        show_pct=_tooltip_show_pct(cfg), show_count=_tooltip_show_count(cfg),
                                                                        value_pct_suffix='%' if _tooltip_value_pct(cfg) else '')}})
    elif stack_col and stack_col in df.columns:
        adf = _agg(df, x_col, y_col, agg, stack_col, cfg=cfg)
        categories = sorted(adf[x_col].astype(str).unique().tolist())
        stacks_ordered = _ordered_stacks(adf[stack_col].astype(str).unique().tolist(), cfg)
        total_value_map = {cat: float(adf[adf[x_col].astype(str) == cat]['_value'].sum() or 0) for cat in categories}
        categories = _ordered_categories(categories, cfg, total_value_map)
        raw_lookup = {sv: {xv: float(dict(zip(adf[adf[stack_col].astype(str) == sv][x_col].astype(str),
                                              adf[adf[stack_col].astype(str) == sv]['_value'])).get(xv, 0) or 0)
                           for xv in categories}
                      for sv in stacks_ordered}
        totals_lookup = {cat: total_value_map.get(cat, 0) for cat in categories}
        series = [make_series(sv, [raw_lookup[sv][xv] for xv in categories], stacked)
                  for sv in reversed(stacks_ordered)]
        _update_chart_axes(option, categories, series, labels['y'] or 'Count', labels, cfg, ax, x_lbl)
        option.update({
                       'tooltip': {'show': cfg.get('show_tooltip', True) is not False, 'trigger': 'axis', 'axisPointer': {'type': 'shadow'},
                                   '__formatter_js__': _rich_tooltip_js(categories, raw_lookup, totals_lookup, percent_decimals, percent_suffix,
                                                                        show_pct=_tooltip_show_pct(cfg), show_count=_tooltip_show_count(cfg),
                                                                        value_pct_suffix='%' if _tooltip_value_pct(cfg) else '')}})
    else:
        adf = _agg(df, x_col, y_col, agg, cfg=cfg)
        value_map = dict(zip(adf[x_col].astype(str), adf['_value']))
        categories = _ordered_categories(adf[x_col].astype(str).tolist(), cfg, value_map)
        ordered_vals = [float(value_map.get(cat, 0) or 0) for cat in categories]
        grand_total = sum(ordered_vals)
        _ltjs = _inline_pct_tooltip_js(percent_decimals, percent_suffix, grand_total,
                                           show_pct=_tooltip_show_pct(cfg), show_count=_tooltip_show_count(cfg),
                                           value_pct_suffix='%' if _tooltip_value_pct(cfg) else '',
                                           categories=categories)
        _update_chart_axes(option, categories, [make_series(labels['y'] or 'Value', ordered_vals)], labels['y'] or 'Count', labels, cfg, ax, x_lbl)
        option.update({
                       'tooltip': {'show': cfg.get('show_tooltip', True) is not False, 'trigger': 'axis', 'axisPointer': {'type': 'shadow'},
                                   '__formatter_js__': _ltjs}})
    option['__category_names__'] = categories
    _add_zoom(option, cfg)
    return option


_DS_AGG_FUNCS = frozenset({'count', 'sum', 'avg', 'min', 'max', 'median'})


def _ds_bucket_label_range(label, bucket):
    """Inverse of _bucket_date_column: the calendar [start, end] Timestamps
    a bucket label covers (e.g. '2025-09' -> (Sep 1, Sep 30))."""
    try:
        if bucket == 'week':
            year_s, week_s = str(label).split('-W')
            import datetime as _dt
            start = pd.Timestamp(_dt.date.fromisocalendar(int(year_s), int(week_s), 1))
            end = pd.Timestamp(_dt.date.fromisocalendar(int(year_s), int(week_s), 7))
            return start, end
        if bucket == 'month':
            start = pd.Timestamp(str(label) + '-01')
            return start, start + pd.offsets.MonthEnd(0)
        if bucket == 'quarter':
            year_s, q_s = str(label).split('-Q')
            start_month = (int(q_s) - 1) * 3 + 1
            start = pd.Timestamp(year=int(year_s), month=start_month, day=1)
            end = pd.Timestamp(year=int(year_s), month=start_month + 2, day=1) + pd.offsets.MonthEnd(0)
            return start, end
        if bucket == 'year':
            y = int(label)
            return pd.Timestamp(year=y, month=1, day=1), pd.Timestamp(year=y, month=12, day=31)
        # 'day' (and fallback)
        d = pd.Timestamp(label)
        return d, d
    except (ValueError, TypeError):
        return None, None


def _date_series_option(df, ds_date_col, ds_lines, cfg, labels, col_defs):
    """Dates chart: one or more lines plotted against a continuous, bucketed
    date axis (every day/week/month/... between the earliest and latest
    relevant date — gaps are zero-filled, not skipped).

    Each entry in ``ds_lines`` is one of:
      base line — {id, label, line_type:'base', agg_func, value_col, filters}
        ``agg_func`` aggregates ``value_col`` per date bucket (count/sum/avg/min/max/median).
      active_range line — {id, label, line_type:'active_range', enter_col, leave_col}
        Counts distinct students whose [enter_col, leave_col] interval
        overlaps the bucket at all (leave_col blank/missing = still active).
        Point-in-time census columns like enrollment dates naturally have no
        row for days in between enter and leave — this line type is what
        makes a continuous "how many were active on this date" line possible.
      calc line — {id, label, line_type:'calc', op:'add'|'subtract', line_a, line_b}
        Combines two other lines (by id) point-by-point.

    If a line's source column(s) aren't in the loaded data (e.g. a table
    that doesn't exist yet), the line still renders — as a flat zero —
    so wiring up the calculation now doesn't break the chart, and it starts
    showing real numbers automatically once the column/table is added.
    """
    if not ds_date_col:
        return _message_option('Select a Date column', cfg, error=True)
    if not ds_lines:
        return _message_option('Add at least one line', cfg, error=True)
    if ds_date_col not in df.columns:
        return _message_option(f'Date column not found in data: {ds_date_col}', cfg, error=True)

    date_bucket = (cfg.get('ds_date_bucket') or 'day').strip().lower()

    # Union every date column referenced by any line (the primary date
    # column plus any Active-Range enter/leave columns) to find the overall
    # span, then generate a continuous calendar axis over it.
    date_parts = [pd.to_datetime(df[ds_date_col], errors='coerce')]
    for line in ds_lines:
        if line.get('line_type') == 'active_range':
            for col_key in ('enter_col', 'leave_col'):
                col = line.get(col_key, '')
                if col and col in df.columns:
                    date_parts.append(pd.to_datetime(df[col], errors='coerce'))
    all_valid_dates = pd.concat(date_parts, ignore_index=True).dropna()
    if all_valid_dates.empty:
        return _message_option('No valid dates in data', cfg, error=True)

    overall_min = all_valid_dates.min().normalize()
    overall_max = all_valid_dates.max().normalize()
    full_days = pd.Series(pd.date_range(overall_min, overall_max, freq='D'))
    all_dates = sorted(set(_bucket_date_column(full_days, date_bucket).tolist()))
    bucket_ranges = {lbl: _ds_bucket_label_range(lbl, date_bucket) for lbl in all_dates}

    base = df.assign(**{ds_date_col: _bucket_date_column(df[ds_date_col], date_bucket)})
    _dup_key = next((c for c in ('ssid', 'student_id', 'stu_annual_key') if c in df.columns), None)

    line_series: dict[str, dict[str, float]] = {}
    line_meta: dict[str, dict] = {}

    for line in ds_lines:
        lid = line.get('id') or line.get('label')
        if not lid:
            continue
        ltype = line.get('line_type', 'base')
        label = (line.get('label') or '').strip() or lid
        line_meta[lid] = {'label': label, 'line_type': ltype}
        if ltype == 'calc':
            continue  # resolved in a second pass, after all other lines are ready

        if ltype == 'active_range':
            enter_col = line.get('enter_col', '')
            leave_col = line.get('leave_col', '')
            if not enter_col or enter_col not in df.columns:
                # Enrollment table not loaded yet — plot as 0, not an error.
                line_series[lid] = {d: 0.0 for d in all_dates}
                continue
            enter_dt = pd.to_datetime(df[enter_col], errors='coerce')
            leave_dt = (pd.to_datetime(df[leave_col], errors='coerce')
                        if leave_col and leave_col in df.columns
                        else pd.Series(pd.NaT, index=df.index))
            valid = enter_dt.notna()
            counts = {}
            for lbl, (start, end) in bucket_ranges.items():
                if start is None:
                    counts[lbl] = 0.0
                    continue
                mask = valid & (enter_dt <= end) & (leave_dt.isna() | (leave_dt >= start))
                counts[lbl] = float(df.loc[mask, _dup_key].nunique()) if _dup_key else float(mask.sum())
            line_series[lid] = counts
            continue

        value_col = line.get('value_col', '')
        agg_func = line.get('agg_func', 'count')
        if agg_func not in _DS_AGG_FUNCS:
            agg_func = 'count'

        if not value_col or value_col not in base.columns:
            # Column not loaded yet (e.g. future absence table) — plot as 0
            # rather than erroring, so the chart is ready before the data is.
            line_series[lid] = {d: 0.0 for d in all_dates}
            continue

        sub = base
        line_filters = line.get('filters') or {}
        if isinstance(line_filters, dict):
            for fcol, fvals in line_filters.items():
                if fcol not in sub.columns or not fvals:
                    continue
                allowed = {str(v) for v in fvals} if isinstance(fvals, list) else {str(fvals)}
                sub = sub[sub[fcol].astype(str).isin(allowed)]

        adf = _agg(sub, ds_date_col, value_col, agg_func, cfg=cfg)
        vm = dict(zip(adf[ds_date_col].astype(str), adf['_value'])) if not adf.empty else {}
        line_series[lid] = {d: float(vm.get(d, 0) or 0) for d in all_dates}

    # Calc lines can reference other calc lines (e.g. an attendance-rate line
    # dividing a "Present" calc line by a "Total" line), so resolution must
    # not assume ds_lines is already in dependency order — resolve whatever
    # has its inputs ready, repeat until nothing more can progress. Anything
    # left over (dangling reference or a cycle) renders as a flat zero,
    # consistent with every other "reference doesn't resolve" case here.
    _DS_CALC_OPS = {
        'add':      lambda a, b: a + b,
        'subtract': lambda a, b: a - b,
        'multiply': lambda a, b: a * b,
        'divide':   lambda a, b: (a / b) if b else 0.0,
    }
    pending_calcs = {
        (line.get('id') or line.get('label')): line
        for line in ds_lines
        if line.get('line_type') == 'calc' and (line.get('id') or line.get('label'))
    }
    for _ in range(len(pending_calcs) + 1):
        if not pending_calcs:
            break
        progressed = False
        for lid, line in list(pending_calcs.items()):
            a_id = line.get('line_a', '')
            b_id = line.get('line_b', '')
            if a_id in line_series and b_id in line_series:
                fn = _DS_CALC_OPS.get(line.get('op', 'subtract'), _DS_CALC_OPS['subtract'])
                a_vals, b_vals = line_series[a_id], line_series[b_id]
                result = {d: fn(a_vals.get(d, 0.0), b_vals.get(d, 0.0)) for d in all_dates}
                if line.get('as_percent'):
                    result = {d: v * 100 for d, v in result.items()}
                line_series[lid] = result
                del pending_calcs[lid]
                progressed = True
        if not progressed:
            break
    for lid in pending_calcs:
        line_series[lid] = {d: 0.0 for d in all_dates}

    option = _base_option(cfg)
    ax = _axis_style(cfg)
    x_lbl = _x_axis_label_style(cfg)
    smooth = bool(cfg.get('smooth', False))
    show_pts = bool(cfg.get('show_points', False))
    opacity = float(cfg.get('opacity', 0.9) or 0.9)
    show_labels = bool(cfg.get('data_labels', False))

    series = []
    line_labels_ordered = []
    percent_names = []
    for line in ds_lines:
        lid = line.get('id') or line.get('label')
        if lid not in line_series:
            continue
        meta = line_meta[lid]
        data = [line_series[lid].get(d, 0.0) for d in all_dates]
        line_labels_ordered.append(meta['label'])
        is_pct = bool(line.get('as_percent'))
        if is_pct:
            percent_names.append(meta['label'])
        label_cfg = {'show': show_labels, 'position': cfg.get('label_position', 'top') or 'top',
                     'fontSize': int(cfg.get('label_font_size', 10) or 10)}
        if is_pct:
            label_cfg['__formatter_js__'] = "function(p){var v=Number(p.value||0);return v.toFixed(1)+'%';}"
        series.append({
            'name': meta['label'],
            'type': 'line',
            'smooth': smooth,
            'showSymbol': show_pts, 'symbolSize': 6 if show_pts else 0,
            'lineStyle': {'opacity': opacity}, 'itemStyle': {'opacity': opacity},
            'label': label_cfg,
            'data': data,
        })

    if not series:
        return _message_option('No lines configured', cfg, error=True)

    _update_chart_axes(option, all_dates, series, labels.get('y', '') or 'Count', labels, cfg, ax, x_lbl)
    tooltip_js = (
        "function(params) {"
        "  if (!Array.isArray(params)) params = [params];"
        f"  var pctNames = {json.dumps(percent_names)};"
        "  var html = '<b>' + ((params[0] && params[0].axisValueLabel) || (params[0] && params[0].axisValue) || '') + '</b>';"
        "  for (var i = 0; i < params.length; i++) {"
        "    var p = params[i];"
        "    var v = Number(p.value);"
        "    var isPct = pctNames.indexOf(p.seriesName) !== -1;"
        "    var vDisp = isPct ? v.toFixed(1) + '%' : (Number.isInteger(v) ? v : v.toFixed(1));"
        "    html += '<br/>' + p.marker + ' ' + p.seriesName + ': <b>' + vDisp + '</b>';"
        "  }"
        "  return html;"
        "}"
    )
    option['tooltip'] = {'show': cfg.get('show_tooltip', True) is not False,
                         'trigger': 'axis', 'axisPointer': {'type': 'line'},
                         '__formatter_js__': tooltip_js}
    option['__category_names__'] = all_dates
    option['_drillMeta'] = {
        'dsDateCol': ds_date_col,
        'dsDateBucket': date_bucket,
        'dsLines': {m['label']: {'id': lid, 'lineType': m['line_type']} for lid, m in line_meta.items()},
    }
    _add_zoom(option, cfg)
    return option


def _pie_option(df, x_col, y_col, agg, cfg, donut=False):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)
    adf = _agg(df, x_col, y_col, agg, cfg=cfg).sort_values('_value', ascending=False).head(30)
    value_map = dict(zip(adf[x_col].astype(str), adf['_value']))
    categories = _ordered_categories(adf[x_col].astype(str).tolist(), cfg, value_map)
    option = _base_option(cfg, axis_trigger=False)
    grand_total = sum(float(v or 0) for v in value_map.values())
    percent_decimals = _percent_decimals(cfg)
    percent_suffix = _percent_suffix(cfg)
    pie_tjs = (
        "function(params) {"
        f"  var grandTotal = {round(grand_total, 6)};"
        "  var v = Number(params.value) || 0;"
        f"  var pct = grandTotal > 0 ? (v / grandTotal * 100).toFixed({percent_decimals}) : '0';"
        "  return '<b>' + params.name + '</b><br/>' + params.marker + ' ' + params.seriesName"
        f"       + ': <b>' + v + '</b> (' + pct + '{percent_suffix})';"
        "}"
    )
    option['series'] = [{
        'name': 'Distribution', 'type': 'pie',
        'radius': ['45%', '70%'] if donut else '70%',
        'data': [{'name': cat, 'value': float(value_map.get(cat, 0) or 0)} for cat in categories],
        'label': {'show': bool(cfg.get('data_labels', False)), 'fontSize': int(cfg.get('label_font_size', 10) or 10)},
    }]
    option['tooltip'] = {'show': cfg.get('show_tooltip', True) is not False, 'trigger': 'item',
                         '__formatter_js__': pie_tjs}
    option['__category_names__'] = categories
    return option


def _scatter_option(df, x_col, y_col, stack_col, size_col, cfg, labels, bubble=False):
    if not x_col or not y_col:
        return _message_option('X and Y are required for scatter', cfg, error=True)
    option = _base_option(cfg)
    ax = _axis_style(cfg)
    dfc = df.copy()
    dfc[x_col] = pd.to_numeric(dfc[x_col], errors='coerce')
    dfc[y_col] = pd.to_numeric(dfc[y_col], errors='coerce')
    if size_col and size_col in dfc.columns:
        dfc[size_col] = pd.to_numeric(dfc[size_col], errors='coerce')
    dfc = dfc.dropna(subset=[x_col, y_col])
    pt = int(cfg.get('point_size', 6) or 6)

    def _pt_data(sub):
        return [[float(x), float(y)] for x, y in zip(sub[x_col], sub[y_col])]

    if stack_col and stack_col in dfc.columns:
        series = [{'name': g, 'type': 'scatter', 'symbolSize': pt,
                   'data': _pt_data(dfc[dfc[stack_col].astype(str) == g])}
                  for g in _ordered_stacks(dfc[stack_col].astype(str).unique().tolist(), cfg)]
    else:
        series = [{'name': labels['y'] or 'Values', 'type': 'scatter', 'symbolSize': pt, 'data': _pt_data(dfc)}]

    option.update({'xAxis': {'type': 'value', 'name': labels['x'], **ax},
                   'yAxis': {'type': 'value', 'name': labels['y'], **ax}, 'series': series})
    _add_zoom(option, cfg)
    return option


def _jitter_option(df, x_col, y_col, stack_col, size_col, cfg, labels):
    """Jitter chart: scatter with random horizontal jitter to spread points
    within each category band.  Good for showing individual data-point
    distributions without overplotting.

    Supports two modes via ``jitter_mode`` config key:
      - ``'raw'`` (default) — show every individual point with jitter
      - ``'aggregate'`` — group by X and aggregate Y (mean/median/count etc.)

    Supports overlay via ``jitter_overlay`` config key:
      - ``'none'`` (default) — no overlay
      - ``'mean'`` — diamond marker at the mean of each category
      - ``'median'`` — line marker at the median of each category
      - ``'box'`` — box-like visual showing Q1-median-Q3 per category
      - ``'mean+box'`` — both mean marker and box overlay

    All x-axis values are treated as strings so numeric-looking categories
    (e.g. grade \"01\", \"02\") sort and display correctly.
    """
    if not x_col or not y_col:
        return _message_option('X (category) and Y (numeric) are required for Jitter', cfg, error=True)
    option = _base_option(cfg)
    ax = _axis_style(cfg)
    x_lbl = _x_axis_label_style(cfg)
    dfc = df.copy()
    # Force x_col to string so numeric codes like "01" stay as categories
    dfc[x_col] = dfc[x_col].astype(str)
    dfc[y_col] = pd.to_numeric(dfc[y_col], errors='coerce')
    dfc = dfc.dropna(subset=[y_col])
    if dfc.empty:
        return _message_option('No numeric data for jitter', cfg)
    pt = int(cfg.get('point_size', 6) or 6)
    mode = cfg.get('jitter_mode', 'raw')
    overlay = cfg.get('jitter_overlay', 'none')
    categories = _ordered_categories(sorted(dfc[x_col].unique().tolist()), cfg)
    cat_index = {v: i for i, v in enumerate(categories)}

    # ── Build formatter as a JS string so it survives JSON serialization ──
    cat_map_js = '{' + ', '.join(f'{json.dumps(str(i))}: {json.dumps(c)}' for i, c in enumerate(categories)) + '}'
    fmt_body = f'var m={cat_map_js};return m[Math.round(value)]||"";'
    x_axis_label = {
        'show': True,
        '__formatter_js__': fmt_body,
        'interval': 1,
        'rotate': 30 if len(categories) > 8 else 0,
    }
    x_axis_label.update({k: v for k, v in x_lbl.items() if k != 'formatter'})

    # ── Aggregate mode: grouped bars/strip with summary stats ────────────
    if mode == 'aggregate':
        agg_func = cfg.get('agg', 'avg')
        adf = _agg(dfc, x_col, y_col, agg_func, stack_col=stack_col, cfg=cfg)
        x_vals = [c for c in categories if c in adf[x_col].values]
        if stack_col and stack_col in adf.columns:
            stacks = _ordered_stacks(adf[stack_col].astype(str).unique().tolist(), cfg)
            series = []
            for g in stacks:
                sub = adf[adf[stack_col].astype(str) == g]
                data = [float(sub[sub[x_col] == c]['_value'].iloc[0]) if c in sub[x_col].values else None
                        for c in x_vals]
                series.append({'name': str(g), 'type': 'bar', 'data': data,
                               'itemStyle': {'borderRadius': [4, 4, 0, 0]}})
        else:
            data = [float(adf[adf[x_col] == c]['_value'].iloc[0]) if c in adf[x_col].values else None
                    for c in x_vals]
            series = [{'name': labels.get('y', y_col), 'type': 'bar', 'data': data,
                       'itemStyle': {'borderRadius': [4, 4, 0, 0]}}]
        option.update({
            'xAxis': {**ax, 'type': 'category', 'data': x_vals, 'axisLabel': x_lbl},
            'yAxis': {**ax, 'type': 'value', 'name': labels.get('y', y_col)},
            'series': series,
        })
        return option

    # ── Raw mode: individual points with jitter ──────────────────────────
    jitter_width = float(cfg.get('jitter_width', 0.3) or 0.3)
    rng = random.Random(42)

    series = []
    groups = [None]
    if stack_col and stack_col in dfc.columns:
        groups = _ordered_stacks(dfc[stack_col].astype(str).unique().tolist(), cfg)
    for g in groups:
        sub = dfc if g is None else dfc[dfc[stack_col].astype(str) == g]
        jittered = []
        for _, row in sub.iterrows():
            cat = str(row[x_col])
            if cat not in cat_index:
                continue
            idx = cat_index[cat]
            val = float(row[y_col])
            offset = rng.uniform(-jitter_width, jitter_width)
            jittered.append([idx + offset, val])
        name = (labels.get('y') or 'Values') if g is None else str(g)
        series.append({'name': name, 'type': 'scatter', 'symbolSize': pt, 'data': jittered,
                       'z': 1})

    # ── Overlay: summary statistics markers per category ────────────────
    if overlay != 'none' and len(categories) > 0:
        mean_points = []
        median_points = []
        box_points_data = []  # [[x, y], ...] for IQR box centers
        box_sizes = []        # [[w, h], ...] for each box

        for ci, cat in enumerate(categories):
            cat_data = dfc[dfc[x_col] == cat][y_col]
            if cat_data.empty:
                continue
            vals = sorted(cat_data.dropna().tolist())
            if not vals:
                continue
            n = len(vals)
            mean_val = sum(vals) / n
            median_val = vals[n // 2] if n % 2 == 1 else (vals[n // 2 - 1] + vals[n // 2]) / 2

            if overlay in ('mean', 'mean+box'):
                mean_points.append([float(ci), mean_val])

            if overlay in ('median', 'mean+box'):
                median_points.append([float(ci), median_val])

            if overlay in ('box', 'mean+box') and n >= 4:
                q1 = vals[n // 4]
                q3 = vals[3 * n // 4]
                box_center = (q1 + q3) / 2
                box_h = q3 - q1
                box_points_data.append([float(ci), box_center])
                box_sizes.append([int(jitter_width * 60), float(box_h)])

        if mean_points:
            series.append({
                'name': 'Mean',
                'type': 'scatter',
                'symbol': 'diamond',
                'symbolSize': pt + 8,
                'data': mean_points,
                'itemStyle': {'color': '#ef4444', 'borderColor': '#b91c1c', 'borderWidth': 1.5},
                'z': 10,
            })

        if median_points:
            series.append({
                'name': 'Median',
                'type': 'scatter',
                'symbol': 'rect',
                'symbolSize': [int(jitter_width * 50), 3],
                'data': median_points,
                'itemStyle': {'color': '#3b82f6', 'borderColor': '#1d4ed8', 'borderWidth': 1},
                'z': 10,
            })

        if box_points_data:
            for i, (pt_data, pt_size) in enumerate(zip(box_points_data, box_sizes)):
                # Each box rendered as a rectangle scatter marker
                pass  # boxes are per-category with different heights; use custom series
            # Use a single series with customSymbol if ECharts supports it,
            # otherwise render each box as its own series with unique symbolSize
            for i, (pt_data, pt_size) in enumerate(zip(box_points_data, box_sizes)):
                series.append({
                    'name': 'IQR' if i == 0 else '',
                    'type': 'scatter',
                    'symbol': 'rect',
                    'symbolSize': pt_size,
                    'data': [pt_data],
                    'itemStyle': {'color': 'rgba(59,130,246,0.15)', 'borderColor': '#3b82f6', 'borderWidth': 1},
                    'z': 5,
                })

    option.update({
        'xAxis': {
            **ax,
            'type': 'value',
            'min': -0.6,
            'max': len(categories) - 0.4,
            'axisLabel': x_axis_label,
            'axisTick': {'show': False},
            'splitLine': {'show': False},
        },
        'yAxis': {**ax, 'type': 'value', 'name': labels.get('y', y_col)},
        'series': series,
    })
    option['grid'] = {**(option.get('grid', {})), 'bottom': '12%'}
    _add_zoom(option, cfg)
    return option


def _heatmap_option(df, x_col, y_col, agg, cfg, labels):
    if not x_col or not y_col:
        return _message_option('X and Y are required for heatmap', cfg, error=True)
    adf = _agg(df, x_col, y_col, agg, stack_col=y_col, cfg=cfg)
    if adf.empty:
        return _message_option('No data loaded', cfg)
    option = _base_option(cfg, axis_trigger=False)
    ax = _axis_style(cfg)
    x_lbl = _x_axis_label_style(cfg)
    x_vals = _ordered_categories(sorted(adf[x_col].astype(str).unique().tolist()), cfg)
    y_vals = _ordered_categories(sorted(adf[y_col].astype(str).unique().tolist()), {'sort_col': cfg.get('sort_col') if cfg.get('sort_col') == '__manual__' else '', 'category_order': cfg.get('category_order', []), 'sort_dir': cfg.get('sort_dir', 'asc')})
    xi, yi = {v: i for i, v in enumerate(x_vals)}, {v: i for i, v in enumerate(y_vals)}
    points, max_val = [], 0
    for _, row in adf.iterrows():
        val = float(row['_value'] or 0)
        points.append([xi[str(row[x_col])], yi[str(row[y_col])], val])
        max_val = max(max_val, val)
    option.update({
        'xAxis': {'type': 'category', 'data': x_vals, 'name': _axis_name(labels['x'], cfg), **ax, 'axisLabel': x_lbl},
        'yAxis': {'type': 'category', 'data': y_vals, 'name': _axis_name(labels['y'], cfg), **ax, 'axisLabel': x_lbl},
        'visualMap': {'min': 0, 'max': max_val or 1, 'calculable': True, 'orient': 'vertical', 'right': 10, 'top': 'center'},
        'series': [{'type': 'heatmap', 'data': points, 'label': {'show': bool(cfg.get('data_labels', False)), 'fontSize': int(cfg.get('label_font_size', 10) or 10)}}],
    })
    option['__category_names__'] = x_vals
    _add_zoom(option, cfg)
    return option


def _histogram_option(df, x_col, stack_col, cfg, labels):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)
    option = _base_option(cfg)
    ax = _axis_style(cfg)
    x_lbl = _x_axis_label_style(cfg)
    bins = max(4, min(40, int(cfg.get('bins', 12) or 12)))

    def hist(vals, nbins):
        s = pd.to_numeric(vals, errors='coerce').dropna()
        if s.empty:
            c = vals.fillna('(Blank)').astype(str).value_counts().sort_index()
            return c.index.tolist(), c.values.tolist()
        bc = pd.cut(s, bins=min(nbins, max(4, int(s.nunique() or 1))), include_lowest=True).value_counts().sort_index()
        return [f'{iv.left:.1f}–{iv.right:.1f}' for iv in bc.index], bc.values.tolist()

    if stack_col and stack_col in df.columns:
        all_x, per_group = [], {}
        for g in _ordered_stacks(df[stack_col].fillna('(Blank)').astype(str).unique().tolist(), cfg):
            sub = df[df[stack_col].fillna('(Blank)').astype(str) == g]
            bns, cnts = hist(sub[x_col], bins)
            per_group[g] = dict(zip(bns, cnts))
            all_x.extend(bns)
        x_axis = _ordered_categories(sorted(set(all_x)), cfg)
        series = [{'name': g, 'type': 'bar', 'stack': 'total', 'data': [int(per_group[g].get(b, 0)) for b in x_axis]}
                  for g in reversed(list(per_group))]
    else:
        x_axis, counts = hist(df[x_col], bins)
        value_map = dict(zip(x_axis, counts))
        x_axis = _ordered_categories(x_axis, cfg, value_map)
        counts = [value_map.get(cat, 0) for cat in x_axis]
        series = [{'name': 'Count', 'type': 'bar', 'data': [int(v) for v in counts]}]

    option.update({'xAxis': {'type': 'category', 'data': x_axis, 'name': _axis_name(labels['x'], cfg), **ax, 'axisLabel': x_lbl},
                   'yAxis': {'type': 'value', 'name': _axis_name('Count', cfg), **ax}, 'series': series})
    option['__category_names__'] = x_axis
    _add_zoom(option, cfg)
    return option


def _boxplot_option(df, x_col, y_col, cfg, labels):
    if not y_col or y_col not in df.columns:
        return _message_option('Y / Value is required', cfg, error=True)
    option = _base_option(cfg)
    ax = _axis_style(cfg)
    x_lbl = _x_axis_label_style(cfg)

    def stats(vals):
        s = pd.to_numeric(vals, errors='coerce').dropna()
        if s.empty:
            return None
        q = s.quantile([0.25, 0.5, 0.75])
        return [float(s.min()), float(q[0.25]), float(q[0.5]), float(q[0.75]), float(s.max())]

    lbls, data = [], []
    if x_col and x_col in df.columns:
        for g in sorted(df[x_col].fillna('(Blank)').astype(str).unique()):
            st = stats(df[df[x_col].fillna('(Blank)').astype(str) == g][y_col])
            if st:
                lbls.append(g)
                data.append(st)
        paired = dict(zip(lbls, data))
        lbls = _ordered_categories(lbls, cfg)
        data = [paired[lbl] for lbl in lbls]
    else:
        st = stats(df[y_col])
        if st:
            lbls, data = ['All'], [st]

    _update_chart_axes(option, lbls, [{'type': 'boxplot', 'data': data}], labels['y'], labels, cfg, ax, x_lbl)
    option['__category_names__'] = lbls
    _add_zoom(option, cfg)
    return option


def _funnel_option(df, x_col, y_col, agg, cfg):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)
    adf = _agg(df, x_col, y_col, agg, cfg=cfg).sort_values('_value', ascending=False)
    value_map = dict(zip(adf[x_col].astype(str), adf['_value']))
    categories = _ordered_categories(adf[x_col].astype(str).tolist(), cfg, value_map)
    option = _base_option(cfg, axis_trigger=False)
    option['series'] = [{'type': 'funnel', 'left': '10%', 'top': 40, 'bottom': 20, 'width': '80%',
                           'sort': 'descending', 'label': {'show': True, 'position': 'inside', 'fontSize': int(cfg.get('label_font_size', 10) or 10)},
                           'data': [{'name': cat, 'value': float(value_map.get(cat, 0) or 0)} for cat in categories]}]
    option['__category_names__'] = categories
    return option


def _treemap_option(df, x_col, y_col, agg, cfg):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)
    adf = _agg(df, x_col, y_col, agg, cfg=cfg)
    value_map = dict(zip(adf[x_col].astype(str), adf['_value']))
    categories = _ordered_categories(adf[x_col].astype(str).tolist(), cfg, value_map)
    option = _base_option(cfg, axis_trigger=False)
    option['series'] = [{'type': 'treemap', 'nodeClick': 'zoomToNode',
                          'data': [{'name': cat, 'value': float(value_map.get(cat, 0) or 0)} for cat in categories]}]
    option['__category_names__'] = categories
    return option


def _radar_option(df, x_col, y_col, agg, cfg):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)
    adf = _agg(df, x_col, y_col, agg, cfg=cfg)
    value_map = dict(zip(adf[x_col].astype(str), adf['_value']))
    names = _ordered_categories(adf[x_col].astype(str).tolist(), cfg, value_map)
    vals = [float(value_map.get(name, 0) or 0) for name in names]
    max_v = max(vals) if vals else 1
    option = _base_option(cfg, axis_trigger=False)
    option.update({'radar': {'indicator': [{'name': n, 'max': max_v * 1.1} for n in names], 'splitArea': {'show': True}},
                   'series': [{'type': 'radar', 'data': [{'name': 'Values', 'value': vals}]}]})
    option['__category_names__'] = names
    return option


def _gauge_option(df, y_col, agg, cfg, labels):
    if not y_col or y_col not in df.columns:
        return _message_option('Y / Value is required', cfg, error=True)
    vals = pd.to_numeric(df[y_col], errors='coerce').dropna()
    if vals.empty:
        return _message_option('No numeric values for gauge', cfg, error=True)
    value = {
        'sum': float(vals.sum()), 'avg': float(vals.mean()), 'min': float(vals.min()),
        'max': float(vals.max()), 'median': float(vals.median()),
        'count_unique': float(vals.nunique()), 'count_occurrence': float(vals.count()),
    }.get(agg, float(len(df)))
    _ad = cfg.get('agg_decimals')
    if _ad is not None and agg in ('avg', 'sum', 'min', 'max', 'median', 'count_unique', 'count_occurrence'):
        try:
            value = round(value, int(_ad))
        except (ValueError, TypeError):
            pass
    max_v = float(vals.max()) * 1.1 if agg not in ('count', 'count_unique', 'count_occurrence') else float(max(len(df), 1)) * 1.2
    option = _base_option(cfg, axis_trigger=False)
    option['series'] = [{'type': 'gauge', 'max': max_v, 'detail': {'formatter': '{value}'},
                          'title': {'show': True, 'offsetCenter': [0, '78%']},
                          'data': [{'value': round(value, 2), 'name': labels.get('y') or 'Value'}]}]
    return option


def _waterfall_option(df, x_col, y_col, agg, cfg, labels):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)
    adf = _agg(df, x_col, y_col, agg, cfg=cfg)
    value_map = dict(zip(adf[x_col].astype(str), adf['_value']))
    categories = _ordered_categories(adf[x_col].astype(str).tolist(), cfg, value_map)
    option = _base_option(cfg)
    option['xAxis'] = {'type': 'category', 'data': categories, 'name': _axis_name(labels.get('x', ''), cfg)}
    option['yAxis'] = {'name': _axis_name(labels.get('y', ''), cfg)}
    option['series'] = [{'type': 'bar', 'name': labels.get('y', 'Value'),
                          'data': [float(value_map.get(cat, 0) or 0) for cat in categories],
                          'label': {'show': bool(cfg.get('data_labels', False)), 'fontSize': int(cfg.get('label_font_size', 10) or 10)}}]
    option['__category_names__'] = categories
    return option

# Expose private names to sibling ``import *`` (PEP 328).
__all__ = [n for n in list(globals()) if not n.startswith('__')]
