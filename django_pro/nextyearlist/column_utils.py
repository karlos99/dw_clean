"""Dynamic column utilities for the Next Year List app.

Delegates test/mark column generation to dataexploration, then injects
NYL-specific column definitions (next_year_school, math_selection).
"""
from dataexploration.column_utils import get_test_columns, get_mark_columns, _get_display_name
from .constants import COLUMN_DEFINITIONS


def get_nyl_test_columns():
    return get_test_columns()


def get_nyl_mark_columns():
    return get_mark_columns()
