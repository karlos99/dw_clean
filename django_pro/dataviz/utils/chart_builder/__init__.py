"""
chart_builder — Server-side ECharts option builder.

Public API is unchanged::

    from dataviz.utils.chart_builder import build_option, CHART_TYPES, …

Layout
------
constants.py          Palettes, PL scales, CHART_TYPES
common.py             Shared helpers (agg, theme, axes, tooltips, maps)
charts.py             Standard chart builders (bar, line, pie, scatter, …)
tables.py             Summary / dynamic summary tables
progress_growth.py    Progress-growth classify + matrix/chart option
assessment_compare.py Assessment-compare grouped/stacked bars
build.py              ``build_option()`` entry point
recommend.py          ``recommend_chart_types`` / ``auto_describe_chart``
"""
from .constants import *  # noqa: F401,F403
from .common import *  # noqa: F401,F403
from .charts import *  # noqa: F401,F403
from .tables import *  # noqa: F401,F403
from .progress_growth import *  # noqa: F401,F403
from .assessment_compare import *  # noqa: F401,F403
from .build import build_option  # noqa: F401
from .recommend import *  # noqa: F401,F403

__all__ = [
    'PALETTES',
    'SEMANTIC_COLORS',
    'CHART_TYPES',
    'build_option',
    'recommend_chart_types',
    'auto_describe_chart',
    'filter_progress_growth_matrix_rows',
    'prepare_progress_growth_rows',
    'consolidate_progress_growth_rows',
    'classify_progress_growth_row',
    'pg_all_key',
]
