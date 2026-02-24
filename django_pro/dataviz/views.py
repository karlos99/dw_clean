import json
import traceback

import pandas as pd

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from dataexploration.column_utils import get_mark_columns, get_test_columns
from dataexploration.constants import COLUMN_CATEGORIES, COLUMN_DEFINITIONS
from dataexploration.models import SavedQuery
from dataexploration.utils import can_user_view_query
from dataexploration.view_modules.column_management import get_saved_default_columns

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
        if agg in ('sum', 'avg') and y_col and y_col in df.columns:
            dfc = df.copy()
            dfc[y_col] = pd.to_numeric(dfc[y_col], errors='coerce')
            grp = dfc.groupby(group_cols, dropna=False)[y_col]
            result = (grp.sum() if agg == 'sum' else grp.mean()).reset_index()
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

    valid_y_cols = [c for c in (y_cols or []) if c in df.columns]
    if len(valid_y_cols) == 1 and not y_col:
        y_col = valid_y_cols[0]

    if len(valid_y_cols) > 1:
        categories = sorted(df[x_col].fillna('(Blank)').astype(str).unique().tolist())
        series = []
        for metric_col in valid_y_cols:
            adf = _agg(df, x_col, metric_col, agg)
            vm = dict(zip(adf[x_col].astype(str), adf['_value']))
            series.append({
                'name': _label(metric_col, col_defs),
                'type': 'bar',
                'stack': 'total' if stacked else None,
                'label': {'show': bool(cfg.get('data_labels', False)), 'position': 'top'},
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
            'data': [float(v or 0) for v in adf['_value'].tolist()],
        }],
    })
    _add_axis_zoom(option)
    return option


def _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, fill, stacked):
    if not x_col:
        return _message_option('X / Category is required', cfg, error=True)

    option = _base_option(cfg, axis_trigger=True)
    axis_style = _axis_style(cfg)
    smooth = bool(cfg.get('smooth', False))

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
                'type': 'line',
                'smooth': smooth,
                'stack': 'total' if stacked else None,
                'areaStyle': {} if fill else None,
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
            'type': 'line',
            'smooth': smooth,
            'areaStyle': {} if fill else None,
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


def _scatter_option(df, x_col, y_col, stack_col, cfg, labels):
    if not x_col or not y_col:
        return _message_option('X and Y are required for scatter', cfg, error=True)

    option = _base_option(cfg, axis_trigger=True)
    axis_style = _axis_style(cfg)

    dfc = df.copy()
    dfc[x_col] = pd.to_numeric(dfc[x_col], errors='coerce')
    dfc[y_col] = pd.to_numeric(dfc[y_col], errors='coerce')
    dfc = dfc.dropna(subset=[x_col, y_col])

    if stack_col and stack_col in dfc.columns:
        series = []
        for g in sorted(dfc[stack_col].astype(str).unique().tolist()):
            sub = dfc[dfc[stack_col].astype(str) == g]
            series.append({
                'name': g,
                'type': 'scatter',
                'symbolSize': 8,
                'data': [[float(x), float(y)] for x, y in zip(sub[x_col], sub[y_col])],
            })
    else:
        series = [{
            'name': labels['y'] or 'Values',
            'type': 'scatter',
            'symbolSize': 8,
            'data': [[float(x), float(y)] for x, y in zip(dfc[x_col], dfc[y_col])],
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

    def hist_data(values, bins=12):
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
            bins, counts = hist_data(sub[x_col])
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
        x_axis, counts = hist_data(df[x_col])
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

    value = float(vals.mean()) if agg == 'avg' else float(vals.sum()) if agg == 'sum' else float(len(df))
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


def _build_option(rows: list, col_defs: dict, cfg: dict) -> dict:
    if not rows:
        return _message_option('No data loaded', cfg)

    chart_type = cfg.get('chart_type', 'bar')
    x_col = cfg.get('x_col', '')
    x_cols_raw = cfg.get('x_cols', [])
    y_col = cfg.get('y_col', '')
    y_cols_raw = cfg.get('y_cols', [])
    stack_col = cfg.get('stack_col', '')
    agg = cfg.get('agg', 'count')

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
    y_cols = [c for c in y_cols_raw if isinstance(c, str) and c in df.columns] if isinstance(y_cols_raw, list) else []

    labels = {
        'x': (' · '.join([_label(c, col_defs) for c in x_cols]) if len(x_cols) > 1 else (_label(x_col, col_defs) if x_col else '')),
        'y': _label(y_col, col_defs) if y_col else ('Count' if agg == 'count' else ''),
        's': _label(stack_col, col_defs) if stack_col else '',
    }

    builders = {
        'bar': lambda: _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, False, col_defs),
        'stacked_bar': lambda: _bar_option(df, x_col, y_col, y_cols, stack_col, agg, cfg, labels, True, col_defs),
        'line': lambda: _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, False, False),
        'area': lambda: _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, True, False),
        'stacked_area': lambda: _line_area_option(df, x_col, y_col, stack_col, agg, cfg, labels, True, True),
        'pie': lambda: _pie_option(df, x_col, y_col, agg, cfg, donut=False),
        'donut': lambda: _pie_option(df, x_col, y_col, agg, cfg, donut=True),
        'scatter': lambda: _scatter_option(df, x_col, y_col, stack_col, cfg, labels),
        'heatmap': lambda: _heatmap_option(df, x_col, y_col, agg, cfg, labels),
        'histogram': lambda: _histogram_option(df, x_col, stack_col, cfg, labels),
        'box': lambda: _boxplot_option(df, x_col, y_col, cfg, labels),
        'violin': lambda: _boxplot_option(df, x_col, y_col, cfg, labels),
        'funnel': lambda: _funnel_option(df, x_col, y_col, agg, cfg),
        'treemap': lambda: _treemap_option(df, x_col, y_col, agg, cfg),
        'radar': lambda: _radar_option(df, x_col, y_col, agg, cfg),
        'gauge': lambda: _gauge_option(df, y_col, agg, cfg, labels),
    }

    try:
        option = builders.get(chart_type, builders['bar'])()
    except Exception as exc:
        option = _message_option(f'Error rendering {chart_type}: {exc}', cfg, error=True)
    return option


class DataVizBuilderView(LoginRequiredMixin, TemplateView):
    template_name = 'dataviz/builder.html'

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

        context.update({
            'saved_queries': accessible_queries,
            'column_definitions': COLUMN_DEFINITIONS,
            'column_categories': COLUMN_CATEGORIES,
            'test_structure': test_structure,
            'marks_structure': marks_structure,
            'default_columns_json': json.dumps(default_columns),
            'column_definitions_json': json.dumps(combined_defs),
        })
        return context


@login_required
@require_POST
def render_chart_api(request):
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
