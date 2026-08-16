"""
School-year alias resolution utilities.

Alias constants (SY_THIS_YEAR, SY_LAST_YEAR, SY_TWO_PRIOR) can be stored
in query/chart/filter configs instead of hard-coded year strings.
Call resolve_school_year() at query runtime to convert them to real values.
"""
from .constants import (
    SY_THIS_YEAR,
    SY_LAST_YEAR,
    SY_TWO_PRIOR,
    SY_THREE_PRIOR,
    SY_FOUR_PRIOR,
    SY_FIVE_PRIOR,
    SCHOOL_YEAR_ALIASES,
)

_ALIAS_OFFSETS = {
    SY_THIS_YEAR: 0,
    SY_LAST_YEAR: -1,
    SY_TWO_PRIOR: -2,
    SY_THREE_PRIOR: -3,
    SY_FOUR_PRIOR: -4,
    SY_FIVE_PRIOR: -5,
}


def _parse_start_year(school_year_str: str) -> int | None:
    """
    Extract the start year from a 'YYYY-YYYY' formatted school year string.
    Returns None if the string is not parseable.
    """
    if not school_year_str:
        return None
    try:
        return int(school_year_str[:4])
    except (ValueError, TypeError):
        return None


def _build_school_year(start: int) -> str:
    """Format an int start year as 'YYYY-YYYY' school year string."""
    return f"{start}-{start + 1}"


def resolve_school_year(value: str) -> str:
    """
    Resolve a school-year value, translating any alias constant to a real year.

    - If *value* is not an alias (e.g. '2024-2025'), it is returned unchanged.
    - If it *is* an alias, the function reads ProjectConfig.default_school_year
      and applies the offset (0 = current, -1 = last, -2 = two years prior).
    - If the config has no default_school_year, the alias is returned as-is so
      the caller can decide how to handle the missing config gracefully.

    Args:
        value: A school year string or one of the SY_* alias constants.

    Returns:
        Resolved school year string.
    """
    offset = _ALIAS_OFFSETS.get(value)
    if offset is None:
        return value  # not an alias — pass through

    try:
        from landing.utils import get_project_config
        cfg = get_project_config()
        default_year = cfg.default_school_year
    except Exception:
        return value  # can't resolve — return as-is

    start = _parse_start_year(default_year)
    if start is None:
        return value  # malformed config — return as-is

    return _build_school_year(start + offset)


def get_year_alias_map() -> dict:
    """
    Return a dict mapping real year strings to their alias constants for years
    that correspond to an alias under the current ProjectConfig.

    Returns:
        dict, e.g. {'2024-2025': '__sy_this__', '2023-2024': '__sy_prev1__', '2022-2023': '__sy_prev2__'}
    """
    result = {}
    for alias in _ALIAS_OFFSETS:
        try:
            real = resolve_school_year(alias)
            if real != alias:
                result[real] = alias
        except Exception:
            pass
    return result


def resolve_relative_column_key(col_key: str) -> str:
    """
    Resolve a relative column key to its specific year equivalent.

    e.g. 'test___sy_this___iready_reading_ss' -> 'test_2024_2025_iready_reading_ss'

    Returns the key unchanged if it contains no alias pattern or alias cannot be resolved.
    """
    for alias in _ALIAS_OFFSETS:
        if alias in col_key:
            real_year = resolve_school_year(alias)
            if real_year == alias:
                return col_key  # can't resolve
            year_underscored = real_year.replace('-', '_')
            return col_key.replace(alias, year_underscored)
    return col_key


_ALIAS_LABELS = {
    SY_THIS_YEAR: "This School Year",
    SY_LAST_YEAR: "Last School Year",
    SY_TWO_PRIOR: "Two Years Prior",
    SY_THREE_PRIOR: "Three Years Prior",
    SY_FOUR_PRIOR: "Four Years Prior",
    SY_FIVE_PRIOR: "Five Years Prior",
}


def get_year_labels_map(years_iterable) -> dict:
    """
    Return a dict mapping each real year string to a display label.

    Years that correspond to a school-year alias (relative to ProjectConfig's
    default_school_year) are shown as e.g. "This School Year".
    All other years map to themselves.

    Args:
        years_iterable: Any iterable of year strings like ['2024-2025', ...].

    Returns:
        dict, e.g. {'2024-2025': 'This School Year', '2023-2024': 'Last School Year', ...}
    """
    # Build reverse map: resolved_year -> alias_label
    resolved: dict[str, str] = {}
    for alias, label in _ALIAS_LABELS.items():
        try:
            real = resolve_school_year(alias)
            if real != alias:  # only if config exists and alias resolved
                resolved[real] = label
        except Exception:
            pass

    return {
        year: (resolved[year] if year in resolved else year)
        for year in years_iterable
    }


def get_school_year_choices(available_years: list[str]) -> list[tuple[str, str]]:
    """
    Build a dropdown choices list combining alias options and real year values.

    Alias options appear first, grouped under a separator, followed by the
    concrete years from the database.

    Args:
        available_years: List of real year strings (e.g. ['2024-2025', ...]).

    Returns:
        List of (value, label) tuples suitable for a <select> widget.
    """
    choices = []

    # Alias group
    alias_choices = [(k, v) for k, v in SCHOOL_YEAR_ALIASES.items()]
    choices.append(('School Year References', alias_choices))

    # Concrete years group
    if available_years:
        concrete = [(y, y) for y in sorted(available_years, reverse=True)]
        choices.append(('Specific School Years', concrete))

    return choices
