"""Progress-growth classification, consolidation, and chart option."""
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

# Expose private names to sibling ``import *`` (PEP 328).
__all__ = [n for n in list(globals()) if not n.startswith('__')]
