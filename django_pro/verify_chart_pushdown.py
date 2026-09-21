"""Live equivalence check: ClickHouse chart aggregation vs the Python builder.

Runs the real ``_clickhouse_aggregate_dataset`` pushdown and the real
``build_option`` Python aggregation over the SAME live ClickHouse population,
then requires an identical ECharts option.

This is the verification harness for the aggregation pushdown: it exercises the
actual generated SQL against real data (not a mocked executor), which is the
only way to prove the SQL predicates and GROUP BY match chart semantics.

Usage (inside the Django container or any host with the DB settings):

    python verify_chart_pushdown.py

Exits non-zero on the first mismatch. Read-only: it never writes to ClickHouse
or PostgreSQL.
"""
import json
import os
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from types import SimpleNamespace  # noqa: E402

from dataviz.utils.chart_builder import build_option  # noqa: E402
from dataviz.utils.compiler import (  # noqa: E402
    build_dataset_col_index, compile_dataset_to_sql, execute_compiled_query_aliased,
)
from dataviz.views.api.chart_api import _clickhouse_aggregate_dataset  # noqa: E402

COLUMNS = [
    'current_student.grade',
    'current_student.school_state_id',
    'current_student.gender',
    'current_student.school_year',
    'current_student.atendace_rate',
    # Summary tables count each student once per cell, so the reference rows
    # have to carry the same dedupe key the pushdown aggregates over.
    'current_student.ssid',
]

DATASET = SimpleNamespace(
    pk=0,
    base_table='current_student',
    distinct=True,
    joins=[],
    filters=[],
    columns=COLUMNS,
    column_mappings={},
    updated_at=None,
    source_type='visual',
)

# chart_type, cfg, needs_value_column
CASES = [
    ('bar count by grade', {
        'chart_type': 'bar', 'x_col': 'current_student.grade',
        'x_cols': ['current_student.grade'], 'agg': 'count',
    }),
    ('pie count by grade', {
        'chart_type': 'pie', 'x_col': 'current_student.grade',
        'x_cols': ['current_student.grade'], 'agg': 'count',
    }),
    ('grouped+stacked bar: grade x year group x gender stack', {
        'chart_type': 'grouped_bar', 'x_col': 'current_student.grade',
        'x_cols': ['current_student.grade'],
        'group_col': 'current_student.school_year',
        'stack_col': 'current_student.gender', 'agg': 'count',
    }),
    ('stacked bar: grade x gender', {
        'chart_type': 'stacked_bar', 'x_col': 'current_student.grade',
        'x_cols': ['current_student.grade'],
        'stack_col': 'current_student.gender', 'agg': 'count',
    }),
    ('stacked percent: grade x gender', {
        'chart_type': 'stacked_bar', 'x_col': 'current_student.grade',
        'x_cols': ['current_student.grade'],
        'stack_col': 'current_student.gender', 'agg': 'count',
        'stacking_mode': 'percent',
    }),
    ('composite axis: grade x school_year', {
        'chart_type': 'bar', 'x_col': '',
        'x_cols': ['current_student.grade', 'current_student.school_year'],
        'agg': 'count',
    }),
    ('avg measure by grade', {
        'chart_type': 'bar', 'x_col': 'current_student.grade',
        'x_cols': ['current_student.grade'],
        'y_col': 'current_student.atendace_rate', 'agg': 'avg',
    }),
    ('sum measure by grade x gender', {
        'chart_type': 'stacked_bar', 'x_col': 'current_student.grade',
        'x_cols': ['current_student.grade'],
        'stack_col': 'current_student.gender',
        'y_col': 'current_student.atendace_rate', 'agg': 'sum',
    }),
    ('count_unique gender by grade', {
        'chart_type': 'bar', 'x_col': 'current_student.grade',
        'x_cols': ['current_student.grade'],
        'y_col': 'current_student.gender', 'agg': 'count_unique',
    }),
    # ── Pivot tables: ClickHouse returns pre-aggregated cells ──────────────
    ('summary table values: grade rows x gender cols (count)', {
        'chart_type': 'summary_table',
        'st_row_col': 'current_student.grade',
        'st_col_col': 'current_student.gender',
        'st_col_mode': 'values',
        'st_agg_func': 'count',
        'st_value_col': '',
    }),
    ('summary table values: grade rows x gender cols (avg attendance)', {
        'chart_type': 'summary_table',
        'st_row_col': 'current_student.grade',
        'st_col_col': 'current_student.gender',
        'st_col_mode': 'values',
        'st_agg_func': 'avg',
        'st_value_col': 'current_student.atendace_rate',
    }),
    ('dynamic summary pivot: grade x gender (count)', {
        'chart_type': 'dynamic_summary_table',
        'dyn_col_mode': 'pivot',
        'dyn_row_cols': ['current_student.grade'],
        'dyn_col_cols': ['current_student.gender'],
        'dyn_agg_func': 'count',
    }),
    ('dynamic summary pivot: grade+year x gender (count)', {
        'chart_type': 'dynamic_summary_table',
        'dyn_col_mode': 'pivot',
        'dyn_row_cols': ['current_student.grade', 'current_student.school_year'],
        'dyn_col_cols': ['current_student.gender'],
        'dyn_agg_func': 'count',
    }),
    ('dynamic summary pivot: grade x gender (avg attendance)', {
        'chart_type': 'dynamic_summary_table',
        'dyn_col_mode': 'pivot',
        'dyn_row_cols': ['current_student.grade'],
        'dyn_col_cols': ['current_student.gender'],
        'dyn_agg_func': 'avg',
        'dyn_value_col': 'current_student.atendace_rate',
    }),
    # ── Calculations mode: one conditional aggregate per base column ──────
    ('dynamic calc: total by grade', {
        'chart_type': 'dynamic_summary_table',
        'dyn_col_mode': 'calculations',
        'dyn_row_cols': ['current_student.grade'],
        'dyn_col_cols': [],
        'dyn_agg_func': 'count',
        'dyn_calculations': [
            {'id': 'c0', 'calc_type': 'base', 'agg_func': 'count'},
        ],
    }),
    ('dynamic calc: threshold + derived percent by grade', {
        'chart_type': 'dynamic_summary_table',
        'dyn_col_mode': 'calculations',
        'dyn_row_cols': ['current_student.grade'],
        'dyn_col_cols': [],
        'dyn_agg_func': 'count',
        'dyn_show_tooltips': True,
        'dyn_calculations': [
            {'id': 'c0', 'calc_type': 'base', 'agg_func': 'count'},
            {'id': 'c1', 'calc_type': 'base', 'agg_func': 'count',
             'filters': [{'col': 'current_student.atendace_rate', 'op': 'lt',
                          'val': '90'}]},
            {'id': 'c2', 'calc_type': 'derived', 'op': 'divide',
             'ref_a': 'c1', 'ref_b': 'c0', 'as_percent': True},
        ],
    }),
    ('dynamic calc: equality threshold by grade x year', {
        'chart_type': 'dynamic_summary_table',
        'dyn_col_mode': 'calculations',
        'dyn_row_cols': ['current_student.grade', 'current_student.school_year'],
        'dyn_col_cols': [],
        'dyn_agg_func': 'count',
        'dyn_calculations': [
            {'id': 'c0', 'calc_type': 'base', 'agg_func': 'count'},
            {'id': 'c1', 'calc_type': 'base', 'agg_func': 'count',
             'filters': [{'col': 'current_student.gender', 'op': 'eq', 'val': 'F'}]},
        ],
    }),
    ('dynamic calc: OR-combined thresholds by grade', {
        'chart_type': 'dynamic_summary_table',
        'dyn_col_mode': 'calculations',
        'dyn_row_cols': ['current_student.grade'],
        'dyn_col_cols': [],
        'dyn_agg_func': 'count',
        'dyn_calculations': [
            {'id': 'c0', 'calc_type': 'base', 'agg_func': 'count'},
            {'id': 'c1', 'calc_type': 'base', 'agg_func': 'count',
             'filters': [{'col': 'current_student.gender', 'op': 'eq', 'val': 'F'},
                         {'col': 'current_student.gender', 'op': 'eq', 'val': 'M',
                          'logic': 'or'}]},
            {'id': 'c2', 'calc_type': 'base', 'agg_func': 'count',
             'filters': [{'col': 'current_student.gender', 'op': 'in',
                          'val': ['F', 'M']}]},
        ],
    }),
]


def reference_rows():
    """Every matching row, exactly as the raw-row chart path would fetch them.

    Executed through the analytics executor so BOTH sides read the same
    ClickHouse snapshot — comparing against a PostgreSQL fallback would only
    measure data-source drift, not aggregation equivalence.
    """
    from clickhouse_utils.executor import QueryExecutor

    sql, params = compile_dataset_to_sql(
        DATASET, user=None, interactive_filters={'status': 'Active'}, limit=None,
        status_filter='Active',
    )
    result = QueryExecutor(analytics=True).execute(sql, params)
    if result.get('error'):
        raise SystemExit(f'reference query failed: {result["error"]}')
    return result['rows']


# Chart-option keys that are order-insensitive hints rather than rendered
# output: they feed the drill-through alias picker, which groups the values.
_ORDER_FREE_KEYS = frozenset({
    '__x_alias_keys__',
    '__series_names__',
})


def _round9(value):
    """Round a float to 9 significant digits (clickhouse-vs-polars-safe)."""
    if isinstance(value, bool) or not isinstance(value, float):
        return value
    if value == 0 or value != value:
        return value
    from math import floor, log10
    digits = 9 - int(floor(log10(abs(value)))) - 1
    return round(value, digits)


def _round_numbers(obj):
    if isinstance(obj, dict):
        return {k: _round_numbers(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_numbers(v) for v in obj]
    return _round9(obj)


def _normalize_formatter_js(value):
    """Round numeric literals inside an option's embedded tooltip JS.

    Tooltip formatters are generated JavaScript that embeds the same values, so
    the last floating-point digit can differ between the two aggregation
    algorithms. Only the numeric literals are normalized; the surrounding code
    must still match exactly.
    """
    import re

    def _replace(match):
        try:
            payload = json.loads(match.group(2))
        except (ValueError, TypeError):
            return match.group(0)
        return f'var {match.group(1)} = {json.dumps(_round_numbers(payload), sort_keys=True)};'

    return re.sub(r'var (rawLookup|totals) = (.*?);', _replace, value, flags=re.S)


def _normalize(option):
    """Copy *option* with order-free hint lists sorted and float noise trimmed."""
    out = {}
    for key, value in option.items():
        if key in _ORDER_FREE_KEYS and isinstance(value, list):
            out[key] = sorted(str(v) for v in value)
        elif isinstance(value, dict):
            out[key] = _normalize(value)
        elif isinstance(value, str) and 'function' in value and 'rawLookup' in value:
            out[key] = _normalize_formatter_js(value)
        else:
            out[key] = value
    return out


def _diff(ref, push, path='$', out=None):
    """Collect differences, allowing insignificant float drift only.

    Averages are computed by different algorithms (ClickHouse running-mean vs
    Polars sum/divide), so results can differ in the last floating-point digit.
    Everything else — keys, structure, strings, category order, integer counts —
    must match exactly.
    """
    if out is None:
        out = []
    if isinstance(ref, bool) or isinstance(push, bool):
        if ref != push:
            out.append(f'{path}: {ref!r} != {push!r}')
        return out
    if isinstance(ref, (int, float)) and isinstance(push, (int, float)):
        if ref == push:
            return out
        scale = max(abs(ref), abs(push), 1.0)
        if abs(ref - push) / scale > 1e-9:
            out.append(f'{path}: {ref!r} != {push!r}')
        return out
    if isinstance(ref, dict) and isinstance(push, dict):
        for key in sorted(set(ref) | set(push)):
            if key not in ref:
                out.append(f'{path}.{key}: missing from reference')
            elif key not in push:
                out.append(f'{path}.{key}: missing from pushdown')
            else:
                _diff(ref[key], push[key], f'{path}.{key}', out)
        return out
    if isinstance(ref, list) and isinstance(push, list):
        if len(ref) != len(push):
            out.append(f'{path}: length {len(ref)} != {len(push)}')
            return out
        for i, (a, b) in enumerate(zip(ref, push)):
            _diff(a, b, f'{path}[{i}]', out)
        return out
    if ref != push:
        out.append(f'{path}: {ref!r} != {push!r}')
    return out


def _expand(cfg):
    """Rewrite a config written in clean aliases to full ``table.column`` keys.

    The dataset used here has empty ``column_mappings``, so the raw-row query
    returns full ``table.column`` keys. Expanding the config keeps the reference
    chart addressing the same columns the pushdown path does.
    """
    alias_to_full = {c.split('.')[-1]: c for c in COLUMNS}
    out = dict(cfg)
    for key in ('x_col', 'y_col', 'stack_col', 'series_col', 'group_col'):
        value = out.get(key)
        if isinstance(value, str) and value and value in alias_to_full:
            out[key] = alias_to_full[value]
    for key in ('x_cols', 'y_cols'):
        value = out.get(key)
        if isinstance(value, list):
            out[key] = [alias_to_full.get(v, v) if isinstance(v, str) else v for v in value]
    return out


def _shrink_rows(rows, cfg):
    """Reference rows limited to the columns *cfg* actually addresses.

    The compiled pushdown SELECTs only the dimension and measure columns, so the
    reference projection has to match or row-level hints such as
    ``__x_alias_keys__`` legitimately differ.
    """
    wanted = set()
    for key in ('x_col', 'y_col', 'stack_col', 'series_col', 'group_col',
                'st_row_col', 'st_col_col', 'st_value_col',
                'dyn_value_col'):
        value = cfg.get(key)
        if isinstance(value, str) and value:
            wanted.add(value)
    for key in ('x_cols', 'y_cols', 'st_row_cols', 'st_col_cols',
                'dyn_row_cols', 'dyn_col_cols'):
        value = cfg.get(key)
        if isinstance(value, list):
            wanted.update(v for v in value if isinstance(v, str) and v)
    if cfg.get('chart_type') in ('summary_table', 'dynamic_summary_table'):
        # The builders count distinct students per cell, so the reference rows
        # need the dedupe key and the SQL result carries it too.
        wanted.add('current_student.ssid')
    # Calculation columns read their own value and threshold-filter columns.
    for calc in (cfg.get('dyn_calculations') or []):
        if not isinstance(calc, dict):
            continue
        if isinstance(calc.get('value_col'), str) and calc['value_col']:
            wanted.add(calc['value_col'])
        raw_filters = calc.get('filters')
        if not isinstance(raw_filters, list):
            raw_filters = [calc['filter']] if calc.get('filter') else []
        for f in raw_filters:
            if isinstance(f, dict) and isinstance(f.get('col'), str) and f['col']:
                wanted.add(f['col'])
    return [{k: v for k, v in row.items() if k in wanted} for row in rows]


def _push_down(cfg):
    """Dispatch to the chart or table-cell pushdown for this config."""
    from dataviz.views.api.chart_api import (
        _clickhouse_aggregate_dataset, _clickhouse_aggregate_table_cells,
        _is_pivot_summary_chart,
    )

    if _is_pivot_summary_chart(cfg):
        return _clickhouse_aggregate_table_cells(
            DATASET, dict(cfg), {'status': 'Active'}, {},
        )
    return _clickhouse_aggregate_dataset(
        DATASET, dict(cfg), {'status': 'Active'}, {},
    )


def main():
    rows = reference_rows()
    print(f'reference population: {len(rows)} rows\n')

    failures = []
    for name, raw_cfg in CASES:
        cfg = _expand(raw_cfg)
        reference_option = _normalize(
            build_option(_shrink_rows(rows, cfg), {}, dict(cfg))
        )

        pushdown = _push_down(cfg)
        if pushdown is None:
            print(f'FAIL  {name}: pushdown declined')
            failures.append(name)
            continue
        compact_result, compact_cfg = pushdown
        pushdown_option = _normalize(build_option(compact_result['rows'], {}, dict(compact_cfg)))

        diffs = _diff(reference_option, pushdown_option)
        status = 'OK   ' if not diffs else 'FAIL '
        print(
            f'{status} {name}: raw={len(rows)} rows -> compact={len(compact_result["rows"])} rows'
        )
        if diffs:
            failures.append(name)
            for line in diffs[:8]:
                print(f'        {line}')
            if len(diffs) > 8:
                print(f'        ... {len(diffs) - 8} more')

    if failures:
        print(f'\n{len(failures)} case(s) diverged: {failures}')
        return 1
    print('\nAll cases identical.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
