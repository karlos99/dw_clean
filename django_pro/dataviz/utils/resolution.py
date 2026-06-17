"""
Resolution utilities for mapping dataset columns to Data Exploration columns.
"""
from dataviz.utils.data_loader import _CS_COLUMNS


def resolve_exploration_column(dataset, dataset_col: str) -> str | None:
    """
    Resolve a dataset column (e.g. 'current_student.grade' or 'tests.test_window')
    to a corresponding Data Exploration column.
    """
    if not dataset_col:
        return None

    # 1. Check explicit user mapping overrides (dataset_col → exploration_col)
    mappings = getattr(dataset, 'column_mappings', {}) or {}
    if dataset_col in mappings:
        return mappings[dataset_col]

    # 2. For JSONB columns like "current_student.add_data->'key'", the suffix after
    #    the last dot is "add_data->'key'", not a clean column name.  Strip the JSONB
    #    arrow path to get the parent column name for the suffix check below.
    if '.' in dataset_col:
        _tbl_part, col_part = dataset_col.split('.', 1)
    else:
        col_part = dataset_col

    if '->' in col_part:
        # JSONB extraction — the key itself is not an exploration column
        clean_col = col_part.split('->', 1)[1].strip("'\"")
    else:
        clean_col = col_part

    # 3. Check if suffix matches a standard exploration column
    if clean_col in _CS_COLUMNS:
        return clean_col

    # 4. Check if suffix is a dynamic test/mark column (e.g. test_2025_2026_...)
    import re
    if clean_col.startswith('test_') or clean_col.startswith('mark_'):
        if re.match(r'^(test|mark)_(?:\d{4}|__sy_)', clean_col):
            return clean_col

    return None
