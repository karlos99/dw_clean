"""Shared student status filter semantics (aligned with data exploration).

Rules:
- ``status`` key absent  → default ``Active``
- explicit empty (``None``, ``''``, ``[]``) → all statuses (no WHERE clause)
- otherwise → filter to the given value(s)
"""

from __future__ import annotations

from typing import Any

STATUS_KEYS = ('status', 'current_student.status')

_UNSET = object()


def normalize_status_value(filters: dict | None, *, pop: bool = True) -> str | list | None:
    """Return status filter value from a filters dict.

    Returns:
        ``'Active'`` when the status key is absent (default).
        ``None`` when status is explicitly empty (all statuses).
        ``str`` or ``list`` for a specific filter.
    """
    if not filters:
        return 'Active'

    found_key = next((k for k in STATUS_KEYS if k in filters), None)
    if found_key is None:
        return 'Active'

    val = filters.pop(found_key) if pop else filters[found_key]

    if val is None or val == '':
        return None
    if isinstance(val, list):
        cleaned = [s for s in val if s]
        return cleaned if cleaned else None
    return val


def append_status_where(
    where_parts: list[str],
    params: list,
    status_val: str | list | None,
    *,
    alias: str = 'cs',
) -> None:
    """Append a ``cs.status`` predicate unless *status_val* is ``None`` (all statuses)."""
    if status_val is None:
        return
    col = f'{alias}.status'
    if isinstance(status_val, list):
        if len(status_val) == 1:
            where_parts.append(f'{col} = %s')
            params.append(status_val[0])
        else:
            where_parts.append(f'{col} = ANY(%s)')
            params.append(status_val)
    else:
        where_parts.append(f'{col} = %s')
        params.append(status_val)


def apply_status_filter(
    where_parts: list[str],
    params: list,
    filters: dict | None,
    *,
    alias: str = 'cs',
    pop: bool = True,
) -> None:
    """Pop status from *filters* (optional) and append the WHERE clause."""
    status_val = normalize_status_value(filters, pop=pop)
    append_status_where(where_parts, params, status_val, alias=alias)


def append_quoted_status_where(
    where_parts: list[str],
    params: list,
    status_val: str | list | None,
    *,
    table: str = 'current_student',
) -> None:
    """Append ``"table"."status"`` predicate for dataset SQL compilation."""
    if status_val is None:
        return
    col = f'"{table}"."status"'
    if isinstance(status_val, list):
        if len(status_val) == 1:
            where_parts.append(f'{col} = %s')
            params.append(status_val[0])
        else:
            where_parts.append(f'{col} = ANY(%s)')
            params.append(status_val)
    else:
        where_parts.append(f'{col} = %s')
        params.append(status_val)


def prepare_interactive_filters(raw_filters: dict | None, dataset, active_tables: set) -> tuple[dict, str | list | None]:
    """Extract status before ``resolve_dataset_filters`` drops empty values."""
    from dataviz.utils.compiler import resolve_dataset_filters

    filters_copy = dict(raw_filters or {})
    status_val = normalize_status_value(filters_copy, pop=True)
    resolved = resolve_dataset_filters(filters_copy, dataset, active_tables)
    return resolved, status_val
