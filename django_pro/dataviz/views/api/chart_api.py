"""Chart API views: preview, save, detail, list partial."""
import hashlib
import json
import logging
import time
import traceback

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST, require_GET

from dataviz.models import SavedChart
from dataviz.utils.chart_builder import build_option
from clickhouse_utils.timing import timed_fn

from ..mixins import is_project_admin, FORBIDDEN

# Bump when chart row-fetch or counting logic changes — busts Redis data/render caches.
_CHART_PIPELINE_VERSION = '16'
_timing_log = logging.getLogger('analytics.timing')

# One ClickHouse query per unique chart+filters under a stampede; waiters poll Redis.
_FLIGHT_LOCK_TTL = 30
_FLIGHT_WAIT_S = 0.05
_FLIGHT_WAIT_TRIES = 80  # 4s; gunicorn timeout is 120s

_CLICKHOUSE_AGGREGATE_CHARTS = frozenset({
    'bar', 'column', 'grouped_bar', 'stacked_bar', 'line', 'area',
    'areaspline', 'stacked_area', 'pie', 'donut',
})


# Chart aggregations ClickHouse can compute over the complete filtered
# population. ``build_option`` applies ``count`` when a row count is requested.
_AGGREGATION_PUSH_DOWN = frozenset({
    'count', 'sum', 'avg', 'min', 'max', 'median',
    'count_unique', 'count_occurrence',
})


def _resolve_dataset_col(value, ds, index):
    """Map a chart-config column reference to its ``table.column`` dataset name."""
    if not value:
        return None
    if value in (ds.columns or []):
        return value
    return index.get(value)


def _measure_display_label(y_col, agg, column_defs=None, col_mappings=None) -> str:
    """Reproduce ``build_option``'s value label for the original measure.

    The pushdown rewrites the config so the pre-computed measure is read from a
    synthetic column and is not re-aggregated. The display label has to be
    resolved here, from the original config, or ``build_option`` sees only the
    placeholder column name and renders that as the series name.
    """
    if not y_col:
        return 'Count' if str(agg or 'count').lower() == 'count' else ''
    key = y_col
    if col_mappings and key not in (column_defs or {}):
        key = col_mappings.get(key, key)
    return str((column_defs or {}).get(key, key)).replace('\n', ' \u2014 ').replace('\r', '')


def _clickhouse_aggregate_dataset(ds, cfg, filters, column_mappings, *,
                                  allow_limit=False, column_defs=None):
    """Return compact chart rows for visual datasets ClickHouse can aggregate.

    Chart builders historically received every matching row and aggregated it
    in Polars. For dimension/measure charts — including grouped and stacked
    bars with a group column and composite multi-column category axes — let
    ClickHouse perform the aggregation and send only grouped rows to Django.

    The returned rows are then rendered by the same ``build_option`` path as
    the raw-row case, so chart semantics, ordering, formatting and colour
    handling stay identical. Only the population scan moves into SQL.

    Chart families that still need per-row data (progress growth, date series,
    scatter/bubble, box/histogram, assessment compare, summary tables, dynamic
    calculations) keep the existing raw-row path until each has its own
    equivalence-tested pushdown.
    """
    chart_type = cfg.get('chart_type', 'bar')
    if chart_type not in _CLICKHOUSE_AGGREGATE_CHARTS:
        return None

    x_cols = cfg.get('x_cols') or []
    if not isinstance(x_cols, list):
        x_cols = []
    x_cols = [c for c in x_cols if isinstance(c, str) and c.strip()]

    x_col = cfg.get('x_col') or ''
    if len(x_cols) > 1:
        # Composite category axis: every x column participates in the grouping.
        group_cols = list(x_cols)
    elif len(x_cols) == 1:
        group_cols = [x_cols[0]]
    else:
        group_cols = [x_col] if x_col else []
    if not group_cols or any(not c for c in group_cols):
        return None

    group_col = cfg.get('group_col') or ''
    series_col = cfg.get('stack_col') or cfg.get('series_col') or ''
    if series_col and series_col in group_cols:
        series_col = ''

    # Multiple value columns build one series each from separate aggregations;
    # that shape needs its own dedicated pushdown, not this single-measure one.
    y_cols = cfg.get('y_cols') or []
    if isinstance(y_cols, list) and len([c for c in y_cols if c]) > 1:
        return None

    y_col = cfg.get('y_col') or None
    aggregation = str(cfg.get('agg', 'count') or 'count').lower()
    if aggregation not in _AGGREGATION_PUSH_DOWN:
        return None
    # Same rule as the Python chart builder: stacking by the value column
    # (e.g. PL stacked + PL avg) must count rows, not average the labels.
    if series_col and y_col and series_col == y_col and aggregation != 'count':
        aggregation = 'count'
        y_col = None
    # ``count_occurrence`` is a non-null row count of the value column; without
    # a value column it is indistinguishable from a plain row count.
    if aggregation == 'count_occurrence' and not y_col:
        aggregation = 'count'

    from dataviz.utils.compiler import (
        build_dataset_col_index, compile_aggregated_dataset_to_sql,
        execute_compiled_query, SQLCompilationError,
    )
    from dataviz.utils.status_filter import prepare_interactive_filters

    index = build_dataset_col_index(ds)

    x_full_list = [_resolve_dataset_col(c, ds, index) for c in group_cols]
    group_full = _resolve_dataset_col(group_col, ds, index)
    series_full = _resolve_dataset_col(series_col, ds, index) if series_col else None
    y_full = _resolve_dataset_col(y_col, ds, index) if y_col else None
    if any(not c for c in x_full_list):
        return None
    if group_col and not group_full:
        return None
    if series_col and not series_full:
        return None
    if y_col and not y_full:
        return None

    active_tables = {ds.base_table} | {j['table'] for j in (ds.joins or []) if j.get('table')}
    resolved_interactive, status_val = prepare_interactive_filters(filters or {}, ds, active_tables)
    # Saved chart rendering is an aggregate_full operation: a row limit would
    # change the values represented by the chart.  Limits are only valid for
    # explicitly bounded builder previews.
    limit_val = _parse_chart_row_limit(cfg) if allow_limit else None

    # ``build_option`` drops null/blank group values before aggregating, so the
    # pushdown must drop the same rows. Only safe when the measure cannot itself
    # be a group column, otherwise the drop would remove valid measure values.
    blank_measure = {y_full, series_full, group_full} - {None}
    filter_blank_group_cols = not (blank_measure & set(x_full_list))

    # Extra dimensions beyond the primary x: the remaining composite-x columns,
    # the bar group column, and the series column. Each becomes its own output
    # column so ``build_option`` can rebuild the exact same categories.
    extra_group_cols = []
    for col_full in x_full_list[1:] + [group_full, series_full]:
        if col_full and col_full not in x_full_list[:1] and col_full not in extra_group_cols:
            extra_group_cols.append(col_full)

    try:
        sql, params = compile_aggregated_dataset_to_sql(
            ds, x_full_list[:1], y_full, aggregation, series_col=series_full,
            user=None, limit=limit_val, interactive_filters=resolved_interactive,
            one_row_per_stu_annual_key=bool(cfg.get('one_row_per_stu_annual_key')),
            date_bucket=(cfg.get('x_date_bucket') or '').strip().lower() or None,
            status_filter=status_val,
            extra_group_cols=extra_group_cols,
            filter_blank_group_cols=filter_blank_group_cols,
            # The raw-row chart path compiles with SELECT DISTINCT over the
            # dataset columns (Dataset.distinct defaults to True). Aggregating
            # over anything wider would count join fan-out rows the chart never
            # saw, so the same projection has to feed the aggregation — unless
            # that projection already carries a per-row unique key, in which
            # case the DISTINCT cannot collapse anything and aggregating the
            # base tables directly selects the same rows for far less work.
            distinct_over_cols=(
                None if _projection_has_unique_key(ds)
                else (list(ds.columns or []) if getattr(ds, 'distinct', True) else None)
            ),
        )
    except SQLCompilationError:
        return None
    if _sql_needs_postgres(sql):
        # ClickHouse-only aggregates cannot run through the PostgreSQL route.
        return None
    try:
        result = execute_compiled_query(sql, params)
    except Exception:
        # The compiled query may be refused by the ClickHouse translator or by
        # the executor itself. The pushdown is an optimization, so any failure
        # falls back to the raw-row path rather than failing the chart.
        _timing_log.warning(
            'op=chart_pushdown fallback=execution chart_type=%s', chart_type,
            exc_info=True,
        )
        return None
    if result.get('error'):
        return None

    alias_for = dict(column_mappings or {})

    def _alias(full_name, fallback):
        return alias_for.get(full_name, fallback)

    x_aliases = [_alias(full, orig) for full, orig in zip(x_full_list, group_cols)]

    # Map every projected SQL dimension back to the output key the chart config
    # addresses. Only these keys (plus the measure) end up on the row: a
    # ``table.column``-style key would be read by ``build_option`` as another
    # column, which both widens the frame and duplicates the dimension.
    key_by_sql_alias = {
        full.replace('.', '_'): alias
        for full, alias in zip(x_full_list, x_aliases)
    }
    if group_full:
        key_by_sql_alias[group_full.replace('.', '_')] = _alias(group_full, group_col)
    if series_full:
        key_by_sql_alias[series_full.replace('.', '_')] = _alias(series_full, series_col)
    series_alias = _alias(series_full, series_col) if series_full else ''

    compact_rows = []
    for row in result.get('rows', []):
        compact = {}
        for sql_alias, value in row.items():
            if sql_alias == '__dv_value':
                compact['__dv_value'] = value
            elif sql_alias in key_by_sql_alias:
                compact[key_by_sql_alias[sql_alias]] = value
        compact_rows.append(compact)

    # Sort the aggregate rows on their dimensions. The chart builder re-orders
    # categories itself (alphabetically, by value, or manually), but tied
    # categories fall back to the incoming row order — sorting here keeps that
    # tie-break identical to the raw-row path, which sorts its own grouping.
    if compact_rows:
        _dim_keys = [k for k in compact_rows[0] if k != '__dv_value']
        if _dim_keys:
            compact_rows.sort(key=lambda row: tuple(str(row.get(k, '')) for k in _dim_keys))

    compact_cfg = dict(cfg)
    if len(x_aliases) > 1:
        # Keep the composite axis in the config; ``build_option`` rebuilds the
        # " \u00b7 "-joined dimension from these columns exactly as it does for raw
        # rows. The joined value is never emitted by SQL.
        compact_cfg['x_cols'] = x_aliases
        compact_cfg['x_col'] = ''
    else:
        compact_cfg['x_col'] = x_aliases[0]
        compact_cfg['x_cols'] = [x_aliases[0]]
    # The measure has already been aggregated by ClickHouse, so the compact rows
    # carry pre-computed values and must not be re-aggregated as a measure.
    # ``__measure_label__`` carries the display name the original config would
    # have produced, so the series and value axis keep their real label.
    compact_cfg['y_col'] = '__dv_value'
    compact_cfg['agg'] = 'sum'
    compact_cfg['__measure_label__'] = _measure_display_label(
        y_col, aggregation, column_defs, column_mappings,
    )
    if group_full:
        compact_cfg['group_col'] = _alias(group_full, group_col)
    if series_full:
        compact_cfg['stack_col'] = series_alias
        compact_cfg['series_col'] = series_alias
    return {'rows': compact_rows, 'columns': list(result.get('columns', [])),
            'row_count': len(compact_rows)}, compact_cfg


def _projection_has_unique_key(ds) -> bool:
    """True when the dataset's own projection carries a per-row unique key.

    The raw-row path fetches ``SELECT DISTINCT`` over ``ds.columns``. When that
    projection includes a student identifier the deduplication cannot collapse
    any rows, so a pushdown can aggregate the base tables directly and skip a
    wide, expensive DISTINCT pass over every column.
    """
    columns = set(ds.columns or [])
    if not columns:
        return False
    return any(
        col in columns
        for col in ('current_student.ssid', 'current_student.student_id',
                    'current_student.stu_annual_key')
    )


_TABLE_AGG_PUSH_DOWN = frozenset({'count', 'sum', 'avg', 'min', 'max', 'median'})


def _dataset_needs_postgres(ds) -> bool:
    """Deprecated: prefer checking the compiled SQL, which is precise.

    Kept for callers that only have the dataset; the pushdowns use
    ``_sql_needs_postgres`` on the statement they are about to run.
    """
    from clickhouse_utils.executor import _sql_uses_pg_add_data_key

    return _sql_uses_pg_add_data_key(' '.join(ds.columns or []))


def _sql_needs_postgres(sql: str) -> bool:
    """True when the executor would route *this statement* to PostgreSQL.

    The pushdown emits ClickHouse-only aggregates, so a statement that
    references a Postgres-only ``add_data`` key (or a table absent from the
    ClickHouse mirror) must keep the raw-row path.
    """
    from clickhouse_utils.executor import (
        _sql_requires_postgres, _sql_uses_pg_add_data_key,
    )

    return _sql_uses_pg_add_data_key(sql) or _sql_requires_postgres(sql)


def _is_pivot_summary_chart(cfg: dict, fallback_type: str = '') -> bool:
    """True for the cross-tab chart families the cell pushdown can serve.

    ``chart_type`` normally lives on the chart model and is merged into the
    config by ``_effective_chart_cfg``; callers that have not merged it yet can
    pass the model value as *fallback_type*.
    """
    chart_type = cfg.get('chart_type') or fallback_type
    return chart_type in ('summary_table', 'dynamic_summary_table')


def _table_row_cols(cfg: dict, ds) -> list:
    """Row dimensions the summary-table builder would use, in config order."""
    if cfg.get('chart_type') == 'dynamic_summary_table':
        cols = cfg.get('dyn_row_cols')
        if cols is None:
            cols = cfg.get('st_row_cols', [])
    else:
        cols = [cfg.get('st_row_col')] if cfg.get('st_row_col') else []
    return [c for c in (cols if isinstance(cols, list) else []) if isinstance(c, str) and c.strip()]


def _table_col_col(cfg: dict, ds) -> str:
    """The single column dimension these pivot cells are computed against."""
    if cfg.get('chart_type') == 'dynamic_summary_table':
        if str(cfg.get('dyn_col_mode', 'pivot')) != 'pivot':
            return ''
        cols = cfg.get('dyn_col_cols')
        if cols is None:
            cols = cfg.get('st_col_cols', [])
        cols = [c for c in (cols if isinstance(cols, list) else []) if isinstance(c, str) and c.strip()]
        # Multi-column mode pivots each column independently; that needs one
        # query per column and is not covered by this single-pivot pushdown.
        return cols[0] if len(cols) == 1 else ''
    if str(cfg.get('st_col_mode', 'values')) != 'values':
        return ''
    return cfg.get('st_col_col') or ''


def _calc_threshold_sql(resolved_filters) -> str:
    """ClickHouse predicate for a calculation's threshold filters.

    Mirrors ``_dyn_filter_expr``: numeric comparisons cast the column to float so
    non-numeric and null values compare as null and drop out, while equality
    compares the stripped string so categorical columns work too.
    """
    _NUMERIC_OPS = {'lt': '<', 'lte': '<=', 'gt': '>', 'gte': '>='}
    # ``logic: 'or'`` folds the filter into the running expression with OR;
    # anything else (including the first filter) combines with AND. That is the
    # same left-to-right fold ``_dynamic_summary_table_option`` performs.
    combined = None
    for f in resolved_filters or []:
        if not isinstance(f, dict):
            continue
        op = f.get('op')
        expr = f.get('__sql_col__')
        if not expr:
            return ''
        predicate = None
        if op in _NUMERIC_OPS:
            try:
                num = float(f.get('val'))
            except (TypeError, ValueError):
                return ''
            predicate = f'({expr} {_NUMERIC_OPS[op]} {num})'
        elif op in ('eq', 'neq'):
            val = f.get('val')
            if val is None or str(val).strip() == '':
                continue
            literal = "'" + str(val).strip().replace("'", "''") + "'"
            stripped = f'trimBoth(toString({expr}))'
            predicate = (f'({stripped} = {literal})' if op == 'eq'
                         else f'({stripped} != {literal})')
        elif op in ('in', 'nin'):
            vals = f.get('val')
            if isinstance(vals, str):
                vals = [v for v in vals.split(',')]
            if not isinstance(vals, (list, tuple, set)):
                return ''
            vals = [str(v).strip() for v in vals if str(v).strip()]
            if not vals:
                # Polars returns none-of / all for empty lists.
                predicate = '0' if op == 'in' else '1'
            else:
                joined = ', '.join("'" + v.replace("'", "''") + "'" for v in vals)
                stripped = f'trimBoth(toString({expr}))'
                predicate = (f'({stripped} IN ({joined}))' if op == 'in'
                             else f'({stripped} NOT IN ({joined}))')
        if predicate is None:
            continue
        if combined is None:
            combined = predicate
        elif str(f.get('logic', 'and')).lower() == 'or':
            combined = f'({combined} OR {predicate})'
        else:
            combined = f'({combined} AND {predicate})'
    return combined or ''


def _calc_measures(cfg, ds, index):
    """SQL measure specs for a dynamic table's base calculation columns.

    Returns ``(measures, row_cols, calc_ids)`` or ``None`` when any calculation
    cannot be expressed as a single ClickHouse aggregate. Derived calculations
    stay in the builder: they are arithmetic over these base columns.
    """
    row_cols = [c for c in (cfg.get('dyn_row_cols') or []) if isinstance(c, str) and c.strip()]
    calcs = [c for c in (cfg.get('dyn_calculations') or []) if isinstance(c, dict)]
    if not calcs:
        return None

    measures = []
    calc_ids = []
    for idx, calc in enumerate(calcs):
        cid = str(calc.get('id') or f'c{idx}')
        calc_ids.append(cid)
        # Classify exactly as ``_dynamic_summary_table_option`` does: an explicit
        # ``calc_type`` wins, and the op/reference heuristic only applies when it
        # is absent. A base column can legitimately carry a stray ``op`` field.
        ctype = calc.get('calc_type')
        if ctype not in ('base', 'derived'):
            ctype = ('derived'
                     if calc.get('op') and (calc.get('ref_a') is not None
                                            or calc.get('ref_b') is not None)
                     else 'base')
        if ctype != 'base':
            continue
        agg = str(calc.get('agg_func') or cfg.get('dyn_agg_func') or 'count').lower()
        if agg not in _TABLE_AGG_PUSH_DOWN:
            return None
        value_col = str(calc.get('value_col') or '')
        value_full = _resolve_dataset_col(value_col, ds, index) if value_col else None
        if agg != 'count' and not value_full:
            return None

        resolved_filters = []
        raw_filters = calc.get('filters')
        if not isinstance(raw_filters, list):
            raw_filters = [calc['filter']] if calc.get('filter') else []
        for f in raw_filters:
            if not isinstance(f, dict):
                continue
            fcol = _resolve_dataset_col(f.get('col'), ds, index)
            if not fcol:
                return None
            ff = dict(f)
            ff['__sql_col__'] = '"' + fcol.replace('.', '"."') + '"'
            resolved_filters.append(ff)
        measures.append({
            'name': cid,
            'aggregation': agg,
            'y_col': value_full,
            'filter_sql': _calc_threshold_sql(resolved_filters),
        })

    if not measures:
        # Only derived columns: nothing to aggregate, nothing to save.
        return None
    return measures, row_cols, calc_ids


def _clickhouse_aggregate_table_cells(ds, cfg, filters, column_mappings, *, column_defs=None):
    """Return pre-aggregated pivot cells for a summary table or dynamic pivot.

    The table builders cross-tabulate raw rows in Polars. This computes the
    same cells in ClickHouse and hands the builder a compact frame it can read
    directly, so the table is assembled from aggregates instead of the full
    population.

    Three shapes are supported: a ``values``-mode summary table (one measure per
    row/column cell), a dynamic ``pivot`` (same, with multiple row dimensions),
    and a dynamic ``calculations`` table (one column per base calculation, with
    its threshold filters applied as conditional aggregates).

    Returns ``(result, compact_cfg)`` or ``None`` to fall back to the raw-row
    path.
    """
    from dataviz.utils.compiler import (
        build_dataset_col_index, compile_aggregated_dataset_to_sql,
        execute_compiled_query, SQLCompilationError,
    )
    from dataviz.utils.status_filter import prepare_interactive_filters

    chart_type = cfg.get('chart_type', '')
    if chart_type not in ('summary_table', 'dynamic_summary_table'):
        return None

    index = build_dataset_col_index(ds)
    is_calc_mode = (
        chart_type == 'dynamic_summary_table'
        and str(cfg.get('dyn_col_mode', 'pivot')) == 'calculations'
    )

    measures = None
    calc_ids = None
    if is_calc_mode:
        parsed = _calc_measures(cfg, ds, index)
        if parsed is None:
            return None
        measures, row_cols, calc_ids = parsed
        if not row_cols:
            # Totals-only tables have no grouping dimension to push down.
            return None
        row_full = [_resolve_dataset_col(c, ds, index) for c in row_cols]
        if any(not c for c in row_full):
            return None
        col_full = None
        col_col = ''
        aggregation = 'count'
        y_full = None
        sql_aggregation = 'count'
    else:
        if chart_type == 'summary_table':
            cfg_agg = cfg.get('st_agg_func', 'count')
            cfg_value_col = cfg.get('st_value_col', '')
        else:
            cfg_agg = cfg.get('dyn_agg_func') or cfg.get('st_agg_func', 'count')
            cfg_value_col = cfg.get('dyn_value_col') or cfg.get('st_value_col', '')

        aggregation = str(cfg_agg or 'count').lower()
        if aggregation not in _TABLE_AGG_PUSH_DOWN:
            return None

        row_cols = _table_row_cols(cfg, ds)
        col_col = _table_col_col(cfg, ds)
        if not row_cols or not col_col:
            return None

        row_full = [_resolve_dataset_col(c, ds, index) for c in row_cols]
        col_full = _resolve_dataset_col(col_col, ds, index)
        if any(not c for c in row_full) or not col_full:
            return None

        # Mirror the builders' dedupe key selection, restricted to columns the
        # dataset actually exposes.
        dedupe_key = ''
        if aggregation == 'count':
            for candidate in ('ssid', 'student_id', 'stu_annual_key'):
                if _resolve_dataset_col(candidate, ds, index):
                    dedupe_key = candidate
                    break
        dedupe_full = _resolve_dataset_col(dedupe_key, ds, index) if dedupe_key else None

        value_col = ''
        value_full = None
        if aggregation != 'count':
            value_col = str(cfg_value_col or '')
            value_full = _resolve_dataset_col(value_col, ds, index) if value_col else None
            if not value_full:
                # Without a usable value column the builder falls back to
                # counting rows, so the pushdown has to as well.
                aggregation = 'count'
                value_col = ''
        # ``avg``/``median`` etc. must run over the numeric projection, and a
        # count counts non-null dedupe keys. Either way the measure is the y
        # column.
        y_full = dedupe_full if aggregation == 'count' else value_full
        sql_aggregation = (
            'count_non_null' if (aggregation == 'count' and dedupe_full) else aggregation
        )

    active_tables = {ds.base_table} | {j['table'] for j in (ds.joins or []) if j.get('table')}
    resolved_interactive, status_val = prepare_interactive_filters(filters or {}, ds, active_tables)

    # The raw path fetches SELECT DISTINCT over every dataset column, but when
    # that projection carries a per-row unique key the DISTINCT cannot collapse
    # anything, so aggregating the base tables directly selects the same rows
    # for far less work.
    distinct_over_cols = (
        None if _projection_has_unique_key(ds)
        else (list(ds.columns or []) if getattr(ds, 'distinct', True) else None)
    )

    try:
        sql, params = compile_aggregated_dataset_to_sql(
            ds, row_full[:1], y_full, sql_aggregation, series_col=col_full,
            user=None, limit=None, interactive_filters=resolved_interactive,
            one_row_per_stu_annual_key=bool(cfg.get('one_row_per_stu_annual_key')),
            status_filter=status_val,
            extra_group_cols=row_full[1:],
            filter_blank_group_cols=True,
            distinct_over_cols=distinct_over_cols,
            measure_specs=measures,
        )
    except SQLCompilationError:
        return None
    if _sql_needs_postgres(sql):
        # ClickHouse-only aggregates cannot run through the PostgreSQL route.
        return None
    try:
        result = execute_compiled_query(sql, params)
    except Exception:
        _timing_log.warning(
            'op=chart_pushdown fallback=execution chart_type=%s', chart_type,
            exc_info=True,
        )
        return None
    if result.get('error'):
        return None
    if not result.get('rows'):
        # Let the raw-row path produce its own "no data" table.
        return None

    row_sql_aliases = [full.replace('.', '_') for full in row_full]

    if is_calc_mode:
        # One column per base calculation, keyed by its stable id. The row
        # dimensions travel on the frame too, because the builder re-derives its
        # row values and blank mask from the dataframe columns.
        compact_rows = []
        for row in result.get('rows', []):
            compact = {out_key: row.get(sql_alias)
                       for sql_alias, out_key in zip(row_sql_aliases, row_cols)}
            for cid in calc_ids:
                compact[cid] = row.get(cid)
            compact_rows.append(compact)

        lookup = {}
        for row in compact_rows:
            row_key = tuple(row.get(c) for c in row_cols)
            if len(row_key) == 1:
                row_key = row_key[0]
            lookup[row_key] = {cid: row.get(cid) for cid in calc_ids}

        compact_cfg = dict(cfg)
        compact_cfg['__compact_calc_cells__'] = lookup
        compact_cfg['dyn_row_cols'] = row_cols
        return {'rows': compact_rows,
                'columns': list(result.get('columns', [])),
                'row_count': len(compact_rows)}, compact_cfg

    # Output keys match the config's own column references, so the builder finds
    # the dimensions it asked for and the compact lookup can key on them.
    key_by_sql_alias = {
        full.replace('.', '_'): out_key
        for full, out_key in zip(row_full + [col_full], row_cols + [col_col])
    }
    compact_rows = []
    for row in result.get('rows', []):
        compact = {}
        for sql_alias, value in row.items():
            if sql_alias == '__dv_value':
                compact['__dv_value'] = value
            elif sql_alias in key_by_sql_alias:
                compact[key_by_sql_alias[sql_alias]] = value
        compact_rows.append(compact)

    if compact_rows:
        dim_cols = [k for k in compact_rows[0] if k != '__dv_value']
        if dim_cols:
            compact_rows.sort(key=lambda r: tuple(str(r.get(k, '')) for k in dim_cols))

    compact_cfg = dict(cfg)
    compact_cfg['__compact_cells__'] = True
    if chart_type == 'dynamic_summary_table':
        compact_cfg['dyn_row_cols'] = row_cols
        compact_cfg['dyn_col_cols'] = [col_col]
    else:
        compact_cfg['st_row_col'] = row_cols[0]
        compact_cfg['st_col_col'] = col_col
    return {'rows': compact_rows, 'columns': list(result.get('columns', [])),
            'row_count': len(compact_rows)}, compact_cfg


def _effective_chart_cfg(chart) -> dict:
    """Merge model-level chart fields into config (older saves omit chart_type)."""
    cfg = dict(chart.config or {})
    cfg['chart_type'] = chart.chart_type or cfg.get('chart_type', 'bar')
    return cfg


_CONFIG_COL_KEYS = frozenset({
    'x_col', 'y_col', 'stack_col', 'series_col', 'group_col',
    'pg_before_col', 'pg_after_col', 'st_row_col', 'st_col_col',
    'st_row_cols', 'st_col_cols', 'st_value_col',
    'dyn_row_cols', 'dyn_col_cols', 'dyn_value_col',
    'sort_col', 'compare_pl_cols',
})


def _remap_config_cols(config: dict, column_mappings: dict | None) -> dict:
    """Remap 'table.column' column references in chart config to their clean aliases.

    Needed for backward-compat: charts saved before alias support stored raw
    table-prefixed names (e.g. x_col='current_student.grade') while the rows
    now carry aliased keys ('grade'). The remapping makes both old and new
    configs work without a data migration.
    """
    if not column_mappings or not config:
        return config

    def _remap(val):
        if isinstance(val, str):
            parts = [p.strip() for p in val.split(',') if p.strip()]
            remapped = [column_mappings.get(p, p) for p in parts]
            return ','.join(remapped)
        if isinstance(val, list):
            return [column_mappings.get(v, v) if isinstance(v, str) else v for v in val]
        return val

    res = {}
    for k, v in config.items():
        if k in _CONFIG_COL_KEYS:
            res[k] = _remap(v)
        elif k == 'dyn_calculations' and isinstance(v, list):
            new_calcs = []
            for item in v:
                if isinstance(item, dict):
                    new_item = dict(item)
                    if 'value_col' in new_item and isinstance(new_item['value_col'], str):
                        new_item['value_col'] = column_mappings.get(new_item['value_col'], new_item['value_col'])
                    f = new_item.get('filter')
                    if isinstance(f, dict) and f.get('col') and isinstance(f['col'], str):
                        new_item['filter'] = {**f, 'col': column_mappings.get(f['col'], f['col'])}
                    filters = new_item.get('filters')
                    if isinstance(filters, list):
                        new_item['filters'] = [
                            {**ff, 'col': column_mappings.get(ff['col'], ff['col'])}
                            if isinstance(ff, dict) and isinstance(ff.get('col'), str)
                            else ff
                            for ff in filters
                        ]
                    new_calcs.append(new_item)
                else:
                    new_calcs.append(item)
            res[k] = new_calcs
        elif k == 'col_header_aliases' and isinstance(v, dict):
            expanded = {}
            for ak, av in v.items():
                expanded[ak] = av
                if isinstance(ak, str):
                    mapped = column_mappings.get(ak)
                    if mapped and mapped != ak:
                        expanded[mapped] = av
                    if '.' in ak:
                        expanded[ak.split('.')[-1]] = av
            res[k] = expanded
        else:
            res[k] = v
    return res


def _parse_chart_row_limit(cfg: dict):
    """Return an explicit row limit from chart config, or None for unlimited."""
    limit_val = (cfg or {}).get('limit')
    try:
        if limit_val is not None and str(limit_val).strip() != '':
            return int(limit_val)
    except (ValueError, TypeError):
        pass
    return None


def _progress_growth_query_columns(chart_type: str, chart_cfg: dict) -> list:
    """PL columns the chart query must fetch — same extras drill-through adds."""
    if chart_type != 'progress_growth':
        return []
    cols = []
    for key in ('pg_before_col', 'pg_after_col'):
        col = (chart_cfg or {}).get(key, '')
        if col and col not in cols:
            cols.append(col)
    return cols


def _dyn_calc_query_columns(chart_cfg: dict) -> list:
    """Value/filter columns calculation-mode tables need beyond the explorer selection."""
    cols = []
    seen = set()
    for calc in (chart_cfg or {}).get('dyn_calculations') or []:
        if not isinstance(calc, dict):
            continue
        vc = calc.get('value_col')
        if vc and vc not in seen:
            seen.add(vc)
            cols.append(vc)
        flist = calc.get('filters') if isinstance(calc.get('filters'), list) else [calc.get('filter')]
        for f in flist:
            col = f.get('col') if isinstance(f, dict) else None
            if col and col not in seen:
                seen.add(col)
                cols.append(col)
    return cols


def _chart_extra_query_columns(chart_cfg: dict) -> list:
    extras = _progress_growth_query_columns((chart_cfg or {}).get('chart_type', ''), chart_cfg)
    for col in _dyn_calc_query_columns(chart_cfg):
        if col not in extras:
            extras.append(col)
    return extras


def _augment_chart_result_rows(chart_type: str, chart_cfg: dict, col_mappings: dict, result: dict) -> dict:
    from dataviz.utils.assessment_enrichment import augment_chart_result_rows
    return augment_chart_result_rows(chart_type, chart_cfg, col_mappings, result)


def _augment_rows_with_assessment_cols(rows: list, needed_keys: list, dataset, source_filters=None) -> list:
    from dataviz.utils.assessment_enrichment import augment_rows_with_assessment_cols
    return augment_rows_with_assessment_cols(rows, needed_keys, dataset, source_filters)


def _get_missing_assessment_cols(cfg: dict, col_mappings: dict, aliased_rows: list) -> list:
    from dataviz.utils.assessment_enrichment import get_missing_assessment_cols
    return get_missing_assessment_cols(cfg, col_mappings, aliased_rows)


def _cfg_from_post(post):
    """Extract chart config dict from form-encoded POST data.

    Maps HTML form field names to the keys expected by
    ``chart_builder.build_option()``.
    """
    show_legend = bool(post.get('show_legend'))
    legend_pos  = post.get('legend_pos', '')

    # Parse color_map JSON
    cm_raw = post.get('color_map', '')
    try:
        color_map = json.loads(cm_raw) if cm_raw else {}
    except (json.JSONDecodeError, ValueError):
        color_map = {}

    # Parse x_value_map JSON (category-axis display aliases)
    xvm_raw = post.get('x_value_map', '')
    try:
        x_value_map = json.loads(xvm_raw) if xvm_raw else {}
    except (json.JSONDecodeError, ValueError):
        x_value_map = {}
    if not isinstance(x_value_map, dict):
        x_value_map = {}

    cha_raw = post.get('col_header_aliases', '')
    try:
        col_header_aliases = json.loads(cha_raw) if cha_raw else {}
    except (json.JSONDecodeError, ValueError):
        col_header_aliases = {}
    if not isinstance(col_header_aliases, dict):
        col_header_aliases = {}

    # Parse stack_order JSON
    so_raw = post.get('stack_order', '')
    try:
        stack_order = json.loads(so_raw) if so_raw else []
    except (json.JSONDecodeError, ValueError):
        stack_order = []

    # Parse compare_pl_cols JSON
    cpc_raw = post.get('compare_pl_cols', '')
    try:
        compare_pl_cols = json.loads(cpc_raw) if cpc_raw else []
    except (json.JSONDecodeError, ValueError):
        compare_pl_cols = []

    # Parse assessment_compare filters
    def _parse_json_list(key):
        raw = post.get(key, '')
        try:
            return json.loads(raw) if raw else []
        except (json.JSONDecodeError, ValueError):
            return []

    ac_include_cats = _parse_json_list('ac_include_cats')
    ac_include_pl   = _parse_json_list('ac_include_pl')
    ac_col_filters_raw = post.get('ac_col_filters', '')
    try:
        ac_col_filters = json.loads(ac_col_filters_raw) if ac_col_filters_raw else {}
    except (json.JSONDecodeError, ValueError):
        ac_col_filters = {}

    # Parse pl_scale (JSON array or comma-separated string)
    pls_raw = post.get('pl_scale', '')
    try:
        if pls_raw and pls_raw.startswith('['):
            pl_scale = json.loads(pls_raw)
        else:
            pl_scale = [v.strip() for v in pls_raw.split(',') if v.strip()]
    except (json.JSONDecodeError, ValueError):
        pl_scale = []

    def _parse_int(val, default=0):
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def _parse_float(val, default=None):
        try:
            return float(val) if val else default
        except (ValueError, TypeError):
            return default

    def _parse_pos(val):
        if not val: return None
        if val in ('center', 'middle', 'left', 'right', 'top', 'bottom'):
            return val
        return _parse_int(val, None)

    return {
        'chart_type':        post.get('chart_type', 'bar'),
        'x_col':             post.get('x_col', ''),
        'x_date_bucket':     post.get('x_date_bucket', ''),
        'y_col':             post.get('y_col', ''),
        'series_col':        post.get('series_col', ''),
        'stack_col':         post.get('series_col', ''),
        'group_col':         post.get('group_col', ''),
        'show_group_total':  post.get('show_group_total', '') in ('1', 'true', 'True', 'on', True),
        'group_total_label': post.get('group_total_label') or None,
        'compare_pl_cols':   compare_pl_cols,
        'ac_include_cats':   ac_include_cats,
        'ac_include_pl':     ac_include_pl,
        'ac_col_filters':    ac_col_filters,
        'agg':               post.get('agg_func', 'count'),
        'agg_decimals':      _parse_int(post.get('agg_decimals'), None),
        # Progress Growth fields
        'pg_before_col':     post.get('pg_before_col', ''),
        'pg_after_col':      post.get('pg_after_col', ''),
        'pg_before_label':   post.get('pg_before_label', ''),
        'pg_after_label':    post.get('pg_after_label', ''),
        'pl_scale':          pl_scale,
        'pg_display':        post.get('pg_display', 'stacked_bar'),
        # Summary Table fields
        'st_row_col':        post.get('st_row_col', ''),
        'st_col_col':        post.get('st_col_col', ''),
        'st_col_mode':       post.get('st_col_mode', 'values'),
        'st_show_row_totals': post.get('st_show_row_totals', '1') == '1',
        'st_show_col_totals': post.get('st_show_col_totals', '1') == '1',
        'st_show_tooltips':   post.get('st_show_tooltips', '1') == '1',
        # Dynamic Summary Table fields
        'st_row_cols':       _parse_json_list('st_row_cols'),
        'st_col_cols':       _parse_json_list('st_col_cols'),
        'st_agg_func':       post.get('st_agg_func', 'count'),
        'st_value_col':      post.get('st_value_col', ''),
        'st_agg_decimals':   _parse_int(post.get('st_agg_decimals'), None),
        'dyn_row_cols':      _parse_json_list('dyn_row_cols'),
        'dyn_col_cols':      _parse_json_list('dyn_col_cols'),
        'dyn_agg_func':      post.get('dyn_agg_func', 'count'),
        'dyn_value_col':     post.get('dyn_value_col', ''),
        'dyn_agg_decimals':  _parse_int(post.get('dyn_agg_decimals'), None),
        'dyn_show_row_totals': post.get('dyn_show_row_totals', '1') == '1',
        'dyn_show_col_totals': post.get('dyn_show_col_totals', '1') == '1',
        'dyn_show_tooltips':  post.get('dyn_show_tooltips', '1') == '1',
        'dyn_col_mode':      post.get('dyn_col_mode', 'pivot'),
        'dyn_calculations':  _parse_json_list('dyn_calculations'),
        'dyn_drill_calc':    post.get('dyn_drill_calc', ''),
        'dyn_totals_only':   post.get('dyn_totals_only', '0') == '1',
        'dyn_display_type':  post.get('dyn_display_type', 'table'),
        # Dates (date_series) fields
        'ds_date_col':       post.get('ds_date_col', ''),
        'ds_date_bucket':    post.get('ds_date_bucket', 'day'),
        'ds_lines':          _parse_json_list('ds_lines'),
        'sort_col':          post.get('sort_col', ''),
        'sort_dir':          post.get('sort_dir', 'asc'),
        'limit':             _parse_int(post.get('limit'), None),
        # Visual options
        'palette':           post.get('palette', ''),
        'legend_pos':        legend_pos if legend_pos else ('bottom' if show_legend else 'hidden'),
        'legend_item_gap':   _parse_int(post.get('legend_item_gap'),  10),
        'legend_font_size':  _parse_int(post.get('legend_font_size'), 11),
        'legend_icon_size':  _parse_int(post.get('legend_icon_size'), 10),
        'legend_offset':     _parse_int(post.get('legend_offset'),     0),
        'legend_left':       _parse_pos(post.get('legend_left')),
        'legend_top':        _parse_pos(post.get('legend_top')),
        'data_labels':       bool(post.get('show_labels')),
        'label_position':    post.get('label_position', 'top'),
        'label_font_size':   _parse_int(post.get('label_font_size'), 10),
        'background':        post.get('background', 'white'),
        'opacity':           float(post.get('opacity', 0.9) or 0.9),
        'bar_border_radius': _parse_int(post.get('bar_border_radius'), 0),
        'show_points':       bool(post.get('show_points')),
        'smooth':            bool(post.get('smooth')),
        'color_map':         color_map,
        'x_value_map':       x_value_map,
        'col_header_aliases': col_header_aliases,
        # Stack order
        'stack_order':       stack_order,
        # Element toggles
        'show_zoom':         bool(post.get('show_zoom', '1')),
        'show_toolbox':      bool(post.get('show_toolbox', '1')),
        'show_grid':         bool(post.get('show_grid', '1')),
        'show_axis_labels':  bool(post.get('show_axis_labels', '1')),
        'show_categories':   bool(post.get('show_categories', '1')),
        'show_axis_titles':  bool(post.get('show_axis_titles', '1')),
        'animation':         bool(post.get('animation', '1')),
        'show_tooltip':           bool(post.get('show_tooltip', '1')),
        'tooltip_show_pct':       bool(post.get('tooltip_show_pct', '1')),
        'tooltip_show_count':     bool(post.get('tooltip_show_count', '1')),
        'tooltip_value_pct':      post.get('tooltip_value_pct') == '1',
        'show_percent':           bool(post.get('show_percent')),
        'show_percent_symbol':    bool(post.get('show_percent_symbol', '1')),
        'percent_decimals':  _parse_int(post.get('percent_decimals'), 1),
        'category_order':    _parse_json_list('category_order'),
        'bar_layout':       post.get('bar_layout', 'grouped'),
        'bar_orientation':  post.get('bar_orientation', 'vertical'),
        'bar_radial':       bool(post.get('bar_radial')),
        'bar_polar':        bool(post.get('bar_polar')),
        'line_layout':      post.get('line_layout', 'standard'),
        'line_fill':        bool(post.get('line_fill')),
        'compare_gap':       _parse_int(post.get('compare_gap'), 30),
        'compare_cat_font_size': _parse_int(post.get('compare_cat_font_size'), 12),
        'compare_cat_rotation':  _parse_int(post.get('compare_cat_rotation'), 0),
        'group_gap_size':        _parse_int(post.get('group_gap_size'), 0),
        'group_gap':             _parse_int(post.get('group_gap'), 0),
        'center_group_labels':   post.get('center_group_labels', '1') in ('1', 'true', 'True', 'on', True),
        # Axis & layout
        'x_label_rotation':  _parse_int(post.get('x_label_rotation'), 0),
        'x_label_font_size': _parse_int(post.get('x_label_font_size'), 11),
        'x_label_max_length': _parse_int(post.get('x_label_max_length'), 0),
        'force_show_all_labels': bool(post.get('force_show_all_labels')),
        'bar_category_gap':  post.get('bar_category_gap') or None,
        'y_axis_min':        post.get('y_axis_min') or None,
        'y_axis_max':        post.get('y_axis_max') or None,
        # Grid + title overrides (from interactive editor)
        'grid_left':   _parse_int(post.get('grid_left'),   None),
        'grid_right':  _parse_int(post.get('grid_right'),  None),
        'grid_top':    _parse_int(post.get('grid_top'),    None),
        'grid_bottom': _parse_int(post.get('grid_bottom'), None),
        'title_left':     _parse_pos(post.get('title_left')),
        'title_top':      _parse_pos(post.get('title_top')),
        'title_font_size': _parse_int(post.get('title_font_size'), 16),
        'label_distance':   _parse_int(post.get('label_distance'),   None),
        'chart_title':      post.get('chart_title', '').strip(),
        'show_title':       bool(post.get('show_title', '1')),
        'chart_subtitle':   post.get('chart_subtitle', '').strip(),
        'show_subtitle':    bool(post.get('show_subtitle')),
        'container_width':  _parse_int(post.get('container_width'),  None),
        'container_height': _parse_int(post.get('container_height'), None),
        'parliament_dot_scale': _parse_float(post.get('parliament_dot_scale'), None),
        'parliament_shape':     post.get('parliament_shape', 'semi-circle'),
        'parliament_rows':      _parse_int(post.get('parliament_rows'), None) if post.get('parliament_rows') else None,
        'parliament_show_ss':   bool(post.get('parliament_show_ss')),
        'one_row_per_stu_annual_key': bool(post.get('one_row_per_stu_annual_key')),
    }


@login_required
@require_POST
def chart_preview_api(request):
    """
    Build chart preview.

    Accepts:
      - JSON with rows + chart_config (direct render)
      - JSON / form with source ref + config (query → render)
    Returns ``dataviz/partials/chart_preview.html``.
    """
    if not is_project_admin(request.user):
        return FORBIDDEN

    is_htmx = bool(request.headers.get('HX-Request'))
    content_type = request.content_type or ''

    def _error(msg):
        if is_htmx:
            return render(request, 'dataviz/partials/chart_preview.html', {'error': msg})
        return JsonResponse({'error': msg}, status=400)

    if 'application/json' in content_type:
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return _error('Invalid JSON')
        # Legacy: rows passed directly
        if 'rows' in body:
            cfg = body.get('chart_config', {})
            if not cfg.get('chart_type'):
                return _error('chart_type is required')
            try:
                option = build_option(body['rows'], body.get('column_definitions', {}), cfg)
                option.pop('__series_names__', None)
                option.pop('__category_names__', None)
                query_columns = list(body['rows'][0].keys()) if body['rows'] else []
                return render(request, 'dataviz/partials/chart_preview.html', {
                    'option_json': json.dumps(option), 'chart_id': 'preview',
                    'query_columns_json': json.dumps(query_columns),
                })
            except Exception as exc:
                return _error(str(exc))
        source_type = body.get('source_type', 'query')
        source_id   = body.get('source_id') or body.get('saved_query_id')
        source_sql  = body.get('source_sql', '')
        cfg = body.get('config', body)
        if not cfg.get('chart_type'):
            cfg['chart_type'] = body.get('chart_type', 'bar')
    else:
        source_type = request.POST.get('source_type', 'query')
        source_id   = request.POST.get('saved_query_id') or request.POST.get('source_id')
        source_sql  = request.POST.get('raw_sql', '')
        cfg = _cfg_from_post(request.POST)

    if not cfg.get('chart_type'):
        return _error('Select a chart type first')

    # If editing a saved chart, capture its id so drill-through works in preview
    _chart_id = None
    if 'application/json' in content_type:
        _chart_id = body.get('id') or body.get('chart_id')
    else:
        _chart_id = request.POST.get('chart_id') or None
    # Coerce to int; fall back to 'preview' for new/unsaved charts
    try:
        _chart_id = int(_chart_id)
    except (TypeError, ValueError):
        _chart_id = None

    # Extract source_filters from the request (chart builder sends JSON-encoded filters)
    source_filters = {}
    if 'application/json' in content_type:
        source_filters = body.get('source_filters', {})
    else:
        sf_raw = request.POST.get('source_filters', '')
        if sf_raw:
            try:
                source_filters = json.loads(sf_raw)
            except (json.JSONDecodeError, ValueError):
                pass

    # ── Preview data cache: reuse query result across visual config tweaks ──
    # Cache keyed by data source config; expires in 2 minutes so fresh data
    # is fetched periodically while still avoiding repeated queries during
    # rapid config iteration.
    _sel_cols = None
    if source_type == 'columns':
        if 'application/json' in content_type:
            _sel_raw = body.get('selected_columns', '[]')
        else:
            _sel_raw = request.POST.get('selected_columns', '[]')
        try:
            _sel_cols = json.loads(_sel_raw) if isinstance(_sel_raw, str) else _sel_raw
        except (json.JSONDecodeError, ValueError):
            _sel_cols = []
        if not isinstance(_sel_cols, list):
            _sel_cols = []
        _sel_cols = list(_sel_cols)
        for _c in _chart_extra_query_columns(cfg):
            if _c not in _sel_cols:
                _sel_cols.append(_c)
    preview_source_hash = hashlib.md5(json.dumps({
        'st': source_type,
        'sid': source_id,
        'ssql': source_sql,
        'sf': source_filters,
        'sc': _sel_cols,
        'xc': _chart_extra_query_columns(cfg),
        'one_row_per_stu_annual_key': bool(cfg.get('one_row_per_stu_annual_key')),
        'x': cfg.get('x_col'),
        'y': cfg.get('y_col'),
        'stack': cfg.get('stack_col') or cfg.get('series_col'),
        'agg': cfg.get('agg'),
        'date_bucket': cfg.get('x_date_bucket'),
        'v': _CHART_PIPELINE_VERSION,
    }, sort_keys=True).encode()).hexdigest()[:12]
    preview_data_key = f'preview_data:{preview_source_hash}'
    preview_cached = cache.get(preview_data_key)

    # Run the query (skip if cached data from a recent preview)
    from dataviz.utils.data_loader import run_saved_query, run_raw_sql, run_column_query
    if preview_cached is not None:
        result = preview_cached
    elif source_type == 'sql':
        result = run_raw_sql(source_sql)
    elif source_type == 'columns':
        selected_columns = _sel_cols or []
        if not selected_columns:
            return _error('Select at least one column in the Explorer')
        result = run_column_query(selected_columns, source_filters)
    elif source_type == 'dataset':
        if not source_id:
            return _error('Select a dataset first')
        from dataviz.models import Dataset
        from dataviz.utils.compiler import (
            compile_dataset_to_sql, execute_compiled_query_aliased,
            generate_column_aliases, dedupe_rows_by_stu_annual_key,
            load_dataset_result,
        )
        from dataviz.utils.status_filter import prepare_interactive_filters
        try:
            ds = Dataset.objects.get(pk=source_id)
        except Dataset.DoesNotExist:
            return _error('Dataset not found')
        col_mappings = ds.column_mappings or generate_column_aliases(ds.columns or [], {})
        limit_val = _parse_chart_row_limit(cfg)
        one_per_sak = bool(cfg.get('one_row_per_stu_annual_key'))
        if getattr(ds, 'source_type', Dataset.SOURCE_VISUAL) == Dataset.SOURCE_SQL:
            res = load_dataset_result(ds, limit=limit_val, one_per_sak=one_per_sak)
            if res.get('error'):
                return _error(res['error'])
            aliased_rows = res.get('rows', [])
            cfg = _remap_config_cols(cfg, col_mappings)
        else:
            active_tables = {ds.base_table} | {j['table'] for j in (ds.joins or []) if j.get('table')}
            resolved_filters, status_val = prepare_interactive_filters(source_filters or {}, ds, active_tables)
            aggregated = _clickhouse_aggregate_dataset(
                ds, cfg, source_filters or {}, col_mappings,
                allow_limit=True,
            )
            if aggregated is not None:
                res, cfg = aggregated
            else:
                sql, params = compile_dataset_to_sql(
                    ds, user=None, interactive_filters=resolved_filters,
                    limit=limit_val, status_filter=status_val,
                    one_row_per_stu_annual_key=one_per_sak,
                )
                res = execute_compiled_query_aliased(sql, params, col_mappings)
                cfg = _remap_config_cols(cfg, col_mappings)
            if res.get('error'):
                return _error(res['error'])
            aliased_rows = res.get('rows', [])
            if one_per_sak and aggregated is None:
                aliased_rows = dedupe_rows_by_stu_annual_key(aliased_rows)
        # Fetch test_*/mark_* columns via data explorer for any alias that wasn't
        # returned by the dataset SQL (dataset without a results/tests JOIN).
        _missing_assessment = _get_missing_assessment_cols(cfg, col_mappings, aliased_rows)
        if _missing_assessment:
            aliased_rows = _augment_rows_with_assessment_cols(
                aliased_rows, _missing_assessment, None, source_filters=source_filters,
            )
        result = {'rows': aliased_rows, 'columns': res.get('columns', []), 'row_count': len(aliased_rows)}
    else:
        if not source_id:
            return _error('Select a data source first')
        extra_cols = _chart_extra_query_columns(cfg)
        result = run_saved_query(
            source_id, source_filters, request.user,
            extra_columns=extra_cols or None,
            apply_user_filters=False,
        )
        result = _augment_chart_result_rows(cfg.get('chart_type', ''), cfg, {}, result)

    # Cache preview data for 2 min so rapid config iteration reuses the query
    if preview_cached is None and not result.get('error') and result.get('rows'):
        cache.set(preview_data_key, result, timeout=120)

    if result.get('error'):
        return _error(result['error'])

    rows = result.get('rows', [])
    if not rows:
        return _error('Query returned no data')

    try:
        option = build_option(rows, {}, cfg)
        # Extract series names for the stack-order UI, then strip the internal key
        series_names    = option.pop('__series_names__', [])
        category_names  = option.pop('__category_names__', [])
        x_alias_keys    = option.pop('__x_alias_keys__', [])
        col_header_keys = option.pop('__col_header_keys__', [])
        ac_avail_cats   = option.pop('__ac_avail_cats__', [])
        ac_avail_pl     = option.pop('__ac_avail_pl__', [])
        query_columns = list(rows[0].keys()) if rows else (result.get('columns', []))
        return render(request, 'dataviz/partials/chart_preview.html', {
            'option_json':        json.dumps(option),
            'series_names_json':  json.dumps(series_names),
            'category_names_json': json.dumps(category_names),
            'x_alias_keys_json':  json.dumps(x_alias_keys),
            'col_header_keys_json': json.dumps(col_header_keys),
            'ac_avail_cats_json': json.dumps(ac_avail_cats),
            'ac_avail_pl_json':   json.dumps(ac_avail_pl),
            'query_columns_json': json.dumps(query_columns),
            'chart_id':           _chart_id or 'preview',
            'drill_enabled':      '1' if _chart_id else '0',
            'drill_source':       'chart_builder' if _chart_id else '',
            'drill_source_id':    str(_chart_id) if _chart_id else '',
            'drill_source_name':  'Chart Builder' if _chart_id else '',
        })
    except Exception as exc:
        return _error(f'Chart error: {exc}')


@login_required
def chart_save_api(request):
    """Create or update a SavedChart. Accepts POST JSON or form-encoded data."""
    if not is_project_admin(request.user):
        return FORBIDDEN
    if request.method not in ('POST', 'PUT'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    is_htmx = bool(request.headers.get('HX-Request'))
    content_type = request.content_type or ''

    if 'application/json' in content_type:
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        name           = (body.get('name') or '').strip()
        chart_id       = body.get('id')
        chart_type     = body.get('chart_type', 'bar')
        config         = body.get('config', {})
        source_type    = body.get('source_type', SavedChart.SOURCE_QUERY)
        source_id      = body.get('source_id')
        source_sql     = body.get('source_sql', '')
        source_filters = body.get('source_filters', {})
        description    = body.get('description', '')
        drill_columns  = body.get('drill_through_columns', [])
        drill_fm       = body.get('drill_through_filter_map', {})
        school_filter_mode = body.get('school_filter_mode', 'enrolled')
        is_user_menu   = bool(body.get('is_user_menu', False))
        user_menu_order = body.get('user_menu_order', 0) or 0
    else:
        name           = request.POST.get('name', '').strip()
        chart_id       = request.POST.get('chart_id') or None
        chart_type     = request.POST.get('chart_type', 'bar')
        source_type    = request.POST.get('source_type', 'query')
        source_id      = request.POST.get('saved_query_id') or request.POST.get('source_id')
        source_sql     = request.POST.get('raw_sql', '')
        description    = request.POST.get('description', '')
        config         = _cfg_from_post(request.POST)
        sf_raw         = request.POST.get('source_filters', '')
        try:
            source_filters = json.loads(sf_raw) if sf_raw else {}
        except (json.JSONDecodeError, ValueError):
            source_filters = {}
        dt_str         = request.POST.get('drill_through_columns_str', '')
        drill_columns  = [c.strip() for c in dt_str.split(',') if c.strip()] if dt_str else []
        drill_fm       = {}
        school_filter_mode = request.POST.get('school_filter_mode', 'enrolled')
        is_user_menu   = request.POST.get('is_user_menu') == 'on'
        try:
            user_menu_order = int(request.POST.get('user_menu_order', 0) or 0)
        except (TypeError, ValueError):
            user_menu_order = 0

    if not name:
        msg = 'Chart name is required'
        if is_htmx:
            return HttpResponse(f'<div class="text-red-600 text-sm py-2">{msg}</div>')
        return JsonResponse({'error': msg}, status=400)

    if chart_id:
        chart = get_object_or_404(SavedChart, pk=chart_id)
    else:
        chart = SavedChart(owner=request.user)

    chart.name                     = name
    chart.description              = description
    chart.chart_type               = chart_type
    chart.config                   = config
    chart.source_type              = source_type
    chart.source_id                = source_id or None
    chart.source_sql               = source_sql
    chart.source_filters           = source_filters
    chart.school_filter_mode       = school_filter_mode
    chart.drill_through_columns    = drill_columns
    chart.drill_through_filter_map = drill_fm
    chart.is_user_menu             = is_user_menu
    chart.user_menu_order          = user_menu_order

    # For Column Picker source, store selected columns inside config
    if source_type == 'columns':
        if 'application/json' in content_type:
            sel_raw = body.get('selected_columns', '[]')
        else:
            sel_raw = request.POST.get('selected_columns', '[]')
        try:
            sel_cols = json.loads(sel_raw) if isinstance(sel_raw, str) else sel_raw
        except (json.JSONDecodeError, ValueError):
            sel_cols = []
        chart.config['selected_columns'] = sel_cols

    chart.save()

    if is_htmx:
        return HttpResponse(
            f'<div class="bg-green-50 border border-green-200 text-green-700 '
            f'text-sm rounded-lg p-3 flex items-center gap-2">'
            f'<svg class="w-4 h-4 shrink-0" fill="currentColor" viewBox="0 0 20 20">'
            f'<path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.857-9.809'
            f'a.75.75 0 00-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 10-1.06 1.06l2.5 '
            f'2.5a.75.75 0 001.137-.089l4-5.5z" clip-rule="evenodd"/></svg>'
            f'<span>Chart &ldquo;{chart.name}&rdquo; saved!</span>'
            f'<a href="{reverse("dataviz:chart_edit", args=[chart.pk])}" '
            f'class="underline ml-auto text-green-800">Edit</a></div>'
        )

    return JsonResponse({'chart': chart.to_dict(), 'success': True})


@login_required
def chart_detail_api(request, chart_id):
    """GET a chart's full dict; PUT to update description / user-menu flag; DELETE to remove it."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    chart = get_object_or_404(SavedChart, pk=chart_id)

    if request.method == 'GET':
        return JsonResponse({'chart': chart.to_dict()})
    if request.method == 'PUT':
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        update_fields = []
        if 'description' in body:
            chart.description = body['description']
            update_fields.append('description')
        if 'is_user_menu' in body:
            chart.is_user_menu = bool(body['is_user_menu'])
            update_fields.append('is_user_menu')
        if not update_fields:
            return JsonResponse({'error': 'No updatable fields provided'}, status=400)
        chart.save(update_fields=update_fields)
        return JsonResponse({
            'success': True,
            'description': chart.description,
            'is_user_menu': chart.is_user_menu,
        })
    if request.method == 'DELETE':
        chart.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@login_required
@require_POST
def chart_move_api(request, chart_id):
    """Move a chart to a different folder by updating its name prefix."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    chart = get_object_or_404(SavedChart, pk=chart_id)
    try:
        body = json.loads(request.body)
        new_folder = body.get('folder', '').strip().strip('/')
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    # Extract current display name (part after last slash)
    display_name = chart.name.split('/')[-1].strip()
    
    if new_folder:
        chart.name = f"{new_folder} / {display_name}"
    else:
        chart.name = display_name
    
    chart.save()
    return JsonResponse({'chart': chart.to_dict(), 'success': True})


@login_required
@require_POST
def chart_duplicate_api(request, chart_id):
    """Duplicate a saved chart."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    chart = get_object_or_404(SavedChart, pk=chart_id)
    
    # Clone the object
    chart.pk = None
    chart.id = None
    chart.name = f"{chart.name} (Copy)"
    chart.save()
    
    return JsonResponse({'chart': chart.to_dict(), 'success': True})


def _can_render_chart(user, chart):
    """Project Admins can render any chart. Other users may only render charts
    exposed in the 'User Charts' menu or already placed on their home dashboard."""
    from ..mixins import is_project_admin
    if user.is_authenticated and is_project_admin(user):
        return True
    if chart.is_user_menu:
        return True
    if user.is_authenticated:
        from dataviz.models import Dashboard
        home = Dashboard.objects.filter(owner=user, is_home=True).first()
        if home and home.chart_slots.filter(chart_id=chart.pk).exists():
            return True
    return False


def _parse_pf_filters(get_params) -> dict:
    """Extract {column: value_or_list} from a QueryDict's pf_* params."""
    filters = {}
    for key in get_params:
        if key.startswith('pf_'):
            vals = [v for v in get_params.getlist(key) if v]
            if vals:
                filters[key[3:]] = vals if len(vals) > 1 else vals[0]
    return filters


def _chart_flight_keys(chart_id, updated_ts, pf_hash, embed_suffix, sync_version):
    lock_key = (
        f'chart_flight:{_CHART_PIPELINE_VERSION}:{chart_id}:'
        f'{updated_ts}:{pf_hash}:{embed_suffix}:{sync_version}'
    )
    return lock_key, f'{lock_key}:done'


def _wait_chart_flight(done_key):
    for _ in range(_FLIGHT_WAIT_TRIES):
        time.sleep(_FLIGHT_WAIT_S)
        done = cache.get(done_key)
        if done is not None:
            payload = dict(done)
            payload['cache_status'] = 'HIT'
            return payload
    return None


def warm_published_page_charts(limit=200, warm_schools=True):
    """Fill Redis for charts on active DataViz pages and dashboards after a ClickHouse sync."""
    from dataviz.models import DataVizPage, DataVizPageSection, DashboardChart
    from clickhouse_utils.thread_pool import get_io_pool

    ids = []
    seen = set()
    qs = (
        DataVizPageSection.objects
        .filter(
            element_type=DataVizPageSection.TYPE_CHART,
            chart_id__isnull=False,
            page__status=DataVizPage.STATUS_ACTIVE,
        )
        .order_by('page_id', 'gs_y', 'gs_x')
        .values_list('chart_id', flat=True)
    )
    for cid in qs.iterator():
        if cid in seen:
            continue
        seen.add(cid)
        ids.append(cid)
        if limit and len(ids) >= limit:
            break

    # Also include charts from home dashboards if limit allows
    if limit is None or len(ids) < limit:
        dash_qs = (
            DashboardChart.objects
            .filter(
                chart_id__isnull=False,
                dashboard__is_home=True,
            )
            .values_list('chart_id', flat=True)
        )
        for cid in dash_qs.iterator():
            if cid in seen:
                continue
            seen.add(cid)
            ids.append(cid)
            if limit and len(ids) >= limit:
                break

    school_ids = ['']
    if warm_schools:
        try:
            from studata.models import School
            active_schools = list(
                School.objects.filter(is_active=True)
                .exclude(state_id__isnull=True)
                .exclude(state_id='')
                .values_list('state_id', flat=True)[:10]
            )
            school_ids.extend(active_schools)
        except Exception:
            pass

    pool = get_io_pool()
    tasks = []
    for cid in ids:
        tasks.append((cid, ''))
        if warm_schools and len(school_ids) > 1:
            for s_id in school_ids[1:]:
                tasks.append((cid, s_id))

    def _warm_one(item):
        c_id, sch_id = item
        try:
            _render_saved_chart(c_id, {}, is_embed=True, school_id=sch_id)
            return True
        except Exception:
            _timing_log.exception('op=warm_published_page_charts chart_id=%s school_id=%s', c_id, sch_id)
            return False
        finally:
            try:
                from django.db import connections
                connections['default'].close()
            except Exception:
                pass

    if len(tasks) <= 1:
        warmed = sum(1 for t in tasks if _warm_one(t))
        return warmed

    pool = get_io_pool()
    futures = [pool.submit(_warm_one, t) for t in tasks]
    warmed = sum(1 for f in futures if f.result())
    return warmed


def _render_saved_chart(chart_id, pf_filters: dict, drill_source='', drill_source_id='',
                         drill_source_name='', is_embed=False, request=None, if_none_match=None,
                         school_id=None):
    """
    Core chart-render pipeline: runs the chart's query (or reuses the 2-layer
    Redis cache), builds the ECharts option, and renders the chart_preview
    partial to HTML. Shared by the single-chart (chart_render_api) and
    batch (chart_batch_render_api) endpoints so both stay in sync.

    ``school_id`` (a studata School ``state_id``) narrows the visual to a
    single school on demand. It is injected as a ``school_state_id`` column
    filter so it flows through every source type exactly like a page-level
    filter. Omit it (or pass ''/'all') to show the default view: all students.

    Safe to call concurrently for different chart_ids from multiple threads:
    each chart's cache keys/queries are independent, Django's DB connections
    and the ClickHouse client are thread-local, and django-redis's client is
    thread-safe.

    Returns one of:
      {'not_modified': True, 'etag': str}
      {'html': str, 'error': str|None, 'cache_status': 'HIT'|'MISS', 'etag': str}
    """
    from dataviz.utils.data_loader import run_saved_query, run_raw_sql, run_column_query

    chart = get_object_or_404(SavedChart, pk=chart_id)
    # Read once per render so a sync that happens mid-request cannot produce
    # mismatched data/render cache keys.
    sync_version = cache.get('analytics:clickhouse_sync_version', 'legacy')

    # ── School scope ────────────────────────────────────────────────
    if school_id and school_id not in ('all', 'All', ''):
        pf_filters = dict(pf_filters or {})
        pf_filters.setdefault('school_state_id', school_id)

    # ── Build cache key ────────────────────────────────────────────
    pf_params = sorted(pf_filters.items())
    pf_hash = hashlib.md5(str(pf_params).encode()).hexdigest()[:12]
    # updated_at as an int-second timestamp busts the key on every chart save
    updated_ts = int(chart.updated_at.timestamp())
    config_hash = hashlib.md5(json.dumps(chart.config, sort_keys=True).encode()).hexdigest()[:12]
    embed_suffix = 'e' if is_embed else 's'
    render_cache_key = (
        f'chart_render:{_CHART_PIPELINE_VERSION}:{chart_id}:{updated_ts}:'
        f'{config_hash}:{pf_hash}:{embed_suffix}:{sync_version}'
    )
    etag = f'W/"{render_cache_key[-16:]}"'

    if if_none_match == etag:
        return {'not_modified': True, 'etag': etag}

    # ── Check Layer 2 Render Cache first (<5ms hit, avoids deserializing raw rows) ──
    cached_html = cache.get(render_cache_key)
    if cached_html is not None:
        return {'html': cached_html, 'error': None, 'cache_status': 'HIT', 'etag': etag}

    lock_key, done_key = _chart_flight_keys(
        chart_id, updated_ts, pf_hash, embed_suffix, sync_version,
    )
    done = cache.get(done_key)
    if done is not None:
        payload = dict(done)
        payload['cache_status'] = 'HIT'
        return payload

    got_lock = cache.add(lock_key, 1, timeout=_FLIGHT_LOCK_TTL)
    if not got_lock:
        waited = _wait_chart_flight(done_key)
        if waited is not None:
            return waited

    # ── Layer 1 – Data cache: raw rows keyed by source + filters ──
    sel_cols_hash = ""
    if chart.source_type == SavedChart.SOURCE_COLUMNS:
        _sel_for_hash = list(chart.config.get('selected_columns', []))
        for _c in _chart_extra_query_columns(chart.config):
            if _c not in _sel_for_hash:
                _sel_for_hash.append(_c)
        sel_cols_hash = hashlib.md5(json.dumps(_sel_for_hash, sort_keys=True).encode()).hexdigest()[:8]

    extra_cols_hash = hashlib.md5(
        json.dumps(_chart_extra_query_columns(chart.config), sort_keys=True).encode()
    ).hexdigest()[:8]

    source_part = (
        f'{_CHART_PIPELINE_VERSION}:{chart.source_type}:{chart.source_id or "_"}:'
        f'{chart.source_sql or "_"}:{sel_cols_hash}:{extra_cols_hash}:{pf_hash}:{updated_ts}:'
        f'{sync_version}'
    )
    data_cache_key = f'chart_data:{source_part}'
    data_cached = cache.get(data_cache_key)
    result = data_cached if data_cached is not None else None

    # ── Cache miss: run the full pipeline ─────────────────────────
    chart_cfg = _effective_chart_cfg(chart)
    try:
        if result is not None:
            # Data cache hit — skip SQL, jump to rendering
            pass
        elif chart.source_type == SavedChart.SOURCE_SQL:
            result = run_raw_sql(chart.source_sql)
        elif chart.source_type == SavedChart.SOURCE_COLUMNS:
            selected_columns = list(chart.config.get('selected_columns', []))
            for c in _chart_extra_query_columns(chart.config):
                if c not in selected_columns:
                    selected_columns.append(c)
            merged_filters = dict(chart.source_filters or {})
            merged_filters.update(pf_filters)
            result = run_column_query(selected_columns, merged_filters)
            result = _augment_chart_result_rows(
                chart.chart_type, chart_cfg, {}, result,
            )
        elif chart.source_type == SavedChart.SOURCE_DATASET:
            from dataviz.models import Dataset
            from dataviz.utils.compiler import (
                compile_dataset_to_sql, execute_compiled_query_aliased,
                generate_column_aliases, dedupe_rows_by_stu_annual_key,
                load_dataset_result,
            )
            from dataviz.utils.status_filter import prepare_interactive_filters
            try:
                ds = Dataset.objects.get(pk=chart.source_id)
            except Dataset.DoesNotExist:
                result = {'error': 'Dataset not found', 'rows': [], 'columns': []}
            else:
                # Dataset-specific data cache — key includes dataset updated_at
                # so schema changes automatically bust stale cached rows.
                col_mappings = ds.column_mappings or generate_column_aliases(ds.columns or [], {})
                raw_filters = dict(chart.source_filters or {})
                raw_filters.update(pf_filters)
                limit_val = _parse_chart_row_limit(chart_cfg)
                one_per_sak = bool(chart_cfg.get('one_row_per_stu_annual_key'))
                ds_updated = int(ds.updated_at.timestamp())
                is_sql_ds = getattr(ds, 'source_type', Dataset.SOURCE_VISUAL) == Dataset.SOURCE_SQL
                if is_sql_ds:
                    ds_filter_hash = hashlib.md5(json.dumps({
                        '__sql__': True,
                        '__one_per_sak__': one_per_sak,
                    }, sort_keys=True, default=str).encode()).hexdigest()[:12]
                else:
                    active_tables = {ds.base_table} | {j['table'] for j in (ds.joins or []) if j.get('table')}
                    resolved_filters, status_val = prepare_interactive_filters(raw_filters, ds, active_tables)
                    ds_filter_hash = hashlib.md5(json.dumps(
                        {
                            **resolved_filters,
                            '__status__': status_val,
                            '__one_per_sak__': one_per_sak,
                        },
                        sort_keys=True, default=str,
                    ).encode()).hexdigest()[:12]
                ds_cache_key = (
                    f'chart_data:ds:{_CHART_PIPELINE_VERSION}:{chart.source_id}:'
                    f'{ds_updated}:{limit_val}:{ds_filter_hash}:{sync_version}'
                )
                ds_cached = cache.get(ds_cache_key)
                used_aggregate = False
                if ds_cached is not None:
                    result = ds_cached
                    chart_cfg = _remap_config_cols(_effective_chart_cfg(chart), col_mappings)
                else:
                    if is_sql_ds:
                        res = load_dataset_result(ds, limit=limit_val, one_per_sak=one_per_sak)
                    else:
                        active_tables = {ds.base_table} | {j['table'] for j in (ds.joins or []) if j.get('table')}
                        resolved_filters, status_val = prepare_interactive_filters(raw_filters, ds, active_tables)
                        if _is_pivot_summary_chart(chart_cfg, chart.chart_type):
                            aggregated = _clickhouse_aggregate_table_cells(
                                ds, chart_cfg, raw_filters, col_mappings,
                                column_defs=chart.column_defs,
                            )
                        else:
                            aggregated = _clickhouse_aggregate_dataset(
                                ds, chart_cfg, raw_filters, col_mappings,
                                column_defs=chart.column_defs,
                            )
                        if aggregated is not None:
                            res, chart_cfg = aggregated
                            used_aggregate = True
                        else:
                            sql, params = compile_dataset_to_sql(
                                ds, user=None, interactive_filters=resolved_filters,
                                limit=limit_val, status_filter=status_val,
                                one_row_per_stu_annual_key=one_per_sak,
                            )
                            res = execute_compiled_query_aliased(sql, params, col_mappings)
                    if res.get('error'):
                        result = {'error': res['error'], 'rows': [], 'columns': []}
                    else:
                        rows = res.get('rows', [])
                        result = {'rows': rows, 'columns': res.get('columns', []), 'row_count': len(rows)}
                        # Compact CH aggregates are chart-specific; do not store
                        # them under the shared dataset-row cache key.
                        if result.get('rows') and not used_aggregate:
                            cache.set(ds_cache_key, result, timeout=1800)
                    if not used_aggregate:
                        chart_cfg = _remap_config_cols(_effective_chart_cfg(chart), col_mappings)

                # After data is retrieved (either from cache or fresh query), augment with assessment cols if needed.
                # This must be done outside the dataset cache because different charts on the same dataset
                # might require different assessment columns.
                if not result.get('error') and result.get('rows'):
                    aliased_rows = result['rows']
                    if one_per_sak and not used_aggregate:
                        aliased_rows = dedupe_rows_by_stu_annual_key(aliased_rows)
                    _missing_assessment = _get_missing_assessment_cols(chart_cfg, col_mappings, aliased_rows)
                    if _missing_assessment:
                        aliased_rows = _augment_rows_with_assessment_cols(
                            aliased_rows, _missing_assessment, None,
                            source_filters=raw_filters,
                        )
                    result['rows'] = aliased_rows
                    result['row_count'] = len(aliased_rows)

        else:
            merged_filters = dict(chart.source_filters or {})
            merged_filters.update(pf_filters)
            user = getattr(request, 'user', None)
            extra_cols = _chart_extra_query_columns(chart_cfg)
            result = run_saved_query(
                chart.source_id, merged_filters, user,
                extra_columns=extra_cols or None,
                apply_user_filters=False,
            )
            result = _augment_chart_result_rows(
                chart.chart_type, chart_cfg, {}, result,
            )

        if result.get('error'):
            option_json = '{}'
            error = result['error']
        else:
            # Save data cache (30 min TTL — confirmed acceptable staleness window)
            if data_cached is None and result.get('rows'):
                cache.set(data_cache_key, result, timeout=1800)
            chart_cfg['is_embed'] = is_embed
            option = build_option(result.get('rows', []), chart.column_defs, chart_cfg)
            option.pop('__series_names__', None)
            option_json = json.dumps(option)
            error = None
    except Exception as exc:
        option_json = '{}'
        error = str(exc)

    ctx = {
        'option_json':       option_json,
        'chart_id':          chart.pk,
        'drill_enabled':     '1',
        'drill_source':      drill_source,
        'drill_source_id':   drill_source_id,
        'drill_source_name': drill_source_name,
        'error':             error,
        'embed':             is_embed,
    }
    html = render_to_string('dataviz/partials/chart_preview.html', ctx, request=request)

    # Cache only successful renders (no error); render cache = 1 hour.
    # Recompute the key from the data we actually just fetched: the
    # lookup above used data_hash='raw' whenever the data cache was cold,
    # but by now `result` holds real rows — a later call whose data cache
    # *is* warm will compute the real data_hash on its lookup, so storing
    # under that same real-data key (not 'raw') is what makes it a HIT.
    if not error:
        cache.set(render_cache_key, html, timeout=3600)

    payload = {'html': html, 'error': error, 'cache_status': 'MISS', 'etag': etag}
    if got_lock:
        cache.set(done_key, {
            'html': html, 'error': error, 'etag': etag,
        }, timeout=_FLIGHT_LOCK_TTL)
        cache.delete(lock_key)
    return payload


@login_required
@require_GET
@timed_fn('chart_render_api')
def chart_render_api(request, chart_id):
    """
    Return a rendered chart partial for a saved chart_id.
    Runs the underlying query and builds the ECharts option.
    Used by page/dashboard view to lazy-load chart data.

    Caching: rendered HTML is cached in Redis keyed by
      chart_id + chart.updated_at + sorted pf_* filter params
    so that repeat views (same filters, any user) return in <10 ms.
    The cache automatically busts when the chart config is saved
    (updated_at changes) or when the 5-minute TTL expires.
    """
    # source/source_id/source_name are breadcrumb-only and do NOT affect
    # query results. But embed mode (source=page/dashboard) changes the
    # rendered HTML (no min-height on the chart container), so it must
    # be included in the render cache key.
    is_embed = request.GET.get('source') in ('page', 'dashboard')
    pf_filters = _parse_pf_filters(request.GET)

    chart = get_object_or_404(SavedChart, pk=chart_id)
    # Restrict the per-user home dashboard to charts the user is allowed to see;
    # all other render contexts (project-admin pages/dashboards, drill) are unaffected.
    if request.GET.get('source_id') == 'home' and not _can_render_chart(request.user, chart):
        return FORBIDDEN

    result = _render_saved_chart(
        chart_id, pf_filters,
        drill_source=request.GET.get('source', ''),
        drill_source_id=request.GET.get('source_id', ''),
        drill_source_name=request.GET.get('source_name', ''),
        is_embed=is_embed, request=request,
        if_none_match=request.headers.get('If-None-Match'),
        school_id=request.GET.get('school_id', ''),
    )
    _timing_log.info(
        'op=chart_render_api cache_status=%s chart_id=%s',
        result.get('cache_status') or ('not_modified' if result.get('not_modified') else 'unknown'),
        chart_id,
    )

    if result.get('not_modified'):
        resp = HttpResponse(status=304)
        resp['ETag'] = result['etag']
        resp['Cache-Control'] = 'private, no-cache, no-store, must-revalidate, max-age=0'
        return resp

    resp = HttpResponse(result['html'], content_type='text/html')
    resp['X-Chart-Cache'] = result['cache_status']
    resp['ETag'] = result['etag']
    resp['Cache-Control'] = 'private, no-cache, no-store, must-revalidate, max-age=0'
    return resp


@login_required
@require_POST
@timed_fn('chart_batch_render_api')
def chart_batch_render_api(request):
    """
    Render multiple saved charts concurrently in one request.

    POST JSON: {"chart_ids": [1, 2, 3], "source": "page", "source_id": "...",
                "source_name": "...", "filters": {"grade": ["K", "1"], ...}}
    ``filters`` uses bare column names (no 'pf_' prefix), since this is a
    JSON body rather than query-string params.

    Returns {"charts": {"<chart_id>": {"html": ..., "error": ...}, ...}}.

    Used by the page viewer to render the initial (above-the-fold) burst of
    charts in parallel via the shared ClickHouse-bound thread pool, instead
    of each chart firing its own sequential/connection-limited request.
    Individual charts still go through chart_render_api's normal lazy-load
    path afterward — this just warms/produces the same cache so those
    requests come back fast.
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    chart_ids = body.get('chart_ids') or []
    if not chart_ids:
        return JsonResponse({'charts': {}})

    source = body.get('source', '')
    source_id = body.get('source_id', '')
    source_name = body.get('source_name', '')
    is_embed = source in ('page', 'dashboard')
    pf_filters = body.get('filters') or {}
    school_id = body.get('school_id') or ''
    etags = body.get('etags') or {}

    # Non-admins may only batch-render their own home-dashboard charts
    permitted = set(chart_ids)
    if source_id == 'home' and not is_project_admin(request.user):
        from django.db.models import Q as _Q
        allowed = set(
            SavedChart.objects
            .filter(pk__in=chart_ids)
            .filter(
                _Q(is_user_menu=True) |
                _Q(dashboard_slots__dashboard__owner=request.user,
                   dashboard_slots__dashboard__is_home=True)
            )
            .distinct()
            .values_list('pk', flat=True)
        )
        permitted = allowed

    from clickhouse_utils.thread_pool import get_io_pool
    pool = get_io_pool()

    def _one(cid):
        if cid not in permitted:
            return cid, {'html': '', 'error': 'Not permitted'}
        try:
            if_none_match = etags.get(str(cid)) or (etags.get(int(cid)) if str(cid).isdigit() else None)
            res = _render_saved_chart(
                cid, pf_filters,
                drill_source=source, drill_source_id=source_id, drill_source_name=source_name,
                is_embed=is_embed, request=request,
                if_none_match=if_none_match,
                school_id=school_id,
            )
            return cid, {
                'html': res.get('html', ''),
                'error': res.get('error'),
                'cache_status': res.get('cache_status'),
                'etag': res.get('etag'),
                'not_modified': bool(res.get('not_modified')),
            }
        except Exception as exc:
            return cid, {'html': '', 'error': str(exc)}

    futures = [pool.submit(_one, cid) for cid in chart_ids]
    charts = {}
    for f in futures:
        cid, payload = f.result()
        charts[str(cid)] = payload

    return JsonResponse({'charts': charts})


@login_required
@require_POST
def chart_save_and_attach_api(request):
    """Save a chart AND attach it as a section to a target page in one call.

    Accepts JSON: the full chart save payload plus:
      - target_page_id: int — the DataVizPage to attach to
      - target_row_id: int (optional) — the DataVizPageRow to place the section in
      - section_gs_w: int (default 6), section_gs_h: int (default 4)
      - section_show_title: bool (default True)
    """
    if not is_project_admin(request.user):
        return FORBIDDEN

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    target_page_id = body.get('target_page_id')
    if not target_page_id:
        return JsonResponse({'error': 'target_page_id is required'}, status=400)

    from dataviz.models import SavedChart, DataVizPage, DataVizPageRow, DataVizPageSection

    try:
        page = DataVizPage.objects.get(pk=target_page_id)
    except DataVizPage.DoesNotExist:
        return JsonResponse({'error': 'Page not found'}, status=404)

    # ── Save the chart (reuse existing save logic) ──
    name = (body.get('name') or 'Untitled').strip()
    chart_id = body.get('id')
    chart_type = body.get('chart_type', 'bar')
    config = body.get('config', {})
    source_type = body.get('source_type', SavedChart.SOURCE_QUERY)
    source_id = body.get('source_id')
    source_sql = body.get('source_sql', '')
    source_filters = body.get('source_filters', {})
    description = body.get('description', '')
    drill_columns = body.get('drill_through_columns', [])
    drill_fm = body.get('drill_through_filter_map', {})
    school_filter_mode = body.get('school_filter_mode', 'enrolled')
    is_user_menu = bool(body.get('is_user_menu', False))
    user_menu_order = body.get('user_menu_order', 0) or 0

    if not name:
        return JsonResponse({'error': 'Chart name is required'}, status=400)

    if chart_id:
        chart = get_object_or_404(SavedChart, pk=chart_id)
    else:
        chart = SavedChart(owner=request.user)

    chart.name = name
    chart.description = description
    chart.chart_type = chart_type
    chart.config = config
    chart.source_type = source_type
    chart.source_id = source_id or None
    chart.source_sql = source_sql
    chart.source_filters = source_filters
    chart.school_filter_mode = school_filter_mode
    chart.drill_through_columns = drill_columns
    chart.drill_through_filter_map = drill_fm
    chart.is_user_menu = is_user_menu
    chart.user_menu_order = user_menu_order

    if source_type == 'columns':
        sel_cols = body.get('selected_columns', [])
        chart.config['selected_columns'] = sel_cols if isinstance(sel_cols, list) else []

    chart.save()

    # ── Attach chart to the target page ──
    target_row_id = body.get('target_row_id')
    if target_row_id:
        try:
            row = DataVizPageRow.objects.get(pk=target_row_id, page=page)
        except DataVizPageRow.DoesNotExist:
            row = None
    else:
        row = None

    # If no row specified or invalid, use the first row of the page, creating one if needed
    if not row:
        row = page.rows.order_by('position').first()
        if not row:
            row = DataVizPageRow.objects.create(
                page=page,
                label='',
                position=0,
            )

    # Determine position for the new section
    last_pos = DataVizPageSection.objects.filter(row=row).order_by('-position').first()
    new_position = (last_pos.position + 1) if last_pos else 0

    section = DataVizPageSection.objects.create(
        page=page,
        row=row,
        element_type='chart',
        chart=chart,
        position=new_position,
        show_title=body.get('section_show_title', True),
        gs_w=body.get('section_gs_w', 6),
        gs_h=body.get('section_gs_h', 4),
    )

    return JsonResponse({
        'chart': chart.to_dict(),
        'section': section.to_dict(),
        'page': page.to_dict(include_sections=False),
        'success': True,
    })


@login_required
@require_POST
def chart_recommend_api(request):
    """Recommend chart types based on selected column names.

    Accepts JSON: { columns: [...], column_definitions: {...} }
    Returns: { recommendations: [{ chart_type, label, icon, reason, score }] }
    """
    if not is_project_admin(request.user):
        return FORBIDDEN

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    columns = body.get('columns', [])
    column_defs = body.get('column_definitions', {})

    from dataviz.utils.chart_builder import recommend_chart_types
    recommendations = recommend_chart_types(columns, column_defs)

    return JsonResponse({'recommendations': recommendations, 'success': True})


@login_required
@require_POST
def chart_describe_api(request):
    """Generate a plain-English description of a chart configuration.

    Accepts JSON: { chart_config: {...}, column_definitions: {...} }
    Returns: { description: '...' }
    """
    if not is_project_admin(request.user):
        return FORBIDDEN

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    cfg = body.get('chart_config', {})
    column_defs = body.get('column_definitions', {})

    from dataviz.utils.chart_builder import auto_describe_chart
    description = auto_describe_chart(cfg, column_defs)

    return JsonResponse({'description': description, 'success': True})


@login_required
@require_GET
def chart_list_partial(request):
    """HTMX-friendly: renders a partial list of saved charts for the chart picker modal."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    q = request.GET.get('q', '').strip()
    charts = SavedChart.objects.all().order_by('name')
    if q:
        charts = charts.filter(name__icontains=q)
    else:
        charts = charts[:200]

    # Build a full tree structure
    root = {'folders': {}, 'charts': []}
    for chart in charts:
        parts = [p.strip() for p in chart.name.split('/')]
        curr = root
        for part in parts[:-1]:
            if part not in curr['folders']:
                curr['folders'][part] = {'folders': {}, 'charts': []}
            curr = curr['folders'][part]
        
        chart.display_name = parts[-1]
        curr['charts'].append(chart)

    return render(request, 'dataviz/partials/chart_picker_list.html', {
        'tree': root,
        'q': q,
    })
