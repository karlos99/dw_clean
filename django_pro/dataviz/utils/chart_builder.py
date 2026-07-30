"""
chart_builder.py — Server-side ECharts option builder.

Extracted from the original dataviz/views.py and upgraded for ECharts 6.
Call `build_option(rows, col_defs, cfg)` to get an ECharts option dict.
"""
import json
import random
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
_MELT_MAX_ROWS   = 600000  # safety cap: refuse melt if result would exceed this many rows

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

        total_melt_rows = len(df) * len(frames)
        if total_melt_rows > _MELT_MAX_ROWS:
            raise RuntimeError(
                f'Melt would produce {total_melt_rows:,} rows (> {_MELT_MAX_ROWS:,} limit). '
                'Select fewer assessment/mark columns.'
            )
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
    total_melt_rows = len(df) * len(score_cols)
    if total_melt_rows > _MELT_MAX_ROWS:
        raise RuntimeError(
            f'Melt would produce {total_melt_rows:,} rows (> {_MELT_MAX_ROWS:,} limit). '
            'Select fewer assessment/mark columns.'
        )
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


def _bucket_date_column(series, bucket):
    """Bucket a raw date/datetime column into a lexicographically-sortable label string."""
    dt = pd.to_datetime(series, errors='coerce')

    def _fmt(ts):
        if pd.isna(ts):
            return None
        if bucket == 'day':
            return ts.strftime('%Y-%m-%d')
        if bucket == 'week':
            return ts.strftime('%G-W%V')
        if bucket == 'month':
            return ts.strftime('%Y-%m')
        if bucket == 'quarter':
            return f'{ts.year}-Q{(ts.month - 1) // 3 + 1}'
        if bucket == 'year':
            return str(ts.year)
        return ts.strftime('%Y-%m-%d')

    return dt.map(_fmt)


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
    
    group_cols = [x_col]
    if stack_col and stack_col in df.columns:
        group_cols.append(stack_col)
    
    df = df.dropna(subset=group_cols)
    
    try:
        if agg in ('sum', 'avg', 'min', 'max', 'median') and y_col and y_col in df.columns:
            dfc = df.assign(**{y_col: pd.to_numeric(df[y_col], errors='coerce')})
            grp = dfc.dropna(subset=[y_col]).groupby(group_cols, dropna=True)[y_col]
            result = {
                'sum': grp.sum(), 'avg': grp.mean(),
                'min': grp.min(), 'max': grp.max(), 'median': grp.median(),
            }.get(agg, grp.size()).reset_index().rename(columns={y_col: '_value'})
            if agg_decimals is not None and '_value' in result.columns:
                result['_value'] = result['_value'].round(agg_decimals)
        elif agg == 'count_unique' and y_col and y_col in df.columns:
            grp = df.dropna(subset=[y_col]).groupby(group_cols, dropna=True)[y_col]
            result = grp.nunique().reset_index().rename(columns={y_col: '_value'})
            if agg_decimals is not None and '_value' in result.columns:
                result['_value'] = result['_value'].round(agg_decimals)
        elif agg == 'count_occurrence' and y_col and y_col in df.columns:
            grp = df.dropna(subset=[y_col]).groupby(group_cols, dropna=True)[y_col]
            result = grp.count().reset_index().rename(columns={y_col: '_value'})
            if agg_decimals is not None and '_value' in result.columns:
                result['_value'] = result['_value'].round(agg_decimals)
        else:
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


def _build_grouped_axis_data(categories, sep=' · '):
    """
    Parses a list of category strings (joined with `sep`)
    and returns a tuple: (leaf_categories, group_labels_list)
    If no grouping exists, returns (categories, None).
    """
    has_grouping = False
    parsed = []
    for cat in categories:
        if sep in cat:
            has_grouping = True
            parts = cat.split(sep)
            parent = sep.join(parts[:-1])
            leaf = parts[-1]
            parsed.append((parent, leaf))
        else:
            parsed.append(("", cat))
            
    if not has_grouping:
        return categories, None
        
    leaf_categories = [p[1] for p in parsed]
    parent_values = [p[0] for p in parsed]
    
    # Identify contiguous groups of parent values
    n = len(parent_values)
    group_labels = [""] * n
    
    i = 0
    while i < n:
        current_parent = parent_values[i]
        # find the end of this contiguous group
        j = i
        while j < n and parent_values[j] == current_parent:
            j += 1
        # Group is from index i to j-1
        middle_idx = (i + j - 1) // 2
        group_labels[middle_idx] = current_parent
        i = j
        
    return leaf_categories, group_labels


def _update_chart_axes(option, categories, series, y_axis_name, labels, cfg, ax, x_lbl, orientation='vertical', radial=False, polar=False):
    leaf_categories, group_labels = _build_grouped_axis_data(categories)
    
    if radial:
        option.pop('grid', None)
        option.update({
            'angleAxis': {'type': 'category', 'data': leaf_categories if group_labels else categories, 'name': _axis_name(labels['x'], cfg), **ax, 'axisLabel': x_lbl},
            'radiusAxis': {'type': 'value', 'name': _axis_name(y_axis_name, cfg), **ax},
            'polar': {'radius': '70%'},
            'series': series,
        })
        option.setdefault('dv', {})['category_axis'] = 'angle'
        return
    if polar:
        option.pop('grid', None)
        option.update({
            'radiusAxis': {'type': 'category', 'data': leaf_categories if group_labels else categories, 'name': _axis_name(labels['x'], cfg), **ax, 'axisLabel': x_lbl},
            'angleAxis': {'type': 'value', 'name': _axis_name(y_axis_name, cfg), **ax},
            'polar': {'radius': '70%'},
            'series': series,
        })
        option.setdefault('dv', {})['category_axis'] = 'radius'
        return
        
    if orientation == 'horizontal':
        if group_labels:
            y_axis_option = [
                {
                    'type': 'category',
                    'data': leaf_categories,
                    'name': _axis_name(labels['x'], cfg),
                    **ax,
                    'axisLabel': x_lbl
                },
                {
                    'type': 'category',
                    'data': group_labels,
                    'position': 'left',
                    'offset': 60,
                    'axisLine': {'show': False},
                    'axisTick': {'show': False},
                    'splitLine': {'show': False},
                    'axisLabel': {'interval': 0, 'textStyle': {'fontWeight': 'bold'}, **x_lbl}
                }
            ]
        else:
            y_axis_option = {'type': 'category', 'data': categories, 'name': _axis_name(labels['x'], cfg), **ax, 'axisLabel': x_lbl}
            
        option.update({
            'xAxis': {'type': 'value', 'name': _axis_name(y_axis_name, cfg), **ax},
            'yAxis': y_axis_option,
            'series': series,
        })
        option.setdefault('dv', {})['category_axis'] = 'y'
        return
        
    if group_labels:
        x_axis_option = [
            {
                'type': 'category',
                'data': leaf_categories,
                'name': _axis_name(labels['x'], cfg),
                **ax,
                'axisLabel': x_lbl
            },
            {
                'type': 'category',
                'data': group_labels,
                'position': 'bottom',
                'offset': 30,
                'axisLine': {'show': False},
                'axisTick': {'show': False},
                'splitLine': {'show': False},
                'axisLabel': {'interval': 0, 'textStyle': {'fontWeight': 'bold'}, **x_lbl}
            }
        ]
    else:
        x_axis_option = {'type': 'category', 'data': categories, 'name': _axis_name(labels['x'], cfg), **ax, 'axisLabel': x_lbl}
        
    option.update({
        'xAxis': x_axis_option,
        'yAxis': {'type': 'value', 'name': _axis_name(y_axis_name, cfg), **ax},
        'series': series,
    })
    option.setdefault('dv', {})['category_axis'] = 'x'


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
    if cfg.get('force_show_all_labels', False):
        lbl['interval'] = 0
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


def _rich_tooltip_js(categories: list, raw_lookup: dict, totals_lookup: dict,
                     percent_decimals: int, percent_suffix: str,
                     show_pct: bool = True, show_count: bool = True,
                     value_pct_suffix: str = '') -> str:
    """Return a JS axis-tooltip formatter that shows count + % + (n=total) per category.

    ``categories``    – list of original full categories
    ``raw_lookup``    – {series_name: {category: raw_value}}
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
        f"  var cats = {json.dumps(categories)};"
        f"  var rawLookup = {json.dumps(raw_lookup)};"
        f"  var totals = {json.dumps(totals_lookup)};"
        "  var dataIdx = params[0].dataIndex;"
        "  var cat = (cats && dataIdx !== undefined && cats[dataIdx]) ? cats[dataIdx] : (params[0].name || '');"
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
                           value_pct_suffix: str = '',
                           categories: list | None = None) -> str:
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
    cats_js = f"var cats = {json.dumps(categories)};" if categories else "var cats = null;"
    cat_resolve = (
        "  var dataIdx = params[0].dataIndex;"
        "  var cat = (cats && dataIdx !== undefined && cats[dataIdx]) ? cats[dataIdx] : (params[0].name || '');"
    )
    return (
        "function(params) {"
        "  if (!Array.isArray(params)) params = [params];"
        f"  {cats_js}"
        f"  {cat_resolve}"
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
    opt['__is_message__'] = True
    opt['__is_error__'] = error
    return opt


def _format_cell_with_tooltip(val, alt_val, agg_func, show_tooltips, decimals=None):
    if not show_tooltips:
        return val

    if alt_val is None:
        return val

    if agg_func == 'count':
        d = decimals if decimals is not None else 2
        alt_rounded = round(alt_val, d)
        tooltip = f"Average: {alt_rounded}"
    else:
        tooltip = f"Count: {int(alt_val)}"

    return {
        'val': val,
        'tooltip': tooltip
    }


# ── Chart type builders ───────────────────────────────────────────────────────

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


def _normalize_pl_val(v):
    if v is None:
        return ''
    
    try:
        import pandas as pd
        if pd.isna(v):
            return ''
    except Exception:
        pass

    try:
        import numpy as np
        if isinstance(v, (int, float, np.integer, np.floating)):
            val_float = float(v)
            if val_float.is_integer():
                return str(int(val_float))
            return str(v)
    except Exception:
        pass

    val_str = str(v).strip()
    if val_str == '' or val_str in {
        'nan', 'NaN', 'None', 'null', 'NULL', '<NA>', '<na>',
        '-', '\u2014', '\u2013', '—', 'N/A', 'n/a',
    }:
        return ''
    if val_str.endswith('.0'):
        try:
            float(val_str)
            return val_str[:-2]
        except ValueError:
            pass
    return val_str


def _auto_detect_pl_scale(values) -> list:
    """Try to order PL values using known scales; fall back to alphabetical."""
    # Ensure all values are strings and filter out empty/null ones, but keep '0'
    normalized_vals = []
    for v in values:
        norm = _normalize_pl_val(v)
        if norm != '':
            normalized_vals.append(norm)
    val_set = set(normalized_vals)
    for scale in _KNOWN_PL_SCALES:
        if val_set.issubset(set(scale)):
            return [s for s in scale if s in val_set]
    # Final fallback: guaranteed string sort to avoid TypeError: '<' not supported between 'float' and 'str'
    return sorted(list(val_set))


_BLANK_PL_VALS = frozenset({'', '-', '\u2014', '\u2013', 'N/A', 'n/a', 'None', 'null', 'NULL'})

_PG_ALL_PREFIX = '__pg_all__'


def pg_all_key(col: str) -> str:
    """Side-channel key storing every PL value seen for a test column (matrix matching)."""
    return f'{_PG_ALL_PREFIX}{col}'


def _cell_pl_values(row: dict, col: str) -> set:
    """All normalized PL values for *col* on this row (matches exploration EXISTS filters)."""
    vals: set = set()
    if not row or not col:
        return vals
    stored = row.get(pg_all_key(col))
    if stored:
        for v in (stored if isinstance(stored, (list, set, tuple)) else [stored]):
            n = _normalize_pl_val(v)
            if n:
                vals.add(n)
    n = _normalize_pl_val(row.get(col))
    if n:
        vals.add(n)
    return vals


def _record_pg_val(row: dict, col: str, val) -> None:
    """Track every PL value seen for a column (exploration uses EXISTS, not last row)."""
    norm_val = _normalize_pl_val(val)
    if not norm_val:
        return
    key = pg_all_key(col)
    existing = row.get(key)
    if not isinstance(existing, list):
        existing = list(existing) if existing else []
    if norm_val not in existing:
        existing.append(norm_val)
    row[key] = existing


def _resolve_row_column(row_keys, col):
    """Map a config column name to the actual key present in row dicts."""
    if not col or not row_keys:
        return col
    from dataexploration.school_year_utils import resolve_relative_column_key

    def _norm_col(c):
        return str(c).replace('-', '_').lower()

    key_map = {_norm_col(k): k for k in row_keys}
    norm = _norm_col(col)
    if norm in key_map:
        return key_map[norm]
    resolved = resolve_relative_column_key(col)
    norm_res = _norm_col(resolved)
    if norm_res in key_map:
        return key_map[norm_res]
    clean = col.split('.')[-1]
    norm_clean = _norm_col(clean)
    if norm_clean in key_map:
        return key_map[norm_clean]
    return col


def _find_student_id_key(row_keys):
    for target in ('ssid', 'student_id', 'stu_annual_key'):
        match = next(
            (k for k in row_keys if k == target or k.split('.')[-1].lower() == target),
            None,
        )
        if match:
            return match
    return None


def _build_pg_rank(pl_scale, rows, before_col, after_col):
    """Build PL rank dict from configured scale or auto-detect from row values."""
    if pl_scale:
        ordered = [_normalize_pl_val(v) for v in pl_scale if str(v).strip()]
    else:
        all_vals = set()
        for r in rows:
            for c in (before_col, after_col):
                v = _normalize_pl_val(r.get(c))
                if v and v not in _BLANK_PL_VALS:
                    all_vals.add(v)
        ordered = _auto_detect_pl_scale(list(all_vals))
    if not ordered:
        return {}
    return {v: i for i, v in enumerate(ordered)}


def _row_student_key(row: dict):
    """Return the first non-empty student identifier on this row."""
    if not row:
        return None
    keys = list(row.keys())
    for target in ('ssid', 'student_id', 'stu_annual_key'):
        col = next(
            (k for k in keys if k == target or k.split('.')[-1].lower() == target),
            None,
        )
        if not col:
            continue
        val = row.get(col)
        if val is not None and str(val).strip() not in _BLANK_PL_VALS:
            return str(val).strip()
    return None


def _dedupe_student_rows(rows: list) -> list:
    """Keep one row per student — uses per-row best id, not a single column."""
    seen = set()
    deduped = []
    for r in rows:
        key = _row_student_key(r)
        if key is None:
            deduped.append(r)
        elif key not in seen:
            seen.add(key)
            deduped.append(r)
    return deduped


def consolidate_progress_growth_rows(rows, before_col, after_col):
    """Merge enrollment rows per student so before/after PL coexist on one row.

    Cross-year PL columns (e.g. two ELPAC summative years) often land on different
    enrollment rows. Data exploration attaches each year's PL via separate subqueries
    onto one student; this mirrors that before matrix counting.
    """
    if not rows or not before_col or not after_col:
        return rows

    row_keys = list(rows[0].keys())
    before_col = _resolve_row_column(row_keys, before_col)
    after_col = _resolve_row_column(row_keys, after_col)

    groups: dict = {}
    unkeyed = []
    for r in rows:
        key = _row_student_key(r)
        if key is None:
            unkeyed.append(r)
        else:
            groups.setdefault(key, []).append(r)

    consolidated = []
    for group in groups.values():
        base = dict(group[0])
        before_vals: set = set()
        after_vals: set = set()
        for r in group:
            before_vals |= _cell_pl_values(r, before_col)
            after_vals |= _cell_pl_values(r, after_col)
        if before_vals:
            base[before_col] = sorted(before_vals, key=lambda v: (len(v), v))[0]
            base[pg_all_key(before_col)] = sorted(before_vals, key=lambda v: (len(v), v))
        if after_vals:
            base[after_col] = sorted(after_vals, key=lambda v: (len(v), v))[0]
            base[pg_all_key(after_col)] = sorted(after_vals, key=lambda v: (len(v), v))
        consolidated.append(base)

    return consolidated + unkeyed


def prepare_progress_growth_rows(rows, before_col, after_col, pl_scale=None):
    """Filter to valid before/after PL pairs and dedupe by student (first kept).

    Mirrors ``_progress_growth_option`` preprocessing so matrix cell counts and
    drill-through student lists stay aligned.
    """
    if not rows or not before_col or not after_col:
        return [], before_col, after_col, {}

    rows = consolidate_progress_growth_rows(rows, before_col, after_col)

    row_keys = list(rows[0].keys())
    before_col = _resolve_row_column(row_keys, before_col)
    after_col = _resolve_row_column(row_keys, after_col)
    rank = _build_pg_rank(pl_scale or [], rows, before_col, after_col)
    if not rank:
        return [], before_col, after_col, rank

    prepared = []
    for r in rows:
        b = _normalize_pl_val(r.get(before_col))
        a = _normalize_pl_val(r.get(after_col))
        if b in rank and a in rank:
            prepared.append(r)

    prepared = _dedupe_student_rows(prepared)

    return prepared, before_col, after_col, rank


def filter_progress_growth_matrix_rows(rows, before_col, after_col, before_val, after_val):
    """Return rows for one matrix cell, deduped by student.

    Uses the same semantics as data exploration column filters: a student matches
    when ANY fetched result PL equals the before value AND ANY equals the after
    value (across enrollment rows), not only when both appear on one row.
    """
    if not rows or not before_col or not after_col:
        return [], before_col, after_col

    row_keys = list(rows[0].keys())
    before_col = _resolve_row_column(row_keys, before_col)
    after_col = _resolve_row_column(row_keys, after_col)
    b_target = _normalize_pl_val(before_val)
    a_target = _normalize_pl_val(after_val)

    groups: dict = {}
    unkeyed = []
    for r in rows:
        key = _row_student_key(r)
        if key is None:
            unkeyed.append(r)
        else:
            groups.setdefault(key, []).append(r)

    matched = []
    for group in groups.values():
        before_vals: set = set()
        after_vals: set = set()
        for r in group:
            before_vals |= _cell_pl_values(r, before_col)
            after_vals |= _cell_pl_values(r, after_col)
        if b_target not in before_vals or a_target not in after_vals:
            continue
        rep = next(
            (
                r for r in group
                if b_target in _cell_pl_values(r, before_col)
                and a_target in _cell_pl_values(r, after_col)
            ),
            next((r for r in group if b_target in _cell_pl_values(r, before_col)), group[0]),
        )
        matched.append(rep)

    for r in unkeyed:
        before_vals = _cell_pl_values(r, before_col)
        after_vals = _cell_pl_values(r, after_col)
        if b_target in before_vals and a_target in after_vals:
            matched.append(r)

    return matched, before_col, after_col


def classify_progress_growth_row(row, before_col, after_col, rank):
    """Return Growth / Sliders / Stickers for one prepared row, or None."""
    b = rank.get(_normalize_pl_val(row.get(before_col)))
    a = rank.get(_normalize_pl_val(row.get(after_col)))
    if b is None or a is None:
        return None
    if a > b:
        return 'Growth'
    if a < b:
        return 'Sliders'
    return 'Stickers'


def _summary_table_option(df, row_col, col_col, cfg, col_defs):
    """Return a special __type__=table option: count cross-tabulation.

    ``row_col`` — the column whose distinct values become row labels.
    ``col_col`` — the column whose distinct values become column headers
                  (``st_col_mode='values'``, the default) OR
                  when ``st_col_mode='columns'``, ``st_col_cols`` lists the
                  dataset columns whose *names* become the column headers and
                  whose *values* are aggregated per row group.

    Config keys (shared with dynamic table):
      st_agg_func     — 'count','sum','avg','min','max','median' (default 'count')
      st_value_col    — value column for non-count aggs (only used in 'values' mode)
      st_agg_decimals — decimal places for avg/median
      st_show_row_totals — bool, show row-total column (default True)
      st_show_col_totals — bool, show column-totals row at bottom (default True)
      st_col_mode     — 'values' (default) or 'columns'
      st_col_cols     — list of column names (only used in 'columns' mode)

    The returned dict uses ``__type__: 'table'`` to signal the JS renderer
    to build an HTML table instead of an ECharts instance.
    """
    _BLANK = frozenset({'', '-', '\u2014', '\u2013', 'N/A', 'n/a', 'None', 'null', 'NULL'})
    _VALID_AGGS = {'count', 'sum', 'avg', 'min', 'max', 'median'}
    st_col_mode = cfg.get('st_col_mode', 'values')
    st_agg_func = cfg.get('st_agg_func', 'count')
    st_agg_func = st_agg_func if st_agg_func in _VALID_AGGS else 'count'
    st_value_col = cfg.get('st_value_col', '')
    _st_raw_decimals = cfg.get('st_agg_decimals')
    st_agg_decimals = int(_st_raw_decimals) if _st_raw_decimals is not None else None
    st_show_row_totals = cfg.get('st_show_row_totals', True)
    st_show_col_totals = cfg.get('st_show_col_totals', True)
    show_tooltips = cfg.get('st_show_tooltips', False)
    st_col_cols_raw = cfg.get('st_col_cols', [])
    st_col_cols = [c for c in (st_col_cols_raw if isinstance(st_col_cols_raw, list) else []) if c and c in df.columns]

    _dup_key = next((c for c in ('ssid', 'student_id', 'stu_annual_key') if c in df.columns), None)
    _agg_map = {'sum': 'sum', 'avg': 'mean', 'min': 'min', 'max': 'max', 'median': 'median'}

    # ── Columns mode: selected dataset columns become headers ──────────────────
    if st_col_mode == 'columns':
        if not row_col or row_col not in df.columns:
            return _message_option('Summary Table: select a Row column', cfg)
        if not st_col_cols:
            return _message_option('Summary Table: select at least one Column (Columns mode)', cfg)

        mask = ~df[row_col].astype(str).str.strip().isin(_BLANK)
        keep_cols = list(dict.fromkeys([row_col] + st_col_cols + ([_dup_key] if _dup_key else [])))
        work = df.loc[mask, keep_cols].copy()
        work[row_col] = work[row_col].astype(str).str.strip()

        if work.empty:
            return _message_option('Summary Table: no data after filtering blanks', cfg)

        row_vals = _auto_detect_pl_scale(work[row_col].unique())
        col_vals = [_label(c, col_defs) if col_defs else c for c in st_col_cols]

        rows_data = []
        col_totals = [0.0] * len(st_col_cols)

        for rv in row_vals:
            sub = work[work[row_col] == rv]
            if _dup_key and st_agg_func == 'count':
                sub = sub.drop_duplicates(subset=[_dup_key, row_col], keep='first')
            cell_vals = []
            row_cells = []
            for ci, cc in enumerate(st_col_cols):
                if cc not in sub.columns:
                    cell_vals.append(0.0)
                    row_cells.append(_format_cell_with_tooltip(0.0, None, st_agg_func, show_tooltips, st_agg_decimals))
                    continue
                col_data = sub[cc]
                if st_agg_func == 'count':
                    raw = float((~col_data.astype(str).str.strip().isin(_BLANK)).sum())
                    num_data = pd.to_numeric(col_data, errors='coerce').dropna()
                    alt_raw = float(num_data.mean()) if not num_data.empty else None
                else:
                    num_data = pd.to_numeric(col_data, errors='coerce').dropna()
                    if num_data.empty:
                        raw = 0.0
                    else:
                        raw = float(num_data.agg(_agg_map[st_agg_func]))
                    if _dup_key:
                        valid_sub = sub[~col_data.astype(str).str.strip().isin(_BLANK)]
                        alt_raw = float(valid_sub[_dup_key].nunique())
                    else:
                        alt_raw = float((~col_data.astype(str).str.strip().isin(_BLANK)).sum())

                if st_agg_decimals is not None and st_agg_func != 'count':
                    raw = round(raw, st_agg_decimals)

                col_totals[ci] += raw
                cell_vals.append(raw)
                
                formatted = _format_cell_with_tooltip(raw, alt_raw, st_agg_func, show_tooltips, st_agg_decimals)
                row_cells.append(formatted)

            rows_data.append([rv] + row_cells + ([sum(cell_vals)] if st_show_row_totals else []))

        if st_agg_decimals is not None:
            col_totals = [round(v, st_agg_decimals) for v in col_totals]
            for row in rows_data:
                if st_show_row_totals:
                    row[-1] = round(row[-1], st_agg_decimals)

        row_label = _label(row_col, col_defs) if col_defs else row_col
        col_label = ' / '.join(col_vals)

        result = {
            '__type__': 'table',
            '__series_names__': [],
            'row_col':     row_col,
            'col_col':     '__st_cols__',
            'row_label':   row_label,
            'col_label':   col_label,
            'col_headers': col_vals,
            'rows':        rows_data,
            'st_show_row_totals': st_show_row_totals,
            'st_show_col_totals': st_show_col_totals,
            'show_tooltips': show_tooltips,
            '_drillMeta': {
                'stType':      'summary_table',
                'stRowCol':    row_col,
                'stColCol':    '__st_cols__',
                'stColCols':   st_col_cols,
                'colHeaders':  col_vals,
            },
        }

        if st_show_col_totals:
            grand_total = sum(col_totals)
            if st_agg_decimals is not None:
                grand_total = round(grand_total, st_agg_decimals)
            result['total_row'] = ['Total'] + col_totals + [grand_total]
        else:
            result['total_row'] = []

        return result

    # ── Values mode: single column's distinct values become headers ────────────
    if not row_col or row_col not in df.columns:
        return _message_option('Summary Table: select a Row column', cfg)
    if not col_col or col_col not in df.columns:
        return _message_option('Summary Table: select a Column column', cfg)

    mask = (
        ~df[row_col].astype(str).str.strip().isin(_BLANK) &
        ~df[col_col].astype(str).str.strip().isin(_BLANK)
    )
    keep_cols = [row_col, col_col] + ([_dup_key] if _dup_key else [])
    if (st_agg_func != 'count' or (show_tooltips and st_value_col)) and st_value_col and st_value_col in df.columns:
        keep_cols.append(st_value_col)
    work = df.loc[mask, keep_cols].copy()
    work[row_col] = work[row_col].astype(str).str.strip()
    work[col_col] = work[col_col].astype(str).str.strip()
    if st_value_col and st_value_col in work.columns:
        work[st_value_col] = pd.to_numeric(work[st_value_col], errors='coerce')

    if work.empty:
        return _message_option('Summary Table: no data after filtering blanks', cfg)

    # Deduplicate so each student is counted at most once per (row_col, col_col) cell.
    if _dup_key and st_agg_func == 'count':
        work = work.drop_duplicates(subset=[_dup_key, row_col, col_col], keep='first')

    # Use auto-detection to get properly ordered PL levels if applicable, 
    # and to ensure all values are strings for sorting.
    row_vals = _auto_detect_pl_scale(work[row_col].unique())
    col_vals = _auto_detect_pl_scale(work[col_col].unique())

    ct_alt = None
    if show_tooltips:
        if st_agg_func == 'count':
            if st_value_col and st_value_col in df.columns:
                work_alt = work.copy()
                work_alt[st_value_col] = pd.to_numeric(work_alt[st_value_col], errors='coerce')
                ct_alt = work_alt.groupby([row_col, col_col])[st_value_col].mean().unstack(fill_value=0)
                ct_alt = ct_alt.reindex(index=row_vals, columns=col_vals, fill_value=0)
        else:
            work_alt = work.copy()
            if st_value_col and st_value_col in work_alt.columns:
                work_alt = work_alt[~work_alt[st_value_col].isna()]
            if _dup_key:
                ct_alt = work_alt.groupby([row_col, col_col])[_dup_key].nunique().unstack(fill_value=0)
            else:
                ct_alt = work_alt.groupby([row_col, col_col]).size().unstack(fill_value=0)
            ct_alt = ct_alt.reindex(index=row_vals, columns=col_vals, fill_value=0)

    if st_agg_func == 'count':
        ct = work.groupby([row_col, col_col]).size().unstack(fill_value=0)
    else:
        ct = work.groupby([row_col, col_col])[st_value_col].agg(_agg_map[st_agg_func]).unstack(fill_value=0)

    ct = ct.reindex(index=row_vals, columns=col_vals, fill_value=0)

    if st_agg_decimals is not None:
        ct = ct.round(st_agg_decimals)

    col_totals = [0.0] * len(col_vals)
    rows_data = []
    for rv in row_vals:
        counts = [float(ct.at[rv, cv]) for cv in col_vals]
        for i, v in enumerate(counts):
            col_totals[i] += v

        row_cells = []
        for i, cv in enumerate(col_vals):
            v = counts[i]
            alt_v = float(ct_alt.at[rv, cv]) if (ct_alt is not None and cv in ct_alt.columns) else None
            formatted = _format_cell_with_tooltip(v, alt_v, st_agg_func, show_tooltips, st_agg_decimals)
            row_cells.append(formatted)

        rows_data.append([rv] + row_cells + ([sum(counts)] if st_show_row_totals else []))

    if st_agg_decimals is not None:
        col_totals = [round(v, st_agg_decimals) for v in col_totals]
        for row in rows_data:
            if st_show_row_totals:
                row[-1] = round(row[-1], st_agg_decimals)

    grand_total = sum(col_totals)
    if st_agg_decimals is not None:
        grand_total = round(grand_total, st_agg_decimals)

    total_row = (['Total'] + col_totals + [grand_total]) if st_show_col_totals else []

    row_label = _label(row_col, col_defs) if col_defs else row_col
    col_label = _label(col_col, col_defs) if col_defs else col_col

    result = {
        '__type__': 'table',
        '__series_names__': [],
        'row_col':     row_col,
        'col_col':     col_col,
        'row_label':   row_label,
        'col_label':   col_label,
        'col_headers': col_vals,
        'rows':        rows_data,
        'total_row':   total_row,
        'st_show_row_totals': st_show_row_totals,
        'st_show_col_totals': st_show_col_totals,
        'show_tooltips': show_tooltips,
        '_drillMeta': {
            'stType':      'summary_table',
            'stRowCol':    row_col,
            'stColCol':    col_col,
            'colHeaders':  col_vals,
        },
    }
    return result


def _convert_table_to_echarts(result, cfg, valid_row):
    _SEP = ' \u25b8 '
    x_data = []
    rows_data = result.get('rows', [])
    for r in rows_data:
        x_val = _SEP.join(str(x) for x in r[:len(valid_row)])
        x_data.append(x_val)

    series_names = []
    if result.get('col_header_groups'):
        for grp in result['col_header_groups']:
            for h in grp['headers']:
                series_names.append(f"{grp['label']}{_SEP}{h}")
    else:
        series_names = result.get('col_headers', [])

    series_list = []
    dyn_display_type = cfg.get('dyn_display_type', 'table')
    is_stacked = dyn_display_type in ('stacked_bar', 'stacked_line')
    stype = 'bar' if 'bar' in dyn_display_type else 'line'

    for ci, sname in enumerate(series_names):
        s_data = []
        for r in rows_data:
            val = r[len(valid_row) + ci]
            s_data.append(val)

        s_obj = {
            'name': sname,
            'type': stype,
            'data': s_data,
        }
        if is_stacked:
            s_obj['stack'] = 'total'
        series_list.append(s_obj)

    title_text = cfg.get('title', '')
    echarts_option = {
        'title': {
            'text': title_text,
            'left': 'center',
            'textStyle': {'fontSize': 14, 'color': '#334155'}
        },
        'tooltip': {
            'trigger': 'axis',
            'axisPointer': {'type': 'shadow' if stype == 'bar' else 'line'}
        },
        'legend': {
            'data': series_names,
            'bottom': 0,
            'type': 'scroll',
            'textStyle': {'fontSize': 10}
        },
        'grid': {
            'top': 40,
            'bottom': 40,
            'left': 50,
            'right': 20,
            'containLabel': True
        },
        'xAxis': {
            'type': 'category',
            'data': x_data,
            'axisLabel': {'fontSize': 10, 'rotate': 15}
        },
        'yAxis': {
            'type': 'value',
            'axisLabel': {'fontSize': 10}
        },
        'series': series_list,
        '_drillMeta': {
            'stType':      'dynamic_summary_table',
            'stRowCol':    valid_row[0] if len(valid_row) == 1 else '__dynst_row__',
            'stColCol':    result.get('col_col', '__calc_cols__'),
            'stRowCols':   valid_row,
            'stColCols':   result.get('_drillMeta', {}).get('stColCols', []),
            'stRowVals':   result.get('_drillMeta', {}).get('stRowVals', []),
            'colHeaders':  result.get('col_headers', []),
            'colHeaderCols': result.get('col_header_cols', []),
        }
    }
    return echarts_option


def _dynamic_summary_table_option(df, row_cols, col_cols, agg_func, value_col, agg_decimals, cfg, col_defs):
    """Return a __type__=table option for multi-dimension cross-tabulation.

    Multiple row columns produce separate label columns in the table.
    Multiple column columns each produce their own header group (multi-level headers).
    A single column column produces a pivoted cross-tab with column headers.
    """
    _SEP = ' \u25b8 '  # ▸ separator for combining multi-column headers
    _BLANK = frozenset({'', '-', '\u2014', '\u2013', 'N/A', 'n/a', 'None', 'null', 'NULL'})
    _dup_key = next((c for c in ('ssid', 'student_id', 'stu_annual_key') if c in df.columns), None)

    dyn_col_mode = cfg.get('dyn_col_mode', 'pivot')
    st_show_row_totals = cfg.get('dyn_show_row_totals')
    if st_show_row_totals is None:
        st_show_row_totals = cfg.get('st_show_row_totals', True)
    st_show_col_totals = cfg.get('dyn_show_col_totals')
    if st_show_col_totals is None:
        st_show_col_totals = cfg.get('st_show_col_totals', True)
    show_tooltips = cfg.get('dyn_show_tooltips', False)

    # ── Calculations mode: user selects multiple calculations as columns ────────────────
    if dyn_col_mode == 'calculations':
        dyn_calculations = cfg.get('dyn_calculations', [])
        if not dyn_calculations:
            return _message_option('Dynamic Summary Table: add at least one calculation column', cfg)
        if not row_cols:
            return _message_option('Dynamic Summary Table: select at least one Row column', cfg)
        valid_row = [c for c in row_cols if c and c in df.columns]
        if not valid_row:
            return _message_option('Dynamic Summary Table: no valid Row columns in data', cfg)

        mask = pd.Series(True, index=df.index)
        for c in valid_row:
            mask &= ~df[c].astype(str).str.strip().isin(_BLANK)

        calc_value_cols = [calc.get('value_col') for calc in dyn_calculations if calc.get('value_col') and calc.get('value_col') in df.columns]
        keep_cols = list(dict.fromkeys(valid_row + calc_value_cols + ([_dup_key] if _dup_key else [])))
        work = df.loc[mask, keep_cols].copy()
        for c in valid_row:
            work[c] = work[c].astype(str).str.strip()

        if work.empty:
            return _message_option('Dynamic Summary Table: no data after filtering blanks', cfg)

        row_val_lists = {}
        for c in valid_row:
            row_val_lists[c] = _auto_detect_pl_scale(work[c].unique())
        row_combos = _build_row_combos(row_val_lists, valid_row)

        _agg_map = {'sum': 'sum', 'avg': 'mean', 'min': 'min', 'max': 'max', 'median': 'median'}

        col_headers = []
        for idx, calc in enumerate(dyn_calculations):
            lbl = calc.get('label', '').strip()
            if not lbl:
                fn = calc.get('agg_func', 'count')
                vc = calc.get('value_col', '')
                vc_lbl = _label(vc, col_defs) if (col_defs and vc) else vc
                lbl = f"{fn.capitalize()} of {vc_lbl}" if vc else fn.capitalize()
            col_headers.append(lbl)

        rows_data = []
        col_totals = [0.0] * len(dyn_calculations)

        for combo in row_combos:
            sub = work
            for r_col, r_val in zip(valid_row, combo):
                sub = sub[sub[r_col] == r_val]

            cell_vals = []
            row_cells = []
            for ci, calc in enumerate(dyn_calculations):
                fn = calc.get('agg_func', 'count')
                vc = calc.get('value_col', '')
                dec = calc.get('decimals')
                try:
                    dec = int(dec) if dec is not None and str(dec).strip() != '' else None
                except ValueError:
                    dec = None

                if fn == 'count':
                    if _dup_key:
                        raw = float(sub.drop_duplicates(subset=[_dup_key] + valid_row, keep='first').shape[0])
                    else:
                        raw = float(sub.shape[0])
                    if vc and vc in sub.columns:
                        num_data = pd.to_numeric(sub[vc], errors='coerce').dropna()
                        alt_raw = float(num_data.mean()) if not num_data.empty else None
                    else:
                        alt_raw = None
                else:
                    if vc and vc in sub.columns:
                        num_data = pd.to_numeric(sub[vc], errors='coerce').dropna()
                        if num_data.empty:
                            raw = 0.0
                        else:
                            raw = float(num_data.agg(_agg_map.get(fn, 'mean')))
                    else:
                        raw = 0.0
                    if _dup_key:
                        valid_sub = sub.copy()
                        if vc and vc in valid_sub.columns:
                            valid_sub[vc] = pd.to_numeric(valid_sub[vc], errors='coerce')
                            valid_sub = valid_sub.dropna(subset=[vc])
                        alt_raw = float(valid_sub[_dup_key].nunique())
                    else:
                        alt_raw = float(sub.shape[0])

                if dec is not None:
                    raw = round(raw, dec)
                col_totals[ci] += raw
                cell_vals.append(raw)

                formatted = _format_cell_with_tooltip(raw, alt_raw, fn, show_tooltips, dec)
                row_cells.append(formatted)

            if st_show_row_totals:
                rows_data.append(list(combo) + row_cells + [sum(cell_vals)])
            else:
                rows_data.append(list(combo) + row_cells)

        for ci, calc in enumerate(dyn_calculations):
            dec = calc.get('decimals')
            try:
                dec = int(dec) if dec is not None and str(dec).strip() != '' else None
            except ValueError:
                dec = None
            if dec is not None:
                col_totals[ci] = round(col_totals[ci], dec)

        grand_total = sum(col_totals)
        total_row = (['Total'] + [''] * (len(valid_row) - 1) + col_totals + [grand_total]) if st_show_col_totals else []
        row_labels = [_label(c, col_defs) if col_defs else c for c in valid_row]

        result = {
            '__type__': 'table',
            '__series_names__': [],
            'row_col':     valid_row[0] if len(valid_row) == 1 else '__dynst_row__',
            'col_col':     '__calc_cols__',
            'row_labels':  row_labels,
            'row_label':   row_labels[0] if len(row_labels) == 1 else _SEP.join(row_labels),
            'col_label':   'Calculations',
            'num_row_labels': len(valid_row),
            'col_headers': col_headers,
            'rows':        rows_data,
            'total_row':   total_row,
            'st_show_row_totals': st_show_row_totals,
            'st_show_col_totals': st_show_col_totals,
            'show_tooltips': show_tooltips,
            '_drillMeta': {
                'stType':      'dynamic_summary_table',
                'stRowCol':    valid_row[0],
                'stColCol':    '__calc_cols__',
                'stRowCols':   valid_row,
                'stColCols':   [],
                'stRowVals':   row_combos,
                'colHeaders':  col_headers,
            },
        }
        if cfg.get('dyn_display_type', 'table') != 'table':
            return _convert_table_to_echarts(result, cfg, valid_row)
        return result

    if not row_cols:
        return _message_option('Dynamic Summary Table: select at least one Row column', cfg)
    if not col_cols:
        return _message_option('Dynamic Summary Table: select at least one Column column', cfg)

    valid_row = [c for c in row_cols if c and c in df.columns]
    valid_col = [c for c in col_cols if c and c in df.columns]

    if not valid_row:
        return _message_option('Dynamic Summary Table: no valid Row columns in data', cfg)
    if not valid_col:
        return _message_option('Dynamic Summary Table: no valid Column columns in data', cfg)

    _VALID_AGGS = {'count', 'sum', 'avg', 'min', 'max', 'median'}
    agg_func = agg_func if agg_func in _VALID_AGGS else 'count'

    # ── Multi-column mode: each column column independently pivoted into its own header group ──
    if len(valid_col) > 1:
        keep_cols = list(dict.fromkeys(valid_row + valid_col + ([_dup_key] if _dup_key else [])))
        if (agg_func != 'count' or (show_tooltips and value_col)) and value_col and value_col in df.columns:
            keep_cols.append(value_col)

        mask = pd.Series(True, index=df.index)
        for c in valid_row + valid_col:
            mask &= ~df[c].astype(str).str.strip().isin(_BLANK)

        work = df.loc[mask, keep_cols].copy()
        for c in valid_row + valid_col:
            work[c] = work[c].astype(str).str.strip()
        if value_col and value_col in work.columns:
            work[value_col] = pd.to_numeric(work[value_col], errors='coerce')

        if work.empty:
            return _message_option('Dynamic Summary Table: no data after filtering blanks', cfg)

        if _dup_key and agg_func == 'count':
            work = work.drop_duplicates(subset=[_dup_key] + valid_row + valid_col, keep='first')

        # Ordered row values
        row_val_lists = {}
        for c in valid_row:
            row_val_lists[c] = _auto_detect_pl_scale(work[c].unique())
        row_combos = _build_row_combos(row_val_lists, valid_row)

        _agg_map = {'sum': 'sum', 'avg': 'mean', 'min': 'min', 'max': 'max', 'median': 'median'}

        # Build an independent pivot table for each column column
        col_groups = []   # [{label, col, headers, pivot}]
        all_col_headers = []
        all_cell_cols = []   # column name tracked per header for drill-through

        for cc in valid_col:
            vals = _auto_detect_pl_scale(work[cc].unique())
            if agg_func == 'count':
                ct = work.groupby(valid_row + [cc]).size().unstack(fill_value=0)
            else:
                ct = work.groupby(valid_row + [cc])[value_col].agg(_agg_map[agg_func]).unstack(fill_value=0)
            ct = ct.reindex(columns=vals, fill_value=0)

            ct_alt = None
            if show_tooltips:
                if agg_func == 'count':
                    if value_col and value_col in df.columns:
                        work_alt = work.copy()
                        work_alt[value_col] = pd.to_numeric(work_alt[value_col], errors='coerce')
                        ct_alt = work_alt.groupby(valid_row + [cc])[value_col].mean().unstack(fill_value=0)
                        ct_alt = ct_alt.reindex(columns=vals, fill_value=0)
                else:
                    work_alt = work.copy()
                    if value_col and value_col in work_alt.columns:
                        work_alt = work_alt[~work_alt[value_col].isna()]
                    if _dup_key:
                        ct_alt = work_alt.groupby(valid_row + [cc])[_dup_key].nunique().unstack(fill_value=0)
                    else:
                        ct_alt = work_alt.groupby(valid_row + [cc]).size().unstack(fill_value=0)
                    ct_alt = ct_alt.reindex(columns=vals, fill_value=0)

            group_label = _label(cc, col_defs) if col_defs else cc
            col_groups.append({
                'label': group_label,
                'col': cc,
                'headers': vals,
                'pivot': ct,
                'pivot_alt': ct_alt
            })
            all_col_headers.extend(vals)
            all_cell_cols.extend([cc] * len(vals))

        # Build rows: row label values + cell values for each group + row total
        rows_data = []
        col_totals = [0.0] * len(all_col_headers)
        for combo in row_combos:
            idx_tuple = tuple(combo)
            cell_vals = []
            row_cells = []
            ci = 0
            for grp in col_groups:
                ct = grp['pivot']
                ct_alt = grp.get('pivot_alt')
                for cv in grp['headers']:
                    if len(valid_row) == 1:
                        raw = float(ct.at[idx_tuple[0], cv]) if cv in ct.columns else 0.0
                        alt_raw = float(ct_alt.at[idx_tuple[0], cv]) if (ct_alt is not None and cv in ct_alt.columns) else None
                    else:
                        try:
                            raw = float(ct.loc[idx_tuple, cv]) if cv in ct.columns else 0.0
                        except KeyError:
                            raw = 0.0
                        try:
                            alt_raw = float(ct_alt.loc[idx_tuple, cv]) if (ct_alt is not None and cv in ct_alt.columns) else None
                        except KeyError:
                            alt_raw = None

                    if agg_decimals is not None:
                        raw = round(raw, agg_decimals)

                    col_totals[ci] += raw
                    cell_vals.append(raw)

                    formatted = _format_cell_with_tooltip(raw, alt_raw, agg_func, show_tooltips, agg_decimals)
                    row_cells.append(formatted)
                    ci += 1

            if st_show_row_totals:
                rows_data.append(list(combo) + row_cells + [sum(cell_vals)])
            else:
                rows_data.append(list(combo) + row_cells)

        if agg_decimals is not None:
            col_totals = [round(v, agg_decimals) for v in col_totals]
            for row in rows_data:
                if st_show_row_totals:
                    row[-1] = round(row[-1], agg_decimals)

        grand_total = sum(col_totals)
        if agg_decimals is not None:
            grand_total = round(grand_total, agg_decimals)

        total_row = (['Total'] + [''] * (len(valid_row) - 1) + col_totals + [grand_total]) if st_show_col_totals else []
        row_labels = [_label(c, col_defs) if col_defs else c for c in valid_row]

        # Build col_header_groups for multi-level header rendering in JS
        col_header_groups = []
        for grp in col_groups:
            col_header_groups.append({
                'label': grp['label'],
                'col': grp['col'],
                'headers': grp['headers'],
            })

        result = {
            '__type__': 'table',
            '__series_names__': [],
            'row_col':     valid_row[0] if len(valid_row) == 1 else '__dynst_row__',
            'col_col':     valid_col[0],
            'row_labels':  row_labels,
            'row_label':   row_labels[0] if len(row_labels) == 1 else _SEP.join(row_labels),
            'col_label':   _SEP.join(_label(c, col_defs) if col_defs else c for c in valid_col),
            'num_row_labels': len(valid_row),
            'col_headers': all_col_headers,
            'col_header_groups': col_header_groups,
            'col_header_cols': all_cell_cols,
            'rows':        rows_data,
            'total_row':   total_row,
            'st_show_row_totals': st_show_row_totals,
            'st_show_col_totals': st_show_col_totals,
            'show_tooltips': show_tooltips,
            '_drillMeta': {
                'stType':      'dynamic_summary_table',
                'stRowCol':    valid_row[0],
                'stColCol':    valid_col[0],
                'stRowCols':   valid_row,
                'stColCols':   valid_col,
                'stRowVals':   row_combos,
                'colHeaders':  all_col_headers,
                'colHeaderCols': all_cell_cols,
            },
        }
        if cfg.get('dyn_display_type', 'table') != 'table':
            return _convert_table_to_echarts(result, cfg, valid_row)
        return result

    # ── Single column column: pivoted cross-tabulation (original behavior) ──
    _COMP_COL = valid_col[0]
    col_col = _COMP_COL

    mask = pd.Series(True, index=df.index)
    for c in valid_row:
        mask &= ~df[c].astype(str).str.strip().isin(_BLANK)
    mask &= ~df[col_col].astype(str).str.strip().isin(_BLANK)

    keep_cols = list(dict.fromkeys(valid_row + [col_col] + ([_dup_key] if _dup_key else [])))
    if (agg_func != 'count' or (show_tooltips and value_col)) and value_col and value_col in df.columns:
        keep_cols.append(value_col)
    work = df.loc[mask, keep_cols].copy()

    for c in valid_row:
        work[c] = work[c].astype(str).str.strip()
    work[col_col] = work[col_col].astype(str).str.strip()
    if value_col and value_col in work.columns:
        work[value_col] = pd.to_numeric(work[value_col], errors='coerce')

    if work.empty:
        return _message_option('Dynamic Summary Table: no data after filtering blanks', cfg)

    if _dup_key and agg_func == 'count':
        work = work.drop_duplicates(subset=[_dup_key] + valid_row + [col_col], keep='first')

    row_val_lists = {}
    for c in valid_row:
        row_val_lists[c] = _auto_detect_pl_scale(work[c].unique())

    row_combos = _build_row_combos(row_val_lists, valid_row)

    group_cols = valid_row + [col_col]
    _agg_map = {'sum': 'sum', 'avg': 'mean', 'min': 'min', 'max': 'max', 'median': 'median'}
    col_vals = _auto_detect_pl_scale(work[col_col].unique())

    ct_alt = None
    if show_tooltips:
        if agg_func == 'count':
            if value_col and value_col in df.columns:
                work_alt = work.copy()
                work_alt[value_col] = pd.to_numeric(work_alt[value_col], errors='coerce')
                ct_alt = work_alt.groupby(group_cols)[value_col].mean().unstack(fill_value=0)
                ct_alt = ct_alt.reindex(columns=col_vals, fill_value=0)
        else:
            work_alt = work.copy()
            if value_col and value_col in work_alt.columns:
                work_alt = work_alt[~work_alt[value_col].isna()]
            if _dup_key:
                ct_alt = work_alt.groupby(group_cols)[_dup_key].nunique().unstack(fill_value=0)
            else:
                ct_alt = work_alt.groupby(group_cols).size().unstack(fill_value=0)
            ct_alt = ct_alt.reindex(columns=col_vals, fill_value=0)

    if agg_func == 'count':
        ct = work.groupby(group_cols).size().unstack(fill_value=0)
    else:
        ct = work.groupby(group_cols)[value_col].agg(_agg_map[agg_func]).unstack(fill_value=0)

    ct = ct.reindex(columns=col_vals, fill_value=0)

    rows_data = []
    col_totals = [0.0] * len(col_vals)
    for combo in row_combos:
        idx_tuple = tuple(combo)
        cell_vals = []
        row_cells = []
        for cv in col_vals:
            if len(valid_row) == 1:
                raw = float(ct.at[idx_tuple[0], cv]) if cv in ct.columns else 0.0
                alt_raw = float(ct_alt.at[idx_tuple[0], cv]) if (ct_alt is not None and cv in ct_alt.columns) else None
            else:
                try:
                    raw = float(ct.loc[idx_tuple, cv]) if cv in ct.columns else 0.0
                except KeyError:
                    raw = 0.0
                try:
                    alt_raw = float(ct_alt.loc[idx_tuple, cv]) if (ct_alt is not None and cv in ct_alt.columns) else None
                except KeyError:
                    alt_raw = None

            if agg_decimals is not None:
                raw = round(raw, agg_decimals)

            col_totals[col_vals.index(cv)] += raw
            cell_vals.append(raw)

            formatted = _format_cell_with_tooltip(raw, alt_raw, agg_func, show_tooltips, agg_decimals)
            row_cells.append(formatted)

        if st_show_row_totals:
            rows_data.append(list(combo) + row_cells + [sum(cell_vals)])
        else:
            rows_data.append(list(combo) + row_cells)

    if agg_decimals is not None:
        col_totals = [round(v, agg_decimals) for v in col_totals]
        for row in rows_data:
            if st_show_row_totals:
                row[-1] = round(row[-1], agg_decimals)

    grand_total = sum(col_totals)
    if agg_decimals is not None:
        grand_total = round(grand_total, agg_decimals)

    total_row = (['Total'] + [''] * (len(valid_row) - 1) + col_totals + [grand_total]) if st_show_col_totals else []

    row_labels = [_label(c, col_defs) if col_defs else c for c in valid_row]
    col_label = _SEP.join(_label(c, col_defs) if col_defs else c for c in valid_col)

    result = {
        '__type__': 'table',
        '__series_names__': [],
        'row_col':     valid_row[0] if len(valid_row) == 1 else '__dynst_row__',
        'col_col':     col_col,
        'row_labels':  row_labels,
        'row_label':   row_labels[0] if len(row_labels) == 1 else _SEP.join(row_labels),
        'col_label':   col_label,
        'num_row_labels': len(valid_row),
        'col_headers': col_vals,
        'rows':        rows_data,
        'total_row':   total_row,
        'st_show_row_totals': st_show_row_totals,
        'st_show_col_totals': st_show_col_totals,
        'show_tooltips': show_tooltips,
        '_drillMeta': {
            'stType':      'dynamic_summary_table',
            'stRowCol':    valid_row[0],
            'stColCol':    col_col,
            'stRowCols':   valid_row,
            'stColCols':   valid_col,
            'stRowVals':   row_combos,
            'colHeaders':  col_vals,
        },
    }

    if cfg.get('dyn_display_type', 'table') != 'table':
        return _convert_table_to_echarts(result, cfg, valid_row)
    return result


def _build_row_combos(val_lists, cols):
    """Build all ordered row value combinations as a list of tuples."""
    if not cols:
        return [()]
    result = [()]
    for col in cols:
        vals = val_lists.get(col, [])
        new_result = []
        for combo in result:
            for val in vals:
                new_result.append(combo + (val,))
        result = new_result
    return result


def _progress_growth_option(df, x_col, pg_before_col, pg_after_col, pl_scale, pg_display, cfg, labels, raw_rows=None):
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
    before_label = cfg.get('pg_before_label') or 'Before PL'
    after_label  = cfg.get('pg_after_label') or 'After PL'
    if pg_before_col not in df.columns or pg_after_col not in df.columns:
        missing = [c for c in (pg_before_col, pg_after_col) if c not in df.columns]
        return _message_option(f'PL column(s) not found in data: {", ".join(missing)}', cfg, error=True)

    # One row per student with both PL columns coalesced (matches data exploration).
    source_rows = raw_rows if raw_rows is not None else df.to_dict('records')
    source_rows = consolidate_progress_growth_rows(source_rows, pg_before_col, pg_after_col)
    df = pd.DataFrame(source_rows)

    _BLANK = frozenset({'', '-', '\u2014', '\u2013', 'N/A', 'n/a', 'None', 'null', 'NULL'})

    # Collect valid PL values
    all_vals = set()
    for col in (pg_before_col, pg_after_col):
        all_vals.update(
            _normalize_pl_val(v) for v in df[col].dropna().astype(str).unique()
            if _normalize_pl_val(v) not in _BLANK
        )

    # Build ordered scale
    if pl_scale:
        ordered = [_normalize_pl_val(v) for v in pl_scale if str(v).strip()]
    else:
        ordered = _auto_detect_pl_scale(list(all_vals))
    if not ordered:
        return _message_option('Could not determine PL scale — define it in the builder', cfg, error=True)

    rank = {v: i for i, v in enumerate(ordered)}

    # Classify each row
    dfc = df.copy()
    dfc['_before'] = dfc[pg_before_col].astype(str).apply(_normalize_pl_val)
    dfc['_after']  = dfc[pg_after_col].astype(str).apply(_normalize_pl_val)

    _dup_key = _find_student_id_key(list(dfc.columns))
    t = _theme(cfg)
    ax = _axis_style(cfg)

    # ── Transition Matrix ────────────────────────────────────────────────────
    if pg_display == 'matrix':
        # Use the exact same row filter + dedupe as drill-through (dict rows,
        # per-row student id) so cell counts always match the drill list.
        crossdata = []
        for i, before in enumerate(ordered):
            for j, after in enumerate(ordered):
                matched, _, _ = filter_progress_growth_matrix_rows(
                    source_rows, pg_before_col, pg_after_col, before, after,
                )
                count = len(matched)
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
            'top':    int(cfg.get('grid_top', 50 if cfg.get('title') else 30) or (50 if cfg.get('title') else 30)),
            'left':   int(cfg.get('grid_left', 55) or 55),
            'right':  int(cfg.get('grid_right', 20) or 20),
            'bottom': int(cfg.get('grid_bottom', 55) or 55),
            'containLabel': True,
        }
        mat_opt.update({
            'xAxis': {
                'type': 'category',
                'data': ordered,
                'name': after_label,
                'nameLocation': 'middle',
                'nameGap': 42,
                'nameTextStyle': {'fontWeight': 'bold', 'color': t['text']},
                **ax,
            },
            'yAxis': {
                'type': 'category',
                'data': ordered,
                'name': before_label,
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

    valid = dfc['_before'].isin(rank) & dfc['_after'].isin(rank)
    dfc = dfc[valid].copy()

    # Bar / donut / pie: one transition per student — dedupe after valid PL filter.
    if _dup_key:
        dfc = dfc.drop_duplicates(subset=_dup_key, keep='first')

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
            'label': {'show': bool(cfg.get('data_labels', True)), 'formatter': '{b}: {c}', 'fontSize': int(cfg.get('label_font_size', 10) or 10)},
        }]
        option['tooltip'] = {
            'show': cfg.get('show_tooltip', True) is not False,
            'trigger': 'item',
            '__formatter_js__': tooltip_js,
        }
        return option

    # ── Stacked / Grouped Bar ─────────────────────────────────────────────────
    option = _base_option(cfg)
    stacked = pg_display != 'grouped_bar'

    def make_series(cat, data_vals):
        label_cfg = {
            'show': show_labels,
            'position': label_pos,
            'fontSize': int(cfg.get('label_font_size', 10) or 10),
        }
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
            f"  var cats = {json.dumps(categories)};"
            f"  var raw = {json.dumps({cat: {xv: raw[cat].get(xv, 0) for xv in categories} for cat in ORDER})};"
            f"  var totals = {json.dumps({xv: cat_totals[xv] for xv in categories})};"
            "  var dataIdx = params[0].dataIndex;"
            "  var cat = (dataIdx !== undefined && cats[dataIdx]) ? cats[dataIdx] : (params[0].name || '');"
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
                'label': {'show': show_labels, 'position': label_pos, 'fontSize': int(cfg.get('label_font_size', 10) or 10)},
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

    df = pd.DataFrame(rows)

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

    # Composite x-axis
    x_cols = [c for c in x_cols_raw if isinstance(c, str) and c in df.columns] if isinstance(x_cols_raw, list) else []
    if len(x_cols) > 1:
        # Vectorized string concat instead of row-wise .agg(join, axis=1) —
        # same output, but avoids a per-row Python call (~60x faster on
        # large result sets).
        _filled = df[x_cols].fillna('(Blank)').astype(str)
        _comp = _filled[x_cols[0]]
        for _c in x_cols[1:]:
            _comp = _comp.str.cat(_filled[_c], sep=' · ')
        df['__x_comp__'] = _comp
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
    ('dynamic_summary_table', 'Dynamic Summary Table', 'chart-dyntable', 'Comparison'),
    ('waterfall',    'Waterfall',     'chart-waterfall',     'Comparison'),
    ('line',         'Line',          'chart-line',          'Trend'),
    ('date_series',  'Dates',         'chart-dates',          'Trend'),
    ('parliament',   'Parliament',    'chart-parliament',    'Proportion'),
    ('spline',       'Parliament',    'chart-parliament',    'Proportion'),   # backward compat alias
    ('pie',          'Pie',           'chart-pie',           'Proportion'),
    ('donut',        'Donut',         'chart-donut',         'Proportion'),
    ('funnel',       'Funnel',        'chart-funnel',        'Proportion'),
    ('treemap',      'Treemap',       'chart-treemap',       'Proportion'),
    ('scatter',      'Scatter',       'chart-scatter',       'Relationship'),
    ('jitter',       'Jitter',        'chart-jitter',        'Relationship'),
    ('bubble',       'Bubble',        'chart-bubble',        'Relationship'),
    ('heatmap',      'Heatmap',       'chart-heatmap',       'Relationship'),
    ('histogram',    'Histogram',     'chart-histogram',     'Distribution'),
    ('box',          'Box Plot',      'chart-box',           'Distribution'),
    ('radar',        'Radar',         'chart-radar',         'Advanced'),
    ('gauge',        'Gauge',         'chart-gauge',         'Advanced'),
]


# ── Chart Recommendation Engine (Phase 6) ──────────────────────────────────────

# Heuristic rules: column patterns → recommended chart types with scores.
# Each rule is (condition_fn, chart_key, score_adjustment, reason).
_RECOMMENDATION_RULES = []


def _register_rule(condition, chart_key, score=10, reason=''):
    _RECOMMENDATION_RULES.append((condition, chart_key, score, reason))


# Assessment PL columns present → Assessment Compare is a top recommendation
def _has_pl_columns(columns, col_defs):
    return any(c.endswith('_PL') and c.startswith('test_') for c in columns)

_register_rule(_has_pl_columns, 'assessment_compare', 25, 'Performance Level columns detected — Assessment Compare shows PL distribution across tests')

# Multiple test score columns in different years → Progress Growth
def _has_multi_year_test_scores(columns, col_defs):
    test_cols = [c for c in columns if c.startswith('test_') and c.endswith('_SS')]
    years = set()
    for c in test_cols:
        m = _YEAR_RE.match(c)
        if m:
            years.add(f"{m.group(1)}-{m.group(2)}")
    return len(years) >= 2

_register_rule(_has_multi_year_test_scores, 'progress_growth', 22, 'Multiple years of test scores found — Progress Growth tracks improvement')

# Single time dimension + numeric metric = Line chart
def _has_time_dimension(columns, col_defs):
    return any(c in ('school_year', 'grade') for c in columns)

_register_rule(_has_time_dimension, 'line', 15, 'Time/grade dimension detected — Line chart shows trends')

# Categorical dimension + numeric value = Bar or Column
def _has_categorical_and_numeric(columns, col_defs):
    has_cat = any(c in ('grade', 'school', 'gender', 'race', 'ell', 'status', 'sped') for c in columns)
    has_num = any(c.endswith('_SS') for c in columns) or any(c in ('student_id', 'atendace_rate', 'attendance_rate') for c in columns)
    return has_cat and has_num

_register_rule(_has_categorical_and_numeric, 'bar', 18, 'Categorical dimension with numeric data — Bar chart is the clearest comparison')

# Many categories → consider pie/donut for proportions
def _has_few_cat_columns(columns, col_defs):
    cat_cols = [c for c in columns if c in ('grade', 'gender', 'race', 'ell', 'status', 'sped')]
    return len(cat_cols) >= 1

_register_rule(_has_few_cat_columns, 'pie', 10, 'Categorical data — Pie/Donut shows proportion breakdown')

_register_rule(_has_few_cat_columns, 'donut', 11, 'Categorical data — Donut chart for proportion breakdown')

# Two numeric columns → scatter
def _has_two_numeric(columns, col_defs):
    num_cols = [c for c in columns if c.endswith('_SS') or c in ('atendace_rate', 'attendance_rate', 'suspension_indicator')]
    return len(num_cols) >= 2

_register_rule(_has_two_numeric, 'scatter', 12, 'Two numeric columns — Scatter plot shows correlation')

# Multiple categorical columns → heatmap or grouped bar
def _has_multiple_categories(columns, col_defs):
    cat_cols = [c for c in columns if c in ('grade', 'school', 'gender', 'race', 'ell', 'status', 'sped')]
    return len(cat_cols) >= 2

_register_rule(_has_multiple_categories, 'heatmap', 8, 'Multiple categories — Heatmap reveals patterns in cross-tabulation')

# Single numeric column → histogram
def _has_single_numeric(columns, col_defs):
    num_cols = [c for c in columns if c.endswith('_SS') or c in ('atendace_rate', 'attendance_rate')]
    return len(num_cols) == 1

_register_rule(_has_single_numeric, 'histogram', 7, 'Single numeric column — Histogram shows distribution')

# Many assessment PL columns → stacked bar works well
def _has_many_pl_columns(columns, col_defs):
    pl_cols = [c for c in columns if c.endswith('_PL') and c.startswith('test_')]
    return len(pl_cols) >= 4

_register_rule(_has_many_pl_columns, 'stacked_bar', 13, 'Many PL columns — Stacked Bar shows distribution composition')

# Dynamic Summary Table for data exploration with many columns
def _has_many_diverse_columns(columns, col_defs):
    return len(columns) >= 8

_register_rule(_has_many_diverse_columns, 'dynamic_summary_table', 7, 'Many columns selected — Dynamic Summary Table lets you cross-tab multiple dimensions')

# Summary Table for data exploration
def _has_diverse_columns(columns, col_defs):
    return len(columns) >= 6

_register_rule(_has_diverse_columns, 'summary_table', 6, 'Many columns selected — Summary Table is great for data exploration')


def recommend_chart_types(columns: list, column_defs: dict | None = None) -> list[dict]:
    """Recommend chart types based on selected columns.

    Returns a list of {chart_type, label, icon, reason, score} sorted by score descending.
    """
    if not columns:
        return []

    col_defs = column_defs or {}
    scores: dict[str, int] = {}
    reasons: dict[str, str] = {}

    for condition, chart_key, score, reason in _RECOMMENDATION_RULES:
        try:
            if condition(columns, col_defs):
                scores[chart_key] = scores.get(chart_key, 0) + score
                if chart_key not in reasons:
                    reasons[chart_key] = reason
        except Exception:
            pass

    # Always recommend bar as a safe fallback
    if 'bar' not in scores:
        scores['bar'] = 5
        reasons['bar'] = 'Bar chart — the most universal and readable chart type'

    # Map chart key to label
    chart_label_map = dict((k, l) for k, l, _, _ in CHART_TYPES)
    chart_icon_map = dict((k, i) for k, _, i, _ in CHART_TYPES)
    chart_group_map = dict((k, g) for k, _, _, g in CHART_TYPES)

    results = []
    for chart_key, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        results.append({
            'chart_type': chart_key,
            'label': chart_label_map.get(chart_key, chart_key),
            'icon': chart_icon_map.get(chart_key, 'chart-bar'),
            'group': chart_group_map.get(chart_key, ''),
            'reason': reasons.get(chart_key, ''),
            'score': score,
        })

    return results[:5]  # top 5


# ── Auto-Description Engine (Phase 6) ──────────────────────────────────────────

_CHART_TYPE_NAME_MAP = dict((k, l) for k, l, _, _ in CHART_TYPES)

# Add some common names not in CHART_TYPES
_CHART_TYPE_NAME_MAP.update({
    'grouped_bar': 'Grouped Bar Chart',
    'stacked_bar': 'Stacked Bar Chart',
    'column': 'Column Chart',
})


def auto_describe_chart(cfg: dict, column_defs: dict | None = None) -> str:
    """Generate a plain-English description of a chart configuration.

    Example: "A bar chart showing count of students by Grade for This School Year,
    grouped by Race, at Lincoln Elementary."
    """
    col_defs = column_defs or {}
    chart_type = cfg.get('chart_type', 'bar')
    chart_type_name = _CHART_TYPE_NAME_MAP.get(chart_type, chart_type.replace('_', ' ').title())

    x_col = cfg.get('x_col', '')
    y_col = cfg.get('y_col', '')
    series_col = cfg.get('series_col', '') or cfg.get('stack_col', '')
    agg = cfg.get('agg', 'count')

    def _col_label(col_key):
        if not col_key:
            return None
        label = col_defs.get(col_key, '')
        if label:
            return str(label).split('\n')[0].strip()
        # Fall back to readable version of the key
        return col_key.replace('_', ' ').title()

    parts = [f'A {chart_type_name}']

    # What's being shown
    y_label = _col_label(y_col)
    if y_label and agg not in ('count', 'count_occurrence'):
        display_agg = agg.replace('_', ' ')
        parts.append(f'showing {display_agg} of {y_label}')
    elif y_label:
        parts.append(f'showing {y_label}')
    elif agg == 'count':
        parts.append('showing count of students')
    elif agg == 'count_occurrence':
        parts.append('showing count of occurrences')

    # What's on the X axis
    x_label = _col_label(x_col)
    if x_label:
        parts.append(f'by {x_label}')

    # Grouping
    s_label = _col_label(series_col)
    if s_label:
        parts.append(f'grouped by {s_label}')

    # School filter info
    school_mode = cfg.get('school_filter_mode', 'enrolled')
    source_filters = cfg.get('source_filters', {})
    school_name = ''
    if isinstance(source_filters, dict):
        school_val = source_filters.get('school', '')
        if isinstance(school_val, list) and school_val:
            school_name = school_val[0]
        elif isinstance(school_val, str) and school_val:
            school_name = school_val

    if school_name:
        scope = 'enrollment' if school_mode == 'enrolled' else 'tested'
        parts.append(f'at {school_name} ({scope} school)')

    # School year context
    if isinstance(source_filters, dict):
        sy = source_filters.get('school_year', '')
        if sy:
            if sy == '__sy_this__':
                parts.append('for This School Year')
            elif sy == '__sy_prev1__':
                parts.append('for Last School Year')
            elif sy == '__sy_prev2__':
                parts.append('for Two Years Prior')
            elif isinstance(sy, str) and sy:
                parts.append(f'for {sy}')

    return ' '.join(parts) + '.'

