"""Shared helpers: meta melt, aggregation, themes, axes, tooltips, maps."""
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
    # Legends show by default: any missing/blank position means "bottom"
    # (visible). Only an explicit 'hidden' value turns them off.
    pos       = (cfg.get('legend_pos') or 'bottom').strip() or 'bottom'
    if pos == 'hidden':
        return {'show': False}

    item_gap   = int(cfg.get('legend_item_gap',   10) or 10)
    font_size  = int(cfg.get('legend_font_size',  11) or 11)
    icon_size  = int(cfg.get('legend_icon_size',  10) or 10)
    # User-overridden legend offset from its docked edge (px)
    legend_offset = cfg.get('legend_offset')   # None = use default
    legend_left   = cfg.get('legend_left')     # None = use default
    legend_top    = cfg.get('legend_top')      # None = use default

    if cfg.get('is_embed', False):
        legend_offset = 0
        legend_left = None
        legend_top = None

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


_X_COMP_SEP = ' · '


def _alias_category_label(label, value_map: dict, sep: str = _X_COMP_SEP):
    """Map a category label, including each part of a composite ``A · B`` label."""
    if label is None:
        return label
    s = str(label)
    if not s:
        return label
    if s in value_map:
        return value_map[s]
    if sep in s:
        return sep.join(value_map.get(part, part) for part in s.split(sep))
    return label


def _apply_x_value_map(option: dict, cfg: dict) -> None:
    """Replace category-axis *display* labels using ``cfg['x_value_map']``.

    Aliases match individual field values (e.g. grade ``K`` or year ``2024``),
    not only full composite labels. On grouped multi-field axes both the leaf
    and outer group axes are remapped. Raw values stay in
    ``__category_names__`` for drill-through.
    """
    raw_map = cfg.get('x_value_map') or {}
    if not isinstance(raw_map, dict) or not raw_map:
        return
    value_map = {
        str(k): str(v)
        for k, v in raw_map.items()
        if k is not None and str(k).strip() != '' and v is not None and str(v).strip() != ''
    }
    if not value_map:
        return

    def _remap_axis(axis):
        if not isinstance(axis, dict) or axis.get('type') != 'category':
            return
        data = axis.get('data')
        if not isinstance(data, list) or not data:
            return
        axis['data'] = [_alias_category_label(v, value_map) for v in data]

    category_axis = (option.get('dv') or {}).get('category_axis', 'x')
    if category_axis == 'y':
        axes = option.get('yAxis')
    elif category_axis == 'angle':
        axes = option.get('angleAxis')
    elif category_axis == 'radius':
        axes = option.get('radiusAxis')
    else:
        axes = option.get('xAxis')

    if isinstance(axes, list):
        # Dual-axis layouts: remap leaf (0) and outer group (1+) separately.
        for axis in axes:
            _remap_axis(axis)
    else:
        _remap_axis(axes)

    bands = option.get('__group_label_bands__')
    if isinstance(bands, list):
        for band in bands:
            if isinstance(band, dict) and 'label' in band:
                band['label'] = _alias_category_label(band.get('label'), value_map)


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


def _group_bands_from_composite(categories, sep=' · '):
    """Return ``[{start, end, label}, ...]`` for contiguous composite parents.

    Spacer categories (leaf containing ``spacer_``) are skipped and split groups.
    """
    bands = []
    i = 0
    n = len(categories)
    while i < n:
        parts = str(categories[i]).split(sep)
        leaf = parts[-1] if parts else ''
        if 'spacer_' in leaf or len(parts) < 2:
            i += 1
            continue
        parent = sep.join(parts[:-1])
        j = i + 1
        while j < n:
            p2 = str(categories[j]).split(sep)
            leaf2 = p2[-1] if p2 else ''
            if 'spacer_' in leaf2 or len(p2) < 2:
                break
            if sep.join(p2[:-1]) != parent:
                break
            j += 1
        bands.append({'start': i, 'end': j - 1, 'label': parent})
        i = j
    return bands


def _attach_group_label_bands(option, categories, cfg, theme_text, axis, offset, cat_font_size, cat_rotation):
    """Place outer group labels: tick-aligned or true-centered via renderer bands."""
    bands = _group_bands_from_composite(categories)
    if not bands:
        return [''] * len(categories)

    center = cfg.get('center_group_labels', True) is not False
    show = cfg.get('show_categories', True) is not False
    group_labels = [''] * len(categories)
    if center:
        option['__group_label_bands__'] = [
            {
                'start': b['start'],
                'end': b['end'],
                'label': b['label'],
                'axis': axis,
                'fontSize': cat_font_size,
                'rotate': cat_rotation,
                'color': theme_text,
                'offset': offset,
                'show': show,
            }
            for b in bands
        ]
    else:
        for b in bands:
            mid = (b['start'] + b['end']) // 2
            group_labels[mid] = b['label']
    return group_labels


def _update_chart_axes(option, categories, series, y_axis_name, labels, cfg, ax, x_lbl, orientation='vertical', radial=False, polar=False):
    leaf_categories, group_labels = _build_grouped_axis_data(categories)
    
    # Insert spacers if group_gap_size is configured
    group_gap_size = int(cfg.get('group_gap_size', 0) or 0)
    if group_labels and group_gap_size > 0:
        new_categories = []
        new_leaf_categories = []
        new_group_labels = []
        spacer_indices = []
        
        last_parent = None
        for idx, cat in enumerate(categories):
            parts = cat.split(' · ')
            parent = ' · '.join(parts[:-1]) if len(parts) > 1 else ""
            if last_parent is not None and parent != last_parent:
                for j in range(group_gap_size):
                    spacer_cat = f"{last_parent} ·  spacer_{idx}_{j}"
                    new_categories.append(spacer_cat)
                    new_leaf_categories.append(" ")
                    new_group_labels.append("")
                    spacer_indices.append(len(new_categories) - 1)
            new_categories.append(cat)
            new_leaf_categories.append(leaf_categories[idx])
            new_group_labels.append(group_labels[idx])
            last_parent = parent
            
        if spacer_indices:
            categories = new_categories
            leaf_categories = new_leaf_categories
            group_labels = new_group_labels
            
            for s in series:
                if isinstance(s, dict) and 'data' in s:
                    new_data = []
                    orig_data = s['data']
                    orig_idx = 0
                    for i in range(len(categories)):
                        if i in spacer_indices:
                            new_data.append(None)
                        else:
                            if orig_idx < len(orig_data):
                                new_data.append(orig_data[orig_idx])
                                orig_idx += 1
                    s['data'] = new_data
    
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
        
    t = _theme(cfg)
    center_groups = cfg.get('center_group_labels', True) is not False
    if orientation == 'horizontal':
        if group_labels:
            cat_font_size = int(cfg.get('compare_cat_font_size', 12) or 12)
            cat_rotation  = int(cfg.get('compare_cat_rotation', 0) or 0)
            x_label_font_size = int(cfg.get('x_label_font_size', 11) or 11)
            x_label_rotation  = int(cfg.get('x_label_rotation', 0) or 0)
            
            # Estimate offset dynamically based on inner axis label rotation
            if x_label_rotation > 0:
                import math
                text_width = 12 * x_label_font_size * 0.6
                rad = math.radians(x_label_rotation)
                height_contrib = text_width * math.sin(rad) + x_label_font_size * math.cos(rad)
                offset = max(60, int(height_contrib + 12))
            else:
                offset = 60

            group_labels = _attach_group_label_bands(
                option, categories, cfg, t['text'], 'y', offset, cat_font_size, cat_rotation,
            )

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
                    'offset': offset,
                    'axisLine': {'show': False},
                    'axisTick': {'show': False},
                    'splitLine': {'show': False},
                    'axisLabel': {
                        'interval': 0,
                        'textStyle': {'fontWeight': 'bold'},
                        'fontSize': cat_font_size,
                        'rotate': cat_rotation,
                        'color': t['text'],
                        # Tick labels hidden when true-centered via graphic bands.
                        'show': (not center_groups) and (cfg.get('show_categories', True) is not False),
                    }
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
        cat_font_size = int(cfg.get('compare_cat_font_size', 12) or 12)
        cat_rotation  = int(cfg.get('compare_cat_rotation', 0) or 0)
        x_label_font_size = int(cfg.get('x_label_font_size', 11) or 11)
        x_label_rotation  = int(cfg.get('x_label_rotation', 0) or 0)

        # Estimate offset dynamically based on inner axis label rotation
        if x_label_rotation > 0:
            import math
            text_width = 12 * x_label_font_size * 0.6
            rad = math.radians(x_label_rotation)
            height_contrib = text_width * math.sin(rad) + x_label_font_size * math.cos(rad)
            offset = max(30, int(height_contrib + 12))
        else:
            offset = 30

        group_labels = _attach_group_label_bands(
            option, categories, cfg, t['text'], 'x', offset, cat_font_size, cat_rotation,
        )

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
                'offset': offset,
                'axisLine': {'show': False},
                'axisTick': {'show': False},
                'splitLine': {'show': False},
                'axisLabel': {
                    'interval': 0,
                    'textStyle': {'fontWeight': 'bold'},
                    'fontSize': cat_font_size,
                    'rotate': cat_rotation,
                    'color': t['text'],
                    'show': (not center_groups) and (cfg.get('show_categories', True) is not False),
                }
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


def _normalize_pl_val(v):
    if v is None:
        return ''

    try:
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
    normalized_vals = []
    for v in values:
        norm = _normalize_pl_val(v)
        if norm != '':
            normalized_vals.append(norm)
    val_set = set(normalized_vals)
    for scale in _KNOWN_PL_SCALES:
        if val_set.issubset(set(scale)):
            return [s for s in scale if s in val_set]
    return sorted(list(val_set))


# Expose private names to sibling ``import *`` (PEP 328).
__all__ = [n for n in list(globals()) if not n.startswith('__')]
