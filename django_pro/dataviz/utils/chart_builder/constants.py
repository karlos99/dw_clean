"""Shared constants (palettes, PL scales, labels)."""
import json
import random
import re

try:
    from dataexploration.school_year_utils import resolve_relative_column_key
except Exception:  # pragma: no cover
    def resolve_relative_column_key(col_key: str) -> str:
        return col_key


# ── Meta-column constants ─────────────────────────────────────────────────────

_YEAR_RE         = re.compile(r'^(?:test|mark)_(\d{4})_(\d{4})_')
_META_DIM_COLS   = {'_assessment', '_test_school_year', '_test_window', '_test_pl', '_mark_school_year', '_mark_period'}
_META_SCORE_COLS = {'_test_score', '_mark_score'}
_MELT_MAX_ROWS   = 600000  # safety cap: refuse melt if result would exceed this many rows

_META_LABEL_MAP = {
    '_assessment':       'Assessment',
    '_test_school_year': 'School Year',
    '_test_window':      'Test Window / Period',
    '_test_score':       'Test Score (SS)',
    '_test_pl':          'Performance Level',
    '_mark_school_year': 'Mark School Year',
    '_mark_period':      'Mark Period',
    '_mark_score':       'Mark Value',
}

# ── Colour palettes ───────────────────────────────────────────────────────────

PALETTES = {
    'default': ['#636EFA', '#EF553B', '#00CC96', '#AB63FA', '#FFA15A', '#19D3F3', '#FF6692', '#B6E880'],
    'blue':    ['#4472c4', '#5b9bd5', '#264478', '#70ad47', '#ffc000', '#f4522d', '#9e480e', '#636363'],
    'warm':    ['#e74c3c', '#e67e22', '#f39c12', '#f1c40f', '#ff5733', '#c0392b', '#d35400', '#e8b800'],
    'earth':   ['#8b7355', '#a9956c', '#c8a96e', '#7f6000', '#5d4e37', '#bfa98e', '#6d4c41', '#4e342e'],
    'green':   ['#27ae60', '#2ecc71', '#1abc9c', '#16a085', '#52be80', '#76d7c4', '#239b56', '#0e6655'],
    'purple':  ['#9b59b6', '#8e44ad', '#a569bd', '#6c3483', '#bb8fce', '#7d3c98', '#d2b4de', '#76448a'],
    'mono':    ['#2c3e50', '#7f8c8d', '#95a5a6', '#bdc3c7', '#34495e', '#566573', '#808b96', '#1a252f'],
    'assessment': ['#27ae60', '#3498db', '#f39c12', '#e74c3c', '#9b59b6', '#95a5a6', '#636363', '#1abc9c'],
    'traffic':    ['#27ae60', '#f39c12', '#e74c3c', '#c0392b', '#2ecc71', '#f1c40f', '#d35400', '#7f8c8d'],
}

# Pre-built semantic colour maps for common data values.
# When a chart's series/category matches one of these keys the colour is fixed
# regardless of palette, giving users instant meaningful colour coding.
SEMANTIC_COLORS = {
    # Assessment performance levels
    'Above Benchmark':          '#27ae60',
    'At Benchmark':             '#3498db',
    'Below Benchmark':          '#f39c12',
    'Well Below Benchmark':     '#e74c3c',
    'Exceeded':                 '#1a8a4a',
    'Met':                      '#27ae60',
    'Nearly Met':               '#f39c12',
    'Not Met':                  '#e74c3c',
    'Proficient':               '#27ae60',
    'Basic':                    '#3498db',
    'Below Basic':              '#f39c12',
    'Far Below Basic':          '#e74c3c',
    'Advanced':                 '#1a8a4a',
    # Boolean / Yes-No
    'Yes':                      '#27ae60',
    'No':                       '#95a5a6',
    # Gender
    'Male':                     '#3498db',
    'Female':                   '#e91e8e',
    # Common grades
    'A':  '#27ae60', 'B': '#3498db', 'C': '#f39c12',
    'D':  '#e67e22', 'F': '#e74c3c',
    'A+': '#1a8a4a', 'A-': '#2ecc71', 'B+': '#2980b9',
    'B-': '#5dade2', 'C+': '#d4ac0d', 'C-': '#f1c40f',
    'D+': '#ca6f1e', 'D-': '#e74c3c',
}

# ── Progress Growth constants ─────────────────────────────────────────────────

# Fixed semantic colours — Growth=green, Stickers=grey, Sliders=orange
_PG_COLORS = {
    'Growth':   '#22c55e',
    'Stickers': '#94a3b8',
    'Sliders':  '#f97316',
}

# Known PL orderings (lowest → highest) for auto-detection.
_KNOWN_PL_SCALES = [
    ['Well Below Benchmark', 'Below Benchmark', 'At Benchmark', 'Above Benchmark'],
    ['Not Met', 'Nearly Met', 'Met', 'Exceeded'],
    ['Far Below Basic', 'Below Basic', 'Basic', 'Proficient', 'Advanced'],
    ['Below Basic', 'Basic', 'Proficient', 'Advanced'],
    ['1', '2', '3', '4'],
]

CHART_TYPES = [
    # (key, label, icon_class, group)
    ('bar',          'Bar',           'chart-bar',           'Comparison'),
    ('assessment_compare', 'Assessment Compare', 'chart-assess', 'Comparison'),
    ('progress_growth',    'Progress Growth',    'chart-growth',  'Comparison'),
    ('summary_table',      'Summary Table',      'chart-table',   'Comparison'),
    ('dynamic_summary_table', 'Dynamic Summary Table', 'chart-dyntable', 'Comparison'),
    ('waterfall',    'Waterfall',     'chart-waterfall',     'Comparison'),
    ('line',         'Line',          'chart-line',          'Trend'),
    ('date_series',  'Dates',         'chart-dates',          'Trend'),
    ('parliament',   'Parliament',    'chart-parliament',    'Proportion'),
    ('spline',       'Parliament',    'chart-parliament',    'Proportion'),   # backward compat alias
    ('pie',          'Pie',           'chart-pie',           'Proportion'),
    ('donut',        'Donut',         'chart-donut',         'Proportion'),
    ('funnel',       'Funnel',        'chart-funnel',        'Proportion'),
    ('treemap',      'Treemap',       'chart-treemap',       'Proportion'),
    ('scatter',      'Scatter',       'chart-scatter',       'Relationship'),
    ('jitter',       'Jitter',        'chart-jitter',        'Relationship'),
    ('bubble',       'Bubble',        'chart-bubble',        'Relationship'),
    ('heatmap',      'Heatmap',       'chart-heatmap',       'Relationship'),
    ('histogram',    'Histogram',     'chart-histogram',     'Distribution'),
    ('box',          'Box Plot',      'chart-box',           'Distribution'),
    ('radar',        'Radar',         'chart-radar',         'Advanced'),
    ('gauge',        'Gauge',         'chart-gauge',         'Advanced'),
]


# ── Chart Recommendation Engine (Phase 6) ──────────────────────────────────────

# Heuristic rules: column patterns → recommended chart types with scores.
# Each rule is (condition_fn, chart_key, score_adjustment, reason).
_RECOMMENDATION_RULES = []

# Expose private names to sibling ``import *`` (PEP 328).
__all__ = [n for n in list(globals()) if not n.startswith('__')]
