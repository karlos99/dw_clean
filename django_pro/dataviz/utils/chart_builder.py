"""
chart_builder.py — Server-side ECharts option builder.

Extracted from the original dataviz/views.py and upgraded for ECharts 6.
Call `build_option(rows, col_defs, cfg)` to get an ECharts option dict.
"""
import json
import re

import pandas as pd

try:
    from dataexploration.school_year_utils import resolve_relative_column_key
except Exception:  # pragma: no cover - keep chart builder usable in isolated contexts
    def resolve_relative_column_key(col_key: str) -> str:
        return col_key

# ── Meta-column constants ─────────────────────────────────────────────────────

_YEAR_RE         = re.compile(r'^(?:test|mark)_(\d{4})_(\d{4})_')
_META_DIM_COLS   = {'_assessment', '_test_school_year', '_test_window', '_test_pl', '_mark_school_year', '_mark_period'}
_META_SCORE_COLS = {'_test_score', '_mark_score'}

_META_LABEL_MAP = {
    '_assessment':       'Assessment',
    '_test_school_year': 'School Year',
    '_test_window':      'Test Window / Period',
    '_test_score':       'Test Score (SS)',
    '_test_pl':          'Performance Level',
    '_mark_school_year': 'Mark School Year',
    '_mark_period':      'Mark Period',
    '_mark_score':       'Mark Value',
}

# ── Colour palettes ───────────────────────────────────────────────────────────

PALETTES = {
    'default': ['#636EFA', '#EF553B', '#00CC96', '#AB63FA', '#FFA15A', '#19D3F3', '#FF6692', '#B6E880'],
    'blue':    ['#4472c4', '#5b9bd5', '#264478', '#70ad47', '#ffc000', '#f4522d', '#9e480e', '#636363'],
    'warm':    ['#e74c3c', '#e67e22', '#f39c12', '#f1c40f', '#ff5733', '#c0392b', '#d35400', '#e8b800'],
    'earth':   ['#8b7355', '#a9956c', '#c8a96e', '#7f6000', '#5d4e37', '#bfa98e', '#6d4c41', '#4e342e'],
    'green':   ['#27ae60', '#2ecc71', '#1abc9c', '#16a085', '#52be80', '#76d7c4', '#239b56', '#0e6655'],
    'purple':  ['#9b59b6', '#8e44ad', '#a569bd', '#6c3483', '#bb8fce', '#7d3c98', '#d2b4de', '#76448a'],
    'mono':    ['#2c3e50', '#7f8c8d', '#95a5a6', '#bdc3c7', '#34495e', '#566573', '#808b96', '#1a252f'],
    'assessment': ['#27ae60', '#3498db', '#f39c12', '#e74c3c', '#9b59b6', '#95a5a6', '#636363', '#1abc9c'],
    'traffic':    ['#27ae60', '#f39c12', '#e74c3c', '#c0392b', '#2ecc71', '#f1c40f', '#d35400', '#7f8c8d'],
}

# Pre-built semantic colour maps for common data values.
# When a chart's series/category matches one of these keys the colour is fixed
# regardless of palette, giving users instant meaningful colour coding.
SEMANTIC_COLORS = {
    # Assessment performance levels
    'Above Benchmark':          '#27ae60',
    'At Benchmark':             '#3498db',
    'Below Benchmark':          '#f39c12',
    'Well Below Benchmark':     '#e74c3c',
    'Exceeded':                 '#1a8a4a',
    'Met':                      '#27ae60',
    'Nearly Met':               '#f39c12',
    'Not Met':                  '#e74c3c',
    'Proficient':               '#27ae60',
    'Basic':                    '#3498db',
    'Below Basic':              '#f39c12',
    'Far Below Basic':          '#e74c3c',
    'Advanced':                 '#1a8a4a',
    # Boolean / Yes-No
    'Yes':                      '#27ae60',
    'No':                       '#95a5a6',
    # Gender
    'Male':                     '#3498db',
    'Female':                   '#e91e8e',
    # Common grades
    'A':  '#27ae60', 'B': '#3498db', 'C': '#f39c12',
    'D':  '#e67e22', 'F': '#e74c3c',
    'A+': '#1a8a4a', 'A-': '#2ecc71', 'B+': '#2980b9',
    'B-': '#5dade2', 'C+': '#d4ac0d', 'C-': '#f1c40f',
    'D+': '#ca6f1e', 'D-': '#e74c3c',
}

# ── Progress Growth constants ─────────────────────────────────────────────────

# Fixed semantic colours — Growth=green, Stickers=grey, Sliders=orange
_PG_COLORS = {
    'Growth':   '#22c55e',
    'Stickers': '#94a3b8',
    'Sliders':  '#f97316',
}

# Known PL orderings (lowest → highest) for auto-detection.
_KNOWN_PL_SCALES = [
    ['Well Below Benchmark', 'Below Benchmark', 'At Benchmark', 'Above Benchmark'],
    ['Not Met', 'Nearly Met', 'Met', 'Exceeded'],
    ['Far Below Basic', 'Below Basic', 'Basic', 'Proficient', 'Advanced'],
    ['Below Basic', 'Basic', 'Proficient', 'Advanced'],
    ['1', '2', '3', '4'],
]

# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_col_year(col: str) -> str:
    m = _YEAR_RE.match(col)
    return f"{m.group(1)}-{m.group(2)}" if m else ''


def _parse_col_meta(col: str, col_defs: dict) -> dict:
    label = col_defs.get(col, col)
    if col.startswith('test_'):
        lines = label.split('\n')
        first = lines[0].strip()
        window = lines[1].strip() if len(lines) > 1 else ''
        if not window and (col.endswith('_SS') or col.endswith('_PL')):
            inner = col[5:-3]
            m2 = re.match(r'\d{4}_\d{4}_(.+)', inner)
            if m2:
                last = m2.group(1).split('_')[-1].lower()
                if last in {'fall', 'winter', 'spring', 'summer', 'eoy', 'moy', 'boy', 'baseline', 'midyear'}:
                    window = last.capitalize()
        year_match = re.search(r'\((\d{4}-\d{4})\)', first)
        year = year_match.group(1) if year_match else _parse_col_year(col)
        if year_match:
            assessment = first[:year_match.start()].strip()
        else:
            assessment = first
        for sfx in (' SS', ' PL', ' Scale Score', ' Perf Lvl'):
            if assessment.endswith(sfx):
                assessment = assessment[:-len(sfx)].strip()
                break
        return {'col_type': 'test', 'assessment': assessment, 'year': year, 'window': window}

    elif col.startswith('mark_'):
        lines = label.split('\n')
        first = lines[0].strip()
        year = lines[1].strip() if len(lines) > 1 else _parse_col_year(col)
        if ')' in first:
            period = first.split(')')[-1].strip()
            course_with_sub = first[:first.rfind(')')].strip()
            assessment = course_with_sub[:course_with_sub.rfind(' (')].strip() if ' (' in course_with_sub else course_with_sub
        else:
            period = ''
            assessment = first
        return {'col_type': 'mark', 'assessment': assessment, 'year': year, 'window': period}

    return {'col_type': 'unknown', 'assessment': col, 'year': '', 'window': ''}


def _maybe_melt_for_meta(df, x_col, stack_col, y_col, y_cols, col_defs):
    uses_meta = (
        x_col     in _META_DIM_COLS | _META_SCORE_COLS
        or stack_col in _META_DIM_COLS
        or y_col     in _META_SCORE_COLS
        or any(c in _META_SCORE_COLS for c in (y_cols or []))
    )
    if not uses_meta:
        return df, y_col, y_cols

    test_ss_cols = [c for c in df.columns if c.startswith('test_') and c.endswith('_SS') and _YEAR_RE.match(c)]
    test_pl_cols = [c for c in df.columns if c.startswith('test_') and c.endswith('_PL') and _YEAR_RE.match(c)]
    mark_cols    = [c for c in df.columns if c.startswith('mark_') and _YEAR_RE.match(c)]

    # Build a mapping from PL col → sibling SS col (same assessment/window, different metric)
    # so we can pair them after melt
    _pl_to_ss = {}
    for pc in test_pl_cols:
        ss_sibling = pc[:-3] + '_SS'
        if ss_sibling in test_ss_cols:
            _pl_to_ss[pc] = ss_sibling

    score_cols   = test_ss_cols + mark_cols
    all_melt_cols = score_cols + [c for c in test_pl_cols if c not in score_cols]
    if not all_melt_cols:
        return df, y_col, y_cols

    meta_cache = {}
    for c in all_melt_cols:
        meta_cache[c] = _parse_col_meta(c, col_defs)

    # -- If _test_pl is used (assessment compare), melt PL columns separately
    # and join with the SS-based melt so we get rows with both _test_score and _test_pl
    if stack_col == '_test_pl' or x_col == '_test_pl':
        id_cols = [c for c in df.columns if c not in set(score_cols) | set(test_pl_cols)]

        frames = []
        # For each PL col that has a matching SS col, melt both together per student
        for pl_col, ss_col in _pl_to_ss.items():
            meta = meta_cache[ss_col]
            sub = df[id_cols + [ss_col, pl_col]].copy()
            sub = sub.rename(columns={ss_col: '_test_score', pl_col: '_test_pl'})
            sub['_test_score'] = pd.to_numeric(sub['_test_score'], errors='coerce')
            sub['_assessment'] = meta['assessment']
            sub['_test_school_year'] = meta['year']
            sub['_test_window'] = meta['window']
            sub['_mark_school_year'] = meta['year']
            sub['_mark_period'] = meta['window']
            frames.append(sub)

        # PL cols with no SS sibling — melt with score as NaN but keep PL
        orphan_pl = [c for c in test_pl_cols if c not in _pl_to_ss]
        for pl_col in orphan_pl:
            meta = meta_cache[pl_col]
            sub = df[id_cols + [pl_col]].copy()
            sub = sub.rename(columns={pl_col: '_test_pl'})
            sub['_test_score'] = float('nan')
            sub['_assessment'] = meta['assessment']
            sub['_test_school_year'] = meta['year']
            sub['_test_window'] = meta['window']
            sub['_mark_school_year'] = meta['year']
            sub['_mark_period'] = meta['window']
            frames.append(sub)

        if not frames:
            return df, y_col, y_cols

        melted = pd.concat(frames, ignore_index=True)
        # Strip blank PL values
        melted = melted[melted['_test_pl'].astype(str).str.strip().ne('')]
        melted = melted[melted['_test_pl'].notna()]

        new_y_col = '_test_score' if (y_col in score_cols or y_col in _META_SCORE_COLS or not y_col) else y_col
        return melted, new_y_col, []

    # -- Standard SS/mark melt (original logic)
    if not score_cols:
        return df, y_col, y_cols

    id_cols = [c for c in df.columns if c not in score_cols]
    melted = df.melt(id_vars=id_cols, value_vars=score_cols, var_name='_source_col', value_name='_test_score')
    melted['_test_score'] = pd.to_numeric(melted['_test_score'], errors='coerce')
    melted = melted.dropna(subset=['_test_score'])
    melted['_assessment']       = melted['_source_col'].map(lambda c: meta_cache[c]['assessment'])
    melted['_test_school_year'] = melted['_source_col'].map(lambda c: meta_cache[c]['year'])
    melted['_test_window']      = melted['_source_col'].map(lambda c: meta_cache[c]['window'])
    melted['_mark_school_year'] = melted['_test_school_year']
    melted['_mark_period']      = melted['_test_window']
    melted = melted.drop(columns=['_source_col'])

    # Also add _test_pl from sibling PL columns if available
    if test_pl_cols and '_test_pl' not in melted.columns:
        # Build a lookup: (assessment, year, window) → PL col name
        pl_lookup = {}
        for pc in test_pl_cols:
            m = meta_cache.get(pc, {})
            key = (m.get('assessment', ''), m.get('year', ''), m.get('window', ''))
            pl_lookup[key] = pc

        def _get_pl(row):
            key = (row.get('_assessment', ''), row.get('_test_school_year', ''), row.get('_test_window', ''))
            pc = pl_lookup.get(key)
            if not pc:
                return ''
            # Find the original row index — we need to look up the original df
            return ''  # PL lookup requires original row context; skip for standard melt

        # Not straightforward with melted data - skip for standard melt path

    new_y_col = '_test_score' if (y_col in score_cols or y_col in _META_SCORE_COLS or not y_col) else y_col
    return melted, new_y_col, []


def _label(col: str, col_defs: dict) -> str:
    raw = col_defs.get(col, col) or col
    return str(raw).replace('\n', ' — ').replace('\r', '')


def _agg(df, x_col, y_col, agg, stack_col='', agg_decimals=None, cfg=None):
    if agg_decimals is None and cfg is not None:
        _raw = cfg.get('agg_decimals')
        if _raw is not None:
            try:
                agg_decimals = int(_raw)
            except (ValueError, TypeError):
                pass
    if x_col not in df.columns:
        return pd.DataFrame()
    
    # Filter out rows where x_col or stack_col are NaN/None before aggregation
    group_cols = [x_col]
    if stack_col and stack_col in df.columns:
        group_cols.append(stack_col)
    
    # Strictly drop rows with NaN in grouping columns
    df = df.dropna(subset=group_cols)
    
    try:
        if agg in ('sum', 'avg', 'min', 'max', 'median') and y_col and y_col in df.columns:
            dfc = df.copy()
            dfc[y_col] = pd.to_numeric(dfc[y_col], errors='coerce')
            # dropna=True (default) ensures NaNs in group_cols don't create a group
            grp = dfc.dropna(subset=[y_col]).groupby(group_cols, dropna=True)[y_col]
            result = {
                'sum': grp.sum(), 'avg': grp.mean(),
                'min': grp.min(), 'max': grp.max(), 'median': grp.median(),
            }.get(agg, grp.size()).reset_index().rename(columns={y_col: '_value'})
            # Apply decimal rounding for avg (or any numeric agg when decimals set)
            if agg_decimals is not None and '_value' in result.columns:
                result['_value'] = result['_value'].round(agg_decimals)
        else:
            # For 'count', we count rows where y_col is not NaN if provided
            if y_col and y_col in df.columns:
                result = df.dropna(subset=[y_col]).groupby(group_cols, dropna=True).size().reset_index(name='_value')
            else:
                result = df.groupby(group_cols, dropna=True).size().reset_index(name='_value')
    except Exception:
        result = df.groupby(group_cols, dropna=True).size().reset_index(name='_value')

    for c in group_cols:
        result[c] = result[c].astype(str)
    return result


def _theme(cfg: dict) -> dict:
    colors = PALETTES.get(cfg.get('palette', 'default'), PALETTES['default'])
    bg_map = {'white': '#ffffff', 'light': '#f8fafc', 'dark': '#1e2432'}
    bg = bg_map.get(cfg.get('background', 'white'), '#ffffff')
    is_dark = cfg.get('background') == 'dark'
    text = '#e5e7eb' if is_dark else '#374151'
    grid = '#4b5563' if is_dark else '#e5e7eb'
    return {'colors': colors, 'bg': bg, 'text': text, 'grid': grid}


def _legend(cfg: dict, text_color: str) -> dict:
    pos       = cfg.get('legend_pos', 'bottom')
    if pos == 'hidden':
        return {'show': False}

    item_gap   = int(cfg.get('legend_item_gap',   10) or 10)
    font_size  = int(cfg.get('legend_font_size',  11) or 11)
    icon_size  = int(cfg.get('legend_icon_size',  10) or 10)
    # User-overridden legend offset from its docked edge (px)
    legend_offset = cfg.get('legend_offset')   # None = use default
    legend_left   = cfg.get('legend_left')     # None = use default
    legend_top    = cfg.get('legend_top')      # None = use default

    base = {
        'show': True,
        'itemGap':   item_gap,
        'itemWidth': icon_size,
        'itemHeight': icon_size,
        'textStyle': {'color': text_color, 'fontSize': font_size},
    }

    def _to_pos(val):
        if val in ('center', 'middle', 'left', 'right', 'top', 'bottom'):
            return val
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    l_left = _to_pos(legend_left)
    l_top  = _to_pos(legend_top)

    # If user has dragged the legend to an exact pixel position, use that
    if l_left is not None and l_top is not None:
        base.update({'left': l_left, 'top': l_top})
        # Detect orientation from saved position or fall back to pos-based
        if pos in ('left', 'right'):
            base['orient'] = 'vertical'
        return base

    # Default dock positions — offset shifts the legend away from its edge
    offset = int(legend_offset or 0)
    if pos == 'top':
        base.update({'top': max(0, 4 + offset), 'left': 'center'})
    elif pos == 'left':
        base.update({'left': max(0, 4 + offset), 'top': 'middle', 'orient': 'vertical'})
    elif pos == 'right':
        base.update({'right': max(0, 4 + offset), 'top': 'middle', 'orient': 'vertical'})
    else:  # bottom
        base.update({'bottom': max(0, offset), 'left': 'center'})
    return base


def _resolve_color_map(cfg: dict, series_names: list[str]) -> dict[str, str]:
    """Build a merged colour map: user overrides > semantic defaults.

    Returns ``{series_name: '#hex'}`` for every name that has a mapping.
    """
    merged: dict[str, str] = {}
    # Layer 1 — semantic defaults (lowest priority)
    for name in series_names:
        sn = str(name).strip()
        if sn in SEMANTIC_COLORS:
            merged[sn] = SEMANTIC_COLORS[sn]
    # Layer 2 — user‑defined color_map (highest priority, overrides semantic)
    user_map = cfg.get('color_map') or {}
    if isinstance(user_map, dict):
        for k, v in user_map.items():
            if isinstance(v, str) and v.startswith('#'):
                merged[str(k).strip()] = v
    return merged


def _apply_color_map(option: dict, cfg: dict) -> None:
    """Mutate *option* in-place: override palette colours so that each series
    uses its mapped colour (from user color_map or semantic defaults).

    Works for all chart types: bar, line, pie, scatter, etc.
    """
    series = option.get('series', [])
    if not series:
        return
    names = [str(s.get('name', '')).strip() for s in series]
    cmap = _resolve_color_map(cfg, names)
    if not cmap:
        return

    palette = option.get('color', PALETTES.get(cfg.get('palette', 'default'), PALETTES['default']))
    # Build a new colour list where mapped names use their fixed colour,
    # un-mapped names cycle through the palette.
    pal_idx = 0
    new_colors: list[str] = []
    for name in names:
        if name in cmap:
            new_colors.append(cmap[name])
        else:
            new_colors.append(palette[pal_idx % len(palette)])
            pal_idx += 1
    option['color'] = new_colors

    # For pie/donut charts push colour into each data item directly
    for s in series:
        if s.get('type') == 'pie':
            data = s.get('data', [])
            for item in data:
                item_name = str(item.get('name', '')).strip()
                if item_name in cmap:
                    item.setdefault('itemStyle', {})['color'] = cmap[item_name]


def _base_option(cfg: dict, axis_trigger: bool = True) -> dict:
    t = _theme(cfg)
    title_text = cfg.get('chart_title', '') or cfg.get('title', '')
    show_toolbox = cfg.get('show_toolbox', True) is not False
    show_tooltip = cfg.get('show_tooltip', True) is not False
    animate      = cfg.get('animation', True) is not False
    return {
        'animation': animate,
        'color': t['colors'],
        'backgroundColor': t['bg'],
        'aria': {'enabled': True},
        'textStyle': {'color': t['text'], 'fontFamily': 'Inter, system-ui, sans-serif'},
        'title': {
            'show': bool(title_text),
            'text': title_text,
            'left': cfg.get('title_left', 'center') if cfg.get('title_left') else 'center',
            'top':  cfg.get('title_top',  'auto')   if cfg.get('title_top')  else 'auto',
            'textStyle': {'color': t['text'], 'fontSize': int(cfg.get('title_font_size', 16) or 16), 'fontWeight': 600},
        },
        'legend': _legend(cfg, t['text']),
        'tooltip': {
            'show': show_tooltip,
            'trigger': 'axis' if axis_trigger else 'item',
            'axisPointer': {'type': 'shadow'} if axis_trigger else {},
        },
        'toolbox': {
            'show': show_toolbox, 'right': 8,
            'feature': {
                'dataZoom': {'yAxisIndex': 'none'},
                'dataView': {'readOnly': True},
                'restore': {},
                'saveAsImage': {},
            },
        },
        'grid': {
            'left':   int(cfg.get('grid_left',   55) or 55),
            'right':  int(cfg.get('grid_right',  20) or 20),
            'top':    int(cfg.get('grid_top',    60 if title_text else 36) or (60 if title_text else 36)),
            'bottom': int(cfg.get('grid_bottom', 80) or 80),
            'containLabel': True,
        },
    }


def _axis_style(cfg: dict) -> dict:
    """Return shared axis style dict (used for both x and y axes)."""
    t = _theme(cfg)
    show_grid   = cfg.get('show_grid', True) is not False
    show_labels = cfg.get('show_axis_labels', True) is not False
    return {
        'axisLine':  {'lineStyle': {'color': t['grid']}},
        'axisTick':  {'lineStyle': {'color': t['grid']}},
        'axisLabel': {'show': show_labels, 'color': t['text']},
        'splitLine': {'show': show_grid, 'lineStyle': {'color': t['grid']}},
    }


def _x_axis_label_style(cfg: dict) -> dict:
    """Return axisLabel overrides for the category axis.

    ``show_categories`` controls whether category text labels are visible.
    It is intentionally independent of ``show_axis_labels`` (which controls
    the value-axis numbers).  The axis line/tick/grid remain visible
    regardless of either setting.
    """
    t = _theme(cfg)
    show_categories = cfg.get('show_categories', True) is not False
    rotation    = int(cfg.get('x_label_rotation', 0) or 0)
    font_size   = int(cfg.get('x_label_font_size', 11) or 11)
    max_len     = int(cfg.get('x_label_max_length', 0) or 0)

    lbl: dict = {'show': show_categories, 'color': t['text'], 'fontSize': font_size}
    if rotation:
        lbl['rotate'] = rotation
    if max_len > 0:
        lbl['__formatter_js__'] = (
            f'function(v){{var s=String(v);return s.length>{max_len}?s.slice(0,{max_len})+"…":s;}}'
        )
    return lbl


def _axis_name(name: str, cfg: dict) -> str:
    return name if cfg.get('show_axis_titles', True) is not False else ''


def _percent_decimals(cfg: dict) -> int:
    try:
        return max(0, min(6, int(cfg.get('percent_decimals', 1) or 1)))
    except (TypeError, ValueError):
        return 1


def _show_percent_symbol(cfg: dict) -> bool:
    return cfg.get('show_percent_symbol', True) is not False


def _percent_suffix(cfg: dict) -> str:
    return '%' if _show_percent_symbol(cfg) else ''


def _tooltip_show_pct(cfg: dict) -> bool:
    return cfg.get('tooltip_show_pct', True) is not False


def _tooltip_show_count(cfg: dict) -> bool:
    return cfg.get('tooltip_show_count', True) is not False


def _tooltip_value_pct(cfg: dict) -> bool:
    """When True, append '%' after the raw value in tooltips (useful for AVG of percentage data)."""
    return cfg.get('tooltip_value_pct', False) is True


def _rich_tooltip_js(raw_lookup: dict, totals_lookup: dict,
                     percent_decimals: int, percent_suffix: str,
                     show_pct: bool = True, show_count: bool = True,
                     value_pct_suffix: str = '') -> str:
    """Return a JS axis-tooltip formatter that shows count + % + (n=total) per category.

    ``raw_lookup``   – {series_name: {category: raw_value}}
    ``totals_lookup`` – {category: total_count}
    ``value_pct_suffix`` – when '%', appends '%' after the raw value (for avg % data)
    """
    zero_pct = "'" + ('0.' + ('0' * percent_decimals) if percent_decimals else '0') + "'"
    count_line = (
        "  if (total > 0) html += ' &nbsp;<span style=\"color:#999\">(n=' + total + ')</span>';"
        if show_count else ""
    )
    pct_fragment = (
        f" + ' (' + pct + '%)';"
        if show_pct else ";"
    )
    pct_calc = (
        f"    var pct = total > 0 ? (cnt / total * 100).toFixed({percent_decimals}) : {zero_pct};"
        if show_pct else ""
    )
    val_display = f"cnt + '{value_pct_suffix}'" if value_pct_suffix else "cnt"
    return (
        "function(params) {"
        "  if (!Array.isArray(params)) params = [params];"
        f"  var rawLookup = {json.dumps(raw_lookup)};"
        f"  var totals = {json.dumps(totals_lookup)};"
        "  var cat = params[0].name || '';"
        "  var total = totals[cat] || 0;"
        "  var html = '<b>' + cat + '</b>';"
        f"{count_line}"
        "  for (var i = params.length - 1; i >= 0; i--) {"
        "    var d = params[i];"
        "    var cnt = (rawLookup[d.seriesName] || {})[cat] || 0;"
        f"{pct_calc}"
        f"    html += '<br/>' + d.marker + ' ' + d.seriesName + ': <b>' + {val_display} + '</b>'{pct_fragment}"
        "  }"
        "  return html;"
        "}"
    )


def _inline_pct_tooltip_js(percent_decimals: int = 1, percent_suffix: str = '%',
                           grand_total: float | None = None,
                           show_pct: bool = True, show_count: bool = True,
                           value_pct_suffix: str = '') -> str:
    """Return a JS axis-tooltip formatter that computes % client-side from the params array.
    If grand_total is provided (single-series), it is embedded and used as the denominator.
    Otherwise the sum of all series values for the axis category is used.
    ``value_pct_suffix`` – when '%', appends '%' after the raw value (for avg % data)
    """
    zero_pct = "'" + ('0.' + ('0' * percent_decimals) if percent_decimals else '0') + "'"
    if grand_total is not None:
        total_expr = str(round(grand_total, 6))
    else:
        total_expr = "params.reduce(function(s,d){return s+(Number(d.value)||0);},0)"
    count_line = (
        "  if (total > 0) html += ' &nbsp;<span style=\"color:#999\">(n=' + Math.round(total) + ')</span>';"
        if show_count else ""
    )
    pct_calc = (
        f"    var pct = total > 0 ? (v / total * 100).toFixed({percent_decimals}) : {zero_pct};"
        if show_pct else ""
    )
    pct_fragment = (
        f" + ' (' + pct + '%)';"
        if show_pct else ";"
    )
    val_display = f"v + '{value_pct_suffix}'" if value_pct_suffix else "v"
    return (
        "function(params) {"
        "  if (!Array.isArray(params)) params = [params];"
        "  var cat = params[0].name || '';"
        f"  var total = {total_expr};"
        "  var html = '<b>' + cat + '</b>';"
        f"{count_line}"
        "  for (var i = params.length - 1; i >= 0; i--) {"
        "    var d = params[i];"
        "    if (d.value == null) continue;"
        "    var v = Number(d.value) || 0;"
        f"{pct_calc}"
        f"    html += '<br/>' + d.marker + ' ' + d.seriesName + ': <b>' + {val_display} + '</b>'{pct_fragment}"
        "  }"
        "  return html;"
        "}"
    )


def _ordered_categories(categories: list[str], cfg: dict, value_map: dict | None = None) -> list[str]:
    cats = [str(c) for c in categories]
    mode = (cfg.get('sort_col') or '').strip()
    reverse = str(cfg.get('sort_dir', 'asc')).lower() == 'desc'
    manual = [str(c) for c in (cfg.get('category_order') or []) if str(c) in cats]
    if mode == '__manual__':
        return manual + [c for c in cats if c not in manual]
    if mode == '_value' and value_map:
        return sorted(cats, key=lambda c: (float(value_map.get(c, 0) or 0), c), reverse=reverse)
    if mode and mode != '_value':
        return sorted(cats, reverse=reverse)
    return cats


def _add_zoom(option: dict, cfg: dict = None, axis: str = 'x') -> None:
    if cfg and cfg.get('show_zoom', True) is False:
        return
    axis_index_key = 'yAxisIndex' if axis == 'y' else 'xAxisIndex'
    option['dataZoom'] = [
        {'type': 'inside', axis_index_key: 0},
        {'type': 'slider', axis_index_key: 0, 'bottom': 14},
    ]


def _ordered_stacks(raw_stacks: list[str], cfg: dict) -> list[str]:
    """Return stack values respecting the user-defined stack_order when present."""
    order = cfg.get('stack_order') or []
    if not order:
        return sorted(raw_stacks)
    # Items in the user order come first (preserve order), then any extras alphabetically
    ordered = [s for s in order if s in raw_stacks]
    extras  = sorted(s for s in raw_stacks if s not in ordered)
    return ordered + extras


def _message_option(text: str, cfg: dict, error: bool = False) -> dict:
    opt = _base_option(cfg, axis_trigger=False)
    opt['xAxis'] = {'show': False}
    opt['yAxis'] = {'show': False}
    opt['series'] = []
    opt['title']['show'] = True
    opt['title']['text'] = text
    opt['title']['textStyle']['color'] = '#dc2626' if error else _theme(cfg)['text']
    opt['title']['top'] = 'middle'
    return opt


# ── Chart type builders ───────────────────────────────────────────────────────

def _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, stacked, col_defs):
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
        label_cfg = {'show': bool(cfg.get('data_labels', False)), 'position': label_pos}
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
        if radial:
            option.pop('grid', None)
            option.update({
                'angleAxis': {'type': 'category', 'data': categories, 'name': _axis_name(labels['x'], cfg), **ax, 'axisLabel': x_lbl},
                'radiusAxis': {'type': 'value', 'name': _axis_name(y_axis_name, cfg), **ax},
                'polar': {'radius': '70%'},
                'series': series,
            })
            option.setdefault('dv', {})['category_axis'] = 'angle'
            return
        if polar:
            option.pop('grid', None)
            option.update({
                'radiusAxis': {'type': 'category', 'data': categories, 'name': _axis_name(labels['x'], cfg), **ax, 'axisLabel': x_lbl},
                'angleAxis': {'type': 'value', 'name': _axis_name(y_axis_name, cfg), **ax},
                'polar': {'radius': '70%'},
                'series': series,
            })
            option.setdefault('dv', {})['category_axis'] = 'radius'
            return
        if orientation == 'horizontal':
            option.update({
                'xAxis': {'type': 'value', 'name': _axis_name(y_axis_name, cfg), **ax},
                'yAxis': {'type': 'category', 'data': categories, 'name': _axis_name(labels['x'], cfg), **ax, 'axisLabel': x_lbl},
                'series': series,
            })
            option.setdefault('dv', {})['category_axis'] = 'y'
            return
        option.update({
            'xAxis': {'type': 'category', 'data': categories, 'name': _axis_name(labels['x'], cfg), **ax, 'axisLabel': x_lbl},
            'yAxis': {'type': 'value', 'name': _axis_name(y_axis_name, cfg), **ax},
            'series': series,
        })
        option.setdefault('dv', {})['category_axis'] = 'x'

    if len(valid_y) > 1:
        categories = sorted(df[x_col].fillna('(Blank)').astype(str).unique().tolist())
        series_value_maps = {}
        series = []
        for mc in valid_y:
            col_agg = agg if pd.api.types.is_numeric_dtype(df[mc]) and agg != 'count' else 'count'
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
        tooltip_js = _rich_tooltip_js(raw_lookup, totals_lookup, percent_decimals, percent_suffix,
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
                                                                         value_pct_suffix='%' if _tooltip_value_pct(cfg) else '')}
    option['__category_names__'] = categories if 'categories' in locals() else ordered_categories
    if (radial or polar) and isinstance(option.get('tooltip'), dict):
        option['tooltip']['axisPointer'] = {'type': 'line'}
    if not radial and not polar:
        _add_zoom(option, cfg, axis='y' if orientation == 'horizontal' else 'x')
    return option


def _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, fill, stacked, y_cols=None, col_defs=None, smooth=False):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)
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
        return {
            'name': name, 'type': 'line', 'smooth': smooth,
            'stack': 'total' if stk else None,
            'areaStyle': {} if fill else None,
            'showSymbol': show_pts, 'symbolSize': 6 if show_pts else 0,
            'lineStyle': {'opacity': opacity}, 'itemStyle': {'opacity': opacity},
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
        option.update({'xAxis': {'type': 'category', 'data': categories, 'name': _axis_name(labels['x'], cfg), **ax, 'axisLabel': x_lbl},
                       'yAxis': {'type': 'value', 'name': _axis_name('Value', cfg), **ax}, 'series': series,
                       'tooltip': {'show': cfg.get('show_tooltip', True) is not False, 'trigger': 'axis', 'axisPointer': {'type': 'shadow'},
                                   '__formatter_js__': _rich_tooltip_js(raw_lookup, totals_lookup, percent_decimals, percent_suffix,
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
        option.update({'xAxis': {'type': 'category', 'data': categories, 'name': _axis_name(labels['x'], cfg), **ax, 'axisLabel': x_lbl},
                       'yAxis': {'type': 'value', 'name': _axis_name(labels['y'] or 'Count', cfg), **ax}, 'series': series,
                       'tooltip': {'show': cfg.get('show_tooltip', True) is not False, 'trigger': 'axis', 'axisPointer': {'type': 'shadow'},
                                   '__formatter_js__': _rich_tooltip_js(raw_lookup, totals_lookup, percent_decimals, percent_suffix,
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
                                           value_pct_suffix='%' if _tooltip_value_pct(cfg) else '')
        option.update({'xAxis': {'type': 'category', 'data': categories, 'name': _axis_name(labels['x'], cfg), **ax, 'axisLabel': x_lbl},
                       'yAxis': {'type': 'value', 'name': _axis_name(labels['y'] or 'Count', cfg), **ax},
                       'series': [make_series(labels['y'] or 'Value', ordered_vals)],
                       'tooltip': {'show': cfg.get('show_tooltip', True) is not False, 'trigger': 'axis', 'axisPointer': {'type': 'shadow'},
                                   '__formatter_js__': _ltjs}})
    option['__category_names__'] = categories
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
        'label': {'show': bool(cfg.get('data_labels', False))},
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
        'series': [{'type': 'heatmap', 'data': points, 'label': {'show': bool(cfg.get('data_labels', False))}}],
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

    option.update({'xAxis': {'type': 'category', 'data': lbls, 'name': _axis_name(labels['x'], cfg), **ax, 'axisLabel': x_lbl},
                   'yAxis': {'type': 'value', 'name': _axis_name(labels['y'], cfg), **ax},
                   'series': [{'type': 'boxplot', 'data': data}]})
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
                           'sort': 'descending', 'label': {'show': True, 'position': 'inside'},
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
    value = {'sum': float(vals.sum()), 'avg': float(vals.mean()), 'min': float(vals.min()),
             'max': float(vals.max()), 'median': float(vals.median())}.get(agg, float(len(df)))
    _ad = cfg.get('agg_decimals')
    if _ad is not None and agg in ('avg', 'sum', 'min', 'max', 'median'):
        try:
            value = round(value, int(_ad))
        except (ValueError, TypeError):
            pass
    max_v = float(vals.max()) * 1.1 if agg != 'count' else float(max(len(df), 1)) * 1.2
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
                          'label': {'show': bool(cfg.get('data_labels', False))}}]
    option['__category_names__'] = categories
    return option


def _auto_detect_pl_scale(values) -> list:
    """Try to order PL values using known scales; fall back to alphabetical."""
    # Ensure all values are strings and filter out empty/null ones, but keep '0'
    val_set = {str(v).strip() for v in values if v is not None and str(v).strip() != ''}
    for scale in _KNOWN_PL_SCALES:
        if val_set.issubset(set(scale)):
            return [s for s in scale if s in val_set]
    # Final fallback: guaranteed string sort to avoid TypeError: '<' not supported between 'float' and 'str'
    return sorted(list(val_set))


def _summary_table_option(df, row_col, col_col, cfg, col_defs):
    """Return a special __type__=table option: count cross-tabulation.

    ``row_col`` — the column whose distinct values become row labels.
    ``col_col`` — the column whose distinct values become column headers.

    The returned dict uses ``__type__: 'table'`` to signal the JS renderer
    to build an HTML table instead of an ECharts instance.
    """
    _BLANK = frozenset({'', '-', '\u2014', '\u2013', 'N/A', 'n/a', 'None', 'null', 'NULL'})

    if not row_col or row_col not in df.columns:
        return _message_option('Summary Table: select a Row column', cfg)
    if not col_col or col_col not in df.columns:
        return _message_option('Summary Table: select a Column column', cfg)

    mask = (
        ~df[row_col].astype(str).str.strip().isin(_BLANK) &
        ~df[col_col].astype(str).str.strip().isin(_BLANK)
    )
    _dup_key = next((c for c in ('ssid', 'student_id', 'stu_annual_key') if c in df.columns), None)
    keep_cols = [row_col, col_col] + ([_dup_key] if _dup_key else [])
    work = df.loc[mask, keep_cols].copy()
    work[row_col] = work[row_col].astype(str).str.strip()
    work[col_col] = work[col_col].astype(str).str.strip()

    if work.empty:
        return _message_option('Summary Table: no data after filtering blanks', cfg)

    # Deduplicate so each student is counted at most once per (row_col, col_col) cell.
    if _dup_key:
        work = work.drop_duplicates(subset=[_dup_key, row_col, col_col], keep='first')

    # Use auto-detection to get properly ordered PL levels if applicable, 
    # and to ensure all values are strings for sorting.
    row_vals = _auto_detect_pl_scale(work[row_col].unique())
    col_vals = _auto_detect_pl_scale(work[col_col].unique())

    ct = work.groupby([row_col, col_col]).size().unstack(fill_value=0)
    ct = ct.reindex(index=row_vals, columns=col_vals, fill_value=0)

    col_totals = [0] * len(col_vals)
    rows_data = []
    for rv in row_vals:
        counts = [int(ct.at[rv, cv]) for cv in col_vals]
        for i, v in enumerate(counts):
            col_totals[i] += v
        rows_data.append([rv] + counts + [sum(counts)])

    grand_total = sum(col_totals)
    total_row = ['Total'] + col_totals + [grand_total]

    row_label = _label(row_col, col_defs) if col_defs else row_col
    col_label = _label(col_col, col_defs) if col_defs else col_col

    return {
        '__type__': 'table',
        '__series_names__': [],
        'row_col':     row_col,
        'col_col':     col_col,
        'row_label':   row_label,
        'col_label':   col_label,
        'col_headers': col_vals,
        'rows':        rows_data,
        'total_row':   total_row,
        '_drillMeta': {
            'stType':      'summary_table',
            'stRowCol':    row_col,
            'stColCol':    col_col,
            'colHeaders':  col_vals,
        },
    }


def _progress_growth_option(df, x_col, pg_before_col, pg_after_col, pl_scale, pg_display, cfg, labels):
    """Progress Growth: classify students as Growth / Sliders / Stickers.

    Compares two PL columns per student using a user-defined (or auto-detected)
    ordered scale.  Students whose PL improved are "Growth", declined are
    "Sliders", unchanged are "Stickers".

    Display modes (``pg_display``):
      stacked_bar  — bars stacked by Growth / Sliders / Stickers, x = group
      grouped_bar  — same but side-by-side
      donut        — overall doughnut distribution
      pie          — overall pie distribution
    """
    if not pg_before_col or not pg_after_col:
        return _message_option('Select a Before PL and an After PL column', cfg, error=True)
    if pg_before_col not in df.columns or pg_after_col not in df.columns:
        missing = [c for c in (pg_before_col, pg_after_col) if c not in df.columns]
        return _message_option(f'PL column(s) not found in data: {", ".join(missing)}', cfg, error=True)

    # Deduplicate rows so each student is counted at most once.
    # The underlying query may return a student on more than one row (e.g.
    # when current_student has multiple enrolment entries for the same year).
    _dup_key = next((c for c in ('ssid', 'student_id', 'stu_annual_key') if c in df.columns), None)
    if _dup_key:
        df = df.drop_duplicates(subset=_dup_key, keep='first')

    _BLANK = frozenset({'', '-', '\u2014', '\u2013', 'N/A', 'n/a', 'None', 'null', 'NULL'})

    # Collect valid PL values
    all_vals = set()
    for col in (pg_before_col, pg_after_col):
        all_vals.update(
            v.strip() for v in df[col].dropna().astype(str).unique()
            if v.strip() not in _BLANK
        )

    # Build ordered scale
    if pl_scale:
        ordered = [str(v).strip() for v in pl_scale if str(v).strip()]
    else:
        ordered = _auto_detect_pl_scale(list(all_vals))
    if not ordered:
        return _message_option('Could not determine PL scale — define it in the builder', cfg, error=True)

    rank = {v: i for i, v in enumerate(ordered)}

    # Classify each row
    dfc = df.copy()
    dfc['_before'] = dfc[pg_before_col].astype(str).str.strip()
    dfc['_after']  = dfc[pg_after_col].astype(str).str.strip()
    valid = dfc['_before'].isin(rank) & dfc['_after'].isin(rank)
    dfc = dfc[valid].copy()

    if dfc.empty:
        return _message_option(
            'No rows matched the PL scale. Check column names and scale values.', cfg, error=True)

    before_rank = dfc['_before'].map(rank)
    after_rank  = dfc['_after'].map(rank)
    dfc['_pg'] = 'Stickers'
    dfc.loc[after_rank > before_rank, '_pg'] = 'Growth'
    dfc.loc[after_rank < before_rank, '_pg'] = 'Sliders'

    ORDER = ['Growth', 'Sliders', 'Stickers']   # consistent display order
    total = len(dfc)
    counts = dfc['_pg'].value_counts()
    opacity = float(cfg.get('opacity', 0.9) or 0.9)
    border_radius = int(cfg.get('bar_border_radius', 0) or 0)
    show_labels = bool(cfg.get('data_labels', False))
    label_pos = cfg.get('label_position', 'top') or 'top'
    show_percent = bool(cfg.get('show_percent', False))
    percent_decimals = _percent_decimals(cfg)
    percent_suffix = _percent_suffix(cfg)
    t = _theme(cfg)
    ax = _axis_style(cfg)

    # ── Donut / Pie ───────────────────────────────────────────────────────────
    if pg_display in ('donut', 'pie'):
        option = _base_option(cfg, axis_trigger=False)
        data = [
            {'name': cat, 'value': int(counts.get(cat, 0)),
             'itemStyle': {'color': _PG_COLORS[cat]}}
            for cat in ORDER if counts.get(cat, 0) > 0
        ]
        _sp = _tooltip_show_pct(cfg)
        _zero = '0.' + '0' * percent_decimals if percent_decimals else '0'
        if _sp:
            tooltip_js = (
                "function(params) {"
                f"  var total = {total};"
                f"  var pct = total > 0 ? (params.value / total * 100).toFixed({percent_decimals}) : '{_zero}';"
                "  return params.marker + ' ' + params.name + ': <b>' + params.value"
                f"       + '</b> (' + pct + '%)';"
                "}"
            )
        else:
            tooltip_js = (
                "function(params) {"
                "  return params.marker + ' ' + params.name + ': <b>' + params.value + '</b>';"
                "}"
            )
        option['color'] = [_PG_COLORS[cat] for cat in ORDER]
        option['series'] = [{
            'name': 'Progress Growth',
            'type': 'pie',
            'radius': ['45%', '70%'] if pg_display == 'donut' else '70%',
            'data': data,
            'label': {'show': bool(cfg.get('data_labels', True)), 'formatter': '{b}: {c}'},
        }]
        option['tooltip'] = {
            'show': cfg.get('show_tooltip', True) is not False,
            'trigger': 'item',
            '__formatter_js__': tooltip_js,
        }
        return option

    # ── Transition Matrix ────────────────────────────────────────────────────
    if pg_display == 'matrix':
        # Cross-tabulation: y-axis rows = Before PL, x-axis columns = After PL.
        # ECharts category y-axis: index 0 at bottom, so ordered[0] (lowest)
        # appears at the bottom and ordered[-1] (highest) at the top.
        crossdata = []
        for i, before in enumerate(ordered):
            for j, after in enumerate(ordered):
                count = int(((dfc['_before'] == before) & (dfc['_after'] == after)).sum())
                if j > i:       # after rank > before rank → Growth
                    color = _PG_COLORS['Growth']
                    alpha = 0.85 if count > 0 else 0.10
                elif j < i:     # after rank < before rank → Sliders
                    color = _PG_COLORS['Sliders']
                    alpha = 0.85 if count > 0 else 0.10
                else:            # diagonal → Stickers
                    color = _PG_COLORS['Stickers']
                    alpha = 0.75 if count > 0 else 0.12
                crossdata.append({
                    'value': [j, i, count],
                    'itemStyle': {
                        'color': color, 'opacity': alpha,
                        'borderColor': t['bg'], 'borderWidth': 2,
                    },
                })
        tooltip_js = (
            "function(params) {"
            f"  var lvls = {json.dumps(ordered)};"
            "  var v = params.value;"
            "  var bef = lvls[v[1]], aft = lvls[v[0]], cnt = v[2];"
            "  var rel = v[0] > v[1] ? '\U0001f331 Growth' : v[0] < v[1] ? '\U0001f4c9 Sliders' : '\U0001f4cc Stickers';"
            "  return '<b>' + bef + ' \u2192 ' + aft + '</b><br/>' + rel + ': <b>' + cnt + '</b> students';"
            "}"
        )
        label_js = "function(p) { return p.value[2] > 0 ? p.value[2] : ''; }"
        mat_opt = _base_option(cfg, axis_trigger=False)
        mat_opt['legend'] = {
            'show': True,
            'bottom': 0,
            'data': [
                {'name': 'Growth',   'icon': 'circle', 'itemStyle': {'color': _PG_COLORS['Growth']}},
                {'name': 'Stickers', 'icon': 'circle', 'itemStyle': {'color': _PG_COLORS['Stickers']}},
                {'name': 'Sliders',  'icon': 'circle', 'itemStyle': {'color': _PG_COLORS['Sliders']}},
            ],
            'textStyle': {'color': t['text']},
        }
        mat_opt['grid'] = {
            'top': 50 if cfg.get('title') else 30,
            'left': 10, 'right': 10, 'bottom': 55,
            'containLabel': True,
        }
        mat_opt.update({
            'xAxis': {
                'type': 'category',
                'data': ordered,
                'name': 'After PL',
                'nameLocation': 'middle',
                'nameGap': 42,
                'nameTextStyle': {'fontWeight': 'bold', 'color': t['text']},
                **ax,
            },
            'yAxis': {
                'type': 'category',
                'data': ordered,
                'name': 'Before PL',
                'nameLocation': 'middle',
                'nameGap': 60,
                'nameTextStyle': {'fontWeight': 'bold', 'color': t['text']},
                **ax,
            },
            'series': [{
                'type': 'heatmap',
                'data': crossdata,
                'label': {
                    'show': True,
                    '__formatter_js__': label_js,
                    'fontSize': 13,
                    'fontWeight': 'bold',
                    'color': '#fff',
                },
            }],
            'tooltip': {
                'show': cfg.get('show_tooltip', True) is not False,
                'trigger': 'item',
                '__formatter_js__': tooltip_js,
            },
        })
        mat_opt['_drillMeta'] = {
            'pgDisplay': 'matrix',
            'xLabels': ordered,   # After PL — x-axis
            'yLabels': ordered,   # Before PL — y-axis
        }
        return mat_opt

    # ── Stacked / Grouped Bar ─────────────────────────────────────────────────
    option = _base_option(cfg)
    stacked = pg_display != 'grouped_bar'

    def make_series(cat, data_vals):
        label_cfg = {'show': show_labels, 'position': label_pos}
        if show_percent:
            label_cfg['__formatter_js__'] = (
                f"function(p){{var v=Number(p.value||0);return v.toFixed({percent_decimals})+'{percent_suffix}';}}"
            )
        s = {
            'name': cat,
            'type': 'bar',
            'stack': 'total' if stacked else None,
            'label': label_cfg,
            'itemStyle': {'color': _PG_COLORS[cat], 'opacity': opacity},
            'data': data_vals,
        }
        if border_radius:
            s['itemStyle']['borderRadius'] = [border_radius, border_radius, 0, 0]
        return s

    if x_col and x_col in dfc.columns:
        categories = sorted(dfc[x_col].fillna('(Blank)').astype(str).unique().tolist())
        raw = {}
        for cat in ORDER:
            sub = dfc[dfc['_pg'] == cat]
            raw[cat] = {k: int(v) for k, v in sub[x_col].fillna('(Blank)').astype(str).value_counts().items()}
        cat_totals = {xv: int(sum(raw[cat].get(xv, 0) for cat in ORDER)) for xv in categories}
        categories = _ordered_categories(categories, cfg, cat_totals)

        series = []
        for cat in ORDER:
            if show_percent:
                vals = [
                    round(raw[cat].get(xv, 0) / cat_totals[xv] * 100, percent_decimals)
                    if cat_totals.get(xv, 0) > 0 else 0.0
                    for xv in categories
                ]
            else:
                vals = [float(raw[cat].get(xv, 0)) for xv in categories]
            series.append(make_series(cat, vals))

        _sp2 = _tooltip_show_pct(cfg)
        _sc2 = _tooltip_show_count(cfg)
        _vp2 = _tooltip_value_pct(cfg)
        _zero2 = '0.' + '0' * percent_decimals if percent_decimals else '0'
        _count_line2 = (
            "  if (total > 0) html += ' <span style=\"color:#999\">(n=' + total + ')</span>';"
            if _sc2 else ""
        )
        _pct_calc2 = (
            f"    var pct = total > 0 ? (cnt / total * 100).toFixed({percent_decimals}) : '{_zero2}';"
            if _sp2 else ""
        )
        _pct_frag2 = " + ' (' + pct + '%)';" if _sp2 else ";"
        _val_disp2 = "cnt + '%'" if _vp2 else "cnt"
        tooltip_js = (
            "function(params) {"
            "  if (!Array.isArray(params)) params = [params];"
            f"  var raw = {json.dumps({cat: {xv: raw[cat].get(xv, 0) for xv in categories} for cat in ORDER})};"
            f"  var totals = {json.dumps({xv: cat_totals[xv] for xv in categories})};"
            "  var cat = params[0].name || '';"
            "  var total = totals[cat] || 0;"
            "  var html = '<b>' + cat + '</b>';"
            f"{_count_line2}"
            "  for (var i = 0; i < params.length; i++) {"
            "    var d = params[i];"
            "    var cnt = (raw[d.seriesName] || {})[cat] || 0;"
            f"{_pct_calc2}"
            f"    html += '<br/>' + d.marker + ' ' + d.seriesName + ': <b>' + {_val_disp2} + '</b>'{_pct_frag2}"
            "  }"
            "  return html;"
            "}"
        )
        y_axis = {'type': 'value', 'name': _axis_name('%' if show_percent else 'Students', cfg), **ax}
        if show_percent:
            y_axis['max'] = 100
        option.update({
            'xAxis': {'type': 'category', 'data': categories, 'name': _axis_name(labels.get('x', ''), cfg), **ax},
            'yAxis': y_axis,
            'series': series,
            'tooltip': {
                'show': cfg.get('show_tooltip', True) is not False,
                'trigger': 'axis',
                'axisPointer': {'type': 'shadow'},
                '__formatter_js__': tooltip_js,
            },
        })
    else:
        # No grouping — overall counts as a simple coloured bar
        overall_counts = [float(counts.get(cat, 0)) for cat in ORDER]
        option.update({
            'xAxis': {'type': 'category', 'data': ORDER, 'name': _axis_name(labels.get('x', ''), cfg), **ax},
            'yAxis': {'type': 'value', 'name': _axis_name('Students', cfg), **ax},
            'series': [{
                'name': 'Students',
                'type': 'bar',
                'data': [
                    {'value': v, 'itemStyle': {'color': _PG_COLORS[cat], 'opacity': opacity}}
                    for cat, v in zip(ORDER, overall_counts)
                ],
                'label': {'show': show_labels, 'position': label_pos},
            }],
        })

    option['__category_names__'] = categories if x_col and x_col in dfc.columns else ORDER
    _add_zoom(option, cfg)
    return option


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

    # ── Build flat x-axis: one position per (category, group) — no spacers ──
    x_labels = []          # xAxis[0] labels: window/year
    cat_for_pos = []       # category name per position (for drill-through)
    col_for_pos = []       # PL column name per position (for drill-through)
    group_ranges = []      # [(start, end, cat_name), ...] for xAxis[1] labels

    flat_pos = 0
    for ci, cat in enumerate(base_categories):
        group_start = flat_pos
        for gi, col in enumerate(ordered_cols):
            x_labels.append(group_labels[col])
            cat_for_pos.append(cat)
            col_for_pos.append(col)
            flat_pos += 1
        group_end = flat_pos - 1
        group_ranges.append((group_start, group_end, cat))

    total_positions = flat_pos

    # Pre-compute raw counts per position
    raw_counts = {}    # {pos: {pv: count}}
    for pos in range(total_positions):
        ci = pos // n_groups
        gi = pos % n_groups
        cat = base_categories[ci]
        col = ordered_cols[gi]
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
            'fontSize': 10,
            'show': cfg.get('show_categories', True) is not False,
        },
        'axisTick': {'show': False},
        'axisLine': {'show': False},
        'splitLine': {'show': False},
    }

    # ── xAxis[1]: category labels, one per group, centred ──
    cat_labels_for_axis = [''] * total_positions
    for start, end, cat_name in group_ranges:
        mid = int(round((start + end) / 2.0))
        if 0 <= mid < total_positions:
            cat_labels_for_axis[mid] = cat_name

    cat_formatter_js = "function(value) { return value || ''; }"

    x_axis_1 = {
        'type': 'category',
        'data': cat_labels_for_axis,
        'position': 'bottom',
        'offset': 22,
        'axisLabel': {
            'interval': 0,
            'fontSize': cat_font_size,
            'fontWeight': 'bold',
            'color': ax.get('axisLabel', {}).get('color', '#475569'),
            'rotate': cat_rotation,
            'margin': 4,
            'show': cfg.get('show_categories', True) is not False,
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


# ── Public entry point ────────────────────────────────────────────────────────

def build_option(rows: list, col_defs: dict, cfg: dict) -> dict:
    """Build an ECharts 6 option dict from raw rows + column definitions + chart config."""
    if not rows:
        return _message_option('No data loaded', cfg)

    chart_type   = cfg.get('chart_type', 'bar')
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
    y_col        = cfg.get('y_col', '')
    y_cols_raw   = cfg.get('y_cols', [])
    stack_col    = cfg.get('stack_col', '')
    compare_pl_cols = cfg.get('compare_pl_cols', [])
    size_col     = cfg.get('size_col', '')
    agg          = str(cfg.get('agg', 'count') or 'count').lower()
    if agg not in {'count', 'sum', 'avg', 'min', 'max', 'median'}:
        agg = 'count'
    _raw_decimals = cfg.get('agg_decimals')
    agg_decimals  = int(_raw_decimals) if _raw_decimals is not None else None
    # Progress Growth extra fields
    pg_before_col = cfg.get('pg_before_col', '')
    pg_after_col  = cfg.get('pg_after_col', '')
    pl_scale      = cfg.get('pl_scale', [])
    pg_display    = cfg.get('pg_display', 'stacked_bar') or 'stacked_bar'
    # Summary Table extra fields
    st_row_col    = cfg.get('st_row_col', '')
    st_col_col    = cfg.get('st_col_col', '')

    df = pd.DataFrame(rows)

    # Apply row limit before any processing (None = no limit)
    _row_limit = cfg.get('limit')
    if _row_limit and _row_limit > 0:
        df = df.head(_row_limit)

    def _resolve_cfg_col(col):
        if not isinstance(col, str):
            return col
        if col in df.columns:
            return col
        resolved = resolve_relative_column_key(col)
        return resolved if resolved in df.columns else col

    # Resolve relative-year config keys against the loaded dataframe columns.
    x_col = _resolve_cfg_col(x_col)
    y_col = _resolve_cfg_col(y_col)
    stack_col = _resolve_cfg_col(stack_col)
    size_col = _resolve_cfg_col(size_col)
    pg_before_col = _resolve_cfg_col(pg_before_col)
    pg_after_col = _resolve_cfg_col(pg_after_col)
    st_row_col = _resolve_cfg_col(st_row_col)
    st_col_col = _resolve_cfg_col(st_col_col)
    x_cols_raw = [_resolve_cfg_col(c) for c in x_cols_raw] if isinstance(x_cols_raw, list) else x_cols_raw
    y_cols_raw = [_resolve_cfg_col(c) for c in y_cols_raw] if isinstance(y_cols_raw, list) else y_cols_raw
    compare_pl_cols = [_resolve_cfg_col(c) for c in compare_pl_cols] if isinstance(compare_pl_cols, list) else compare_pl_cols

    # Composite x-axis
    x_cols = [c for c in x_cols_raw if isinstance(c, str) and c in df.columns] if isinstance(x_cols_raw, list) else []
    if len(x_cols) > 1:
        df['__x_comp__'] = df[x_cols].fillna('(Blank)').astype(str).agg(' · '.join, axis=1)
        x_col = '__x_comp__'
    elif len(x_cols) == 1 and not x_col:
        x_col = x_cols[0]

    x_col       = x_col       if x_col       in df.columns else ''
    y_col       = y_col       if y_col       in df.columns else ''
    stack_col   = stack_col   if stack_col   in df.columns else ''
    size_col    = size_col    if size_col    in df.columns else ''
    y_cols    = [c for c in y_cols_raw if isinstance(c, str) and c in df.columns] if isinstance(y_cols_raw, list) else []

    # Meta melt
    trigger = _META_DIM_COLS | _META_SCORE_COLS
    if x_col in trigger or stack_col in trigger or y_col in trigger or any(c in trigger for c in y_cols):
        df, y_col, y_cols = _maybe_melt_for_meta(df, x_col, stack_col, y_col, y_cols, col_defs)
        x_col       = x_col       if x_col       in df.columns else ''
        y_col       = y_col       if y_col       in df.columns else ''
        stack_col   = stack_col   if stack_col   in df.columns else ''
        size_col    = size_col    if size_col    in df.columns else ''
        y_cols    = [c for c in y_cols if c in df.columns]

    # Drop rows where key categorical columns contain placeholder / blank values
    # (e.g. '—' assigned by data_loader for students with no test result)
    _BLANK_VALUES = frozenset({'', '-', '\u2014', '\u2013', 'N/A', 'n/a', 'None', 'null', 'NULL', 'nan', 'NAN', 'NaN'})
    for _col in (stack_col, x_col):
        if _col and _col in df.columns:
            # Drop actual NaNs
            df = df.dropna(subset=[_col])
            # Drop blank-like strings
            if not pd.api.types.is_numeric_dtype(df[_col]):
                df = df[~df[_col].astype(str).str.strip().isin(_BLANK_VALUES)]
    if df.empty:
        return _message_option('All rows filtered out (no valid data)', cfg)

    labels = {
        'x': (' · '.join([_label(c, col_defs) for c in x_cols]) if len(x_cols) > 1
              else (_META_LABEL_MAP.get(x_col) or _label(x_col, col_defs)) if x_col else ''),
        'y': (_META_LABEL_MAP.get(y_col) or _label(y_col, col_defs)) if y_col else ('Count' if agg == 'count' else ''),
        's': (_META_LABEL_MAP.get(stack_col) or _label(stack_col, col_defs)) if stack_col else '',
    }

    stacked = cfg.get('stacking_mode', 'normal') == 'percent' or chart_type in ('stacked_bar', 'stacked_area')
    smooth  = chart_type in ('spline', 'areaspline')

    builders = {
        'bar':          lambda: _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, False, col_defs),
        'column':       lambda: _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, False, col_defs),
        'grouped_bar':  lambda: _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, False, col_defs),
        'stacked_bar':  lambda: _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, True,  col_defs),
        'waterfall':    lambda: _waterfall_option(df, x_col, y_col, agg, cfg, labels),
        'line':         lambda: _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, False, False, y_cols, col_defs),
        'spline':       lambda: _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, False, False, y_cols, col_defs, smooth=True),
        'area':         lambda: _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, True,  False, y_cols, col_defs),
        'areaspline':   lambda: _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, True,  False, y_cols, col_defs, smooth=True),
        'stacked_area': lambda: _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, True,  True,  y_cols, col_defs),
        'pie':          lambda: _pie_option(df, x_col, y_col, agg, cfg, donut=False),
        'donut':        lambda: _pie_option(df, x_col, y_col, agg, cfg, donut=True),
        'scatter':      lambda: _scatter_option(df, x_col, y_col, stack_col, size_col, cfg, labels, bubble=False),
        'bubble':       lambda: _scatter_option(df, x_col, y_col, stack_col, size_col, cfg, labels, bubble=True),
        'heatmap':      lambda: _heatmap_option(df, x_col, y_col, agg, cfg, labels),
        'histogram':    lambda: _histogram_option(df, x_col, stack_col, cfg, labels),
        'box':          lambda: _boxplot_option(df, x_col, y_col, cfg, labels),
        'funnel':       lambda: _funnel_option(df, x_col, y_col, agg, cfg),
        'treemap':      lambda: _treemap_option(df, x_col, y_col, agg, cfg),
        'radar':        lambda: _radar_option(df, x_col, y_col, agg, cfg),
        'gauge':        lambda: _gauge_option(df, y_col, agg, cfg, labels),
        'assessment_compare': lambda: _assessment_compare_option(df, x_col, compare_pl_cols, cfg, labels, col_defs),
        'progress_growth': lambda: _progress_growth_option(df, x_col, pg_before_col, pg_after_col, pl_scale, pg_display, cfg, labels),
        'summary_table':   lambda: _summary_table_option(df, st_row_col, st_col_col, cfg, col_defs),
    }

    try:
        option = builders.get(chart_type, builders['bar'])()
    except Exception as exc:
        option = _message_option(f'Error rendering {chart_type}: {exc}', cfg, error=True)

    # Apply colour mapping (user overrides + semantic defaults) to all chart types
    _apply_color_map(option, cfg)

    # Apply category-axis label overrides (rotation, font size, truncation)
    x_label_style = _x_axis_label_style(cfg)
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
            for axis in axes:
                if isinstance(axis, dict):
                    axis.setdefault('axisLabel', {}).update(x_label_style)
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

    # Apply bar category gap
    bar_gap = cfg.get('bar_category_gap')
    if bar_gap not in (None, ''):
        for s in option.get('series', []):
            if isinstance(s, dict) and s.get('type') == 'bar':
                s['barCategoryGap'] = str(bar_gap) + '%'

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


# ── Chart type registry (used by builder UI) ─────────────────────────────────

CHART_TYPES = [
    # (key, label, icon_class, group)
    ('bar',          'Bar',           'chart-bar',           'Comparison'),
    ('assessment_compare', 'Assessment Compare', 'chart-assess', 'Comparison'),
    ('progress_growth',    'Progress Growth',    'chart-growth',  'Comparison'),
    ('summary_table',      'Summary Table',      'chart-table',   'Comparison'),
    ('waterfall',    'Waterfall',     'chart-waterfall',     'Comparison'),
    ('line',         'Line',          'chart-line',          'Trend'),
    ('spline',       'Smooth Line',   'chart-spline',        'Trend'),
    ('area',         'Area',          'chart-area',          'Trend'),
    ('areaspline',   'Smooth Area',   'chart-area-s',        'Trend'),
    ('stacked_area', 'Stacked Area',  'chart-area-stacked',  'Trend'),
    ('pie',          'Pie',           'chart-pie',           'Proportion'),
    ('donut',        'Donut',         'chart-donut',         'Proportion'),
    ('funnel',       'Funnel',        'chart-funnel',        'Proportion'),
    ('treemap',      'Treemap',       'chart-treemap',       'Proportion'),
    ('scatter',      'Scatter',       'chart-scatter',       'Relationship'),
    ('bubble',       'Bubble',        'chart-bubble',        'Relationship'),
    ('heatmap',      'Heatmap',       'chart-heatmap',       'Relationship'),
    ('histogram',    'Histogram',     'chart-histogram',     'Distribution'),
    ('box',          'Box Plot',      'chart-box',           'Distribution'),
    ('radar',        'Radar',         'chart-radar',         'Advanced'),
    ('gauge',        'Gauge',         'chart-gauge',         'Advanced'),
]
