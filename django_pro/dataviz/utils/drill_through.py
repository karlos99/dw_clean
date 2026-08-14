"""
drill_through.py — Build drill-through filters from ECharts click params
and execute the resulting query for the drill-through data table.
"""
import hashlib
import json

from django.core.cache import cache

from .data_loader import run_saved_query, run_raw_sql, run_column_query, enrich_drill_rows


DRILL_CACHE_TTL = 300


def _drill_user_part(user) -> str:
    if not user:
        return ''
    user_role = getattr(user, 'active_role', None)
    role_name = user_role.name if user_role else 'None'
    active_school = getattr(user, 'active_school', None)
    school_id = active_school.state_id if active_school else 'All'
    return f":u{user.pk}:r{role_name}:s{school_id}"


def _drill_fetch_hash(chart, drill_filters: dict, extra_columns: list | None) -> str:
    payload = {
        'f': drill_filters,
        'sf': chart.source_filters or {},
        'sid': chart.source_id,
        'ec': sorted(extra_columns) if extra_columns else None,
        'one_per_sak': bool((chart.config or {}).get('one_row_per_stu_annual_key')),
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _drill_post_cfg_sig(chart) -> str:
    cfg = chart.config or {}
    relevant = {}
    for k, v in cfg.items():
        if k.startswith(('pg_', 'ds_', 'dyn_', 'st_')) or k in ('agg', 'one_row_per_stu_annual_key'):
            relevant[k] = v
    return hashlib.md5(json.dumps(relevant, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _drill_post_hash(chart, fetch_hash: str) -> str:
    payload = {
        'fetch_hash': fetch_hash,
        'ct': chart.chart_type,
        'cfg_sig': _drill_post_cfg_sig(chart),
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _drill_needs_post_filter_cache(chart, drill_filters: dict) -> bool:
    if chart.chart_type in ('progress_growth', 'date_series', 'dynamic_summary_table', 'summary_table'):
        return True
    if (chart.config or {}).get('one_row_per_stu_annual_key'):
        return True
    if chart.chart_type in ('bar', 'grouped_bar', 'stacked_bar') and (chart.config or {}).get('agg', 'count') == 'count':
        return True
    for fkey in drill_filters:
        bare = fkey.split('.')[-1]
        if bare.startswith(('test_', 'mark_')):
            return True
    return False


def _can_sql_paginate_drill(chart, drill_filters: dict, sort_by: str) -> bool:
    if _drill_needs_post_filter_cache(chart, drill_filters):
        return False
    if chart.source_type not in ('query', 'columns'):
        return False
    if sort_by:
        bare = sort_by.split('.')[-1]
        if bare.startswith(('test_', 'mark_')):
            return False
    return True


def split_composite_value(val: str, expected_len: int) -> list:
    """
    Split a composite category/series value by common separators.
    First normalizes whitespace (like non-breaking spaces).
    Returns list of parts, stripped. If splitting doesn't yield the
    expected number of parts, returns a list containing just the original val.
    """
    if not val:
        return []
    
    # Normalize non-breaking spaces and other weird whitespace
    normalized = val.replace('\xa0', ' ').replace('\u200b', '').strip()
    
    # Try different separators in order of preference.
    separators = [' · ', ' • ', ' — ', ' - ', ' | ', '·', '•', '—', '-', '|']
    for sep in separators:
        if sep in normalized:
            parts = [p.strip() for p in normalized.split(sep)]
            if len(parts) == expected_len:
                return parts
            # Try filtering out empty parts
            filtered = [p for p in parts if p]
            if len(filtered) == expected_len:
                return filtered
                
    return [normalized]


def _find_id_col(row_dict):
    """Find student identifier column in row_dict, prioritizing ssid, then student_id, then stu_annual_key."""
    if not row_dict:
        return None
    for target in ('ssid', 'student_id', 'stu_annual_key'):
        match = next((k for k in row_dict if k == target or k.split('.')[-1].lower() == target), None)
        if match:
            return match
    return None


def _deduplicate_count_drill_rows(rows, chart):
    """Keep one drill row per student for row-count bar charts.

    Drill queries add identifier/extra columns to the chart query. For joined
    datasets those columns can make otherwise identical rows distinct, even
    though the chart count was calculated from one row per student. Numeric
    and occurrence aggregations intentionally retain their source rows.

    When the chart uses ``one_row_per_stu_annual_key``, always collapse on
    ``stu_annual_key`` so drill matches the dataset chart row set.
    """
    if not rows:
        return rows

    if (chart.config or {}).get('one_row_per_stu_annual_key'):
        from dataviz.utils.compiler import dedupe_rows_by_stu_annual_key
        return dedupe_rows_by_stu_annual_key(rows)

    if chart.chart_type not in ('bar', 'grouped_bar', 'stacked_bar'):
        return rows
    if (chart.config or {}).get('agg', 'count') != 'count':
        return rows

    id_col = _find_id_col(rows[0])
    if not id_col:
        return rows

    seen = set()
    deduped = []
    for row in rows:
        key = row.get(id_col)
        if key is None or not str(key).strip():
            deduped.append(row)
            continue
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    return deduped


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
        row_val = params.get('st_row_val', '') or params.get('x_val', '')
        col_val = params.get('st_col_val', '') or params.get('series_val', '')
        if row_col and row_val:
            filters[row_col] = row_val
        if col_col and col_val:
            if chart.config.get('st_col_mode') == 'columns':
                st_col_cols = chart.config.get('st_col_cols', [])
                col_defs = chart.column_defs or {}
                matched_col = None
                for cc in st_col_cols:
                    lbl = col_defs.get(cc, cc) if col_defs else cc
                    if cc == col_val or lbl == col_val:
                        matched_col = cc
                        break
                if matched_col:
                    filters['dyn_calc_value_col'] = matched_col
            else:
                filters[col_col] = col_val
        return filters

    # dynamic_summary_table: multi-dimension drill — each row/col dimension
    # is a composite key separated by ▸.  Split and map to individual columns.
    # Multi-column mode: st_col_val is "col_name ▸ value" per clicked cell group.
    if chart.chart_type == 'dynamic_summary_table':
        _SEP = ' \u25b8 '
        row_cols = chart.config.get('dyn_row_cols')
        if row_cols is None:
            row_cols = chart.config.get('st_row_cols', [])
        col_cols = chart.config.get('dyn_col_cols')
        if col_cols is None:
            col_cols = chart.config.get('st_col_cols', [])
        row_val = params.get('st_row_val', '') or params.get('x_val', '')
        col_val = params.get('st_col_val', '') or params.get('series_val', '')

        # Calculations mode: the clicked column is a user-defined aggregate
        # (e.g. "Average of Reading Score"), not a real data column, so there
        # is no column value to filter by.  Map the cell's label back to its
        # calculation definition and, for non-count aggregates, restrict the
        # drill to students who actually contributed a value — otherwise the
        # drill list shows every student in the row group instead of just
        # the ones the calculation was computed over.
        if chart.config.get('dyn_col_mode') == 'calculations' and col_val:
            col_defs = chart.column_defs or {}
            for calc in chart.config.get('dyn_calculations', []):
                fn = calc.get('agg_func', 'count')
                vc = calc.get('value_col', '')
                lbl = (calc.get('label') or '').strip()
                if not lbl:
                    vc_lbl = col_defs.get(vc, vc) if vc else vc
                    lbl = f"{fn.capitalize()} of {vc_lbl}" if vc else fn.capitalize()
                if lbl == col_val:
                    if vc:
                        filters['dyn_calc_value_col'] = vc
                    break
            if row_val and row_cols:
                parts = row_val.split(_SEP)
                for i, col in enumerate(row_cols):
                    if i < len(parts):
                        filters[col] = parts[i]
            return filters

        # Multi-column mode: st_col_val encodes "col_name ▸ cell_value"
        if col_val and _SEP in col_val and len(col_cols) > 1:
            parts = col_val.split(_SEP, 1)
            if len(parts) == 2 and parts[0] in col_cols:
                filters[parts[0]] = parts[1]
        elif col_val and col_cols:
            # Standard mode: st_col_val is the cell value or ▸-separated composite
            parts = col_val.split(_SEP)
            for i, col in enumerate(col_cols):
                if i < len(parts):
                    filters[col] = parts[i]
        if row_val and row_cols:
            parts = row_val.split(_SEP)
            for i, col in enumerate(row_cols):
                if i < len(parts):
                    filters[col] = parts[i]
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

        pg_display = chart.config.get('pg_display', '')
        # x_col only groups rows in the bar display modes — donut/pie
        # classify across the whole dataset, so an x_col left over from a
        # previous edit must not be applied as a filter here.
        x_col = fmap.get('x_col', chart.config.get('x_col', '')) if pg_display in ('stacked_bar', 'grouped_bar') else ''
        series_val = params.get('series_val', '')
        x_val = params.get('x_val', '')
        _PG_CATS = frozenset({'Growth', 'Sliders', 'Stickers'})
        if series_val in _PG_CATS:
            # Stacked/grouped bar with x grouping — x_val is the group category.
            # "(Blank)" is a synthetic bucket the chart uses for rows with no
            # x_col value — it is not a literal database value, so sending it
            # as a SQL equality filter would match zero rows.  Leave x_col
            # unfiltered and let execute_drill_query's post-fetch pg_cat
            # classifier select the blank-x_col rows instead.
            if x_col and x_val and x_val != '(Blank)':
                filters[x_col] = x_val
            elif x_col and x_val == '(Blank)':
                filters['pg_x_blank'] = x_col
            filters['pg_cat'] = series_val
        elif x_val in _PG_CATS:
            # Donut/pie or overall bar — x_val IS the pg category
            filters['pg_cat'] = x_val
        return filters

    # date_series (Dates chart): x_val is a bucketed date label (e.g. a whole
    # week/month), not a literal value in the date column, so it can't be a
    # SQL equality filter — store it as a sentinel for post-fetch bucketing.
    # series_val is the clicked line's label; carry over that line's own
    # value column / filters so the drill matches exactly what the line
    # counted. Calc lines (derived by +/- of two other lines) have no rows
    # of their own, so only the date sentinel is set for those.
    if chart.chart_type == 'date_series':
        x_val = params.get('x_val', '')
        series_val = params.get('series_val', '')
        if x_val:
            filters['ds_date_val'] = x_val
        ds_lines = chart.config.get('ds_lines', [])
        line = next((l for l in ds_lines if (l.get('label') or l.get('id')) == series_val), None)
        line_type = line.get('line_type', 'base') if line else None
        if line and line_type == 'active_range':
            # No single column to require non-blank — the drill instead needs
            # to re-run the enter/leave overlap test against the bucket's
            # date range, done post-fetch in execute_drill_query.
            filters['ds_active_line_id'] = line.get('id') or line.get('label')
        elif line and line_type != 'calc':
            value_col = line.get('value_col', '')
            if value_col:
                filters['ds_value_col_required'] = value_col
            line_filters = line.get('filters') or {}
            if isinstance(line_filters, dict):
                for fcol, fval in line_filters.items():
                    if fcol:
                        filters[fcol] = fval
        return filters

    # ── Handle multiple X-axis composite columns ──
    x_col_raw = fmap.get('x_col') or chart.config.get('x_col') or chart.config.get('x_cols') or ''
    x_cols_list = []
    if isinstance(x_col_raw, str) and x_col_raw:
        x_cols_list = [c.strip() for c in x_col_raw.split(',') if c.strip()]
    elif isinstance(x_col_raw, list):
        for item in x_col_raw:
            if isinstance(item, str):
                x_cols_list.extend([c.strip() for c in item.split(',') if c.strip()])
            elif item:
                x_cols_list.append(str(item).strip())

    x_val = params.get('x_val', '')
    if x_val:
        if len(x_cols_list) > 1:
            parts = split_composite_value(x_val, len(x_cols_list))
            if len(parts) == len(x_cols_list):
                for col_name, part_val in zip(x_cols_list, parts):
                    if col_name:
                        filters[col_name] = part_val
            else:
                # ECharts did not concatenate or splitting didn't match (single value clicked)
                # Apply our heuristic to map x_val and series_val to the two columns
                if len(x_cols_list) == 2:
                    # If there is a stack/series column configured on the chart,
                    # then series_val belongs to that column, not the X-axis.
                    has_stack = bool(fmap.get('series_col') or chart.config.get('stack_col') or chart.config.get('series_col'))
                    val_a = '' if has_stack else params.get('series_val', '')
                    val_b = x_val
                    col_a, col_b = x_cols_list[0], x_cols_list[1]
                    
                    if val_a and val_b:
                        grades_set = {'K', 'KN', 'TK', 'PK', 'PK-4', 'PK-3', 'PK-5', '01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '12'}
                        is_a_grade = val_a in grades_set or val_a.isdigit()
                        is_b_grade = val_b in grades_set or val_b.isdigit()
                        
                        is_col_a_grade = 'grade' in col_a.lower()
                        is_col_b_grade = 'grade' in col_b.lower()
                        
                        if is_col_a_grade and is_b_grade:
                            filters[col_a] = val_b
                            filters[col_b] = val_a
                        elif is_col_b_grade and is_a_grade:
                            filters[col_a] = val_b
                            filters[col_b] = val_a
                        else:
                            filters[col_a] = val_a
                            filters[col_b] = val_b
                    else:
                        target_col = col_b
                        if val_b:
                            grades_set = {'K', 'KN', 'TK', 'PK', 'PK-4', 'PK-3', 'PK-5', '01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '12'}
                            is_b_grade = val_b in grades_set or val_b.isdigit()
                            is_col_a_grade = 'grade' in col_a.lower()
                            is_col_b_grade = 'grade' in col_b.lower()
                            if is_col_a_grade and is_b_grade:
                                target_col = col_a
                            elif is_col_b_grade and not is_b_grade:
                                target_col = col_a
                        filters[target_col] = val_b
                else:
                    for c in x_cols_list:
                        filters[c] = x_val
        else:
            col_name = x_cols_list[0] if x_cols_list else (x_col_raw if isinstance(x_col_raw, str) else '')
            if col_name:
                filters[col_name] = x_val

    # assessment_compare: drill_col is the specific PL column (e.g.
    # test_2025_2026_DIBELS_Reading_BOY_PL).  The series_val is the PL value
    # ("Above Benchmark").  Filter that column directly — it already encodes
    # the test, subject, window, and year so no extra filters are needed.
    drill_col = params.get('drill_col', '')
    series_val = params.get('series_val', '')
    if drill_col and series_val:
        filters[drill_col] = series_val
    elif series_val:
        group_col = chart.config.get('group_col', '')
        series_col = fmap.get('series_col', chart.config.get('stack_col', ''))
        
        # Check if it's a grouped + stacked chart series value (e.g. "Group — Stack")
        split_success = False
        if group_col and series_col:
            parts = split_composite_value(series_val, 2)
            if len(parts) == 2:
                filters[group_col] = parts[0]
                filters[series_col] = parts[1]
                split_success = True
        
        if not split_success and series_col:
            filters[series_col] = series_val

    # Post-process filters to ensure no keys contain commas (safety net split)
    final_filters = {}
    for key, val in filters.items():
        if isinstance(key, str) and ',' in key:
            sub_keys = [k.strip() for k in key.split(',') if k.strip()]
            if isinstance(val, str):
                sub_vals = split_composite_value(val, len(sub_keys))
                if len(sub_vals) == len(sub_keys):
                    for sk, sv in zip(sub_keys, sub_vals):
                        if sk:
                            final_filters[sk] = sv
                else:
                    # Try heuristic if 2 keys
                    if len(sub_keys) == 2:
                        col_a, col_b = sub_keys[0], sub_keys[1]
                        grades_set = {'K', 'KN', 'TK', 'PK', 'PK-4', 'PK-3', 'PK-5', '01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '12'}
                        is_val_grade = val in grades_set or str(val).isdigit()
                        is_col_a_grade = 'grade' in col_a.lower()
                        is_col_b_grade = 'grade' in col_b.lower()
                        if is_col_a_grade and is_val_grade:
                            final_filters[col_a] = val
                        elif is_col_b_grade and is_val_grade:
                            final_filters[col_b] = val
                        else:
                            for sk in sub_keys:
                                final_filters[sk] = val
                    else:
                        for sk in sub_keys:
                            final_filters[sk] = val
        else:
            final_filters[key] = val

    return final_filters


def _apply_user_access_filter(rows, cols, user):
    """Post-fetch filter: remove rows the user should not see.
    Only applies to non-admin users; checks for accessible student keys
    or permitted schools in the result columns.
    Returns (filtered_rows, cols).
    """
    if user.is_superuser:
        return rows, cols
    if getattr(user, 'is_project_admin', False):
        return rows, cols
    # Check all user roles for District/Admin access (not just active_role)
    user_roles = [r.name for r in user.roles.all()]
    unrestricted_names = {'Project Admin', 'District Admin', 'District View'}
    if any(name in unrestricted_names or 'District' in name for name in user_roles):
        return rows, cols

    # Try to match student identifiers in the columns
    student_id_cols = [c for c in cols if c in ('ssid', 'student_id', 'stu_annual_key', 'local_student_id')
                       or c.endswith('.ssid') or c.endswith('.student_id')
                       or c.endswith('.stu_annual_key') or c.endswith('.local_student_id')]
    if not student_id_cols:
        return rows, cols  # No student columns found — can't filter

    # Get accessible student keys
    accessible_keys = None
    has_accessible_method = hasattr(user, 'get_accessible_student_keys')
    if has_accessible_method:
        keys = user.get_accessible_student_keys()
        if keys is not None:
            accessible_keys = set(keys)

    # Get permitted school IDs for school-level filtering
    permitted_schools = None
    has_school_filter = False
    if hasattr(user, 'active_school') and user.active_school:
        permitted_schools = {user.active_school.state_id}
        has_school_filter = True
    elif hasattr(user, 'schools') and isinstance(user.schools, list) and user.schools:
        permitted_schools = set(user.schools)
        has_school_filter = True
    else:
        # No schools assigned → user should see nothing (deny-by-default)
        has_school_filter = True

    school_id_cols = [c for c in cols if c in ('school_state_id', 'school_code', 'state_school_id')
                      or c.endswith('.school_state_id') or c.endswith('.school_code')]

    filtered = []
    for row in rows:
        # Check student key access (teacher-level)
        if accessible_keys is not None:
            matched = False
            for col in student_id_cols:
                val = str(row.get(col, '')).strip()
                if val and val in accessible_keys:
                    matched = True
                    break
            if not matched:
                continue  # Skip — student not in teacher's roster

        # Check school access (school-level).  If permitted_schools is
        # None the user is truly unrestricted (already returned above).
        # An empty set means deny-all (no schools assigned).
        if has_school_filter:
            if not permitted_schools:
                continue  # Empty set → deny all
            if school_id_cols:
                matched = False
                for col in school_id_cols:
                    val = str(row.get(col, '')).strip()
                    if val and val in permitted_schools:
                        matched = True
                        break
                if not matched:
                    continue  # Skip — not in permitted school
            # If no school_id_cols found but has_school_filter, we can't
            # verify school membership.  Allow through (lenient).

        filtered.append(row)

    return filtered, cols


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
    # dynamic_summary_table (calculations mode): extract the value-col
    # sentinel before it leaks into SQL filters, and ensure that column is
    # fetched so we can restrict the drill to students who actually
    # contributed a value to the aggregate.
    dyn_calc_value_col = None
    if 'dyn_calc_value_col' in drill_filters and chart.chart_type == 'dynamic_summary_table':
        drill_filters = dict(drill_filters)
        dyn_calc_value_col = drill_filters.pop('dyn_calc_value_col')
        extra_columns = list(extra_columns or [])
        if dyn_calc_value_col not in extra_columns:
            extra_columns.append(dyn_calc_value_col)

    # date_series (Dates chart): extract the bucketed-date + value-col /
    # active-range sentinels before they leak into SQL filters, and ensure
    # the columns they need are fetched for the post-fetch filtering below.
    ds_date_val = None
    ds_value_col_required = None
    ds_active_enter_col = None
    ds_active_leave_col = None
    if chart.chart_type == 'date_series' and (
        'ds_date_val' in drill_filters or 'ds_value_col_required' in drill_filters or 'ds_active_line_id' in drill_filters
    ):
        drill_filters = dict(drill_filters)
        ds_date_val = drill_filters.pop('ds_date_val', None)
        ds_value_col_required = drill_filters.pop('ds_value_col_required', None)
        ds_active_line_id = drill_filters.pop('ds_active_line_id', None)
        cfg = chart.config
        extra_columns = list(extra_columns or [])
        if ds_active_line_id:
            _line = next(
                (l for l in cfg.get('ds_lines', []) if (l.get('id') or l.get('label')) == ds_active_line_id),
                None,
            )
            if _line:
                ds_active_enter_col = _line.get('enter_col', '')
                ds_active_leave_col = _line.get('leave_col', '')
        for fk in (cfg.get('ds_date_col', ''), ds_value_col_required or '', ds_active_enter_col or '', ds_active_leave_col or ''):
            if fk and fk not in extra_columns:
                extra_columns.append(fk)

    # progress_growth: extract the pg_cat sentinel before it leaks into SQL
    # filters, and ensure the before/after PL columns are fetched.
    pg_cat_filter = None
    pg_x_blank_col = None
    if 'pg_cat' in drill_filters and chart.chart_type == 'progress_growth':
        drill_filters = dict(drill_filters)          # don't mutate caller's dict
        pg_cat_filter = drill_filters.pop('pg_cat')
        pg_x_blank_col = drill_filters.pop('pg_x_blank', None)
        cfg = chart.config
        extra_columns = list(extra_columns or [])
        for fk in (cfg.get('pg_before_col', ''), cfg.get('pg_after_col', ''), pg_x_blank_col or ''):
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

    # Cache the raw fetch (everything above this point only affects which
    # filters/extra_columns we fetch with — already baked into the key below).
    # Scoped per-user exactly like data_loader.py:_run_saved_query_sql, since
    # drill-through (unlike chart rendering) applies row-level access control
    # — role, school, and even per-teacher restrictions can change the rows
    # returned for the same chart+filters.
    user_part = _drill_user_part(user)
    _fetch_hash = _drill_fetch_hash(chart, drill_filters, extra_columns)
    updated_ts = int(chart.updated_at.timestamp())
    _drill_cache_key = f"drill_fetch:{chart.pk}:{updated_ts}:{chart.source_type}:{_fetch_hash}{user_part}"
    _cached_fetch = cache.get(_drill_cache_key)
    sql_total_rows = None

    if _cached_fetch is not None:
        rows, cols, error = _cached_fetch
    elif chart.source_type == 'sql':
        result = run_raw_sql(chart.source_sql)
        rows = result.get('rows', [])
        cols = result.get('columns', [])
        error = result.get('error')
        # Post-fetch user access filtering for SQL-source charts
        if user and rows and not error:
            rows, cols = _apply_user_access_filter(rows, cols, user)
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
        result = run_column_query(
            selected_columns, merged_filters, user=user, apply_user_filters=True,
            limit=per_page if _can_sql_paginate_drill(chart, drill_filters, sort_by) else None,
            offset=(page - 1) * per_page if _can_sql_paginate_drill(chart, drill_filters, sort_by) else None,
            order_by=sort_by if _can_sql_paginate_drill(chart, drill_filters, sort_by) and sort_by else None,
        )
        rows = result.get('rows', [])
        cols = result.get('columns', [])
        error = result.get('error')
        if result.get('total_rows') is not None:
            sql_total_rows = result.get('total_rows')
    elif chart.source_type == 'dataset':
        from dataviz.models import Dataset
        from dataviz.utils.compiler import compile_dataset_to_sql, execute_compiled_query, execute_compiled_query_aliased, COLUMN_WHITELIST, get_columns_for_table
        from dataviz.utils.data_loader import enrich_drill_rows as _enrich_drill_rows
        try:
            ds = Dataset.objects.get(pk=chart.source_id)
        except Dataset.DoesNotExist:
            rows = []
            cols = []
            error = 'Dataset not found'
        else:
            merged_filters = dict(chart.source_filters or {})
            merged_filters.update(drill_filters)

            # Map standard keys in merged_filters to "table.column" format for compile_dataset_to_sql
            active_tables = {ds.base_table}
            for j in (ds.joins or []):
                if j.get('table'):
                    active_tables.add(j['table'])

            def _allowed_columns(tbl: str) -> set:
                return set(get_columns_for_table(tbl)) | set(COLUMN_WHITELIST.get(tbl, set()))

            # Resolve all filter keys (bare exploration names, JSONB key suffixes,
            # clean aliases from column_mappings, or already-qualified table.column)
            # into the table.column format required by compile_dataset_to_sql.
            from dataviz.utils.compiler import resolve_dataset_filters as _resolve_filters
            from dataviz.utils.status_filter import prepare_interactive_filters
            dataset_filters, status_val = prepare_interactive_filters(merged_filters, ds, active_tables)

            # Extract school_year for the explorer fallback (used below when yr_col
            # is absent from the dataset columns).
            school_year = None
            for fk, fval in merged_filters.items():
                if fk.split('.')[-1] == 'school_year':
                    school_year = fval
                    break
            
            # Find the best student identifier, year, and annual key columns whitelisted in active tables
            student_identifier_col = None
            for tbl in ['current_student', 'student_enroll', 'results', 'student_marks_list', 'schedule_list']:
                if tbl in active_tables and 'ssid' in COLUMN_WHITELIST.get(tbl, {}):
                    student_identifier_col = f"{tbl}.ssid"
                    break
            if not student_identifier_col and 'ssid' in COLUMN_WHITELIST.get(ds.base_table, {}):
                student_identifier_col = f"{ds.base_table}.ssid"
            
            if not student_identifier_col:
                for tbl in ['current_student', 'student_enroll', 'schedule_list', 'student_marks_list', 'results']:
                    if tbl in active_tables and 'student_id' in COLUMN_WHITELIST.get(tbl, {}):
                        student_identifier_col = f"{tbl}.student_id"
                        break
                if not student_identifier_col and 'student_id' in COLUMN_WHITELIST.get(ds.base_table, {}):
                    student_identifier_col = f"{ds.base_table}.student_id"

            if not student_identifier_col:
                student_identifier_col = f"{ds.base_table}.ssid"

            key_col = None
            for tbl in ['current_student', 'student_enroll', 'schedule_list', 'student_marks_list']:
                if tbl in active_tables and 'stu_annual_key' in COLUMN_WHITELIST.get(tbl, {}):
                    key_col = f"{tbl}.stu_annual_key"
                    break
            if not key_col and 'stu_annual_key' in COLUMN_WHITELIST.get(ds.base_table, {}):
                key_col = f"{ds.base_table}.stu_annual_key"

            yr_col = None
            for tbl in ['current_student', 'student_enroll', 'results', 'schedule_list', 'student_marks_list', 'tests']:
                if tbl in active_tables and 'school_year' in COLUMN_WHITELIST.get(tbl, {}):
                    yr_col = f"{tbl}.school_year"
                    break
            if not yr_col and 'school_year' in COLUMN_WHITELIST.get(ds.base_table, {}):
                yr_col = f"{ds.base_table}.school_year"

            dataset_columns = list(ds.columns or [])
            if student_identifier_col not in dataset_columns:
                dataset_columns.append(student_identifier_col)
            if key_col and key_col not in dataset_columns:
                dataset_columns.append(key_col)
            if yr_col and yr_col not in dataset_columns:
                dataset_columns.append(yr_col)
            
            # Dynamically select any filtered dataset columns so they are returned in the rows
            for fk in dataset_filters:
                if fk not in dataset_columns:
                    parts = fk.split('.', 1)
                    if len(parts) == 2:
                        tbl, col = parts
                        if tbl in active_tables and (col in _allowed_columns(tbl) or col.split('->', 1)[0] in _allowed_columns(tbl)):
                            dataset_columns.append(fk)
            
            class MockDataset:
                def __init__(self, original, columns):
                    self.base_table = original.base_table
                    self.distinct = original.distinct
                    self.joins = original.joins
                    self.filters = original.filters
                    self.status_filter = getattr(original, 'status_filter', None)
                    self.columns = columns
                    self.column_mappings = getattr(original, 'column_mappings', None) or {}
                    self.updated_at = getattr(original, 'updated_at', None)
            
            mock_ds = MockDataset(ds, dataset_columns)
            one_per_sak = bool((chart.config or {}).get('one_row_per_stu_annual_key'))
            sql, params = compile_dataset_to_sql(
                mock_ds, user=user, interactive_filters=dataset_filters,
                limit=None, status_filter=status_val,
                one_row_per_stu_annual_key=one_per_sak,
            )
            # Use aliased executor so column_mappings are applied to result keys
            _col_mappings = getattr(mock_ds, 'column_mappings', {}) or {}
            ds_result = execute_compiled_query_aliased(sql, params, _col_mappings)
            if one_per_sak and not ds_result.get('error') and ds_result.get('rows'):
                from dataviz.utils.compiler import dedupe_rows_by_stu_annual_key
                ds_result = dict(ds_result)
                ds_result['rows'] = dedupe_rows_by_stu_annual_key(ds_result['rows'])
                ds_result['row_count'] = len(ds_result['rows'])
            
            error = ds_result.get('error')
            if error:
                rows = []
                cols = []
            else:
                ds_rows = ds_result.get('rows', [])
                keys = []
                if key_col:
                    _key_alias = _col_mappings.get(key_col, key_col.split('.')[-1])
                    keys = list(set(str(r.get(_key_alias) or '').strip() for r in ds_rows))
                    keys = [k for k in keys if k]
                
                ssids = []
                if student_identifier_col:
                    _sid_alias = _col_mappings.get(student_identifier_col, student_identifier_col.split('.')[-1])
                    ssids = list(set(str(r.get(_sid_alias) or '').strip() for r in ds_rows))
                    ssids = [s for s in ssids if s]

                # Determine default columns from the dataset columns if none are requested
                if not extra_columns:
                    ds_cols = []
                    for c in (ds.columns or []):
                        col_name = _col_mappings.get(c, c.split('.')[-1])
                        if col_name not in ds_cols:
                            ds_cols.append(col_name)
                    for base_col in ['student_id', 'student_name', 'grade', 'school', 'school_year']:
                        if base_col not in ds_cols:
                            ds_cols.append(base_col)
                    # Include all column_mappings aliases so the explorer
                    # fetches assessment scores via its results/tests join logic.
                    for alias in (ds.column_mappings or {}).values():
                        if alias and alias not in ds_cols:
                            ds_cols.append(alias)
                    extra_columns = ds_cols
                else:
                    # Map any prefixed columns in extra_columns to clean column names
                    clean_extra_cols = []
                    for c in extra_columns:
                        clean_col = c.split('.')[-1]
                        if clean_col not in clean_extra_cols:
                            clean_extra_cols.append(clean_col)
                    extra_columns = clean_extra_cols
                
                if not ssids and not keys:
                    rows = []
                    cols = extra_columns or []
                else:
                    explorer_filters = {}
                    if status_val is None:
                        explorer_filters['status'] = []
                    elif status_val != 'Active':
                        explorer_filters['status'] = status_val

                    if yr_col:
                        _yr_alias = _col_mappings.get(yr_col, yr_col.split('.')[-1])
                        years = list(set(str(r.get(_yr_alias) or '').strip() for r in ds_rows))
                        years = [y for y in years if y]
                        if years:
                            explorer_filters['school_year'] = years
                    elif school_year:
                        explorer_filters['school_year'] = school_year

                    rows = [dict(r) for r in ds_rows]
                    rows, cols, error = _enrich_drill_rows(
                        rows, extra_columns or [], explorer_filters, user=user,
                    )
                    if not error:
                        ordered_cols = []
                        for c in dataset_columns:
                            clean_c = _col_mappings.get(c, c.split('.')[-1])
                            if clean_c not in ordered_cols:
                                ordered_cols.append(clean_c)
                        for c in cols:
                            if c not in ordered_cols:
                                ordered_cols.append(c)
                        cols = ordered_cols
    else:
        # Merge saved source_filters with the click-derived drill_filters
        merged_filters = dict(chart.source_filters or {})
        merged_filters.update(drill_filters)
        result = run_saved_query(
            chart.source_id, merged_filters, user,
            extra_columns=extra_columns,
            apply_user_filters=True,
            limit=per_page if _can_sql_paginate_drill(chart, drill_filters, sort_by) else None,
            offset=(page - 1) * per_page if _can_sql_paginate_drill(chart, drill_filters, sort_by) else None,
            order_by=sort_by if _can_sql_paginate_drill(chart, drill_filters, sort_by) and sort_by else None,
        )
        rows = result.get('rows', [])
        cols = result.get('columns', [])
        error = result.get('error')
        if result.get('total_rows') is not None:
            sql_total_rows = result.get('total_rows')

    if _cached_fetch is None and not error:
        cache.set(_drill_cache_key, (rows, cols, error), DRILL_CACHE_TTL)

    # Tier-2 cache: post-filtered row set (before UI column_filters / sort / page)
    _post_hash = _drill_post_hash(chart, _fetch_hash)
    _drill_post_key = f"drill_post:{chart.pk}:{updated_ts}:{_post_hash}{user_part}"
    _cached_post = cache.get(_drill_post_key)

    if _cached_post is not None:
        rows, cols, all_columns = _cached_post
    else:
        # Post-fetch filtering for dynamic columns (test_* / mark_*).
        # The SQL data_loader skips these at the SQL level because the data
        # is fetched in a separate bulk phase.  We apply them here in Python.
        if rows and drill_filters:
            for fkey, fval in drill_filters.items():
                col_name = fkey.split('.')[-1]
                if not col_name.startswith(('test_', 'mark_')):
                    continue
                if isinstance(fval, list):
                    vals = set(str(v) for v in fval)
                    rows = [r for r in rows if str(r.get(col_name) or '\u2014').strip() in vals or
                            ('(Blank)' in vals and (not r.get(col_name) or r.get(col_name) == '\u2014'))]
                else:
                    target = str(fval).strip()
                    if target == '(Blank)':
                        rows = [r for r in rows if not r.get(col_name) or r.get(col_name) == '\u2014']
                    else:
                        rows = [r for r in rows if str(r.get(col_name) or '').strip() == target]

        if rows and chart.chart_type in ('summary_table', 'dynamic_summary_table'):
            _id_col = _find_id_col(rows[0])
            if _id_col:
                _seen: set = set()
                _deduped: list = []
                for _r in rows:
                    _k = _r.get(_id_col)
                    if _k not in _seen:
                        _seen.add(_k)
                        _deduped.append(_r)
                rows = _deduped

        rows = _deduplicate_count_drill_rows(rows, chart)

        if dyn_calc_value_col and rows:
            row_keys = list(rows[0].keys())
            from dataexploration.school_year_utils import resolve_relative_column_key

            def _norm_col(c):
                return str(c).replace('-', '_').lower()

            key_map = {_norm_col(k): k for k in row_keys}

            def _resolve_calc_col(col):
                if not col:
                    return col
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

            calc_target_col = _resolve_calc_col(dyn_calc_value_col)

            def _has_calc_value(v):
                if v is None:
                    return False
                s = str(v).strip()
                if not s or s in {'-', '\u2014', '\u2013', 'N/A', 'n/a', 'None', 'null', 'NULL'}:
                    return False
                return True

            rows = [r for r in rows if _has_calc_value(r.get(calc_target_col))]

        if rows and chart.chart_type == 'date_series' and (ds_date_val or ds_value_col_required or ds_active_enter_col):
            row_keys = list(rows[0].keys())
            from dataexploration.school_year_utils import resolve_relative_column_key

            def _norm_ds_col(c):
                return str(c).replace('-', '_').lower()

            _ds_key_map = {_norm_ds_col(k): k for k in row_keys}

            def _resolve_ds_col(col):
                if not col:
                    return col
                norm = _norm_ds_col(col)
                if norm in _ds_key_map:
                    return _ds_key_map[norm]
                resolved = resolve_relative_column_key(col)
                norm_res = _norm_ds_col(resolved)
                if norm_res in _ds_key_map:
                    return _ds_key_map[norm_res]
                clean = col.split('.')[-1]
                norm_clean = _norm_ds_col(clean)
                if norm_clean in _ds_key_map:
                    return _ds_key_map[norm_clean]
                return col

            if ds_active_enter_col:
                import pandas as pd
                from dataviz.utils.chart_builder import _ds_bucket_label_range
                date_bucket = (chart.config.get('ds_date_bucket') or 'day').strip().lower()
                start, end = _ds_bucket_label_range(ds_date_val, date_bucket) if ds_date_val else (None, None)
                enter_col = _resolve_ds_col(ds_active_enter_col)
                leave_col = _resolve_ds_col(ds_active_leave_col) if ds_active_leave_col else None
                if start is not None and enter_col:
                    enter_dt = pd.Series([r.get(enter_col) for r in rows])
                    enter_dt = pd.to_datetime(enter_dt, errors='coerce')
                    if leave_col:
                        leave_dt = pd.to_datetime(pd.Series([r.get(leave_col) for r in rows]), errors='coerce')
                    else:
                        leave_dt = pd.Series(pd.NaT, index=enter_dt.index)
                    mask = enter_dt.notna() & (enter_dt <= end) & (leave_dt.isna() | (leave_dt >= start))
                    rows = [r for r, keep in zip(rows, mask.tolist()) if keep]

                _id_col = _find_id_col(rows[0]) if rows else None
                if _id_col:
                    _seen: set = set()
                    _deduped: list = []
                    for _r in rows:
                        _k = _r.get(_id_col)
                        if _k not in _seen:
                            _seen.add(_k)
                            _deduped.append(_r)
                    rows = _deduped

            if ds_date_val and not ds_active_enter_col:
                date_col = _resolve_ds_col(chart.config.get('ds_date_col', ''))
                if date_col:
                    import pandas as pd
                    from dataviz.utils.chart_builder import _bucket_date_column
                    date_bucket = (chart.config.get('ds_date_bucket') or 'day').strip().lower()
                    bucketed = _bucket_date_column(pd.Series([r.get(date_col) for r in rows]), date_bucket)
                    rows = [r for r, b in zip(rows, bucketed) if b == ds_date_val]

            if ds_value_col_required and rows:
                value_col = _resolve_ds_col(ds_value_col_required)

                def _has_ds_value(v):
                    if v is None:
                        return False
                    s = str(v).strip()
                    if not s or s in {'-', '—', '–', 'N/A', 'n/a', 'None', 'null', 'NULL'}:
                        return False
                    return True

                rows = [r for r in rows if _has_ds_value(r.get(value_col))]

        if rows and chart.chart_type == 'progress_growth' and (pg_cat_filter or pg_before_val):
            cfg = chart.config
            before_col = cfg.get('pg_before_col', '')
            after_col = cfg.get('pg_after_col', '')
            pl_scale = cfg.get('pl_scale', [])
            if before_col and after_col:
                from dataviz.utils.chart_builder import (
                    _BLANK_PL_VALS,
                    _resolve_row_column,
                    classify_progress_growth_row,
                    filter_progress_growth_matrix_rows,
                    prepare_progress_growth_rows,
                )

                if pg_before_val:
                    rows, before_col, after_col = filter_progress_growth_matrix_rows(
                        rows, before_col, after_col, pg_before_val, pg_after_val,
                    )
                else:
                    rows, before_col, after_col, rank = prepare_progress_growth_rows(
                        rows, before_col, after_col, pl_scale=pl_scale or None,
                    )

                pg_display = cfg.get('pg_display', '')
                x_col = cfg.get('x_col', '') if pg_display in ('stacked_bar', 'grouped_bar') else ''
                if pg_x_blank_col:
                    blank_col = _resolve_row_column(list(rows[0].keys()) if rows else [], pg_x_blank_col)
                    rows = [r for r in rows if str(r.get(blank_col) or '').strip() in _BLANK_PL_VALS]
                elif x_col and x_col not in drill_filters:
                    x_col = _resolve_row_column(list(rows[0].keys()) if rows else [], x_col)
                    rows = [r for r in rows if str(r.get(x_col) or '').strip() not in _BLANK_PL_VALS]

                if pg_cat_filter:
                    rows = [
                        r for r in rows
                        if classify_progress_growth_row(r, before_col, after_col, rank) == pg_cat_filter
                    ]

        all_columns = list(cols)
        if not error:
            cache.set(_drill_post_key, (rows, cols, all_columns), DRILL_CACHE_TTL)

    # Per-column filters (from header filter dropdowns)
    if column_filters and rows:
        for fkey, fvals in column_filters.items():
            if not fvals:
                continue
            allowed = set(fvals) if isinstance(fvals, list) else {fvals}
            rows = [r for r in rows if str(r.get(fkey, '')) in allowed]

    # Sorting (skip when SQL already ordered the page)
    if sort_by and rows and sql_total_rows is None:
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
        row_keys = list(rows[0].keys())
        col_to_row_key = {}
        for c in columns:
            c_clean = c.split('.')[-1].strip()
            if c in row_keys:
                col_to_row_key[c] = c
            elif c_clean in row_keys:
                col_to_row_key[c] = c_clean
            else:
                match = next((rk for rk in row_keys if rk.split('.')[-1].strip() == c_clean), None)
                if match:
                    col_to_row_key[c] = match

        keep_row_keys = set(col_to_row_key.values())
        for rk in row_keys:
            if rk.split('.')[-1].strip() in ('ssid', 'student_id', 'stu_annual_key'):
                keep_row_keys.add(rk)

        new_rows = []
        for r in rows:
            new_row = {}
            for c in columns:
                rk = col_to_row_key.get(c)
                if rk is not None and rk in r:
                    new_row[c] = r[rk]
            for rk in keep_row_keys:
                if rk in r and rk not in new_row:
                    new_row[rk] = r[rk]
            new_rows.append(new_row)
        rows = new_rows
        cols = [c for c in columns if c in col_to_row_key]

    # Add flat student identifier keys for template ease
    for r in rows:
        for k, v in list(r.items()):
            k_clean = k.split('.')[-1].lower()
            if k_clean in ('ssid', 'student_id', 'stu_annual_key') and k_clean not in r:
                r[k_clean] = v

    total = sql_total_rows if sql_total_rows is not None else len(rows)
    if sql_total_rows is not None:
        paged = rows
    else:
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
    user_part = _drill_user_part(user)
    _fetch_hash = _drill_fetch_hash(chart, drill_filters, None)
    updated_ts = int(chart.updated_at.timestamp())
    _post_hash = _drill_post_hash(chart, _fetch_hash)
    _drill_post_key = f"drill_post:{chart.pk}:{updated_ts}:{_post_hash}{user_part}"
    cached = cache.get(_drill_post_key)
    if cached is None:
        execute_drill_query(chart, drill_filters, columns=[], user=user, page=1, per_page=50)
        cached = cache.get(_drill_post_key)
    rows = cached[0] if cached else []

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
    table.
    For datasets, this is restricted to the columns defined in the dataset (along
    with their mapped aliases).
    For raw SQL, it is restricted to the columns returned by the query.
    For standard explorer queries, it is the full data exploration column list.
    """
    if chart.source_type == 'dataset' and chart.source_id:
        from dataviz.models import Dataset
        try:
            ds = Dataset.objects.get(pk=chart.source_id)
            cols = []
            # Add dataset columns (cleaned to unqualified)
            for c in (ds.columns or []):
                cols.append(c.split('.')[-1])
            # Add any mapped aliases
            for alias in (ds.column_mappings or {}).values():
                if alias and alias.split('.')[-1] not in cols:
                    cols.append(alias.split('.')[-1])
            # Include standard base columns that are automatically merged in datasets
            for c in ['student_id', 'student_name', 'grade', 'school', 'school_year', 'ssid']:
                if c not in cols:
                    cols.append(c)
            return cols
        except Dataset.DoesNotExist:
            return []
            
    elif chart.source_type == 'sql':
        # For raw SQL, we can't join other tables. The only available columns
        # are those returned by the query.
        from dataviz.utils.data_loader import run_raw_sql
        result = run_raw_sql(chart.source_sql)
        cols = [c.split('.')[-1] for c in result.get('columns', [])]
        return cols

    # Default: data exploration columns
    from dataexploration.constants import COLUMN_DEFINITIONS
    if isinstance(COLUMN_DEFINITIONS, dict):
        base = list(COLUMN_DEFINITIONS.keys())
    else:
        base = [k for k, _v in COLUMN_DEFINITIONS]

    # Dynamic columns from the saved query's config
    dynamic = []
    if chart.source_type == 'query' and chart.source_id:
        from dataexploration.models import SavedQuery
        try:
            sq = SavedQuery.objects.get(pk=chart.source_id)
            for c in (sq.config or {}).get('columns', []):
                if c.startswith(('test_', 'mark_')) and c not in base:
                    dynamic.append(c)
        except SavedQuery.DoesNotExist:
            pass

    return base + dynamic
