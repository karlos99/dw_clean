"""Chart type recommendations and auto-description."""
import json
import random
import re

try:
    from dataexploration.school_year_utils import resolve_relative_column_key
except Exception:  # pragma: no cover
    def resolve_relative_column_key(col_key: str) -> str:
        return col_key

from .constants import *  # noqa: F401,F403
from .common import *  # noqa: F401,F403

def _register_rule(condition, chart_key, score=10, reason=''):
    _RECOMMENDATION_RULES.append((condition, chart_key, score, reason))


# Assessment PL columns present → Assessment Compare is a top recommendation
def _has_pl_columns(columns, col_defs):
    return any(c.endswith('_PL') and c.startswith('test_') for c in columns)

_register_rule(_has_pl_columns, 'assessment_compare', 25, 'Performance Level columns detected — Assessment Compare shows PL distribution across tests')

# Multiple test score columns in different years → Progress Growth
def _has_multi_year_test_scores(columns, col_defs):
    test_cols = [c for c in columns if c.startswith('test_') and c.endswith('_SS')]
    years = set()
    for c in test_cols:
        m = _YEAR_RE.match(c)
        if m:
            years.add(f"{m.group(1)}-{m.group(2)}")
    return len(years) >= 2

_register_rule(_has_multi_year_test_scores, 'progress_growth', 22, 'Multiple years of test scores found — Progress Growth tracks improvement')

# Single time dimension + numeric metric = Line chart
def _has_time_dimension(columns, col_defs):
    return any(c in ('school_year', 'grade') for c in columns)

_register_rule(_has_time_dimension, 'line', 15, 'Time/grade dimension detected — Line chart shows trends')

# Categorical dimension + numeric value = Bar or Column
def _has_categorical_and_numeric(columns, col_defs):
    has_cat = any(c in ('grade', 'school', 'gender', 'race', 'ell', 'status', 'sped') for c in columns)
    has_num = any(c.endswith('_SS') for c in columns) or any(c in ('student_id', 'atendace_rate', 'attendance_rate') for c in columns)
    return has_cat and has_num

_register_rule(_has_categorical_and_numeric, 'bar', 18, 'Categorical dimension with numeric data — Bar chart is the clearest comparison')

# Many categories → consider pie/donut for proportions
def _has_few_cat_columns(columns, col_defs):
    cat_cols = [c for c in columns if c in ('grade', 'gender', 'race', 'ell', 'status', 'sped')]
    return len(cat_cols) >= 1

_register_rule(_has_few_cat_columns, 'pie', 10, 'Categorical data — Pie/Donut shows proportion breakdown')

_register_rule(_has_few_cat_columns, 'donut', 11, 'Categorical data — Donut chart for proportion breakdown')

# Two numeric columns → scatter
def _has_two_numeric(columns, col_defs):
    num_cols = [c for c in columns if c.endswith('_SS') or c in ('atendace_rate', 'attendance_rate', 'suspension_indicator')]
    return len(num_cols) >= 2

_register_rule(_has_two_numeric, 'scatter', 12, 'Two numeric columns — Scatter plot shows correlation')

# Multiple categorical columns → heatmap or grouped bar
def _has_multiple_categories(columns, col_defs):
    cat_cols = [c for c in columns if c in ('grade', 'school', 'gender', 'race', 'ell', 'status', 'sped')]
    return len(cat_cols) >= 2

_register_rule(_has_multiple_categories, 'heatmap', 8, 'Multiple categories — Heatmap reveals patterns in cross-tabulation')

# Single numeric column → histogram
def _has_single_numeric(columns, col_defs):
    num_cols = [c for c in columns if c.endswith('_SS') or c in ('atendace_rate', 'attendance_rate')]
    return len(num_cols) == 1

_register_rule(_has_single_numeric, 'histogram', 7, 'Single numeric column — Histogram shows distribution')

# Many assessment PL columns → stacked bar works well
def _has_many_pl_columns(columns, col_defs):
    pl_cols = [c for c in columns if c.endswith('_PL') and c.startswith('test_')]
    return len(pl_cols) >= 4

_register_rule(_has_many_pl_columns, 'stacked_bar', 13, 'Many PL columns — Stacked Bar shows distribution composition')

# Dynamic Summary Table for data exploration with many columns
def _has_many_diverse_columns(columns, col_defs):
    return len(columns) >= 8

_register_rule(_has_many_diverse_columns, 'dynamic_summary_table', 7, 'Many columns selected — Dynamic Summary Table lets you cross-tab multiple dimensions')

# Summary Table for data exploration
def _has_diverse_columns(columns, col_defs):
    return len(columns) >= 6

_register_rule(_has_diverse_columns, 'summary_table', 6, 'Many columns selected — Summary Table is great for data exploration')


def recommend_chart_types(columns: list, column_defs: dict | None = None) -> list[dict]:
    """Recommend chart types based on selected columns.

    Returns a list of {chart_type, label, icon, reason, score} sorted by score descending.
    """
    if not columns:
        return []

    col_defs = column_defs or {}
    scores: dict[str, int] = {}
    reasons: dict[str, str] = {}

    for condition, chart_key, score, reason in _RECOMMENDATION_RULES:
        try:
            if condition(columns, col_defs):
                scores[chart_key] = scores.get(chart_key, 0) + score
                if chart_key not in reasons:
                    reasons[chart_key] = reason
        except Exception:
            pass

    # Always recommend bar as a safe fallback
    if 'bar' not in scores:
        scores['bar'] = 5
        reasons['bar'] = 'Bar chart — the most universal and readable chart type'

    # Map chart key to label
    chart_label_map = dict((k, l) for k, l, _, _ in CHART_TYPES)
    chart_icon_map = dict((k, i) for k, _, i, _ in CHART_TYPES)
    chart_group_map = dict((k, g) for k, _, _, g in CHART_TYPES)

    results = []
    for chart_key, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        results.append({
            'chart_type': chart_key,
            'label': chart_label_map.get(chart_key, chart_key),
            'icon': chart_icon_map.get(chart_key, 'chart-bar'),
            'group': chart_group_map.get(chart_key, ''),
            'reason': reasons.get(chart_key, ''),
            'score': score,
        })

    return results[:5]  # top 5


# ── Auto-Description Engine (Phase 6) ──────────────────────────────────────────

_CHART_TYPE_NAME_MAP = dict((k, l) for k, l, _, _ in CHART_TYPES)

# Add some common names not in CHART_TYPES
_CHART_TYPE_NAME_MAP.update({
    'grouped_bar': 'Grouped Bar Chart',
    'stacked_bar': 'Stacked Bar Chart',
    'column': 'Column Chart',
})


def auto_describe_chart(cfg: dict, column_defs: dict | None = None) -> str:
    """Generate a plain-English description of a chart configuration.

    Example: "A bar chart showing count of students by Grade for This School Year,
    grouped by Race, at Lincoln Elementary."
    """
    col_defs = column_defs or {}
    chart_type = cfg.get('chart_type', 'bar')
    chart_type_name = _CHART_TYPE_NAME_MAP.get(chart_type, chart_type.replace('_', ' ').title())

    x_col = cfg.get('x_col', '')
    y_col = cfg.get('y_col', '')
    series_col = cfg.get('series_col', '') or cfg.get('stack_col', '')
    agg = cfg.get('agg', 'count')

    def _col_label(col_key):
        if not col_key:
            return None
        label = col_defs.get(col_key, '')
        if label:
            return str(label).split('\n')[0].strip()
        # Fall back to readable version of the key
        return col_key.replace('_', ' ').title()

    parts = [f'A {chart_type_name}']

    # What's being shown
    y_label = _col_label(y_col)
    if y_label and agg not in ('count', 'count_occurrence'):
        display_agg = agg.replace('_', ' ')
        parts.append(f'showing {display_agg} of {y_label}')
    elif y_label:
        parts.append(f'showing {y_label}')
    elif agg == 'count':
        parts.append('showing count of students')
    elif agg == 'count_occurrence':
        parts.append('showing count of occurrences')

    # What's on the X axis
    x_label = _col_label(x_col)
    if x_label:
        parts.append(f'by {x_label}')

    # Grouping
    s_label = _col_label(series_col)
    if s_label:
        parts.append(f'grouped by {s_label}')

    # School filter info
    school_mode = cfg.get('school_filter_mode', 'enrolled')
    source_filters = cfg.get('source_filters', {})
    school_name = ''
    if isinstance(source_filters, dict):
        school_val = source_filters.get('school', '')
        if isinstance(school_val, list) and school_val:
            school_name = school_val[0]
        elif isinstance(school_val, str) and school_val:
            school_name = school_val

    if school_name:
        scope = 'enrollment' if school_mode == 'enrolled' else 'tested'
        parts.append(f'at {school_name} ({scope} school)')

    # School year context
    if isinstance(source_filters, dict):
        sy = source_filters.get('school_year', '')
        if sy:
            if sy == '__sy_this__':
                parts.append('for This School Year')
            elif sy == '__sy_prev1__':
                parts.append('for Last School Year')
            elif sy == '__sy_prev2__':
                parts.append('for Two Years Prior')
            elif isinstance(sy, str) and sy:
                parts.append(f'for {sy}')

    return ' '.join(parts) + '.'

# Expose private names to sibling ``import *`` (PEP 328).
__all__ = [n for n in list(globals()) if not n.startswith('__')]
