"""Summary table and dynamic summary table builders."""
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

# Comparison operators available to a dynamic-summary-table calculation
# column's optional threshold filter.  lt/lte/gt/gte are numeric ranges
# (e.g. "count students with attendance_rate < 85"); eq/neq match a specific
# value (e.g. gender == "F"), whose options are prepopulated from the column.
_DYN_FILTER_OPS = frozenset({'lt', 'lte', 'gt', 'gte', 'eq', 'neq'})


def _dyn_filter_expr(col: str, op: str, val):
    """Polars expression for a threshold comparison on ``col``.

    Numeric ranges cast the column to float — rows whose value is non-numeric
    or null compare as null and are excluded, matching how non-count
    aggregations skip invalid values elsewhere.  Equality compares the raw
    (stripped) string value so categorical columns like gender work too.
    """
    if op == 'eq':
        return pl.col(col).cast(pl.Utf8).str.strip_chars() == str(val).strip()
    if op == 'neq':
        return pl.col(col).cast(pl.Utf8).str.strip_chars() != str(val).strip()
    x = pl.col(col).cast(pl.Float64, strict=False)
    if op == 'lt':
        return x < val
    if op == 'lte':
        return x <= val
    if op == 'gt':
        return x > val
    if op == 'gte':
        return x >= val
    return None


_DYN_CALC_OPS = {
    'add':      lambda a, b: a + b,
    'subtract': lambda a, b: a - b,
    'multiply': lambda a, b: a * b,
    'divide':   lambda a, b: (a / b) if b else 0.0,
}


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

        mask = blank_filter_expr([row_col], _BLANK)
        keep_cols = list(dict.fromkeys([row_col] + st_col_cols + ([_dup_key] if _dup_key else [])))
        work = df.filter(mask).select(keep_cols)
        work = strip_str_cols(work, [row_col])

        if work.is_empty():
            return _message_option('Summary Table: no data after filtering blanks', cfg)

        row_vals = _auto_detect_pl_scale(work[row_col].unique().to_list())
        col_vals = [_label(c, col_defs) if col_defs else c for c in st_col_cols]

        rows_data = []
        col_totals = [0.0] * len(st_col_cols)

        for rv in row_vals:
            sub = work.filter(pl.col(row_col) == rv)
            if _dup_key and st_agg_func == 'count':
                sub = sub.unique(subset=[_dup_key, row_col], keep='first')
            cell_vals = []
            row_cells = []
            for ci, cc in enumerate(st_col_cols):
                if cc not in sub.columns:
                    cell_vals.append(0.0)
                    row_cells.append(_format_cell_with_tooltip(0.0, None, st_agg_func, show_tooltips, st_agg_decimals))
                    continue
                col_data = sub[cc]
                if st_agg_func == 'count':
                    raw = float(sub.filter(~pl.col(cc).cast(pl.Utf8).str.strip_chars().is_in(list(_BLANK))).height)
                    num_data = to_numeric_series(col_data).drop_nulls()
                    alt_raw = float(num_data.mean()) if num_data.len() > 0 else None
                else:
                    num_data = to_numeric_series(col_data).drop_nulls()
                    if num_data.len() == 0:
                        raw = 0.0
                    else:
                        raw = float(getattr(num_data, _agg_map[st_agg_func])())
                    if _dup_key:
                        valid_sub = sub.filter(~pl.col(cc).cast(pl.Utf8).str.strip_chars().is_in(list(_BLANK)))
                        alt_raw = float(valid_sub.select(pl.col(_dup_key).n_unique()).item())
                    else:
                        alt_raw = float(sub.filter(~pl.col(cc).cast(pl.Utf8).str.strip_chars().is_in(list(_BLANK))).height)

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

    mask = blank_filter_expr([row_col, col_col], _BLANK)
    keep_cols = [row_col, col_col] + ([_dup_key] if _dup_key else [])
    if (st_agg_func != 'count' or (show_tooltips and st_value_col)) and st_value_col and st_value_col in df.columns:
        keep_cols.append(st_value_col)
    work = df.filter(mask).select(keep_cols)
    work = strip_str_cols(work, [row_col, col_col])
    if st_value_col and st_value_col in work.columns:
        work = work.with_columns(to_numeric_expr(st_value_col).alias(st_value_col))

    if work.is_empty():
        return _message_option('Summary Table: no data after filtering blanks', cfg)

    # Deduplicate so each student is counted at most once per (row_col, col_col) cell.
    if _dup_key and st_agg_func == 'count':
        work = work.unique(subset=[_dup_key, row_col, col_col], keep='first')

    row_vals = _auto_detect_pl_scale(work[row_col].unique().to_list())
    col_vals = _auto_detect_pl_scale(work[col_col].unique().to_list())

    ct_alt_lookup = None
    if show_tooltips:
        if st_agg_func == 'count':
            if st_value_col and st_value_col in df.columns:
                work_alt = work.with_columns(to_numeric_expr(st_value_col).alias(st_value_col))
                ct_alt_lookup = pivot_lookup(work_alt, [row_col], col_col, 'avg', st_value_col, _agg_map)
        else:
            work_alt = work
            if st_value_col and st_value_col in work_alt.columns:
                work_alt = work_alt.drop_nulls(subset=[st_value_col])
            if _dup_key:
                grouped = work_alt.group_by([row_col, col_col]).agg(pl.col(_dup_key).n_unique().alias('_value'))
            else:
                grouped = work_alt.group_by([row_col, col_col]).len().rename({'len': '_value'})
            ct_alt_lookup = {}
            for row in grouped.iter_rows(named=True):
                ct_alt_lookup[(row[row_col], row[col_col])] = float(row['_value'] or 0)

    ct_lookup = pivot_lookup(
        work, [row_col], col_col,
        st_agg_func if st_agg_func != 'count' else 'count',
        st_value_col if st_agg_func != 'count' else '',
        _agg_map,
    )

    col_totals = [0.0] * len(col_vals)
    rows_data = []
    for rv in row_vals:
        counts = [pivot_cell(ct_lookup, rv, cv) for cv in col_vals]
        for i, v in enumerate(counts):
            col_totals[i] += v

        row_cells = []
        for i, cv in enumerate(col_vals):
            v = counts[i]
            alt_v = pivot_cell(ct_alt_lookup, rv, cv, default=None) if ct_alt_lookup is not None else None
            if alt_v == 0.0 and (rv, cv) not in (ct_alt_lookup or {}):
                alt_v = None
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
        totals_only = cfg.get('dyn_totals_only', False) in (True, 1, '1', 'true', 'True')
        if totals_only:
            # Totals-only mode is defined by its total row; keep it available
            # even if an older saved config had column totals disabled.
            st_show_col_totals = True
        if not row_cols and not totals_only:
            return _message_option('Dynamic Summary Table: select at least one Row column', cfg)
        valid_row = [c for c in row_cols if c and c in df.columns]
        if not valid_row and not totals_only:
            return _message_option('Dynamic Summary Table: no valid Row columns in data', cfg)

        # Normalize each calculation: assign a stable id (charts saved before ids
        # fall back to positional c0/c1/…) and classify as 'base' — an aggregate
        # of a value column, optionally restricted by a numeric threshold filter
        # (e.g. "count of students with attendance_rate < 85") — or 'derived',
        # arithmetic combining two other calculation columns (e.g. below ÷ total).
        parsed_calcs = []
        for idx, calc in enumerate(dyn_calculations):
            if not isinstance(calc, dict):
                continue
            cid = str(calc.get('id') or f'c{idx}')
            ctype = calc.get('calc_type')
            if ctype not in ('base', 'derived'):
                ctype = ('derived'
                         if calc.get('op') and (calc.get('ref_a') is not None or calc.get('ref_b') is not None)
                         else 'base')
            parsed = dict(calc)
            parsed['id'] = cid
            parsed['calc_type'] = ctype
            parsed['visible'] = calc.get('visible', True) is not False
            parsed_calcs.append(parsed)

        if not parsed_calcs:
            return _message_option('Dynamic Summary Table: add at least one calculation column', cfg)

        def _calc_decimals(calc):
            dec = calc.get('decimals')
            try:
                return int(dec) if dec is not None and str(dec).strip() != '' else None
            except (ValueError, TypeError):
                return None

        calc_value_cols = [c.get('value_col') for c in parsed_calcs
                           if c.get('calc_type') == 'base' and c.get('value_col') and c.get('value_col') in df.columns]
        filter_cols = [f.get('col') for c in parsed_calcs if c.get('calc_type') == 'base'
                       for f in (c.get('filters') if isinstance(c.get('filters'), list) else [c.get('filter')])
                       if isinstance(f, dict) and f.get('col') and f.get('col') in df.columns]
        keep_cols = list(dict.fromkeys(valid_row + calc_value_cols + filter_cols + ([_dup_key] if _dup_key else [])))
        work = df.filter(blank_filter_expr(valid_row, _BLANK)).select(keep_cols) if valid_row else df.select(keep_cols)
        work = strip_str_cols(work, valid_row)

        if work.is_empty():
            return _message_option('Dynamic Summary Table: no data after filtering blanks', cfg)

        row_val_lists = {}
        for c in valid_row:
            row_val_lists[c] = _auto_detect_pl_scale(work[c].unique().to_list())
        row_combos = [()] if totals_only else _build_row_combos(row_val_lists, valid_row)

        _agg_map = {'sum': 'sum', 'avg': 'mean', 'min': 'min', 'max': 'max', 'median': 'median'}
        base_ids = [c['id'] for c in parsed_calcs if c['calc_type'] == 'base']

        output_calcs = [c for c in parsed_calcs if c['visible']]
        percent_columns = [i for i, c in enumerate(output_calcs) if c.get('as_percent')]
        col_headers = []
        for idx, calc in enumerate(output_calcs):
            lbl = calc.get('label', '').strip()
            if not lbl:
                if calc['calc_type'] == 'derived':
                    lbl = f"Calc #{idx + 1}"
                else:
                    fn = calc.get('agg_func', 'count')
                    vc = calc.get('value_col', '')
                    vc_lbl = _label(vc, col_defs) if (col_defs and vc) else vc
                    lbl = f"{fn.capitalize()} of {vc_lbl}" if vc else fn.capitalize()
            col_headers.append(lbl)

        rows_data = []
        col_totals = {cid: 0.0 for cid in base_ids}

        for combo in row_combos:
            sub = work
            for r_col, r_val in zip(valid_row, combo):
                sub = sub.filter(pl.col(r_col) == r_val)

            # Base columns: aggregate (optionally filtered) and keep the raw
            # numeric value for arithmetic/totals alongside the display cell.
            base_raw = {}
            base_display = {}
            for calc in parsed_calcs:
                if calc['calc_type'] != 'base':
                    continue
                cid = calc['id']
                fn = calc.get('agg_func', 'count')
                vc = calc.get('value_col', '')
                dec = _calc_decimals(calc)

                csub = sub
                filters = calc.get('filters') if isinstance(calc.get('filters'), list) else [calc.get('filter')]
                filter_expr = None
                for f in filters:
                    if not isinstance(f, dict) or not f.get('col') or f.get('op') not in _DYN_FILTER_OPS:
                        continue
                    fop = f['op']
                    fcol = f['col']
                    expr = None
                    if fop in ('lt', 'lte', 'gt', 'gte'):
                        try:
                            fval = float(f['val'])
                        except (TypeError, ValueError):
                            fval = None
                        if fval is not None and fcol in csub.columns:
                            expr = _dyn_filter_expr(fcol, fop, fval)
                    else:  # eq / neq
                        fval = f.get('val')
                        if fval is not None and str(fval).strip() != '' and fcol in csub.columns:
                            expr = _dyn_filter_expr(fcol, fop, fval)
                    if expr is not None:
                        if filter_expr is None or str(f.get('logic', 'and')).lower() != 'or':
                            filter_expr = expr if filter_expr is None else (filter_expr & expr)
                        else:
                            filter_expr = filter_expr | expr
                if filter_expr is not None:
                    csub = csub.filter(filter_expr)

                if fn == 'count':
                    if _dup_key:
                        raw = float(csub.unique(subset=[_dup_key] + valid_row, keep='first').height)
                    else:
                        raw = float(csub.height)
                    if vc and vc in csub.columns:
                        num_data = to_numeric_series(csub[vc]).drop_nulls()
                        alt_raw = float(num_data.mean()) if num_data.len() > 0 else None
                    else:
                        alt_raw = None
                else:
                    if vc and vc in csub.columns:
                        num_data = to_numeric_series(csub[vc]).drop_nulls()
                        if num_data.len() == 0:
                            raw = 0.0
                        else:
                            raw = float(getattr(num_data, _agg_map.get(fn, 'mean'))())
                    else:
                        raw = 0.0
                    if _dup_key:
                        valid_sub = csub
                        if vc and vc in valid_sub.columns:
                            valid_sub = valid_sub.with_columns(to_numeric_expr(vc).alias(vc)).drop_nulls(subset=[vc])
                        alt_raw = float(valid_sub.select(pl.col(_dup_key).n_unique()).item())
                    else:
                        alt_raw = float(csub.height)

                if dec is not None:
                    raw = round(raw, dec)
                base_raw[cid] = raw
                col_totals[cid] += raw
                base_display[cid] = _format_cell_with_tooltip(raw, alt_raw, fn, show_tooltips, dec)

            # Derived columns may reference other derived columns (e.g. first
            # Present = Total − Absent, then Rate = Present ÷ Total), so resolve
            # in repeated passes until nothing more can progress — any dangling
            # reference or cycle renders as 0.0, matching the date-series lines.
            derived_vals = {}
            pending = {c['id']: c for c in parsed_calcs if c['calc_type'] == 'derived'}
            for _ in range(len(pending) + 1):
                if not pending:
                    break
                progressed = False
                pool = {**base_raw, **derived_vals}
                for cid, calc in list(pending.items()):
                    ra = str(calc.get('ref_a') or '')
                    rb = str(calc.get('ref_b') or '')
                    if ra in pool and rb in pool:
                        fn = _DYN_CALC_OPS.get(calc.get('op', 'add'), _DYN_CALC_OPS['add'])
                        v = fn(pool[ra], pool[rb])
                        if calc.get('as_percent'):
                            v = v * 100
                        derived_vals[cid] = v
                        del pending[cid]
                        progressed = True
                if not progressed:
                    break
            for cid in pending:
                derived_vals[cid] = 0.0

            row_cells = []
            for calc in output_calcs:
                if calc['calc_type'] == 'base':
                    row_cells.append(base_display[calc['id']])
                else:
                    v = derived_vals.get(calc['id'], 0.0)
                    dec = _calc_decimals(calc)
                    if dec is not None:
                        v = round(v, dec)
                    row_cells.append(v)

            if st_show_row_totals and not totals_only:
                # Row total sums only base columns — a sum of derived values
                # (rates/percentages) would be meaningless.
                row_total = sum(base_raw[cid] for cid in base_ids)
                rows_data.append(list(combo) + row_cells + [row_total])
            elif not totals_only:
                rows_data.append(list(combo) + row_cells)
            elif cfg.get('dyn_display_type', 'table') != 'table':
                # Chart renderers need a category even though table view uses
                # the dedicated total row below.
                rows_data.append(['Total'] + row_cells)

        # Round base column totals, then recompute derived column totals from
        # their input totals so a rate column's Total is the rate of the totals
        # (e.g. Total below ÷ Total students), not a sum of per-row percentages.
        col_total_display = {}
        for calc in parsed_calcs:
            cid = calc['id']
            dec = _calc_decimals(calc)
            if calc['calc_type'] == 'base':
                v = col_totals[cid]
                if dec is not None:
                    v = round(v, dec)
                col_total_display[cid] = v
        pending_totals = {c['id']: c for c in parsed_calcs if c['calc_type'] == 'derived'}
        for _ in range(len(pending_totals) + 1):
            if not pending_totals:
                break
            progressed = False
            for cid, calc in list(pending_totals.items()):
                ra = str(calc.get('ref_a') or '')
                rb = str(calc.get('ref_b') or '')
                if ra not in col_total_display or rb not in col_total_display:
                    continue
                fn = _DYN_CALC_OPS.get(calc.get('op', 'add'), _DYN_CALC_OPS['add'])
                v = fn(col_total_display[ra], col_total_display[rb])
                if calc.get('as_percent'):
                    v = v * 100
                if dec is not None:
                    v = round(v, dec)
                col_total_display[cid] = v
                del pending_totals[cid]
                progressed = True
            if not progressed:
                break
        for cid in pending_totals:
            col_total_display[cid] = 0.0

        ordered_totals = [col_total_display[c['id']] for c in output_calcs]
        grand_total = sum(col_total_display[cid] for cid in base_ids if any(c['id'] == cid and c['visible'] for c in parsed_calcs))
        total_row_values = ordered_totals if totals_only else ordered_totals + [grand_total]
        total_row = (['Total'] + [''] * (len(valid_row) - 1) + total_row_values) if st_show_col_totals else []
        row_labels = [_label(c, col_defs) if col_defs else c for c in valid_row]

        result = {
            '__type__': 'table',
            '__series_names__': [],
            'row_col':     valid_row[0] if len(valid_row) == 1 else '__dynst_row__',
            'col_col':     '__calc_cols__',
            'row_labels':  ['Total'] if totals_only else row_labels,
            'row_label':   'Total' if totals_only else (row_labels[0] if len(row_labels) == 1 else _SEP.join(row_labels)),
            'col_label':   'Calculations',
            'num_row_labels': 1 if totals_only else len(valid_row),
            'col_headers': col_headers,
            'percent_columns': percent_columns,
            'rows':        rows_data,
            'total_row':   total_row,
            'total_row_has_grand_total': not totals_only,
            'total_row_drillable': totals_only,
            'st_show_row_totals': False if totals_only else st_show_row_totals,
            'st_show_col_totals': st_show_col_totals,
            'show_tooltips': show_tooltips,
            '_drillMeta': {
                'stType':      'dynamic_summary_table',
                'stRowCol':    valid_row[0] if valid_row else '__dynst_total__',
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

        mask = blank_filter_expr(valid_row + valid_col, _BLANK)
        work = df.filter(mask).select(keep_cols)
        work = strip_str_cols(work, valid_row + valid_col)
        if value_col and value_col in work.columns:
            work = work.with_columns(to_numeric_expr(value_col).alias(value_col))

        if work.is_empty():
            return _message_option('Dynamic Summary Table: no data after filtering blanks', cfg)

        if _dup_key and agg_func == 'count':
            work = work.unique(subset=[_dup_key] + valid_row + valid_col, keep='first')

        # Ordered row values
        row_val_lists = {}
        for c in valid_row:
            row_val_lists[c] = _auto_detect_pl_scale(work[c].unique().to_list())
        row_combos = _build_row_combos(row_val_lists, valid_row)

        _agg_map = {'sum': 'sum', 'avg': 'mean', 'min': 'min', 'max': 'max', 'median': 'median'}

        # Build an independent pivot table for each column column
        col_groups = []   # [{label, col, headers, pivot}]
        all_col_headers = []
        all_cell_cols = []   # column name tracked per header for drill-through

        for cc in valid_col:
            vals = _auto_detect_pl_scale(work[cc].unique().to_list())
            ct_lookup = pivot_lookup(
                work, valid_row, cc,
                agg_func if agg_func != 'count' else 'count',
                value_col if agg_func != 'count' else '',
                _agg_map,
            )
            ct_alt_lookup = None
            if show_tooltips:
                if agg_func == 'count':
                    if value_col and value_col in df.columns:
                        work_alt = work.with_columns(to_numeric_expr(value_col).alias(value_col))
                        ct_alt_lookup = pivot_lookup(work_alt, valid_row, cc, 'avg', value_col, _agg_map)
                else:
                    work_alt = work
                    if value_col and value_col in work_alt.columns:
                        work_alt = work_alt.drop_nulls(subset=[value_col])
                    if _dup_key:
                        grouped = work_alt.group_by(valid_row + [cc]).agg(pl.col(_dup_key).n_unique().alias('_value'))
                    else:
                        grouped = work_alt.group_by(valid_row + [cc]).len().rename({'len': '_value'})
                    ct_alt_lookup = {}
                    for row in grouped.iter_rows(named=True):
                        row_key = tuple(row[c] for c in valid_row)
                        ct_alt_lookup[(row_key, row[cc])] = float(row['_value'] or 0)

            group_label = _label(cc, col_defs) if col_defs else cc
            col_groups.append({
                'label': group_label,
                'col': cc,
                'headers': vals,
                'pivot': ct_lookup,
                'pivot_alt': ct_alt_lookup,
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
                        raw = pivot_cell(ct, idx_tuple[0], cv)
                        alt_raw = pivot_cell(ct_alt, idx_tuple[0], cv, default=None) if ct_alt is not None else None
                    else:
                        raw = pivot_cell(ct, idx_tuple, cv)
                        alt_raw = pivot_cell(ct_alt, idx_tuple, cv, default=None) if ct_alt is not None else None
                    if alt_raw == 0.0 and ct_alt is not None and (idx_tuple if len(valid_row) > 1 else idx_tuple[0], cv) not in ct_alt:
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

    mask = blank_filter_expr(valid_row + [col_col], _BLANK)
    keep_cols = list(dict.fromkeys(valid_row + [col_col] + ([_dup_key] if _dup_key else [])))
    if (agg_func != 'count' or (show_tooltips and value_col)) and value_col and value_col in df.columns:
        keep_cols.append(value_col)
    work = df.filter(mask).select(keep_cols)

    work = strip_str_cols(work, valid_row + [col_col])
    if value_col and value_col in work.columns:
        work = work.with_columns(to_numeric_expr(value_col).alias(value_col))

    if work.is_empty():
        return _message_option('Dynamic Summary Table: no data after filtering blanks', cfg)

    if _dup_key and agg_func == 'count':
        work = work.unique(subset=[_dup_key] + valid_row + [col_col], keep='first')

    row_val_lists = {}
    for c in valid_row:
            row_val_lists[c] = _auto_detect_pl_scale(work[c].unique().to_list())

    row_combos = _build_row_combos(row_val_lists, valid_row)

    group_cols = valid_row + [col_col]
    _agg_map = {'sum': 'sum', 'avg': 'mean', 'min': 'min', 'max': 'max', 'median': 'median'}
    col_vals = _auto_detect_pl_scale(work[col_col].unique().to_list())

    ct_alt_lookup = None
    if show_tooltips:
        if agg_func == 'count':
            if value_col and value_col in df.columns:
                work_alt = work.with_columns(to_numeric_expr(value_col).alias(value_col))
                ct_alt_lookup = pivot_lookup(work_alt, valid_row, col_col, 'avg', value_col, _agg_map)
        else:
            work_alt = work
            if value_col and value_col in work_alt.columns:
                work_alt = work_alt.drop_nulls(subset=[value_col])
            if _dup_key:
                grouped = work_alt.group_by(group_cols).agg(pl.col(_dup_key).n_unique().alias('_value'))
            else:
                grouped = work_alt.group_by(group_cols).len().rename({'len': '_value'})
            ct_alt_lookup = {}
            for row in grouped.iter_rows(named=True):
                row_key = tuple(row[c] for c in valid_row) if len(valid_row) > 1 else row[valid_row[0]]
                ct_alt_lookup[(row_key, row[col_col])] = float(row['_value'] or 0)

    ct_lookup = pivot_lookup(
        work, valid_row, col_col,
        agg_func if agg_func != 'count' else 'count',
        value_col if agg_func != 'count' else '',
        _agg_map,
    )

    rows_data = []
    col_totals = [0.0] * len(col_vals)
    for combo in row_combos:
        idx_tuple = tuple(combo)
        cell_vals = []
        row_cells = []
        for cv in col_vals:
            if len(valid_row) == 1:
                raw = pivot_cell(ct_lookup, idx_tuple[0], cv)
                alt_raw = pivot_cell(ct_alt_lookup, idx_tuple[0], cv, default=None) if ct_alt_lookup is not None else None
            else:
                raw = pivot_cell(ct_lookup, idx_tuple, cv)
                alt_raw = pivot_cell(ct_alt_lookup, idx_tuple, cv, default=None) if ct_alt_lookup is not None else None
            if alt_raw == 0.0 and ct_alt_lookup is not None:
                key = (idx_tuple if len(valid_row) > 1 else idx_tuple[0], cv)
                if key not in ct_alt_lookup:
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

# Expose private names to sibling ``import *`` (PEP 328).
__all__ = [n for n in list(globals()) if not n.startswith('__')]
