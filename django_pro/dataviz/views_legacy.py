import hashlib
import json
import re
import traceback

import pandas as pd

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.cache import cache
from django.db import connections
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView


def _is_project_admin(user) -> bool:
    """Return True for superusers and Project Admins only."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if hasattr(user, 'active_role') and user.active_role and user.active_role.name == 'Project Admin':
        return True
    if hasattr(user, 'roles') and user.roles.filter(name='Project Admin').exists():
        return True
    return False


_FORBIDDEN = JsonResponse({'error': 'Forbidden'}, status=403)

from dataexploration.column_utils import get_mark_columns, get_test_columns
from dataexploration.constants import COLUMN_CATEGORIES, COLUMN_DEFINITIONS
from dataexploration.models import SavedQuery
from dataexploration.utils import can_user_view_query
from dataexploration.view_modules.column_management import get_saved_default_columns

# ── Test / Mark meta-column helpers ────────────────────────────────────────
_YEAR_RE = re.compile(r'^(?:test|mark)_(\d{4})_(\d{4})_')
_META_DIM_COLS  = {'_assessment', '_test_school_year', '_test_window', '_mark_school_year', '_mark_period'}
_META_SCORE_COLS = {'_test_score', '_mark_score'}


def _parse_col_year(col: str) -> str:
    """Extract 'YYYY-YYYY' school year from a test/mark column key."""
    m = _YEAR_RE.match(col)
    return f"{m.group(1)}-{m.group(2)}" if m else ''


def _parse_col_meta(col: str, col_defs: dict) -> dict:
    """
    Parse a test_* or mark_* column key using its col_defs label for reliable extraction.

    Returns dict:
      col_type   : 'test' | 'mark'
      assessment : display name of the test/course   (e.g. 'NWEA Math', 'Algebra')
      year       : 'YYYY-YYYY'
      window     : test window or mark period string  (e.g. 'Fall', 'Q1')
    """
    label = col_defs.get(col, col)

    if col.startswith('test_'):
        # Label format built by column_utils:
        #   "{TestName} {Subject} (YYYY-YYYY) {metric}"
        #   optionally followed by "\n{window}"
        lines = label.split('\n')
        first = lines[0].strip()
        window = lines[1].strip() if len(lines) > 1 else ''

        # If no window in label, try key suffix season keyword as fallback
        if not window and (col.endswith('_SS') or col.endswith('_PL')):
            inner = col[5:-3]  # strip 'test_' and '_SS'/'_PL'
            m2 = re.match(r'\d{4}_\d{4}_(.+)', inner)
            if m2:
                last = m2.group(1).split('_')[-1].lower()
                if last in {'fall', 'winter', 'spring', 'summer', 'baseline', 'midyear'}:
                    window = last.capitalize()
                elif last in {'eoy', 'moy', 'boy'}:
                    window = last.upper()

        year_match = re.search(r'\((\d{4}-\d{4})\)', first)
        year = year_match.group(1) if year_match else _parse_col_year(col)

        # Assessment = everything before " (YYYY-YYYY)", minus trailing metric suffix
        if year_match:
            assessment = first[:year_match.start()].strip()
        else:
            assessment = first
        # Strip trailing metric label (' SS', ' PL', ' Scale Score', etc.)
        for sfx in (' SS', ' PL', ' Scale Score', ' Perf Lvl'):
            if assessment.endswith(sfx):
                assessment = assessment[:-len(sfx)].strip()
                break

        return {'col_type': 'test', 'assessment': assessment, 'year': year, 'window': window}

    elif col.startswith('mark_'):
        # Label format: "{CourseName} ({Subject}) {Period}\n{YYYY-YYYY}"
        lines = label.split('\n')
        first = lines[0].strip()
        year = lines[1].strip() if len(lines) > 1 else _parse_col_year(col)

        # Period = text after last ')'
        if ')' in first:
            period = first.split(')')[-1].strip()
            course_with_sub = first[:first.rfind(')')].strip()
            # Course name = text before ' (' of the subject
            assessment = course_with_sub[:course_with_sub.rfind(' (')].strip() if ' (' in course_with_sub else course_with_sub
        else:
            period = ''
            assessment = first

        return {'col_type': 'mark', 'assessment': assessment, 'year': year, 'window': period}

    return {'col_type': 'unknown', 'assessment': col, 'year': '', 'window': ''}


_META_LABEL_MAP = {
    '_assessment':       'Assessment',
    '_test_school_year': 'School Year',
    '_test_window':      'Test Window / Period',
    '_test_score':       'Test Score (SS)',
    '_mark_school_year': 'Mark School Year',
    '_mark_period':      'Mark Period',
    '_mark_score':       'Mark Value',
}


def _maybe_melt_for_meta(
    df: pd.DataFrame, x_col: str, stack_col: str, y_col: str, y_cols: list, col_defs: dict
):
    """
    When a meta-dimension or synthetic score column is used as any chart axis / series,
    melt ALL test_*_SS and mark_* columns from wide → long and inject computed columns:
      _assessment, _test_school_year, _test_window, _mark_school_year, _mark_period,
      _test_score  (numeric SS for tests; numeric value for marks)

    Returns (new_df, new_y_col, new_y_cols).
    """
    uses_meta = (
        x_col     in _META_DIM_COLS | _META_SCORE_COLS
        or stack_col in _META_DIM_COLS
        or y_col     in _META_SCORE_COLS
        or any(c in _META_SCORE_COLS for c in (y_cols or []))
    )
    if not uses_meta:
        return df, y_col, y_cols

    test_ss_cols = [c for c in df.columns if c.startswith('test_') and c.endswith('_SS') and _YEAR_RE.match(c)]
    mark_cols    = [c for c in df.columns if c.startswith('mark_') and _YEAR_RE.match(c)]
    score_cols   = test_ss_cols + mark_cols
    if not score_cols:
        return df, y_col, y_cols

    # Build meta lookup from col_defs once (avoids per-row lambda overhead)
    meta_cache = {c: _parse_col_meta(c, col_defs) for c in score_cols}

    id_cols = [c for c in df.columns if c not in score_cols]
    melted = df.melt(
        id_vars=id_cols,
        value_vars=score_cols,
        var_name='_source_col',
        value_name='_test_score',
    )
    melted['_test_score'] = pd.to_numeric(melted['_test_score'], errors='coerce')
    melted = melted.dropna(subset=['_test_score'])  # drop students with no score for that test

    melted['_assessment']       = melted['_source_col'].map(lambda c: meta_cache[c]['assessment'])
    melted['_test_school_year'] = melted['_source_col'].map(lambda c: meta_cache[c]['year'])
    melted['_test_window']      = melted['_source_col'].map(lambda c: meta_cache[c]['window'])
    # Alias mark year / period to the same columns so mark data works with split-by-year/period too
    melted['_mark_school_year'] = melted['_test_school_year']
    melted['_mark_period']      = melted['_test_window']
    melted = melted.drop(columns=['_source_col'])

    new_y_col = '_test_score' if (y_col in score_cols or y_col in _META_SCORE_COLS or not y_col) else y_col
    return melted, new_y_col, []


_PALETTES = {
    'default': ['#636EFA', '#EF553B', '#00CC96', '#AB63FA', '#FFA15A', '#19D3F3', '#FF6692', '#B6E880'],
    'blue':    ['#4472c4', '#5b9bd5', '#264478', '#70ad47', '#ffc000', '#f4522d', '#9e480e', '#636363'],
    'warm':    ['#e74c3c', '#e67e22', '#f39c12', '#f1c40f', '#ff5733', '#c0392b', '#d35400', '#e8b800'],
    'earth':   ['#8b7355', '#a9956c', '#c8a96e', '#7f6000', '#5d4e37', '#bfa98e', '#6d4c41', '#4e342e'],
    'green':   ['#27ae60', '#2ecc71', '#1abc9c', '#16a085', '#52be80', '#76d7c4', '#239b56', '#0e6655'],
    'purple':  ['#9b59b6', '#8e44ad', '#a569bd', '#6c3483', '#bb8fce', '#7d3c98', '#d2b4de', '#76448a'],
    'mono':    ['#2c3e50', '#7f8c8d', '#95a5a6', '#bdc3c7', '#34495e', '#566573', '#808b96', '#1a252f'],
}


def _label(col: str, col_defs: dict) -> str:
    raw = col_defs.get(col, col) or col
    return str(raw).replace('\n', ' — ').replace('\r', '')


def _agg(df: pd.DataFrame, x_col: str, y_col: str, agg: str, stack_col: str = '') -> pd.DataFrame:
    if x_col not in df.columns:
        return pd.DataFrame()

    group_cols = [x_col]
    if stack_col and stack_col in df.columns:
        group_cols.append(stack_col)

    try:
        if agg in ('sum', 'avg', 'min', 'max', 'median') and y_col and y_col in df.columns:
            dfc = df.copy()
            dfc[y_col] = pd.to_numeric(dfc[y_col], errors='coerce')
            grp = dfc.groupby(group_cols, dropna=False)[y_col]
            agg_result = {
                'sum': grp.sum(),
                'avg': grp.mean(),
                'min': grp.min(),
                'max': grp.max(),
                'median': grp.median(),
            }.get(agg, grp.size())
            result = agg_result.reset_index()
            result = result.rename(columns={y_col: '_value'})
        else:
            result = df.groupby(group_cols, dropna=False).size().reset_index(name='_value')
    except Exception:
        result = df.groupby(group_cols, dropna=False).size().reset_index(name='_value')

    for c in group_cols:
        result[c] = result[c].fillna('(Blank)').astype(str)
    return result


def _theme(cfg: dict) -> dict:
    colors = _PALETTES.get(cfg.get('palette', 'default'), _PALETTES['default'])
    bg_map = {'white': '#ffffff', 'light': '#f8fafc', 'dark': '#1e2432'}
    bg = bg_map.get(cfg.get('background', 'white'), '#ffffff')
    is_dark = cfg.get('background') == 'dark'
    text = '#e5e7eb' if is_dark else '#374151'
    grid = '#4b5563' if is_dark else '#e5e7eb'
    return {'colors': colors, 'bg': bg, 'text': text, 'grid': grid}


def _legend(cfg: dict, text: str) -> dict:
    legend_pos = cfg.get('legend_pos', 'bottom')
    if legend_pos == 'hidden':
        return {'show': False}

    base = {
        'show': True,
        'textStyle': {'color': text, 'fontSize': 11},
    }

    if legend_pos == 'top':
        base.update({'top': 4, 'left': 'center'})
    elif legend_pos == 'left':
        base.update({'left': 4, 'top': 'middle', 'orient': 'vertical'})
    elif legend_pos == 'right':
        base.update({'right': 4, 'top': 'middle', 'orient': 'vertical'})
    else:
        base.update({'bottom': 0, 'left': 'center'})
    return base


def _base_option(cfg: dict, axis_trigger: bool = True) -> dict:
    theme = _theme(cfg)
    title_text = cfg.get('title', '')
    return {
        'animation': True,
        'color': theme['colors'],
        'backgroundColor': theme['bg'],
        'aria': {'enabled': True},
        'textStyle': {'color': theme['text'], 'fontFamily': 'Inter, system-ui, sans-serif'},
        'title': {
            'show': bool(title_text),
            'text': title_text,
            'left': 'center',
            'textStyle': {'color': theme['text'], 'fontSize': 15, 'fontWeight': 600},
        },
        'legend': _legend(cfg, theme['text']),
        'tooltip': {
            'trigger': 'axis' if axis_trigger else 'item',
            'axisPointer': {'type': 'shadow'} if axis_trigger else {},
        },
        'toolbox': {
            'show': True,
            'right': 8,
            'feature': {
                'dataZoom': {'yAxisIndex': 'none'},
                'dataView': {'readOnly': True},
                'restore': {},
                'saveAsImage': {},
            },
        },
        'grid': {'left': 55, 'right': 20, 'top': 50 if title_text else 24, 'bottom': 60, 'containLabel': True},
        'dv': {
            'curve': cfg.get('curve', 'linear'),
            'opacity': float(cfg.get('opacity', 0.9) or 0.9),
            'point_size': int(cfg.get('point_size', 6) or 6),
            'zoom_pan': bool(cfg.get('zoom_pan', True)),
            'line_width': int(cfg.get('line_width', 2) or 2),
            'border_radius': int(cfg.get('border_radius', 6) or 6),
            'stacking_mode': 'percent' if str(cfg.get('stacking_mode', 'normal')) == 'percent' else 'normal',
            'tooltip_shared': bool(cfg.get('tooltip_shared', True)),
            'animate': bool(cfg.get('animate', True)),
            'inverted': bool(cfg.get('inverted', False)),
            'polar': bool(cfg.get('polar', False)),
        },
    }


def _add_axis_zoom(option: dict) -> None:
    option['dataZoom'] = [
        {'type': 'inside', 'xAxisIndex': 0},
        {'type': 'slider', 'xAxisIndex': 0, 'bottom': 14},
    ]


def _axis_style(cfg: dict) -> dict:
    theme = _theme(cfg)
    return {
        'axisLine': {'lineStyle': {'color': theme['grid']}},
        'axisTick': {'lineStyle': {'color': theme['grid']}},
        'axisLabel': {'color': theme['text']},
        'splitLine': {'lineStyle': {'color': theme['grid']}},
    }


def _message_option(text: str, cfg: dict, error: bool = False) -> dict:
    opt = _base_option(cfg, axis_trigger=False)
    opt['title'] = {
        'show': True,
        'text': text,
        'left': 'center',
        'top': 'middle',
        'textStyle': {'color': '#dc2626' if error else _theme(cfg)['text'], 'fontSize': 14, 'fontWeight': 500},
    }
    opt['xAxis'] = {'show': False}
    opt['yAxis'] = {'show': False}
    opt['series'] = []
    return opt


def _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, stacked, col_defs):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)

    option = _base_option(cfg, axis_trigger=True)
    axis_style = _axis_style(cfg)
    opacity = float(cfg.get('opacity', 0.9) or 0.9)

    valid_y_cols = [c for c in (y_cols or []) if c in df.columns]
    if len(valid_y_cols) == 1 and not y_col:
        y_col = valid_y_cols[0]

    if len(valid_y_cols) > 1:
        categories = sorted(df[x_col].fillna('(Blank)').astype(str).unique().tolist())
        series = []
        for metric_col in valid_y_cols:
            # Use count for non-numeric columns; apply chosen agg for numerics
            col_agg = agg if (pd.api.types.is_numeric_dtype(df[metric_col])
                              and agg in ('sum', 'avg', 'min', 'max', 'median')) else 'count'
            adf = _agg(df, x_col, metric_col, col_agg)
            vm = dict(zip(adf[x_col].astype(str), adf['_value']))
            series.append({
                'name': _label(metric_col, col_defs),
                'type': 'bar',
                'stack': 'total' if stacked else None,
                'label': {'show': bool(cfg.get('data_labels', False)), 'position': 'top'},
                'itemStyle': {'opacity': opacity},
                'data': [float(vm.get(xv, 0) or 0) for xv in categories],
            })

        option.update({
            'xAxis': {'type': 'category', 'data': categories, 'name': labels['x'], **axis_style},
            'yAxis': {'type': 'value', 'name': 'Value', **axis_style},
            'series': series,
        })
        _add_axis_zoom(option)
        return option

    if stack_col and stack_col in df.columns:
        adf = _agg(df, x_col, y_col, agg, stack_col)
        categories = sorted(adf[x_col].astype(str).unique().tolist())
        stacks = sorted(adf[stack_col].astype(str).unique().tolist())
        series = []
        for stack_value in stacks:
            sub = adf[adf[stack_col].astype(str) == stack_value]
            vm = dict(zip(sub[x_col].astype(str), sub['_value']))
            series.append({
                'name': stack_value,
                'type': 'bar',
                'stack': 'total' if stacked else None,
                'label': {'show': bool(cfg.get('data_labels', False)), 'position': 'top'},
                'itemStyle': {'opacity': opacity},
                'data': [float(vm.get(xv, 0) or 0) for xv in categories],
            })
        option.update({
            'xAxis': {'type': 'category', 'data': categories, 'name': labels['x'], **axis_style},
            'yAxis': {'type': 'value', 'name': labels['y'] or 'Count', **axis_style},
            'series': series,
        })
        _add_axis_zoom(option)
        return option

    adf = _agg(df, x_col, y_col, agg).sort_values('_value', ascending=False)
    option.update({
        'xAxis': {'type': 'category', 'data': adf[x_col].astype(str).tolist(), 'name': labels['x'], **axis_style},
        'yAxis': {'type': 'value', 'name': labels['y'] or 'Count', **axis_style},
        'series': [{
            'name': labels['y'] or 'Value',
            'type': 'bar',
            'label': {'show': bool(cfg.get('data_labels', False)), 'position': 'top'},
            'itemStyle': {'opacity': opacity},
            'data': [float(v or 0) for v in adf['_value'].tolist()],
        }],
    })
    _add_axis_zoom(option)
    return option


def _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, fill, stacked, y_cols=None, col_defs=None, series_type='line'):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)

    option = _base_option(cfg, axis_trigger=True)
    axis_style = _axis_style(cfg)
    smooth = bool(cfg.get('smooth', False))
    show_points = bool(cfg.get('show_points', False))
    opacity = float(cfg.get('opacity', 0.9) or 0.9)
    curve = cfg.get('curve', 'linear')

    valid_y_cols = [c for c in (y_cols or []) if c in df.columns]
    if len(valid_y_cols) == 1 and not y_col:
        y_col = valid_y_cols[0]

    if len(valid_y_cols) > 1:
        categories = sorted(df[x_col].fillna('(Blank)').astype(str).unique().tolist())
        series = []
        for metric_col in valid_y_cols:
            col_agg = agg if (pd.api.types.is_numeric_dtype(df[metric_col])
                              and agg in ('sum', 'avg', 'min', 'max', 'median')) else 'count'
            adf = _agg(df, x_col, metric_col, col_agg)
            vm = dict(zip(adf[x_col].astype(str), adf['_value']))
            series.append({
                'name': _label(metric_col, col_defs or {}),
                'type': series_type,
                'smooth': smooth,
                'stack': 'total' if stacked else None,
                'areaStyle': {} if fill else None,
                'showSymbol': show_points,
                'symbolSize': 6 if show_points else 0,
                'lineStyle': {'opacity': opacity},
                'itemStyle': {'opacity': opacity},
                'curve': curve,
                'data': [float(vm.get(xv, 0) or 0) for xv in categories],
            })
        option.update({
            'xAxis': {'type': 'category', 'data': categories, 'name': labels['x'], **axis_style},
            'yAxis': {'type': 'value', 'name': 'Value', **axis_style},
            'series': series,
        })
        _add_axis_zoom(option)
        return option

    if stack_col and stack_col in df.columns:
        adf = _agg(df, x_col, y_col, agg, stack_col)
        categories = sorted(adf[x_col].astype(str).unique().tolist())
        stacks = sorted(adf[stack_col].astype(str).unique().tolist())
        series = []
        for stack_value in stacks:
            sub = adf[adf[stack_col].astype(str) == stack_value]
            vm = dict(zip(sub[x_col].astype(str), sub['_value']))
            series.append({
                'name': stack_value,
                'type': series_type,
                'smooth': smooth,
                'stack': 'total' if stacked else None,
                'areaStyle': {} if fill else None,
                'showSymbol': show_points,
                'symbolSize': 6 if show_points else 0,
                'lineStyle': {'opacity': opacity},
                'itemStyle': {'opacity': opacity},
                'curve': curve,
                'data': [float(vm.get(xv, 0) or 0) for xv in categories],
            })
        option.update({
            'xAxis': {'type': 'category', 'data': categories, 'name': labels['x'], **axis_style},
            'yAxis': {'type': 'value', 'name': labels['y'] or 'Count', **axis_style},
            'series': series,
        })
        _add_axis_zoom(option)
        return option

    adf = _agg(df, x_col, y_col, agg)
    option.update({
        'xAxis': {'type': 'category', 'data': adf[x_col].astype(str).tolist(), 'name': labels['x'], **axis_style},
        'yAxis': {'type': 'value', 'name': labels['y'] or 'Count', **axis_style},
        'series': [{
            'name': labels['y'] or 'Value',
            'type': series_type,
            'smooth': smooth,
            'areaStyle': {} if fill else None,
            'showSymbol': show_points,
            'symbolSize': 6 if show_points else 0,
            'lineStyle': {'opacity': opacity},
            'itemStyle': {'opacity': opacity},
            'curve': curve,
            'data': [float(v or 0) for v in adf['_value'].tolist()],
        }],
    })
    _add_axis_zoom(option)
    return option


def _pie_option(df, x_col, y_col, agg, cfg, donut=False):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)

    adf = _agg(df, x_col, y_col, agg).sort_values('_value', ascending=False).head(30)
    option = _base_option(cfg, axis_trigger=False)
    option.update({
        'series': [{
            'name': 'Distribution',
            'type': 'pie',
            'radius': ['45%', '70%'] if donut else '70%',
            'data': [
                {'name': str(r[x_col]), 'value': float(r['_value'] or 0)}
                for _, r in adf.iterrows()
            ],
            'label': {'show': bool(cfg.get('data_labels', False))},
        }],
    })
    return option


def _scatter_option(df, x_col, y_col, stack_col, size_col, cfg, labels, bubble=False):
    if not x_col or not y_col:
        return _message_option('X and Y are required for scatter', cfg, error=True)

    option = _base_option(cfg, axis_trigger=True)
    axis_style = _axis_style(cfg)

    dfc = df.copy()
    dfc[x_col] = pd.to_numeric(dfc[x_col], errors='coerce')
    dfc[y_col] = pd.to_numeric(dfc[y_col], errors='coerce')
    if size_col and size_col in dfc.columns:
        dfc[size_col] = pd.to_numeric(dfc[size_col], errors='coerce')
    dfc = dfc.dropna(subset=[x_col, y_col])

    point_size = int(cfg.get('point_size', 6) or 6)
    min_size = max(3, int(point_size * 0.8))
    max_size = max(8, int(point_size * 2.4))

    size_min = size_max = None
    if bubble and size_col and size_col in dfc.columns:
        valid_sizes = dfc[size_col].dropna()
        if not valid_sizes.empty:
            size_min = float(valid_sizes.min())
            size_max = float(valid_sizes.max())

    def bubble_size(value):
        if size_min is None or size_max is None or size_max <= size_min:
            return max_size
        return float(min_size + ((float(value) - size_min) / (size_max - size_min)) * (max_size - min_size))

    if stack_col and stack_col in dfc.columns:
        series = []
        for g in sorted(dfc[stack_col].astype(str).unique().tolist()):
            sub = dfc[dfc[stack_col].astype(str) == g]
            series.append({
                'name': g,
                'type': 'scatter',
                'symbolSize': point_size,
                'bubble': bubble,
                'data': [
                    [
                        float(x),
                        float(y),
                        (float(sz) if (bubble and size_col and pd.notna(sz)) else None),
                        (bubble_size(float(sz)) if (bubble and size_col and pd.notna(sz)) else point_size),
                    ]
                    for x, y, sz in zip(sub[x_col], sub[y_col], sub[size_col] if (size_col and size_col in sub.columns) else [None] * len(sub))
                ],
            })
    else:
        series = [{
            'name': labels['y'] or 'Values',
            'type': 'scatter',
            'symbolSize': point_size,
            'bubble': bubble,
            'data': [
                [
                    float(x),
                    float(y),
                    (float(sz) if (bubble and size_col and pd.notna(sz)) else None),
                    (bubble_size(float(sz)) if (bubble and size_col and pd.notna(sz)) else point_size),
                ]
                for x, y, sz in zip(dfc[x_col], dfc[y_col], dfc[size_col] if (size_col and size_col in dfc.columns) else [None] * len(dfc))
            ],
        }]

    option.update({
        'xAxis': {'type': 'value', 'name': labels['x'], **axis_style},
        'yAxis': {'type': 'value', 'name': labels['y'], **axis_style},
        'series': series,
    })
    _add_axis_zoom(option)
    return option


def _heatmap_option(df, x_col, y_col, agg, cfg, labels):
    if not x_col or not y_col:
        return _message_option('X and Y are required for heatmap', cfg, error=True)

    adf = _agg(df, x_col, y_col, agg, stack_col=y_col)
    option = _base_option(cfg, axis_trigger=False)
    axis_style = _axis_style(cfg)

    if adf.empty:
        return _message_option('No data loaded', cfg)

    x_values = sorted(adf[x_col].astype(str).unique().tolist())
    y_values = sorted(adf[y_col].astype(str).unique().tolist())
    xi = {v: i for i, v in enumerate(x_values)}
    yi = {v: i for i, v in enumerate(y_values)}

    points = []
    max_val = 0
    for _, row in adf.iterrows():
        val = float(row['_value'] or 0)
        points.append([xi[str(row[x_col])], yi[str(row[y_col])], val])
        max_val = max(max_val, val)

    option.update({
        'xAxis': {'type': 'category', 'data': x_values, 'name': labels['x'], **axis_style},
        'yAxis': {'type': 'category', 'data': y_values, 'name': labels['y'], **axis_style},
        'visualMap': {
            'min': 0,
            'max': max_val or 1,
            'calculable': True,
            'orient': 'vertical',
            'right': 10,
            'top': 'center',
        },
        'series': [{
            'type': 'heatmap',
            'data': points,
            'label': {'show': bool(cfg.get('data_labels', False))},
        }],
    })
    _add_axis_zoom(option)
    return option


def _histogram_option(df, x_col, stack_col, cfg, labels):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)

    option = _base_option(cfg, axis_trigger=True)
    axis_style = _axis_style(cfg)
    bins_count = cfg.get('bins', 12)
    try:
        bins_count = int(bins_count)
    except (TypeError, ValueError):
        bins_count = 12
    bins_count = max(4, min(40, bins_count))

    def hist_data(values, bins):
        vals = pd.to_numeric(values, errors='coerce').dropna()
        if vals.empty:
            counts = values.fillna('(Blank)').astype(str).value_counts().sort_index()
            return counts.index.tolist(), counts.values.tolist()

        binned = pd.cut(vals, bins=min(bins, max(4, int(vals.nunique() or 1))), include_lowest=True)
        counts = binned.value_counts().sort_index()
        labels_out = [f'{iv.left:.1f}–{iv.right:.1f}' for iv in counts.index]
        return labels_out, counts.values.tolist()

    if stack_col and stack_col in df.columns:
        series = []
        all_x = []
        per_group = {}
        for g in sorted(df[stack_col].fillna('(Blank)').astype(str).unique().tolist()):
            sub = df[df[stack_col].fillna('(Blank)').astype(str) == g]
            bins, counts = hist_data(sub[x_col], bins_count)
            per_group[g] = dict(zip(bins, counts))
            all_x.extend(bins)
        x_axis = sorted(set(all_x))

        for g, vm in per_group.items():
            series.append({
                'name': g,
                'type': 'bar',
                'stack': 'total',
                'data': [int(vm.get(b, 0)) for b in x_axis],
            })
    else:
        x_axis, counts = hist_data(df[x_col], bins_count)
        series = [{'name': 'Count', 'type': 'bar', 'data': [int(v) for v in counts]}]

    option.update({
        'xAxis': {'type': 'category', 'data': x_axis, 'name': labels['x'], **axis_style},
        'yAxis': {'type': 'value', 'name': 'Count', **axis_style},
        'series': series,
    })
    _add_axis_zoom(option)
    return option


def _boxplot_option(df, x_col, y_col, cfg, labels):
    if not y_col or y_col not in df.columns:
        return _message_option('Y / Value is required', cfg, error=True)

    option = _base_option(cfg, axis_trigger=True)
    axis_style = _axis_style(cfg)

    def stats(vals):
        s = pd.to_numeric(vals, errors='coerce').dropna()
        if s.empty:
            return None
        q = s.quantile([0.25, 0.5, 0.75])
        return [float(s.min()), float(q.loc[0.25]), float(q.loc[0.5]), float(q.loc[0.75]), float(s.max())]

    labels_out = []
    data_out = []

    if x_col and x_col in df.columns:
        for group in sorted(df[x_col].fillna('(Blank)').astype(str).unique().tolist()):
            sub = df[df[x_col].fillna('(Blank)').astype(str) == group]
            st = stats(sub[y_col])
            if st:
                labels_out.append(group)
                data_out.append(st)
    else:
        st = stats(df[y_col])
        if st:
            labels_out = ['All']
            data_out = [st]

    option.update({
        'xAxis': {'type': 'category', 'data': labels_out, 'name': labels['x'], **axis_style},
        'yAxis': {'type': 'value', 'name': labels['y'], **axis_style},
        'series': [{'type': 'boxplot', 'data': data_out}],
    })
    _add_axis_zoom(option)
    return option


def _funnel_option(df, x_col, y_col, agg, cfg):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)

    adf = _agg(df, x_col, y_col, agg).sort_values('_value', ascending=False)
    option = _base_option(cfg, axis_trigger=False)
    option.update({
        'series': [{
            'type': 'funnel',
            'left': '10%',
            'top': 40,
            'bottom': 20,
            'width': '80%',
            'sort': 'descending',
            'label': {'show': True, 'position': 'inside'},
            'data': [{'name': str(r[x_col]), 'value': float(r['_value'] or 0)} for _, r in adf.iterrows()],
        }],
    })
    return option


def _treemap_option(df, x_col, y_col, agg, cfg):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)

    adf = _agg(df, x_col, y_col, agg)
    option = _base_option(cfg, axis_trigger=False)
    option.update({
        'series': [{
            'type': 'treemap',
            'nodeClick': 'zoomToNode',
            'data': [{'name': str(r[x_col]), 'value': float(r['_value'] or 0)} for _, r in adf.iterrows()],
        }],
    })
    return option


def _radar_option(df, x_col, y_col, agg, cfg):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)

    adf = _agg(df, x_col, y_col, agg)
    values = [float(v or 0) for v in adf['_value'].tolist()]
    names = adf[x_col].astype(str).tolist()
    max_val = max(values) if values else 1

    option = _base_option(cfg, axis_trigger=False)
    option.update({
        'radar': {
            'indicator': [{'name': n, 'max': max_val * 1.1 if max_val else 1} for n in names],
            'splitArea': {'show': True},
        },
        'series': [{
            'type': 'radar',
            'data': [{'name': 'Values', 'value': values}],
        }],
    })
    return option


def _gauge_option(df, y_col, agg, cfg, labels):
    if not y_col or y_col not in df.columns:
        return _message_option('Y / Value is required', cfg, error=True)

    vals = pd.to_numeric(df[y_col], errors='coerce').dropna()
    if vals.empty:
        return _message_option('No numeric values for gauge', cfg, error=True)

    value = {
        'sum': float(vals.sum()),
        'avg': float(vals.mean()),
        'min': float(vals.min()),
        'max': float(vals.max()),
        'median': float(vals.median()),
    }.get(agg, float(len(df)))
    max_val = float(vals.max()) * 1.1 if agg != 'count' else float(max(len(df), 1)) * 1.2

    option = _base_option(cfg, axis_trigger=False)
    option.update({
        'series': [{
            'type': 'gauge',
            'max': max_val,
            'detail': {'formatter': '{value}'},
            'title': {'show': True, 'offsetCenter': [0, '78%']},
            'data': [{'value': round(value, 2), 'name': labels.get('y') or 'Value'}],
        }],
    })
    return option


def _solid_gauge_option(df, y_col, agg, cfg, labels):
    """Solid gauge – filled arc variant."""
    if not y_col or y_col not in df.columns:
        return _message_option('Y / Value is required', cfg, error=True)

    vals = pd.to_numeric(df[y_col], errors='coerce').dropna()
    if vals.empty:
        return _message_option('No numeric values for gauge', cfg, error=True)

    value = {
        'sum': float(vals.sum()),
        'avg': float(vals.mean()),
        'min': float(vals.min()),
        'max': float(vals.max()),
        'median': float(vals.median()),
    }.get(agg, float(len(df)))
    max_val = float(vals.max()) * 1.1 if agg != 'count' else float(max(len(df), 1)) * 1.2

    option = _base_option(cfg, axis_trigger=False)
    option.update({
        'series': [{
            'type': 'solidgauge',
            'max': max_val,
            'data': [{'value': round(value, 2), 'name': labels.get('y') or 'Value'}],
        }],
    })
    return option


def _waterfall_option(df, x_col, y_col, agg, cfg, labels):
    """Waterfall chart – sequential additive values."""
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)

    adf = _agg(df, x_col, y_col, agg)
    categories = adf[x_col].astype(str).tolist()
    values = [float(v or 0) for v in adf['_value'].tolist()]

    option = _base_option(cfg, axis_trigger=True)
    option['xAxis'] = {'type': 'category', 'data': categories, 'name': labels.get('x', '')}
    option['yAxis'] = {'name': labels.get('y', '')}
    option.update({
        'series': [{
            'type': 'waterfall',
            'name': labels.get('y', 'Value'),
            'data': values,
            'label': {'show': bool(cfg.get('data_labels', False))},
        }],
    })
    return option


def _pyramid_option(df, x_col, y_col, agg, cfg):
    """Pyramid chart – inverted funnel."""
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)

    adf = _agg(df, x_col, y_col, agg).sort_values('_value', ascending=True)
    option = _base_option(cfg, axis_trigger=False)
    option.update({
        'series': [{
            'type': 'pyramid',
            'left': '10%',
            'top': 40,
            'bottom': 20,
            'width': '80%',
            'label': {'show': True, 'position': 'inside'},
            'data': [{'name': str(r[x_col]), 'value': float(r['_value'] or 0)} for _, r in adf.iterrows()],
        }],
    })
    return option


def _build_option(rows: list, col_defs: dict, cfg: dict) -> dict:
    if not rows:
        return _message_option('No data loaded', cfg)

    chart_type = cfg.get('chart_type', 'bar')
    x_col = cfg.get('x_col', '')
    x_cols_raw = cfg.get('x_cols', [])
    y_col = cfg.get('y_col', '')
    y_cols_raw = cfg.get('y_cols', [])
    stack_col = cfg.get('stack_col', '')
    size_col = cfg.get('size_col', '')
    agg = str(cfg.get('agg', 'count') or 'count').lower()
    if agg not in {'count', 'sum', 'avg', 'min', 'max', 'median'}:
        agg = 'count'

    df = pd.DataFrame(rows)

    x_cols = [c for c in x_cols_raw if isinstance(c, str) and c in df.columns] if isinstance(x_cols_raw, list) else []
    if len(x_cols) > 1:
        composite_col = '__x_comp__'
        df[composite_col] = df[x_cols].fillna('(Blank)').astype(str).agg(' · '.join, axis=1)
        x_col = composite_col
    elif len(x_cols) == 1 and not x_col:
        x_col = x_cols[0]

    x_col = x_col if x_col in df.columns else ''
    y_col = y_col if y_col in df.columns else ''
    stack_col = stack_col if stack_col in df.columns else ''
    size_col = size_col if size_col in df.columns else ''
    y_cols = [c for c in y_cols_raw if isinstance(c, str) and c in df.columns] if isinstance(y_cols_raw, list) else []

    # ── Meta-column melt (test school year / test window / mark period) ─────
    # Must run BEFORE label building and BEFORE column validation re-check
    _meta_trigger = _META_DIM_COLS | _META_SCORE_COLS
    if x_col in _meta_trigger or stack_col in _meta_trigger or y_col in _meta_trigger or any(c in _meta_trigger for c in y_cols):
        df, y_col, y_cols = _maybe_melt_for_meta(df, x_col, stack_col, y_col, y_cols, col_defs)
        # Re-validate columns against (possibly melted) dataframe
        x_col = x_col if x_col in df.columns else ''
        y_col = y_col if y_col in df.columns else ''
        stack_col = stack_col if stack_col in df.columns else ''
        size_col = size_col if size_col in df.columns else ''
        y_cols = [c for c in y_cols if c in df.columns]

    labels = {
        'x': (' · '.join([_label(c, col_defs) for c in x_cols]) if len(x_cols) > 1
              else (_META_LABEL_MAP.get(x_col) or _label(x_col, col_defs)) if x_col else ''),
        'y': (_META_LABEL_MAP.get(y_col) or _label(y_col, col_defs)) if y_col else ('Count' if agg == 'count' else ''),
        's': (_META_LABEL_MAP.get(stack_col) or _label(stack_col, col_defs)) if stack_col else '',
    }

    builders = {
        'bar': lambda: _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, False, col_defs),
        'column': lambda: _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, False, col_defs),
        'grouped_bar': lambda: _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, False, col_defs),
        'stacked_bar': lambda: _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, True, col_defs),
        'waterfall': lambda: _waterfall_option(df, x_col, y_col, agg, cfg, labels),
        'line': lambda: _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, False, False, y_cols, col_defs),
        'spline': lambda: _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, False, False, y_cols, col_defs, series_type='spline'),
        'area': lambda: _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, True, False, y_cols, col_defs),
        'areaspline': lambda: _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, True, False, y_cols, col_defs, series_type='areaspline'),
        'stacked_area': lambda: _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, True, True, y_cols, col_defs),
        'pie': lambda: _pie_option(df, x_col, y_col, agg, cfg, donut=False),
        'donut': lambda: _pie_option(df, x_col, y_col, agg, cfg, donut=True),
        'scatter': lambda: _scatter_option(df, x_col, y_col, stack_col, size_col, cfg, labels, bubble=False),
        'bubble': lambda: _scatter_option(df, x_col, y_col, stack_col, size_col, cfg, labels, bubble=True),
        'heatmap': lambda: _heatmap_option(df, x_col, y_col, agg, cfg, labels),
        'histogram': lambda: _histogram_option(df, x_col, stack_col, cfg, labels),
        'box': lambda: _boxplot_option(df, x_col, y_col, cfg, labels),
        'funnel': lambda: _funnel_option(df, x_col, y_col, agg, cfg),
        'pyramid': lambda: _pyramid_option(df, x_col, y_col, agg, cfg),
        'treemap': lambda: _treemap_option(df, x_col, y_col, agg, cfg),
        'radar': lambda: _radar_option(df, x_col, y_col, agg, cfg),
        'gauge': lambda: _gauge_option(df, y_col, agg, cfg, labels),
        'solid_gauge': lambda: _solid_gauge_option(df, y_col, agg, cfg, labels),
        'polar': lambda: _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, False, col_defs),
    }

    try:
        option = builders.get(chart_type, builders['bar'])()
    except Exception as exc:
        option = _message_option(f'Error rendering {chart_type}: {exc}', cfg, error=True)
    return option


class DataVizBuilderView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = 'dataviz/builder.html'

    def test_func(self):
        return _is_project_admin(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        query_list = (
            SavedQuery.objects
            .select_related('owner', 'folder')
            .prefetch_related('shares__shared_with')
            .order_by('name')
        )
        accessible_queries = [q for q in query_list if can_user_view_query(self.request.user, q)]

        default_columns = get_saved_default_columns()
        test_structure = get_test_columns()
        marks_structure = get_mark_columns()
        combined_defs = dict(COLUMN_DEFINITIONS)

        # ── Fetch school_years and grades for multi-select filters ──────
        school_years_key = "dataexp:school_years"
        school_years = cache.get(school_years_key)
        if not school_years:
            with connections['default'].cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT school_year FROM current_student "
                    "WHERE school_year IS NOT NULL ORDER BY school_year DESC"
                )
                school_years = [row[0] for row in cursor.fetchall()]
                cache.set(school_years_key, school_years, 3600)

        # Grade list (role-/school-scoped)
        user = self.request.user
        is_unrestricted = (
            user.is_superuser
            or getattr(user, 'is_project_admin', False)
            or (
                hasattr(user, 'active_role') and user.active_role
                and 'District' in user.active_role.name
            )
        )

        grade_filter_clauses = ["grade IS NOT NULL"]
        grade_filter_params = []

        if hasattr(user, 'active_school') and user.active_school:
            grade_filter_clauses.append("school_state_id = %s")
            grade_filter_params.append(user.active_school.state_id)
        elif not is_unrestricted:
            if hasattr(user, 'schools') and isinstance(user.schools, list) and user.schools:
                grade_filter_clauses.append("school_state_id = ANY(%s)")
                grade_filter_params.append(user.schools)
            else:
                grade_filter_clauses.append("1=0")

        if not is_unrestricted and hasattr(user, 'grade_levels'):
            user_grade_levels = user.grade_levels
            if isinstance(user_grade_levels, list) and len(user_grade_levels) > 0:
                grade_restricted_roles = {'School Gr View', 'School View', 'School Admin'}
                current_role = None
                if hasattr(user, 'active_role') and user.active_role:
                    current_role = user.active_role.name
                elif user.roles.count() == 1:
                    current_role = user.roles.first().name
                if current_role in grade_restricted_roles:
                    grade_filter_clauses.append("grade = ANY(%s)")
                    grade_filter_params.append(user_grade_levels)

        active_school_id = getattr(getattr(user, 'active_school', None), 'state_id', None)
        grade_levels_key = json.dumps(getattr(user, 'grade_levels', None) or [], sort_keys=True)
        schools_key = json.dumps(getattr(user, 'schools', None) or [], sort_keys=True)
        grades_key = (
            f"dataviz:grades:{user.id}:{active_school_id}:"
            f"{hashlib.md5(grade_levels_key.encode()).hexdigest()}:"
            f"{hashlib.md5(schools_key.encode()).hexdigest()}"
        )
        grades = cache.get(grades_key)
        if not grades:
            with connections['default'].cursor() as cursor:
                where_sql = " AND ".join(grade_filter_clauses)
                cursor.execute(
                    f"SELECT DISTINCT grade FROM current_student WHERE {where_sql} ORDER BY grade",
                    grade_filter_params,
                )
                grades = [row[0] for row in cursor.fetchall()]
                cache.set(grades_key, grades, 3600)

        context.update({
            'saved_queries': accessible_queries,
            'column_definitions': COLUMN_DEFINITIONS,
            'column_categories': COLUMN_CATEGORIES,
            'test_structure': test_structure,
            'marks_structure': marks_structure,
            'default_columns_json': json.dumps(default_columns),
            'column_definitions_json': json.dumps(combined_defs),
            'school_years': school_years,
            'grades': grades,
        })
        return context


@login_required
@require_POST
def render_chart_api(request):
    if not _is_project_admin(request.user):
        return _FORBIDDEN
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    rows = body.get('rows', [])
    col_defs = body.get('column_definitions', {})
    cfg = body.get('chart_config', {})

    if not cfg.get('chart_type'):
        return JsonResponse({'error': 'chart_config.chart_type is required'}, status=400)

    try:
        option = _build_option(rows, col_defs, cfg)
        return JsonResponse({'option': option, 'success': True})
    except Exception as exc:
        return JsonResponse({'error': str(exc), 'detail': traceback.format_exc()}, status=500)


# ─────────────────────────────────────────────────────────────────────────────
# SQL Lab
# ─────────────────────────────────────────────────────────────────────────────

_SQL_READONLY_RE = re.compile(
    r'^\s*(select|with|explain|show|describe|desc)\b',
    re.IGNORECASE,
)
_SQL_DANGER_RE = re.compile(
    r'\b(insert|update|delete|drop|truncate|alter|create|replace|call|exec|execute|grant|revoke|copy|vacuum)\b',
    re.IGNORECASE,
)

MAX_SQL_ROWS = 2000


@login_required
@require_POST
def sql_lab_api(request):
    """Execute a read-only SQL query against the default database and return rows + column info."""
    if not _is_project_admin(request.user):
        return _FORBIDDEN
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    sql = (body.get('sql') or '').strip()
    if not sql:
        return JsonResponse({'error': 'No SQL provided'}, status=400)

    if not _SQL_READONLY_RE.match(sql):
        return JsonResponse({'error': 'Only SELECT / WITH queries are allowed.'}, status=400)
    if _SQL_DANGER_RE.search(sql):
        return JsonResponse({'error': 'Query contains disallowed keywords.'}, status=400)

    try:
        from django.db import connection
        with connection.cursor() as cursor:
            # Enforce database-level read-only: always roll back so no writes
            # can persist even if the regex guards are somehow bypassed.
            cursor.execute("BEGIN READ ONLY")
            try:
                cursor.execute(sql)
                if cursor.description is None:
                    cursor.execute("ROLLBACK")
                    return JsonResponse({'columns': [], 'rows': [], 'row_count': 0})
                columns = [col[0] for col in cursor.description]
                raw_rows = cursor.fetchmany(MAX_SQL_ROWS)
                rows = [dict(zip(columns, row)) for row in raw_rows]
                has_more = len(raw_rows) == MAX_SQL_ROWS
            finally:
                # Rollback is safe and fast — we never intend to commit
                cursor.execute("ROLLBACK")

        # Infer column types from first non-null value
        col_types = {}
        for col in columns:
            for row in rows:
                v = row.get(col)
                if v is not None:
                    col_types[col] = 'number' if isinstance(v, (int, float)) else 'string'
                    break
            col_types.setdefault(col, 'string')

        return JsonResponse({
            'columns':    columns,
            'col_types':  col_types,
            'rows':       rows,
            'row_count':  len(rows),
            'has_more':   has_more,
        })
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)


@login_required
def sql_schema_api(request):
    """Return list of user-visible tables / views for the SQL Lab table browser."""
    if not _is_project_admin(request.user):
        return _FORBIDDEN
    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT table_name, table_type
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type IN ('BASE TABLE', 'VIEW')
                ORDER BY table_type, table_name
            """)
            tables = [{'name': row[0], 'type': row[1]} for row in cursor.fetchall()]
        return JsonResponse({'tables': tables})
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)


# ─────────────────────────────────────────────────────────────────────────────
# Saved Charts
# ─────────────────────────────────────────────────────────────────────────────

from dataviz.models import SavedChart, Dashboard, DashboardChart


@login_required
def chart_list_api(request):
    if not _is_project_admin(request.user):
        return _FORBIDDEN
    if request.method == 'GET':
        charts = SavedChart.objects.filter(owner=request.user)
        return JsonResponse({'charts': [c.to_dict() for c in charts]})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@login_required
@require_POST
def save_chart_api(request):
    if not _is_project_admin(request.user):
        return _FORBIDDEN
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    chart_id = body.get('id')
    name = (body.get('name') or '').strip()
    if not name:
        return JsonResponse({'error': 'name is required'}, status=400)

    if chart_id:
        try:
            chart = SavedChart.objects.get(pk=chart_id, owner=request.user)
        except SavedChart.DoesNotExist:
            return JsonResponse({'error': 'Chart not found'}, status=404)
    else:
        chart = SavedChart(owner=request.user)

    chart.name        = name
    chart.description = body.get('description', '')
    chart.chart_type  = body.get('chart_type', 'bar')
    chart.config      = body.get('config', {})
    chart.source_type = body.get('source_type', SavedChart.SOURCE_QUERY)
    chart.source_id   = body.get('source_id') or None
    chart.source_sql  = body.get('source_sql', '')
    chart.source_filters = body.get('source_filters', {})
    chart.column_defs = body.get('column_defs', {})
    chart.save()
    return JsonResponse({'chart': chart.to_dict(), 'success': True})


@login_required
def chart_detail_api(request, chart_id):
    if not _is_project_admin(request.user):
        return _FORBIDDEN
    try:
        chart = SavedChart.objects.get(pk=chart_id, owner=request.user)
    except SavedChart.DoesNotExist:
        return JsonResponse({'error': 'Chart not found'}, status=404)

    if request.method == 'GET':
        return JsonResponse({'chart': chart.to_dict()})
    if request.method == 'DELETE':
        chart.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


# ─────────────────────────────────────────────────────────────────────────────
# Dashboards
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def dashboard_list_api(request):
    if not _is_project_admin(request.user):
        return _FORBIDDEN
    if request.method == 'GET':
        dashboards = Dashboard.objects.filter(owner=request.user)
        return JsonResponse({'dashboards': [d.to_dict(include_charts=True) for d in dashboards]})

    if request.method == 'POST':
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON body'}, status=400)
        name = (body.get('name') or '').strip()
        if not name:
            return JsonResponse({'error': 'name is required'}, status=400)
        db = Dashboard.objects.create(
            name=name,
            description=body.get('description', ''),
            owner=request.user,
        )
        return JsonResponse({'dashboard': db.to_dict(), 'success': True})

    return JsonResponse({'error': 'Method not allowed'}, status=405)


@login_required
def dashboard_detail_api(request, dashboard_id):
    if not _is_project_admin(request.user):
        return _FORBIDDEN
    try:
        db = Dashboard.objects.get(pk=dashboard_id, owner=request.user)
    except Dashboard.DoesNotExist:
        return JsonResponse({'error': 'Dashboard not found'}, status=404)

    if request.method == 'GET':
        return JsonResponse({'dashboard': db.to_dict(include_charts=True)})

    if request.method == 'DELETE':
        db.delete()
        return JsonResponse({'success': True})

    if request.method in ('POST', 'PUT'):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON body'}, status=400)
        db.name = (body.get('name') or db.name).strip()
        db.description = body.get('description', db.description)
        db.save()
        return JsonResponse({'dashboard': db.to_dict(), 'success': True})

    return JsonResponse({'error': 'Method not allowed'}, status=405)


@login_required
def dashboard_add_chart_api(request, dashboard_id):
    """Add or remove a SavedChart from a Dashboard."""
    if not _is_project_admin(request.user):
        return _FORBIDDEN
    try:
        db = Dashboard.objects.get(pk=dashboard_id, owner=request.user)
    except Dashboard.DoesNotExist:
        return JsonResponse({'error': 'Dashboard not found'}, status=404)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    action   = body.get('action', 'add')   # 'add' | 'remove' | 'reorder'
    chart_id = body.get('chart_id')

    if action == 'add' and chart_id:
        try:
            chart = SavedChart.objects.get(pk=chart_id, owner=request.user)
        except SavedChart.DoesNotExist:
            return JsonResponse({'error': 'Chart not found'}, status=404)
        last_pos = db.chart_slots.count()
        slot = DashboardChart.objects.create(
            dashboard=db, chart=chart,
            position=last_pos,
            width_class=body.get('width_class', DashboardChart.WIDTH_HALF),
        )
        return JsonResponse({'slot': slot.to_dict(), 'success': True})

    if action == 'add_html':
        last_pos = db.chart_slots.count()
        slot = DashboardChart.objects.create(
            dashboard=db, chart=None,
            element_type='html',
            html_content=body.get('html_content', '<h3>Title</h3>'),
            position=last_pos,
            width_class=body.get('width_class', DashboardChart.WIDTH_FULL),
            height_class=body.get('height_class', DashboardChart.HEIGHT_SM),
        )
        return JsonResponse({'slot': slot.to_dict(), 'success': True})

    if action == 'remove' and chart_id:
        DashboardChart.objects.filter(dashboard=db, chart_id=chart_id).delete()
        return JsonResponse({'success': True})

    if action == 'reorder':
        order = body.get('order', [])   # list of slot_ids in new order
        for idx, slot_id in enumerate(order):
            DashboardChart.objects.filter(pk=slot_id, dashboard=db).update(position=idx)
        return JsonResponse({'success': True})

    return JsonResponse({'error': 'Invalid action'}, status=400)


@login_required
def dashboard_slot_api(request, slot_id):
    """Update width or delete a single slot."""
    if not _is_project_admin(request.user):
        return _FORBIDDEN
    try:
        slot = DashboardChart.objects.select_related('dashboard').get(
            pk=slot_id, dashboard__owner=request.user
        )
    except DashboardChart.DoesNotExist:
        return JsonResponse({'error': 'Slot not found'}, status=404)

    if request.method == 'DELETE':
        slot.delete()
        return JsonResponse({'success': True})

    if request.method in ('POST', 'PUT'):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON body'}, status=400)
        if 'width_class' in body:
            slot.width_class = body['width_class']
        if 'height_class' in body:
            slot.height_class = body['height_class']
        if 'show_title' in body:
            slot.show_title = bool(body['show_title'])
        if 'position' in body:
            slot.position = int(body['position'])
        if 'slot_filters' in body:
            slot.slot_filters = body['slot_filters'] if isinstance(body['slot_filters'], list) else []
        if 'html_content' in body:
            slot.html_content = body['html_content']
        slot.save()
        return JsonResponse({'slot': slot.to_dict(), 'success': True})

    return JsonResponse({'error': 'Method not allowed'}, status=405)
