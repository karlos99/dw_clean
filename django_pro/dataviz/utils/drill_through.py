"""
drill_through.py — Build drill-through filters from ECharts click params
and execute the resulting query for the drill-through data table.
"""
from .data_loader import run_saved_query, run_raw_sql, run_column_query


def build_drill_filter(chart, params: dict) -> dict:
    """
    Translate an ECharts click event params dict into dataexploration filter dict.

    ``params`` is the JS params object forwarded via query-string:
      x_val      — the category / x-axis value clicked
      series_val — the series name clicked (stack column value), if any
      drill_col  — (assessment_compare) the specific PL column for this bar

    ``chart.drill_through_filter_map`` defines the column-key mapping, e.g.:
      {'x_col': 'grade', 'series_col': 'race'}

    Returns a dict suitable for merging into a SavedQuery filter config.
    """
    fmap = chart.drill_through_filter_map or {}
    filters = {}

    # summary_table: row_val + col_val map directly to the two selected columns.
    if chart.chart_type == 'summary_table':
        row_col = chart.config.get('st_row_col', '')
        col_col = chart.config.get('st_col_col', '')
        row_val = params.get('st_row_val', '')
        col_val = params.get('st_col_val', '')
        if row_col and row_val:
            filters[row_col] = row_val
        if col_col and col_val:
            filters[col_col] = col_val
        return filters

    # progress_growth: series classification is derived (Growth/Sliders/Stickers),
    # not a real column value.  Apply the x-group filter and store the pg
    # category as a sentinel key for post-fetch Python filtering.
    if chart.chart_type == 'progress_growth':
        # Matrix mode: before_val + after_val pinpoint the exact cell transition.
        before_val = params.get('before_val', '')
        after_val  = params.get('after_val', '')
        if before_val and after_val:
            filters['pg_before_val'] = before_val
            filters['pg_after_val']  = after_val
            return filters

        x_col = fmap.get('x_col', chart.config.get('x_col', ''))
        series_val = params.get('series_val', '')
        x_val = params.get('x_val', '')
        _PG_CATS = frozenset({'Growth', 'Sliders', 'Stickers'})
        if series_val in _PG_CATS:
            # Stacked/grouped bar with x grouping — x_val is the group category
            if x_col and x_val:
                filters[x_col] = x_val
            filters['pg_cat'] = series_val
        elif x_val in _PG_CATS:
            # Donut/pie or overall bar — x_val IS the pg category
            filters['pg_cat'] = x_val
        return filters

    x_col = fmap.get('x_col', chart.config.get('x_col', ''))
    if x_col and params.get('x_val'):
        filters[x_col] = params['x_val']

    # assessment_compare: drill_col is the specific PL column (e.g.
    # test_2025_2026_DIBELS_Reading_BOY_PL).  The series_val is the PL value
    # ("Above Benchmark").  Filter that column directly — it already encodes
    # the test, subject, window, and year so no extra filters are needed.
    drill_col = params.get('drill_col', '')
    if drill_col and params.get('series_val'):
        filters[drill_col] = params['series_val']
    else:
        series_col = fmap.get('series_col', chart.config.get('stack_col', ''))
        if series_col and params.get('series_val'):
            filters[series_col] = params['series_val']

    return filters


def execute_drill_query(
    chart,
    drill_filters: dict,
    columns: list,
    user,
    page: int = 1,
    per_page: int = 50,
    sort_by: str = '',
    sort_dir: str = 'asc',
    column_filters: dict | None = None,
    extra_columns: list | None = None,
) -> dict:
    """
    Run the chart's underlying query with drill_filters merged in.
    Supports sorting, per-column filtering, pagination, and extra columns
    beyond the original query.

    Returns {'rows', 'columns', 'all_columns', 'total_rows', 'page',
             'per_page', 'error'}.
    """
    # progress_growth: extract the pg_cat sentinel before it leaks into SQL
    # filters, and ensure the before/after PL columns are fetched.
    pg_cat_filter = None
    if 'pg_cat' in drill_filters and chart.chart_type == 'progress_growth':
        drill_filters = dict(drill_filters)          # don't mutate caller's dict
        pg_cat_filter = drill_filters.pop('pg_cat')
        cfg = chart.config
        extra_columns = list(extra_columns or [])
        for fk in (cfg.get('pg_before_col', ''), cfg.get('pg_after_col', '')):
            if fk and fk not in extra_columns:
                extra_columns.append(fk)

    # Matrix mode: before_val + after_val = exact cell transition filter.
    pg_before_val = None
    pg_after_val  = None
    if 'pg_before_val' in drill_filters and chart.chart_type == 'progress_growth':
        drill_filters = dict(drill_filters)
        pg_before_val = drill_filters.pop('pg_before_val')
        pg_after_val  = drill_filters.pop('pg_after_val', '')
        cfg = chart.config
        extra_columns = list(extra_columns or [])
        for fk in (cfg.get('pg_before_col', ''), cfg.get('pg_after_col', '')):
            if fk and fk not in extra_columns:
                extra_columns.append(fk)

    if chart.source_type == 'sql':
        result = run_raw_sql(chart.source_sql)
        rows = result.get('rows', [])
        cols = result.get('columns', [])
        error = result.get('error')
    elif chart.source_type == 'columns':
        # Column Picker source: merge source_filters + drill_filters
        merged_filters = dict(chart.source_filters or {})
        merged_filters.update(drill_filters)
        selected_columns = chart.config.get('selected_columns', [])
        # Append extra columns for drill table
        if extra_columns:
            sel_set = set(selected_columns)
            for c in extra_columns:
                if c not in sel_set:
                    selected_columns.append(c)
                    sel_set.add(c)
        result = run_column_query(selected_columns, merged_filters)
        rows = result.get('rows', [])
        cols = result.get('columns', [])
        error = result.get('error')
    else:
        # Merge saved source_filters with the click-derived drill_filters
        merged_filters = dict(chart.source_filters or {})
        merged_filters.update(drill_filters)
        result = run_saved_query(
            chart.source_id, merged_filters, user,
            extra_columns=extra_columns,
        )
        rows = result.get('rows', [])
        cols = result.get('columns', [])
        error = result.get('error')

    # Post-fetch filtering for dynamic columns (test_* / mark_*).
    # The SQL data_loader skips these at the SQL level because the data
    # is fetched in a separate bulk phase.  We apply them here in Python.
    if rows and drill_filters:
        for fkey, fval in drill_filters.items():
            if not fkey.startswith(('test_', 'mark_')):
                continue
            if isinstance(fval, list):
                vals = set(fval)
                rows = [r for r in rows if r.get(fkey) in vals]
            else:
                rows = [r for r in rows if r.get(fkey) == fval]

    # summary_table: deduplicate by student id AFTER dynamic column filters so
    # the row count matches the chart (which deduplicates on the same filtered
    # set before doing the groupby count).
    if rows and chart.chart_type == 'summary_table':
        _id_col = next(
            (c for c in ('ssid', 'student_id', 'stu_annual_key') if c in rows[0]),
            None,
        )
        if _id_col:
            _seen: set = set()
            _deduped: list = []
            for _r in rows:
                _k = _r.get(_id_col)
                if _k not in _seen:
                    _seen.add(_k)
                    _deduped.append(_r)
            rows = _deduped

    # Post-fetch classification filter for progress_growth.
    # pg_cat_filter is one of 'Growth', 'Sliders', 'Stickers'.
    if pg_cat_filter and rows:
        # Deduplicate by student identifier before classification so the drill
        # list and count match the chart (which also deduplicates its input).
        _id_col = next(
            (c for c in ('ssid', 'student_id', 'stu_annual_key') if c in rows[0]),
            None,
        )
        if _id_col:
            _seen: set = set()
            _deduped: list = []
            for _r in rows:
                _k = _r.get(_id_col)
                if _k not in _seen:
                    _seen.add(_k)
                    _deduped.append(_r)
            rows = _deduped

        cfg = chart.config
        before_col = cfg.get('pg_before_col', '')
        after_col  = cfg.get('pg_after_col', '')
        pl_scale   = cfg.get('pl_scale', [])
        x_col      = cfg.get('x_col', '')
        if before_col and after_col:
            # Replicate build_option's _BLANK_VALUES filter for x_col.
            # The chart drops rows with blank x_col before counting; the drill
            # must do the same so totals match when no x_col SQL filter was added
            # (e.g. the user clicked a donut/pie slice with no group filter).
            _BLANK = frozenset({'', '-', '\u2014', '\u2013', 'N/A', 'n/a', 'None', 'null', 'NULL'})
            if x_col and x_col not in drill_filters:
                # x_col is not already being filtered by SQL — drop blank x_col rows
                rows = [r for r in rows if str(r.get(x_col) or '').strip() not in _BLANK]

            if not pl_scale:
                from dataviz.utils.chart_builder import _auto_detect_pl_scale
                all_vals: set = set()
                for r in rows:
                    for c in (before_col, after_col):
                        v = str(r.get(c) or '').strip()
                        if v and v not in _BLANK:
                            all_vals.add(v)
                pl_scale = _auto_detect_pl_scale(list(all_vals))
            rank = {v: i for i, v in enumerate(pl_scale)}

            def _pg_class(row):
                b = rank.get(str(row.get(before_col) or '').strip())
                a = rank.get(str(row.get(after_col) or '').strip())
                if b is None or a is None:
                    return None
                if a > b:
                    return 'Growth'
                if a < b:
                    return 'Sliders'
                return 'Stickers'

            rows = [r for r in rows if _pg_class(r) == pg_cat_filter]

    # Matrix drill: filter to the exact Before PL → After PL cell clicked.
    if pg_before_val and rows:
        _id_col = next(
            (c for c in ('ssid', 'student_id', 'stu_annual_key') if c in rows[0]),
            None,
        )
        if _id_col:
            _seen: set = set()
            _deduped: list = []
            for _r in rows:
                _k = _r.get(_id_col)
                if _k not in _seen:
                    _seen.add(_k)
                    _deduped.append(_r)
            rows = _deduped

        cfg = chart.config
        before_col = cfg.get('pg_before_col', '')
        after_col  = cfg.get('pg_after_col', '')
        if before_col and after_col:
            rows = [
                r for r in rows
                if str(r.get(before_col) or '').strip() == pg_before_val.strip()
                and str(r.get(after_col)  or '').strip() == (pg_after_val or '').strip()
            ]

    # Keep a snapshot of all available columns before column restriction
    all_columns = list(cols)

    # Per-column filters (from header filter dropdowns)
    if column_filters and rows:
        for fkey, fvals in column_filters.items():
            if not fvals:
                continue
            allowed = set(fvals) if isinstance(fvals, list) else {fvals}
            rows = [r for r in rows if str(r.get(fkey, '')) in allowed]

    # Sorting
    if sort_by and rows:
        reverse = sort_dir.lower() == 'desc'

        def _sort_key(row):
            v = row.get(sort_by, '')
            if v is None:
                v = ''
            # Attempt numeric sort
            try:
                return (0, float(v))
            except (ValueError, TypeError):
                return (1, str(v).lower())

        rows.sort(key=_sort_key, reverse=reverse)

    # Apply column selection if provided
    if columns and rows:
        # Always keep 'ssid' in row data for student summary links,
        # even if it's not a visible column.
        keep = set(columns) | {'ssid'}
        rows = [{k: r.get(k) for k in keep if k in r} for r in rows]
        cols = [c for c in columns if any(c in r for r in rows[:1])]

    total = len(rows)
    start = (page - 1) * per_page
    paged = rows[start:start + per_page]

    return {
        'rows': paged,
        'columns': cols,
        'all_columns': all_columns,
        'total_rows': total,
        'page': page,
        'per_page': per_page,
        'error': error,
    }


def get_column_distinct_values(chart, drill_filters: dict, column: str, user) -> list:
    """
    Return the distinct non-empty values for *column* from the drill query
    result set.  Used to populate the header filter dropdowns.
    """
    result = execute_drill_query(
        chart, drill_filters, columns=[], user=user,
        page=1, per_page=999_999,
    )
    rows = result.get('rows', [])
    seen = set()
    out = []
    for r in rows:
        v = str(r.get(column, ''))
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    out.sort(key=str.lower)
    return out


def get_all_available_columns(chart, user) -> list:
    """
    Return the full universe of column keys that can be added to the drill
    table — the base COLUMN_DEFINITIONS keys plus any dynamic test_*/mark_*
    columns from the saved query config.
    """
    from dataexploration.constants import COLUMN_DEFINITIONS

    # Base static columns
    if isinstance(COLUMN_DEFINITIONS, dict):
        base = list(COLUMN_DEFINITIONS.keys())
    else:
        base = [k for k, _v in COLUMN_DEFINITIONS]

    # Dynamic columns from the saved query's config
    dynamic = []
    if chart.source_type != 'sql' and chart.source_id:
        from dataexploration.models import SavedQuery
        try:
            sq = SavedQuery.objects.get(pk=chart.source_id)
            for c in (sq.config or {}).get('columns', []):
                if c.startswith(('test_', 'mark_')) and c not in base:
                    dynamic.append(c)
        except SavedQuery.DoesNotExist:
            pass

    return base + dynamic
