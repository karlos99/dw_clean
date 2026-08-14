"""Assessment-compare grouped/stacked chart builder."""
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

def _assessment_compare_option(df, x_col, compare_pl_cols, cfg, labels, col_defs):
    """Grouped + stacked bar comparing 2+ PL columns from the same assessment.

    Two-level x-axis layout:
      - xAxis[0]: window/year labels right below each bar
      - xAxis[1]: category names centred below each group (one label per category)
    Gap between category groups is controlled via barCategoryGap percentage.
    """
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)
    if not compare_pl_cols or len(compare_pl_cols) < 2:
        return _message_option('Select at least 2 PL columns to compare', cfg, error=True)

    valid_cols = [c for c in compare_pl_cols if c in df.columns]
    if len(valid_cols) < 2:
        return _message_option('Need at least 2 valid PL columns in the data', cfg, error=True)

    # ── Apply user-defined filters on x-col categories and PL values ──────
    ac_include_cats = cfg.get('ac_include_cats') or []   # list of category values to keep
    ac_include_pl   = cfg.get('ac_include_pl')   or []   # list of PL values to keep
    # Extra column filters: {col: [val, ...]}
    ac_col_filters  = cfg.get('ac_col_filters')  or {}

    # ── Apply user-defined filters and order on x-col categories ──────
    if x_col and x_col in df.columns:
        unique_vals = df[x_col].fillna('(Blank)').astype(str).unique().tolist()
        if ac_include_cats:
            # Respect user order and filter
            base_categories = [c for c in ac_include_cats if c in unique_vals]
            df = df[df[x_col].fillna('(Blank)').astype(str).isin(ac_include_cats)]
        else:
            base_categories = sorted(unique_vals)
    else:
        base_categories = []
    if base_categories:
        category_value_map = {cat: int((df[x_col].fillna('(Blank)').astype(str) == cat).sum()) for cat in base_categories}
        base_categories = _ordered_categories(base_categories, cfg, category_value_map)

    if ac_col_filters:
        for fcol, fvals in ac_col_filters.items():
            if fcol in df.columns and fvals:
                df = df[df[fcol].fillna('(Blank)').astype(str).isin(fvals)]
    if df.empty:
        return _message_option('No data after applying filters', cfg, error=True)

    # Parse metadata for each PL column
    col_meta = {c: _parse_col_meta(c, col_defs) for c in valid_cols}

    # Auto-detect compare dimension labels
    windows = [col_meta[c]['window'] for c in valid_cols]
    years   = [col_meta[c]['year']   for c in valid_cols]
    if len(set(windows)) > 1:
        group_labels = {c: col_meta[c]['window'] or c for c in valid_cols}
    elif len(set(years)) > 1:
        group_labels = {c: col_meta[c]['year'] or c for c in valid_cols}
    else:
        group_labels = {c: f"{col_meta[c]['window']} {col_meta[c]['year']}".strip() or c for c in valid_cols}

    option = _base_option(cfg)
    ax = _axis_style(cfg)
    opacity       = float(cfg.get('opacity', 0.9) or 0.9)
    border_radius = int(cfg.get('bar_border_radius', 0) or 0)
    label_pos     = cfg.get('label_position', 'top') or 'top'
    show_labels   = bool(cfg.get('data_labels', False))
    show_percent  = bool(cfg.get('show_percent', False))
    percent_decimals = _percent_decimals(cfg)
    percent_suffix = _percent_suffix(cfg)

    # User-configurable compare layout options
    _raw_gap = cfg.get('compare_gap', 30)
    compare_gap   = int(_raw_gap) if _raw_gap is not None and str(_raw_gap) != '' else 30
    cat_font_size = int(cfg.get('compare_cat_font_size', 12) or 12)
    cat_rotation  = int(cfg.get('compare_cat_rotation', 0) or 0)
    x_label_font_size = int(cfg.get('x_label_font_size', 10) or 10)
    x_label_rotation  = int(cfg.get('x_label_rotation', 0) or 0)

    # Collect all unique PL values across selected columns (skip blanks)
    _BLANK = frozenset({'', '-', '\u2014', '\u2013', 'N/A', 'n/a', 'None', 'null', 'NULL', 'nan', 'NAN', 'NaN'})
    all_pl = set()
    for c in valid_cols:
        vals = df[c].dropna().astype(str).str.strip()
        all_pl.update(v for v in vals.unique() if v not in _BLANK)

    # Respect user order for PL values if selected, else use default scale detection/sorting
    if ac_include_pl:
        pl_vals = [pv for pv in ac_include_pl if pv in all_pl]
    else:
        pl_vals = _ordered_stacks(list(all_pl), cfg)

    if not pl_vals:
        return _message_option('No PL values remain after filtering — adjust PL filter', cfg, error=True)

    # Order groups: respect selection order from valid_cols (which comes from compare_pl_cols)
    ordered_cols = valid_cols
    n_groups = len(ordered_cols)

    # Assign a fixed colour per PL value using semantic colours + palette fallback
    palette = PALETTES.get(cfg.get('palette', 'assessment'), PALETTES['assessment'])
    pl_color = {}
    pal_idx = 0
    for pv in pl_vals:
        if pv in SEMANTIC_COLORS:
            pl_color[pv] = SEMANTIC_COLORS[pv]
        else:
            pl_color[pv] = palette[pal_idx % len(palette)]
            pal_idx += 1

    # ── Build flat x-axis: one position per (category, group) ──
    x_labels = []          # xAxis[0] labels: window/year
    cat_for_pos = []       # category name per position (for drill-through)
    col_for_pos = []       # PL column name per position (for drill-through)
    group_ranges = []      # [(start, end, cat_name), ...] for xAxis[1] labels
    spacer_positions = set()

    group_gap_size = int(cfg.get('group_gap_size', 0) or 0)

    flat_pos = 0
    pos_map = {}
    for ci, cat in enumerate(base_categories):
        if ci > 0 and group_gap_size > 0:
            for j in range(group_gap_size):
                x_labels.append(" ")
                cat_for_pos.append("")
                col_for_pos.append("")
                spacer_positions.add(flat_pos)
                flat_pos += 1
                
        group_start = flat_pos
        for gi, col in enumerate(ordered_cols):
            x_labels.append(group_labels[col])
            cat_for_pos.append(cat)
            col_for_pos.append(col)
            pos_map[flat_pos] = (cat, col)
            flat_pos += 1
        group_end = flat_pos - 1
        group_ranges.append((group_start, group_end, cat))

    total_positions = flat_pos

    # Pre-compute raw counts per position
    raw_counts = {}    # {pos: {pv: count}}
    for pos in range(total_positions):
        if pos in spacer_positions:
            raw_counts[pos] = {pv: 0 for pv in pl_vals}
            continue
        cat, col = pos_map[pos]
        counts = {}
        for pv in pl_vals:
            mask = (df[x_col].fillna('(Blank)').astype(str) == cat) & (df[col].astype(str).str.strip() == pv)
            counts[pv] = int(mask.sum())
        raw_counts[pos] = counts

    # Totals per position
    totals = {pos: sum(counts.values()) for pos, counts in raw_counts.items()}

    # ── Build series ──
    series = []
    for pv in reversed(pl_vals):
        data_vals = []
        for pos in range(total_positions):
            if pos in spacer_positions:
                data_vals.append(None)
                continue
            cnt = raw_counts[pos].get(pv, 0)
            if show_percent and totals[pos] > 0:
                data_vals.append(round(cnt / totals[pos] * 100, percent_decimals))
            else:
                data_vals.append(float(cnt))
        s = {
            'name': pv,
            'type': 'bar',
            'stack': 'total',
            'xAxisIndex': 0,
            'barCategoryGap': f'{compare_gap}%',
            'label': {
                'show': show_labels,
                'position': label_pos,
                'fontSize': int(cfg.get('label_font_size', 10) or 10),
                **({'__formatter_js__': f"function(p){{var v=Number(p.value||0);return v.toFixed({percent_decimals})+'{percent_suffix}';}}"} if show_percent else {}),
            },
            'itemStyle': {'color': pl_color[pv], 'opacity': opacity},
            'data': data_vals,
        }
        if border_radius:
            s['itemStyle']['borderRadius'] = [border_radius, border_radius, 0, 0]
        series.append(s)

    # ── Tooltip: respect tooltipShowPct / tooltipShowCount flags ──
    raw_counts_flat = {str(p): {pv: raw_counts[p].get(pv, 0) for pv in pl_vals} for p in range(total_positions)}
    totals_flat = {str(p): totals[p] for p in range(total_positions)}
    display_names = {}
    for pos in range(total_positions):
        ci = pos // n_groups
        gi = pos % n_groups
        display_names[str(pos)] = f"{base_categories[ci]} \u00b7 {group_labels[ordered_cols[gi]]}"

    _sp3 = _tooltip_show_pct(cfg)
    _sc3 = _tooltip_show_count(cfg)
    _vp3 = _tooltip_value_pct(cfg)
    _zero3 = '0.' + '0' * percent_decimals if percent_decimals else '0'
    _count_line3 = (
        "  if (total > 0) html += ' &nbsp;<span style=\"color:#999\">(n=' + total + ')</span>';"
        if _sc3 else ""
    )
    _pct_calc3 = (
        f"    var pct = total > 0 ? (cnt / total * 100).toFixed({percent_decimals}) : '{_zero3}';"
        if _sp3 else ""
    )
    _pct_frag3 = " + ' (' + pct + '%)';" if _sp3 else ";"
    _val_disp3 = "cnt + '%'" if _vp3 else "cnt"
    tooltip_js = (
        "function(params) {"
        "  if (!Array.isArray(params)) params = [params];"
        f"  var rawCounts = {json.dumps(raw_counts_flat)};"
        f"  var totals = {json.dumps(totals_flat)};"
        f"  var names = {json.dumps(display_names)};"
        "  var idx = params[0].dataIndex;"
        "  var cat = names[String(idx)] || params[0].name || '';"
        "  var total = totals[String(idx)] || 0;"
        "  var html = '<b>' + cat + '</b>';"
        f"{_count_line3}"
        "  for (var i = params.length - 1; i >= 0; i--) {"
        "    var d = params[i];"
        "    if (d.value == null) continue;"
        "    var cnt = (rawCounts[String(idx)] || {})[d.seriesName] || 0;"
        f"{_pct_calc3}"
        f"    html += '<br/>' + d.marker + ' ' + d.seriesName + ': <b>' + {_val_disp3} + '</b>'{_pct_frag3}"
        "  }"
        "  return html;"
        "}"
    )

    # ── xAxis[0]: window/year labels right below each bar ──
    x_axis_0 = {
        'type': 'category',
        'data': x_labels,
        'position': 'bottom',
        **ax,
        'axisLabel': {
            **ax.get('axisLabel', {}),
            'interval': 0,
            'fontSize': x_label_font_size,
            'rotate': x_label_rotation,
            'show': cfg.get('show_categories', True) is not False,
        },
        'axisTick': {'show': False},
        'axisLine': {'show': False},
        'splitLine': {'show': False},
    }

    # ── xAxis[1]: category labels, one per group (true-centered via graphic bands) ──
    center_groups = cfg.get('center_group_labels', True) is not False
    show_cats = cfg.get('show_categories', True) is not False
    cat_labels_for_axis = [''] * total_positions
    if not center_groups:
        for start, end, cat_name in group_ranges:
            mid = (start + end) // 2
            if 0 <= mid < total_positions:
                cat_labels_for_axis[mid] = cat_name

    cat_formatter_js = "function(value) { return value || ''; }"

    # Estimate offset dynamically based on inner axis label rotation
    if x_label_rotation > 0:
        import math
        # Approx width of text = 12 (characters) * font_size * 0.6
        text_width = 12 * x_label_font_size * 0.6
        rad = math.radians(x_label_rotation)
        height_contrib = text_width * math.sin(rad) + x_label_font_size * math.cos(rad)
        offset = max(22, int(height_contrib + 12))
    else:
        offset = 22

    if center_groups:
        option['__group_label_bands__'] = [
            {
                'start': start,
                'end': end,
                'label': cat_name,
                'axis': 'x',
                'fontSize': cat_font_size,
                'rotate': cat_rotation,
                'color': ax.get('axisLabel', {}).get('color', '#475569'),
                'offset': offset,
                'show': show_cats,
            }
            for start, end, cat_name in group_ranges
        ]

    x_axis_1 = {
        'type': 'category',
        'data': cat_labels_for_axis,
        'position': 'bottom',
        'offset': offset,
        'axisLabel': {
            'interval': 0,
            'fontSize': cat_font_size,
            'fontWeight': 'bold',
            'color': ax.get('axisLabel', {}).get('color', '#475569'),
            'rotate': cat_rotation,
            'margin': 4,
            'show': (not center_groups) and show_cats,
            '__formatter_js__': cat_formatter_js,
        },
        'axisTick': {'show': False},
        'axisLine': {'show': False},
        'splitLine': {'show': False},
    }

    # ── Y-axis ──
    y_name = _axis_name('%' if show_percent else 'Count', cfg)
    y_max = 100 if show_percent else None
    y_axis = {'type': 'value', 'name': y_name, **ax}
    if y_max:
        y_axis['max'] = y_max

    option.update({
        'xAxis': [x_axis_0, x_axis_1],
        'yAxis': y_axis,
        'series': series,
        'tooltip': {
            'show': cfg.get('show_tooltip', True) is not False,
            'trigger': 'axis',
            'axisPointer': {'type': 'shadow'},
            '__formatter_js__': tooltip_js,
        },
    })

    # Expose available filter values so the builder UI can populate filter pickers
    option['__ac_avail_cats__'] = base_categories          # all unique x-col values
    option['__ac_avail_pl__']   = _ordered_stacks(list(all_pl), cfg)  # all PL values (unfiltered)
    option['__category_names__'] = base_categories

    # Embed drill-through metadata so the click handler can resolve
    # the real category from a dataIndex (needed because xAxis[0] shows window/year).
    option['_drillMeta'] = {
        'catForPos': cat_for_pos,        # dataIndex → real category value
        'colForPos': col_for_pos,        # dataIndex → PL column name
        'nGroups': n_groups,
    }

    # Adjust grid bottom to accommodate two axis label rows
    if 'grid' not in option:
        option['grid'] = {}
    option['grid']['bottom'] = max(option['grid'].get('bottom', 60), 90 + cat_font_size + (cat_rotation // 3))

    _add_zoom(option, cfg)
    return option

# Expose private names to sibling ``import *`` (PEP 328).
__all__ = [n for n in list(globals()) if not n.startswith('__')]
