"""Structured CDE 5x5 status and change cut points for goal calculation."""

from __future__ import annotations

from typing import Any

from ca_dash.services.grid.templates import (
    ACADEMIC_CHANGE_DESC,
    CCI_CHANGE_DESC,
    CCI_STATUS_DESC,
    CHRONIC_CHANGE_DESC,
    CHRONIC_STATUS_DESC,
    ELPI_CHANGE_DESC,
    ELPI_STATUS_DESC,
    ELA11_STATUS_DESC,
    ELA38_STATUS_DESC,
    GRAD_CHANGE_DESC,
    GRAD_STATUS_DESC,
    SCIENCE_CHANGE_DESC,
    SCIENCE_STATUS_DESC,
    MATH11_STATUS_DESC,
    MATH38_STATUS_DESC,
    SUSP_ELEM_CHANGE_DESC,
    SUSP_HS_CHANGE_DESC,
    SUSP_LEA_ELEM_STATUS_DESC,
    SUSP_LEA_HS_CHANGE_DESC,
    SUSP_LEA_HS_STATUS_DESC,
    SUSP_MS_CHANGE_DESC,
    SUSP_SCHOOL_ELEM_STATUS_DESC,
    SUSP_SCHOOL_HS_STATUS_DESC,
    SUSP_SCHOOL_MS_STATUS_DESC,
    SUSP_UNIFIED_CHANGE_DESC,
    SUSP_UNIFIED_STATUS_DESC,
)


def _rng(min_val: float | None, max_val: float | None, label: str, description: str) -> dict[str, Any]:
    return {"min": min_val, "max": max_val, "label": label, "description": description}


def _status(
    ranges: list[tuple[float | None, float | None, str]],
    descriptions: list[str],
) -> list[dict[str, Any]]:
    return [
        _rng(lo, hi, label, descriptions[i])
        for i, (lo, hi, label) in enumerate(ranges)
    ]


def _change(
    ranges: list[tuple[float | None, float | None, str]],
    descriptions: list[str],
) -> list[dict[str, Any]]:
    return [
        _rng(lo, hi, label, descriptions[i])
        for i, (lo, hi, label) in enumerate(ranges)
    ]


# Row order: index 0 = top row on grid (Very Low for chronic, Very High for ELA)
CHRONIC_STATUS = _status(
    [
        (None, 2.5, "Very Low"),
        (2.6, 5.0, "Low"),
        (5.1, 10.0, "Medium"),
        (10.1, 20.0, "High"),
        (20.1, None, "Very High"),
    ],
    CHRONIC_STATUS_DESC,
)

ELPI_STATUS = _status(
    [
        (65.0, None, "Very High"),
        (55.0, 64.9, "High"),
        (45.0, 54.9, "Medium"),
        (35.0, 44.9, "Low"),
        (None, 34.9, "Very Low"),
    ],
    ELPI_STATUS_DESC,
)

ELA38_STATUS = _status(
    [
        (45.0, None, "Very High"),
        (10.0, 44.9, "High"),
        (-5.0, 9.9, "Medium"),
        (-70.0, -5.1, "Low"),
        (None, -70.1, "Very Low"),
    ],
    ELA38_STATUS_DESC,
)

ELA11_STATUS = _status(
    [
        (75.0, None, "Very High"),
        (30.0, 74.9, "High"),
        (0.0, 29.9, "Medium"),
        (-45.0, -0.1, "Low"),
        (None, -45.1, "Very Low"),
    ],
    ELA11_STATUS_DESC,
)

MATH38_STATUS = _status(
    [
        (35.0, None, "Very High"),
        (0.0, 34.9, "High"),
        (-25.0, -0.1, "Medium"),
        (-95.0, -25.1, "Low"),
        (None, -95.1, "Very Low"),
    ],
    MATH38_STATUS_DESC,
)

MATH11_STATUS = _status(
    [
        (25.0, None, "Very High"),
        (0.0, 24.9, "High"),
        (-60.0, -0.1, "Medium"),
        (-115.0, -60.1, "Low"),
        (None, -115.1, "Very Low"),
    ],
    MATH11_STATUS_DESC,
)

SUSP_SCHOOL_ELEM_STATUS = _status(
    [
        (None, 0.5, "Very Low"),
        (0.6, 1.0, "Low"),
        (1.1, 3.0, "Medium"),
        (3.1, 6.0, "High"),
        (6.1, None, "Very High"),
    ],
    SUSP_SCHOOL_ELEM_STATUS_DESC,
)

SUSP_SCHOOL_MS_STATUS = _status(
    [
        (None, 0.5, "Very Low"),
        (0.6, 2.0, "Low"),
        (2.1, 8.0, "Medium"),
        (8.1, 12.0, "High"),
        (12.1, None, "Very High"),
    ],
    SUSP_SCHOOL_MS_STATUS_DESC,
)

SUSP_SCHOOL_HS_STATUS = _status(
    [
        (None, 0.5, "Very Low"),
        (0.6, 1.5, "Low"),
        (1.6, 6.0, "Medium"),
        (6.1, 10.0, "High"),
        (10.1, None, "Very High"),
    ],
    SUSP_SCHOOL_HS_STATUS_DESC,
)

SUSP_UNIFIED_STATUS = _status(
    [
        (None, 1.0, "Very Low"),
        (1.1, 2.5, "Low"),
        (2.6, 4.5, "Medium"),
        (4.6, 8.0, "High"),
        (8.1, None, "Very High"),
    ],
    SUSP_UNIFIED_STATUS_DESC,
)

SUSP_LEA_ELEM_STATUS = _status(
    [
        (None, 0.5, "Very Low"),
        (0.6, 1.5, "Low"),
        (1.6, 3.0, "Medium"),
        (3.1, 6.0, "High"),
        (6.1, None, "Very High"),
    ],
    SUSP_LEA_ELEM_STATUS_DESC,
)

SUSP_LEA_HS_STATUS = _status(
    [
        (None, 1.5, "Very Low"),
        (1.6, 3.5, "Low"),
        (3.6, 6.0, "Medium"),
        (6.1, 9.0, "High"),
        (9.1, None, "Very High"),
    ],
    SUSP_LEA_HS_STATUS_DESC,
)

GRAD_STATUS = _status(
    [
        (95.0, None, "Very High"),
        (90.5, 94.9, "High"),
        (80.0, 90.4, "Medium"),
        (68.0, 79.9, "Low"),
        (None, 67.9, "Very Low"),
    ],
    GRAD_STATUS_DESC,
)

CCI_STATUS = _status(
    [
        (70.0, None, "Very High"),
        (55.0, 69.9, "High"),
        (35.0, 54.9, "Medium"),
        (10.0, 34.9, "Low"),
        (None, 9.9, "Very Low"),
    ],
    CCI_STATUS_DESC,
)

# Column order: index 0 = leftmost column on grid
ACADEMIC_CHANGE = _change(
    [
        (None, -15.1, "Declined Significantly"),
        (-15.0, -3.0, "Declined"),
        (-2.9, 2.9, "Maintained"),
        (3.0, 14.9, "Increased"),
        (15.0, None, "Increased Significantly"),
    ],
    ACADEMIC_CHANGE_DESC,
)

CHRONIC_CHANGE = _change(
    [
        (3.1, None, "Increased Significantly"),
        (0.5, 3.0, "Increased"),
        (-0.4, 0.4, "Maintained"),
        (-2.9, -0.5, "Declined"),
        (None, -3.0, "Declined Significantly"),
    ],
    CHRONIC_CHANGE_DESC,
)

ELPI_CHANGE = _change(
    [
        (None, -10.1, "Declined Significantly"),
        (-10.0, -2.0, "Declined"),
        (-1.9, 1.9, "Maintained"),
        (2.0, 9.9, "Increased"),
        (10.0, None, "Increased Significantly"),
    ],
    ELPI_CHANGE_DESC,
)

SUSP_ELEM_CHANGE = _change(
    [
        (2.1, None, "Increased Significantly"),
        (0.3, 2.0, "Increased"),
        (-0.2, 0.2, "Maintained"),
        (-0.9, -0.3, "Declined"),
        (None, -1.0, "Declined Significantly"),
    ],
    SUSP_ELEM_CHANGE_DESC,
)

SUSP_MS_CHANGE = _change(
    [
        (4.1, None, "Increased Significantly"),
        (0.3, 4.0, "Increased"),
        (-0.2, 0.2, "Maintained"),
        (-2.9, -0.3, "Declined"),
        (None, -3.0, "Declined Significantly"),
    ],
    SUSP_MS_CHANGE_DESC,
)

SUSP_HS_CHANGE = _change(
    [
        (3.1, None, "Increased Significantly"),
        (0.3, 3.0, "Increased"),
        (-0.2, 0.2, "Maintained"),
        (-1.9, -0.3, "Declined"),
        (None, -2.0, "Declined Significantly"),
    ],
    SUSP_HS_CHANGE_DESC,
)

SUSP_UNIFIED_CHANGE = _change(
    [
        (2.1, None, "Increased Significantly"),
        (0.3, 2.0, "Increased"),
        (-0.2, 0.2, "Maintained"),
        (-1.9, -0.3, "Declined"),
        (None, -2.0, "Declined Significantly"),
    ],
    SUSP_UNIFIED_CHANGE_DESC,
)

SUSP_LEA_HS_CHANGE = _change(
    [
        (3.1, None, "Increased Significantly"),
        (0.5, 3.0, "Increased"),
        (-0.4, 0.4, "Maintained"),
        (-2.9, -0.5, "Declined"),
        (None, -3.0, "Declined Significantly"),
    ],
    SUSP_LEA_HS_CHANGE_DESC,
)

GRAD_CHANGE = _change(
    [
        (None, -5.1, "Declined Significantly"),
        (-5.0, -1.0, "Declined"),
        (-0.9, 0.9, "Maintained"),
        (1.0, 4.9, "Increased"),
        (5.0, None, "Increased Significantly"),
    ],
    GRAD_CHANGE_DESC,
)

CCI_CHANGE = _change(
    [
        (None, -9.1, "Declined Significantly"),
        (-9.0, -2.0, "Declined"),
        (-1.9, 1.9, "Maintained"),
        (2.0, 8.9, "Increased"),
        (9.0, None, "Increased Significantly"),
    ],
    CCI_CHANGE_DESC,
)

SCIENCE_STATUS = _status(
    [
        (65.0, None, "Very High"),
        (55.0, 64.9, "High"),
        (45.0, 54.9, "Medium"),
        (35.0, 44.9, "Low"),
        (None, 34.9, "Very Low"),
    ],
    SCIENCE_STATUS_DESC,
)

SCIENCE_CHANGE = _change(
    [
        (None, -5.1, "Declined Significantly"),
        (-5.0, -2.0, "Declined"),
        (-1.9, 1.9, "Maintained"),
        (2.0, 4.9, "Increased"),
        (5.0, None, "Increased Significantly"),
    ],
    SCIENCE_CHANGE_DESC,
)


THRESHOLD_CONFIGS: dict[str, dict[str, Any]] = {
    "chronic": {
        "status_ranges": CHRONIC_STATUS,
        "change_ranges": CHRONIC_CHANGE,
        "lower_is_better": True,
    },
    "elpi": {
        "status_ranges": ELPI_STATUS,
        "change_ranges": ELPI_CHANGE,
        "lower_is_better": False,
    },
    "ela_38": {
        "status_ranges": ELA38_STATUS,
        "change_ranges": ACADEMIC_CHANGE,
        "lower_is_better": False,
    },
    "ela_11": {
        "status_ranges": ELA11_STATUS,
        "change_ranges": ACADEMIC_CHANGE,
        "lower_is_better": False,
    },
    "math_38": {
        "status_ranges": MATH38_STATUS,
        "change_ranges": ACADEMIC_CHANGE,
        "lower_is_better": False,
    },
    "math_11": {
        "status_ranges": MATH11_STATUS,
        "change_ranges": ACADEMIC_CHANGE,
        "lower_is_better": False,
    },
    "susp_school_elem": {
        "status_ranges": SUSP_SCHOOL_ELEM_STATUS,
        "change_ranges": SUSP_ELEM_CHANGE,
        "lower_is_better": True,
    },
    "susp_school_middle": {
        "status_ranges": SUSP_SCHOOL_MS_STATUS,
        "change_ranges": SUSP_MS_CHANGE,
        "lower_is_better": True,
    },
    "susp_school_hs": {
        "status_ranges": SUSP_SCHOOL_HS_STATUS,
        "change_ranges": SUSP_HS_CHANGE,
        "lower_is_better": True,
    },
    "susp_unified": {
        "status_ranges": SUSP_UNIFIED_STATUS,
        "change_ranges": SUSP_UNIFIED_CHANGE,
        "lower_is_better": True,
    },
    "susp_lea_elem": {
        "status_ranges": SUSP_LEA_ELEM_STATUS,
        "change_ranges": SUSP_UNIFIED_CHANGE,
        "lower_is_better": True,
    },
    "susp_lea_hs": {
        "status_ranges": SUSP_LEA_HS_STATUS,
        "change_ranges": SUSP_LEA_HS_CHANGE,
        "lower_is_better": True,
    },
    "grad": {
        "status_ranges": GRAD_STATUS,
        "change_ranges": GRAD_CHANGE,
        "lower_is_better": False,
    },
    "cci": {
        "status_ranges": CCI_STATUS,
        "change_ranges": CCI_CHANGE,
        "lower_is_better": False,
    },
    "science": {
        "status_ranges": SCIENCE_STATUS,
        "change_ranges": SCIENCE_CHANGE,
        "lower_is_better": False,
    },
}


def resolve_thresholds(template_key: str) -> dict[str, Any]:
    if template_key not in THRESHOLD_CONFIGS:
        return THRESHOLD_CONFIGS["chronic"]
    return THRESHOLD_CONFIGS[template_key]


def status_threshold(row: int, template_key: str, lower_is_better: bool | None = None) -> float | None:
    """Return the numeric edge needed to enter the given status row."""
    cfg = resolve_thresholds(template_key)
    if lower_is_better is None:
        lower_is_better = cfg["lower_is_better"]
    rng = cfg["status_ranges"][row]
    if lower_is_better:
        return rng["max"] if rng["max"] is not None else rng["min"]
    return rng["min"] if rng["min"] is not None else rng["max"]


def change_range(col: int, template_key: str) -> dict[str, Any]:
    """Return min/max/label/description for the given change column."""
    cfg = resolve_thresholds(template_key)
    return cfg["change_ranges"][col]


def value_in_range(value: float, rng: dict[str, Any]) -> bool:
    lo = rng["min"]
    hi = rng["max"]
    if lo is not None and value < lo:
        return False
    if hi is not None and value > hi:
        return False
    return True


def change_level_from_value(value: float | None, template_key: str) -> int:
    """Map YoY change points to CDE change level 1–5 (grid column index + 1)."""
    if value is None:
        return 0
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0
    if v != v:
        return 0
    cfg = resolve_thresholds(template_key)
    for i, rng in enumerate(cfg["change_ranges"]):
        if value_in_range(v, rng):
            return i + 1
    return 0
