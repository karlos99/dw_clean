"""Dynamic column utilities for the Next Year List app.

Delegates test/mark column generation to dataexploration, then injects
NYL-specific column definitions (next_year_school, math_selection).
"""
from dataexploration.column_utils import get_test_columns, get_mark_columns, _get_display_name
from .constants import COLUMN_DEFINITIONS

NYL_JOIN = (
    " LEFT JOIN nextyearlist_placement nyl ON nyl.ssid = cs.ssid AND nyl.school_year = cs.school_year"
    " LEFT JOIN mathr_recommendation mr ON mr.ssid = cs.ssid AND mr.school_year = cs.school_year"
)

def get_nyl_test_columns(user=None, column_filters=None):
    from .view_modules.student_list_views import _apply_nyl_school_filter
    where_clauses = []
    params = []
    if user:
        _apply_nyl_school_filter(where_clauses, params, user)
    
    return get_test_columns(
        column_filters=column_filters,
        extra_join_sql=NYL_JOIN,
        extra_where_clauses=where_clauses,
        extra_params=params
    )

def get_nyl_mark_columns(user=None, column_filters=None):
    from .view_modules.student_list_views import _apply_nyl_school_filter
    where_clauses = []
    params = []
    if user:
        _apply_nyl_school_filter(where_clauses, params, user)
    
    return get_mark_columns(
        column_filters=column_filters,
        extra_join_sql=NYL_JOIN,
        extra_where_clauses=where_clauses,
        extra_params=params
    )
