import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


def build_grouped_axis_data(categories):
    """
    Parses a list of category strings (possibly joined with ' — ')
    and returns a tuple: (leaf_categories, group_labels_list)
    If no grouping exists, returns (categories, None).
    """
    has_grouping = False
    parsed = []
    for cat in categories:
        if ' — ' in cat:
            has_grouping = True
            parts = cat.split(' — ')
            parent = " — ".join(parts[:-1])
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


def aggregate_chart_data(rows, chart_type, x_col, y_col, aggregation, series_col=None, custom_config=None, pre_aggregated=False):
    """
    Aggregates list of row dicts into a structured option format for Apache ECharts.
    Returns a dictionary of option overrides.

    When ``pre_aggregated=True``, rows are expected to already be grouped (one row
    per x/series combination with the value already aggregated).  The Python
    aggregation step is skipped — the y_col value from each row is used directly.
    """
    if not rows:
        return {
            'error': 'No data found for this query',
            'legend': {'data': []},
            'xAxis': {'data': []},
            'series': []
        }

    custom_config = custom_config or {}
    chart_type = (chart_type or 'bar').lower()
    aggregation = (aggregation or 'count').lower()
    variation = custom_config.get('variation', 'standard').lower()

    # Parse multiple X-axis columns (comma-separated)
    x_cols = [x.strip() for x in x_col.split(',') if x.strip()] if x_col else []

    # Parse multiple series/grouping columns (comma-separated)
    series_cols = []
    if series_col:
        series_cols = [s.strip() for s in series_col.split(',') if s.strip()]

    # Step 1: Collect X, Y, and Series values
    # When pre_aggregated, each row is one (x, series) group with value directly
    grouped_data = defaultdict(list)
    x_vals_seen = set()
    series_vals_seen = set()

    # Lookup key for pre-aggregated rows (column names use underscores, not dots)
    _y_key = y_col.replace('.', '_') if pre_aggregated else y_col
    _x_key = x_col.replace('.', '_') if pre_aggregated else x_col

    for row in rows:
        # Resolve X-Axis value
        if x_cols:
            x_vals = []
            for col in x_cols:
                lookup = col.replace('.', '_') if pre_aggregated else col
                val = row.get(lookup)
                if val is None and '.' in col:
                    val = row.get(col.split('.')[1])
                x_vals.append(str(val) if val is not None and val != '' else '(Blank)')
            x_val = " — ".join(x_vals)
        else:
            x_val = '(Blank)'

        y_val = row.get(_y_key)
        if y_val is None and '.' in y_col:
            y_val = row.get(y_col.split('.')[1])

        if x_val is None or x_val == '':
            x_val = '(Blank)'
        else:
            x_val = str(x_val)

        # Series grouping value
        series_val = 'Total'
        if series_cols:
            vals = []
            for s_col in series_cols:
                lookup = s_col.replace('.', '_') if pre_aggregated else s_col
                val = row.get(lookup)
                if val is None and '.' in s_col:
                    val = row.get(s_col.split('.')[1])
                vals.append(str(val) if val is not None and val != '' else '(Blank)')
            series_val = " — ".join(vals)

        x_vals_seen.add(x_val)
        if series_cols:
            series_vals_seen.add(series_val)

        if pre_aggregated:
            # Row is already aggregated — store value directly
            try:
                grouped_data[(x_val, series_val)].append(float(y_val) if y_val not in (None, '—') else 0)
            except (ValueError, TypeError):
                grouped_data[(x_val, series_val)].append(0)
        elif aggregation in {'sum', 'avg', 'min', 'max'}:
            try:
                if y_val is not None and y_val != '—':
                    grouped_data[(x_val, series_val)].append(float(y_val))
            except (ValueError, TypeError):
                pass
        else:
            if y_val is not None:
                grouped_data[(x_val, series_val)].append(y_val)

    # Convert sets to sorted lists and apply custom sorting
    x_categories = list(x_vals_seen)
    sort_order = custom_config.get('sort_order', 'asc')
    custom_order = custom_config.get('custom_order', [])

    if sort_order == 'asc':
        x_categories = sorted(x_categories)
    elif sort_order == 'desc':
        x_categories = sorted(x_categories, reverse=True)
    elif sort_order in {'value_asc', 'value_desc'} and not pre_aggregated:
        def _x_raw_total(x_val):
            s_names = list(series_vals_seen) if series_cols else ['Total']
            total = 0
            for s in s_names:
                vals = grouped_data.get((x_val, s), [])
                if aggregation == 'count':
                    total += len(vals)
                elif aggregation in {'sum', 'avg'}:
                    total += sum(v for v in vals if isinstance(v, (int, float)))
                elif aggregation == 'max':
                    total += max((v for v in vals if isinstance(v, (int, float))), default=0)
                elif aggregation == 'min':
                    total += min((v for v in vals if isinstance(v, (int, float))), default=0)
            return total
        x_categories = sorted(x_categories, key=_x_raw_total, reverse=(sort_order == 'value_desc'))
    elif sort_order in {'value_asc', 'value_desc'}:
        # Pre-aggregated: sort by single value per series
        def _x_agg_total(x_val):
            s_names = list(series_vals_seen) if series_cols else ['Total']
            return sum(grouped_data.get((x_val, s), [0])[0] for s in s_names)
        x_categories = sorted(x_categories, key=_x_agg_total, reverse=(sort_order == 'value_desc'))
    elif sort_order == 'custom' and custom_order:
        ordered = [v for v in custom_order if v in x_categories]
        ordered.extend([v for v in x_categories if v not in ordered])
        x_categories = ordered

    series_names = sorted(list(series_vals_seen)) if series_cols else ['Total']

    # Step 2: Compute aggregations
    def perform_aggregation(vals):
        if not vals:
            return 0
        if aggregation == 'count':
            return len(vals)
        elif aggregation == 'sum':
            return sum(vals)
        elif aggregation == 'avg':
            return sum(vals) / len(vals)
        elif aggregation == 'min':
            return min(vals)
        elif aggregation == 'max':
            return max(vals)
        return len(vals)

    aggregated_map = {}
    for key, vals in grouped_data.items():
        aggregated_map[key] = perform_aggregation(vals)

    # Step 3: Format series based on chart type
    series_list = []
    
    # Custom colors mapping
    custom_colors = custom_config.get('colors', [])
    color_palette = custom_colors if isinstance(custom_colors, list) and custom_colors else None

    # Per-value color mappings and formatting
    value_colors = custom_config.get('value_colors', {})
    value_labels = custom_config.get('value_labels', {})
    value_prefix = custom_config.get('value_prefix', '')
    value_suffix = custom_config.get('value_suffix', '')
    stacked_percent = custom_config.get('stacked_percent', False)
    bar_gap = custom_config.get('bar_gap', None)
    bar_category_gap = custom_config.get('bar_category_gap', None)

    # Generate title text if custom title is empty
    title_text = custom_config.get('title_custom', '')
    if not title_text:
        x_name = x_col.split('.')[-1].replace('_', ' ').title()
        y_name = y_col.split('.')[-1].replace('_', ' ').title()
        agg_name = aggregation.title()
        if series_cols:
            grp_names = ", ".join([s.split('.')[-1].replace('_', ' ').title() for s in series_cols])
            title_text = f"{agg_name} of {y_name} by {x_name} (Grouped by {grp_names})"
        else:
            title_text = f"{agg_name} of {y_name} by {x_name}"

    if chart_type in {'pie', 'doughnut'}:
        pie_data = []
        for x_val in x_categories:
            val = 0
            if series_cols:
                for s_name in series_names:
                    val += aggregated_map.get((x_val, s_name), 0)
            else:
                val = aggregated_map.get((x_val, 'Total'), 0)

            item = {
                'name': value_labels.get(x_val, x_val),
                'value': round(val, 2) if isinstance(val, float) else val
            }

            # Apply per-value color if specified
            if x_val in value_colors:
                item['itemStyle'] = {'color': value_colors[x_val]}

            pie_data.append(item)

        radius = '50%'
        if chart_type == 'doughnut':
            if variation == 'semi_circle':
                radius = ['50%', '70%']
            else:
                radius = ['40%', '70%']

        series_dict = {
            'name': custom_config.get('y_title', y_col.split('.')[-1].replace('_', ' ').title()),
            'type': 'pie',
            'radius': radius,
            'data': pie_data,
            'emphasis': {
                'itemStyle': {
                    'shadowBlur': 10,
                    'shadowOffsetX': 0,
                    'shadowColor': 'rgba(0, 0, 0, 0.5)'
                }
            }
        }

        # Nightingale Rose variation
        if variation == 'rose':
            series_dict['roseType'] = 'radius'

        # Semi-Circle variation
        if variation == 'semi_circle' and chart_type == 'doughnut':
            series_dict['startAngle'] = 180
            series_dict['endAngle'] = 360
            series_dict['center'] = ['50%', '75%']

        series_list.append(series_dict)
        
        legend_data = [value_labels.get(x, x) for x in x_categories]
        xAxis_option = None
        yAxis_option = None
        polar_option = None
        angleAxis_option = None
        radiusAxis_option = None

    elif chart_type == 'funnel':
        funnel_data = []
        for x_val in x_categories:
            val = 0
            if series_cols:
                for s_name in series_names:
                    val += aggregated_map.get((x_val, s_name), 0)
            else:
                val = aggregated_map.get((x_val, 'Total'), 0)

            item = {
                'name': value_labels.get(x_val, x_val),
                'value': round(val, 2) if isinstance(val, float) else val
            }

            # Apply per-value color if specified
            if x_val in value_colors:
                item['itemStyle'] = {'color': value_colors[x_val]}

            funnel_data.append(item)

        funnel_data = sorted(funnel_data, key=lambda k: k['value'], reverse=True)
        
        series_list.append({
            'name': custom_config.get('y_title', y_col.split('.')[-1].replace('_', ' ').title()),
            'type': 'funnel',
            'left': '10%',
            'width': '80%',
            'data': funnel_data
        })
        legend_data = [d['name'] for d in funnel_data]
        xAxis_option = None
        yAxis_option = None
        polar_option = None
        angleAxis_option = None
        radiusAxis_option = None

    elif chart_type == 'gauge':
        overall_vals = []
        for val_list in grouped_data.values():
            overall_vals.extend(val_list)
        gauge_val = perform_aggregation(overall_vals)
        
        series_list.append({
            'name': custom_config.get('y_title', y_col.split('.')[-1].replace('_', ' ').title()),
            'type': 'gauge',
            'detail': {'formatter': '{value}'},
            'data': [{'value': round(gauge_val, 2) if isinstance(gauge_val, float) else gauge_val, 'name': custom_config.get('x_title', 'Value')}]
        })
        legend_data = []
        xAxis_option = None
        yAxis_option = None
        polar_option = None
        angleAxis_option = None
        radiusAxis_option = None

    else:
        # Bar / Line (vertical, horizontal, polar, stacked, grouped)
        legend_data = [value_labels.get(name, name) for name in series_names] if series_cols else ['Total']
        is_polar = (variation == 'polar')

        # For 100% stacked, calculate percentages per x_val
        if stacked_percent and variation in {'stacked', 'horizontal_stacked'}:
            x_totals = {}
            for x_val in x_categories:
                total = 0
                for s_name in series_names:
                    total += aggregated_map.get((x_val, s_name), 0)
                x_totals[x_val] = total if total > 0 else 1

        for idx, s_name in enumerate(series_names):
            series_data = []
            for x_val in x_categories:
                val = aggregated_map.get((x_val, s_name), 0)

                if stacked_percent and variation in {'stacked', 'horizontal_stacked'}:
                    # Convert to percentage
                    total = x_totals.get(x_val, 1)
                    val_rounded = round((val / total) * 100, 1) if total > 0 else 0
                else:
                    val_rounded = round(val, 2) if isinstance(val, float) else val

                # Apply per-value color if available and single series
                if len(series_names) == 1 and x_val in value_colors:
                    series_data.append({
                        'value': val_rounded,
                        'itemStyle': {'color': value_colors[x_val]},
                        'name': value_labels.get(x_val, x_val)
                    })
                else:
                    series_data.append(val_rounded)

            series_dict = {
                'name': value_labels.get(s_name, s_name),
                'type': 'line' if chart_type == 'line' else 'bar',
                'data': series_data,
            }

            if chart_type == 'bar':
                if bar_gap is not None:
                    series_dict['barGap'] = bar_gap
                if bar_category_gap is not None:
                    series_dict['barCategoryGap'] = bar_category_gap

            if is_polar:
                series_dict['coordinateSystem'] = 'polar'

            # Enable stacking
            if (variation in {'stacked', 'horizontal_stacked', 'stacked_area'} or custom_config.get('stacked', False)):
                series_dict['stack'] = 'total'

            # Enable area styling for lines
            if chart_type == 'line' and 'area' in variation:
                series_dict['areaStyle'] = {}

            if s_name in value_colors:
                series_dict['itemStyle'] = {'color': value_colors[s_name]}
            elif color_palette and idx < len(color_palette):
                series_dict['itemStyle'] = {'color': color_palette[idx]}

            series_list.append(series_dict)

        leaf_categories, group_labels = build_grouped_axis_data(x_categories)

        if is_polar:
            polar_option = {}
            angleAxis_option = {
                'type': 'category',
                'data': leaf_categories,
                'boundaryGap': True,
            }
            radiusAxis_option = {
                'type': 'value'
            }
            xAxis_option = None
            yAxis_option = None
        else:
            polar_option = None
            angleAxis_option = None
            radiusAxis_option = None
            
            if variation in {'horizontal', 'horizontal_stacked'}:
                # Swapped category / value axis
                xAxis_option = {
                    'type': 'value',
                    'name': custom_config.get('y_title', y_col.split('.')[-1].replace('_', ' ').title()),
                    'nameLocation': 'end',
                    'splitLine': {'show': custom_config.get('show_gridlines', True)}
                }
                
                if group_labels:
                    yAxis_option = [
                        {
                            'type': 'category',
                            'data': leaf_categories,
                            'axisLabel': {'interval': 0}
                        },
                        {
                            'type': 'category',
                            'data': group_labels,
                            'position': 'left',
                            'offset': 45,
                            'axisLine': {'show': False},
                            'axisTick': {'show': False},
                            'splitLine': {'show': False},
                            'axisLabel': {'interval': 0, 'textStyle': {'fontWeight': 'bold'}}
                        }
                    ]
                else:
                    yAxis_option = {
                        'type': 'category',
                        'data': x_categories,
                        'name': custom_config.get('x_title', x_col.split('.')[-1].replace('_', ' ').title()),
                        'nameLocation': 'middle',
                        'nameGap': 40,
                        'axisLabel': {'interval': 0}
                    }
            else:
                if group_labels:
                    xAxis_option = [
                        {
                            'type': 'category',
                            'data': leaf_categories,
                            'axisLabel': {'interval': 0, 'rotate': 30 if len(leaf_categories) > 8 else 0}
                        },
                        {
                            'type': 'category',
                            'data': group_labels,
                            'position': 'bottom',
                            'offset': 20,
                            'axisLine': {'show': False},
                            'axisTick': {'show': False},
                            'splitLine': {'show': False},
                            'axisLabel': {'interval': 0, 'textStyle': {'fontWeight': 'bold'}}
                        }
                    ]
                else:
                    xAxis_option = {
                        'type': 'category',
                        'data': x_categories,
                        'name': custom_config.get('x_title', x_col.split('.')[-1].replace('_', ' ').title()),
                        'nameLocation': 'middle',
                        'nameGap': 30,
                        'axisLabel': {'interval': 0, 'rotate': 30 if len(x_categories) > 8 else 0}
                    }

                y_label = custom_config.get('y_title', y_col.split('.')[-1].replace('_', ' ').title())
                if stacked_percent:
                    y_label = 'Percentage (%)'

                yAxis_option = {
                    'type': 'value',
                    'name': y_label,
                    'nameLocation': 'end',
                    'splitLine': {'show': custom_config.get('show_gridlines', True)},
                    'max': 100 if stacked_percent else None
                }

    # Build tooltip configuration
    tooltip_config = {
        'trigger': 'item' if (chart_type in {'pie', 'doughnut', 'gauge', 'funnel'} or is_polar) else 'axis',
        'show': custom_config.get('show_tooltip', True) and chart_type not in {'gauge'},
        'axisPointer': {
            'type': 'shadow' if chart_type == 'bar' else 'line'
        }
    }

    # Store formatting hints for frontend to apply
    if value_prefix or value_suffix:
        tooltip_config['_prefix'] = value_prefix
        tooltip_config['_suffix'] = value_suffix
    if stacked_percent and chart_type == 'bar':
        tooltip_config['_show_percent'] = True

    # Construct final ECharts option payload
    echarts_option = {
        'title': {
            'text': title_text,
            'left': 'center',
            'textStyle': {'fontFamily': 'Inter, sans-serif'}
        },
        'tooltip': tooltip_config,
        'legend': {
            'show': custom_config.get('show_legend', True),
            'data': legend_data,
            'orient': custom_config.get('legend_position', 'horizontal'),
            'bottom': 10 if custom_config.get('legend_position') != 'right' else None,
            'right': 10 if custom_config.get('legend_position') == 'right' else None,
            'top': 'middle' if custom_config.get('legend_position') == 'right' else None
        },
        'series': series_list,
        'grid': {
            'left': '8%' if (group_labels and variation in {'horizontal', 'horizontal_stacked'}) else '3%',
            'right': '4%',
            'bottom': '20%' if (group_labels and variation not in {'horizontal', 'horizontal_stacked'}) else '15%',
            'containLabel': True
        }
    }

    if xAxis_option:
        echarts_option['xAxis'] = xAxis_option
    if yAxis_option:
        echarts_option['yAxis'] = yAxis_option
    if polar_option is not None:
        echarts_option['polar'] = polar_option
        echarts_option['angleAxis'] = angleAxis_option
        echarts_option['radiusAxis'] = radiusAxis_option

    if color_palette:
        echarts_option['color'] = color_palette

    return echarts_option
