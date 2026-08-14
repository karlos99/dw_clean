"""Shared assessment column enrichment for charts and drill-through."""

_CONFIG_COL_KEYS = frozenset({
    'x_col', 'y_col', 'stack_col', 'series_col', 'group_col',
    'pg_before_col', 'pg_after_col', 'st_row_col', 'st_col_col',
    'st_row_cols', 'st_col_cols', 'st_value_col',
    'dyn_row_cols', 'dyn_col_cols', 'dyn_value_col',
    'sort_col', 'compare_pl_cols',
})


def _norm_col(c: str) -> str:
    return str(c).replace('-', '_').lower()


def get_missing_assessment_cols(cfg: dict, col_mappings: dict, aliased_rows: list) -> list:
    cols = list(col_mappings.values())
    cfg_cols = []
    for k, v in cfg.items():
        if k in _CONFIG_COL_KEYS:
            if isinstance(v, str):
                cfg_cols.extend([p.strip() for p in v.split(',') if p.strip()])
            elif isinstance(v, list):
                cfg_cols.extend([str(item).strip() for item in v if isinstance(item, str)])

    from dataexploration.school_year_utils import resolve_relative_column_key
    resolved_cfg_cols = [resolve_relative_column_key(c) for c in cfg_cols]
    cols.extend(resolved_cfg_cols)

    seen = set()
    missing = []
    blank_vals = {'', '\u2014', '-', 'none', 'null', 'nan', 'n/a'}

    def _col_has_data(c_norm: str) -> bool:
        for r in aliased_rows:
            for k, v in r.items():
                if _norm_col(k) == c_norm:
                    if v is not None and str(v).strip().lower() not in blank_vals:
                        return True
        return False

    existing_cols = set()
    if aliased_rows:
        existing_cols = {_norm_col(c) for c in aliased_rows[0].keys()}

    for c in cols:
        if not c:
            continue
        c_norm = _norm_col(c)
        if c_norm in seen:
            continue
        seen.add(c_norm)
        if not (c.startswith('test_') or c.startswith('mark_')):
            continue
        if c_norm not in existing_cols or not _col_has_data(c_norm):
            missing.append(c)
    return missing


def augment_rows_with_assessment_cols(rows: list, needed_keys: list, dataset, source_filters=None) -> list:
    """Bulk-fetch missing test/mark columns onto existing rows."""
    if not rows or not needed_keys:
        return rows
    from dataviz.utils.data_loader import enrich_drill_rows, _parse_year
    from dataexploration.school_year_utils import resolve_relative_column_key

    explorer_filters: dict = {}
    if source_filters and 'status' in source_filters:
        explorer_filters['status'] = source_filters['status']

    needed_years = []
    for k in needed_keys:
        res_k = resolve_relative_column_key(k)
        prefix = 'test_' if str(k).startswith('test_') else 'mark_' if str(k).startswith('mark_') else ''
        yr = _parse_year(res_k, prefix) if prefix else None
        if yr:
            needed_years.append(yr)

    raw_years = []
    for r in rows:
        sy = r.get('school_year')
        if not sy:
            for k, v in r.items():
                if str(k).endswith('school_year') and v:
                    sy = v
                    break
        if sy:
            raw_years.append(str(sy).strip())
    all_years = list({y for y in raw_years + needed_years if y})
    if all_years:
        explorer_filters['school_year'] = all_years

    enriched, _, error = enrich_drill_rows(
        rows, needed_keys, explorer_filters, user=None,
    )
    if error:
        return rows
    return enriched


def augment_chart_result_rows(chart_type: str, chart_cfg: dict, col_mappings: dict, result: dict) -> dict:
    """Ensure test/mark PL columns are present on chart rows."""
    if result.get('error') or not result.get('rows'):
        return result
    missing = get_missing_assessment_cols(chart_cfg, col_mappings or {}, result['rows'])
    if missing:
        result = dict(result)
        result['rows'] = augment_rows_with_assessment_cols(result['rows'], missing, None)
    return result
