"""CDE 5x5 color placement grids from fivebyfivecolortables.asp."""

from __future__ import annotations

# Color ints: 1=Red, 2=Orange, 3=Yellow, 4=Green, 5=Blue, 0=N/A (no color)

STATUS_ROWS = ["Very Low", "Low", "Medium", "High", "Very High"]
STATUS_ROWS_ELPI = ["Very High", "High", "Medium", "Low", "Very Low"]

CHANGE_COLS_INCREASE_BAD = [
    "Increased Significantly",
    "Increased",
    "Maintained",
    "Declined",
    "Declined Significantly",
]

CHANGE_COLS_ELPI = [
    "Declined Significantly",
    "Declined",
    "Maintained",
    "Increased",
    "Increased Significantly",
]

# CDE status / change justifications (fivebyfivecolortables.asp)
CHRONIC_STATUS_DESC = [
    "2.5% or less in current year",
    "2.6% to 5.0% in current year",
    "5.1% to 10.0% in current year",
    "10.1% to 20.0% in current year",
    "20.1% or greater in current year",
]
CHRONIC_CHANGE_DESC = [
    "Increased by 3.1 p.pts or more from prior year",
    "Increased by 0.5 to 3.0 p.pts from prior year",
    "Maintained (±0.4 p.pts or fewer) from prior year",
    "Declined by 0.5 to 2.9 p.pts from prior year",
    "Declined by 3.0 p.pts or more from prior year",
]

ELPI_STATUS_DESC = [
    "65.0% or greater in current year",
    "55.0% to 64.9% in current year",
    "45.0% to 54.9% in current year",
    "35.0% to 44.9% in current year",
    "34.9% or less in current year",
]
ELPI_CHANGE_DESC = [
    "Declined by 10.1 p.pts or more from prior year",
    "Declined by 2.0 to 10.0 p.pts from prior year",
    "Maintained (±1.9 p.pts or fewer) from prior year",
    "Increased by 2.0 to 9.9 p.pts from prior year",
    "Increased by 10.0 p.pts or more from prior year",
]

ACADEMIC_CHANGE_DESC = [
    "Declined by 15.1 points or more from prior year",
    "Declined by 3.0 to 15.0 points from prior year",
    "Maintained (±2.9 points or fewer) from prior year",
    "Increased by 3.0 to 14.9 points from prior year",
    "Increased by 15.0 points or more from prior year",
]

SCIENCE_STATUS_DESC = [
    "65.0 Science Points or more in current year",
    "55.0 to 64.9 Science Points in current year",
    "45.0 to 54.9 Science Points in current year",
    "35.0 to 44.9 Science Points in current year",
    "34.9 Science Points or fewer in current year",
]
SCIENCE_CHANGE_DESC = [
    "Declined by 5.1 points or more from prior year",
    "Declined by 2.0 to 5.0 points from prior year",
    "Declined or increased by 1.9 points or fewer from prior year",
    "Increased by 2.0 to 4.9 points from prior year",
    "Increased by 5.0 points or more from prior year",
]

ELA38_STATUS_DESC = [
    "+45.0 points or more in Current Year",
    "+10.0 to +44.9 points in Current Year",
    "-5.0 points to +9.9 points in Current Year",
    "-5.1 to -70.0 points in Current Year",
    "-70.1 points or fewer in Current Year",
]

ELA11_STATUS_DESC = [
    "+75.0 points or more in Current Year",
    "+30.0 to +74.9 points in Current Year",
    "0.0 to +29.9 points in Current Year",
    "-0.1 to -45.0 points in Current Year",
    "-45.1 points or fewer in Current Year",
]

MATH38_STATUS_DESC = [
    "+35.0 points or more in Current Year",
    "0.0 to +34.9 points in Current Year",
    "-0.1 to -25.0 points in Current Year",
    "-25.1 to -95.0 points in Current Year",
    "-95.1 points or fewer in Current Year",
]

MATH11_STATUS_DESC = [
    "+25.0 points or more in Current Year",
    "0.0 to +24.9 points in Current Year",
    "-0.1 to -60.0 points in Current Year",
    "-60.1 to -115.0 points in Current Year",
    "-115.1 points or fewer in Current Year",
]

SUSP_ELEM_CHANGE_DESC = [
    "Increased by 2.1 p.pts or more",
    "Increased by 0.3 to 2.0 p.pts",
    "Maintained (±0.2 p.pts or fewer)",
    "Declined by 0.3 to 0.9 p.pts",
    "Declined by 1.0 p.pts or more",
]

SUSP_MS_CHANGE_DESC = [
    "Increased by 4.1 p.pts or more",
    "Increased by 0.3 to 4.0 p.pts",
    "Maintained (±0.2 p.pts or fewer)",
    "Declined by 0.3 to 2.9 p.pts",
    "Declined by 3.0 p.pts or more",
]

SUSP_HS_CHANGE_DESC = [
    "Increased by 3.1 p.pts or more",
    "Increased by 0.3 to 3.0 p.pts",
    "Maintained (±0.2 p.pts or fewer)",
    "Declined by 0.3 to 1.9 p.pts",
    "Declined by 2.0 p.pts or more",
]

SUSP_UNIFIED_CHANGE_DESC = [
    "Increased by 2.1 p.pts or more",
    "Increased by 0.3 to 2.0 p.pts",
    "Maintained (±0.2 p.pts or fewer)",
    "Declined by 0.3 to 1.9 p.pts",
    "Declined by 2.0 p.pts or more",
]

SUSP_LEA_HS_CHANGE_DESC = [
    "Increased by 3.1 p.pts or more",
    "Increased by 0.5 to 3.0 p.pts",
    "Maintained (±0.4 p.pts or fewer)",
    "Declined by 0.5 to 2.9 p.pts",
    "Declined by 3.0 p.pts or more",
]

SUSP_LEA_ELEM_STATUS_DESC = [
    "0.5% or less in Current Year",
    "0.6% to 1.5% in Current Year",
    "1.6% to 3.0% in Current Year",
    "3.1% to 6.0% in Current Year",
    "6.1% or greater in Current Year",
]

SUSP_LEA_HS_STATUS_DESC = [
    "1.5% or less in Current Year",
    "1.6% to 3.5% in Current Year",
    "3.6% to 6.0% in Current Year",
    "6.1% to 9.0% in Current Year",
    "9.1% or greater in Current Year",
]

SUSP_UNIFIED_STATUS_DESC = [
    "1.0% or less in Current Year",
    "1.1% to 2.5% in Current Year",
    "2.6% to 4.5% in Current Year",
    "4.6% to 8.0% in Current Year",
    "8.1% or greater in Current Year",
]

SUSP_SCHOOL_ELEM_STATUS_DESC = [
    "0.5% or less in Current Year",
    "0.6% to 1.0% in Current Year",
    "1.1% to 3.0% in Current Year",
    "3.1% to 6.0% in Current Year",
    "6.1% or greater in Current Year",
]

SUSP_SCHOOL_MS_STATUS_DESC = [
    "0.5% or less in Current Year",
    "0.6% to 2.0% in Current Year",
    "2.1% to 8.0% in Current Year",
    "8.1% to 12.0% in Current Year",
    "12.1% or greater in Current Year",
]

SUSP_SCHOOL_HS_STATUS_DESC = [
    "0.5% or less in Current Year",
    "0.6% to 1.5% in Current Year",
    "1.6% to 6.0% in Current Year",
    "6.1% to 10.0% in Current Year",
    "10.1% or greater in Current Year",
]

GRAD_STATUS_DESC = [
    "95.0% or greater in Current Year",
    "90.5% to 94.9% in Current Year",
    "80.0% to 90.4% in Current Year",
    "68.0% to 79.9% in Current Year",
    "67.9% or less in Current Year",
]

GRAD_CHANGE_DESC = [
    "Declined by 5.1 p.pts or more from prior year",
    "Declined by 1.0 to 5.0 p.pts from prior year",
    "Maintained (±0.9 p.pts or fewer) from prior year",
    "Increased by 1.0 to 4.9 p.pts from prior year",
    "Increased by 5.0 p.pts or more from prior year",
]

CCI_STATUS_DESC = [
    "70.0% or greater in Current Year",
    "55.0% to 69.9% in Current Year",
    "35.0% to 54.9% in Current Year",
    "10.0% to 34.9% in Current Year",
    "9.9% or lower in Current Year",
]

CCI_CHANGE_DESC = [
    "Declined by 9.1 p.pts or more from prior year",
    "Declined by 2.0 to 9.0 p.pts from prior year",
    "Maintained (±1.9 p.pts or fewer) from prior year",
    "Increased by 2.0 to 8.9 p.pts from prior year",
    "Increased by 9.0 p.pts or more from prior year",
]


def _box_map(status_top_is_low: bool = True) -> dict[int, tuple[int, int]]:
    """Map CDE box numbers (10–50 top row … 210–250 bottom) to grid row/col indices."""
    del status_top_is_low  # row order follows CDE box table, not parameter name
    result: dict[int, tuple[int, int]] = {}
    for status in range(1, 6):
        row = 5 - status
        for change in range(1, 6):
            col = change - 1
            box = (5 - status) * 50 + change * 10
            result[box] = (row, col)
    return result


def _template(
    cells: list[list[int]],
    status_labels: list[str],
    change_labels: list[str],
    status_descriptions: list[str],
    change_descriptions: list[str],
    title: str,
    logic_summary: str,
    status_top_is_low: bool = True,
    axis_status_title: str = "Status Level",
    axis_change_title: str = "Change Level",
) -> dict:
    return {
        "title": title,
        "logic_summary": logic_summary,
        "axis_status_title": axis_status_title,
        "axis_change_title": axis_change_title,
        "status_labels": status_labels,
        "change_labels": change_labels,
        "status_descriptions": status_descriptions,
        "change_descriptions": change_descriptions,
        "cells": cells,
        "box_map": _box_map(status_top_is_low),
    }


CHRONIC = _template(
    cells=[[3, 4, 5, 5, 5], [2, 3, 4, 4, 5], [2, 2, 3, 4, 4], [1, 2, 2, 3, 3], [1, 1, 1, 2, 3]],
    status_labels=STATUS_ROWS,
    change_labels=CHANGE_COLS_INCREASE_BAD,
    status_descriptions=CHRONIC_STATUS_DESC,
    change_descriptions=CHRONIC_CHANGE_DESC,
    title="Chronic Absenteeism",
    logic_summary="Rows = current-year absenteeism rate (Status). Columns = year-over-year change in rate. Lower absenteeism and decline are better.",
)

ELPI = _template(
    cells=[[3, 4, 5, 5, 5], [2, 3, 4, 4, 5], [2, 2, 3, 4, 4], [1, 2, 2, 3, 3], [1, 1, 1, 2, 3]],
    status_labels=STATUS_ROWS_ELPI,
    change_labels=CHANGE_COLS_ELPI,
    status_descriptions=ELPI_STATUS_DESC,
    change_descriptions=ELPI_CHANGE_DESC,
    title="English Learner Progress (ELPI)",
    logic_summary="Rows = current-year ELPI rate (Status). Columns = change from prior year. Higher progress and increase are better.",
    status_top_is_low=False,
)

ELA_38 = _template(
    cells=[[4, 4, 5, 5, 5], [4, 4, 4, 4, 5], [3, 3, 3, 4, 4], [2, 2, 2, 3, 3], [1, 1, 1, 2, 2]],
    status_labels=STATUS_ROWS_ELPI,
    change_labels=CHANGE_COLS_ELPI,
    status_descriptions=ELA38_STATUS_DESC,
    change_descriptions=ACADEMIC_CHANGE_DESC,
    title="ELA: Grades 3–8",
    logic_summary="Rows = distance from standard (Status). Columns = change in points from prior year. Applies to grades 3–8 Smarter Balanced / CAA results.",
    status_top_is_low=False,
)

ELA_11 = _template(
    cells=[[4, 4, 5, 5, 5], [4, 4, 4, 4, 5], [3, 3, 3, 4, 4], [2, 2, 2, 3, 3], [1, 1, 1, 2, 2]],
    status_labels=STATUS_ROWS_ELPI,
    change_labels=CHANGE_COLS_ELPI,
    status_descriptions=ELA11_STATUS_DESC,
    change_descriptions=ACADEMIC_CHANGE_DESC,
    title="ELA: Grade 11",
    logic_summary="Rows = distance from standard (Status). Columns = change in points from prior year. Grade 11 uses separate cut points.",
    status_top_is_low=False,
)

MATH_38 = _template(
    cells=[[4, 4, 5, 5, 5], [4, 4, 4, 4, 5], [3, 3, 3, 4, 4], [2, 2, 2, 3, 3], [1, 1, 1, 2, 2]],
    status_labels=STATUS_ROWS_ELPI,
    change_labels=CHANGE_COLS_ELPI,
    status_descriptions=MATH38_STATUS_DESC,
    change_descriptions=ACADEMIC_CHANGE_DESC,
    title="Math: Grades 3–8",
    logic_summary="Rows = distance from standard (Status). Columns = change in points from prior year. Applies to grades 3–8.",
    status_top_is_low=False,
)

MATH_11 = _template(
    cells=[[4, 4, 5, 5, 5], [4, 4, 4, 4, 5], [3, 3, 3, 4, 4], [2, 2, 2, 3, 3], [1, 1, 1, 2, 2]],
    status_labels=STATUS_ROWS_ELPI,
    change_labels=CHANGE_COLS_ELPI,
    status_descriptions=MATH11_STATUS_DESC,
    change_descriptions=ACADEMIC_CHANGE_DESC,
    title="Math: Grade 11",
    logic_summary="Rows = distance from standard (Status). Columns = change in points from prior year. Grade 11 uses separate cut points.",
    status_top_is_low=False,
)

SUSP_SCHOOL_ELEM = _template(
    cells=[[0, 4, 5, 5, 5], [0, 3, 4, 4, 5], [2, 2, 3, 4, 4], [1, 2, 2, 3, 3], [1, 1, 1, 2, 3]],
    status_labels=STATUS_ROWS,
    change_labels=CHANGE_COLS_INCREASE_BAD,
    status_descriptions=SUSP_SCHOOL_ELEM_STATUS_DESC,
    change_descriptions=SUSP_ELEM_CHANGE_DESC,
    title="Suspension: Elementary Schools",
    logic_summary="Rows = current-year suspension rate. Columns = change from prior year. Elementary school cut points.",
)

SUSP_SCHOOL_MIDDLE = _template(
    cells=[[0, 4, 5, 5, 5], [0, 3, 4, 4, 5], [2, 2, 3, 4, 4], [1, 2, 2, 3, 3], [1, 1, 1, 2, 3]],
    status_labels=STATUS_ROWS,
    change_labels=CHANGE_COLS_INCREASE_BAD,
    status_descriptions=SUSP_SCHOOL_MS_STATUS_DESC,
    change_descriptions=SUSP_MS_CHANGE_DESC,
    title="Suspension: Middle Schools",
    logic_summary="Rows = current-year suspension rate. Columns = change from prior year. Middle school cut points.",
)

SUSP_SCHOOL_HS = _template(
    cells=[[0, 4, 5, 5, 5], [0, 3, 4, 4, 5], [2, 2, 3, 4, 4], [1, 2, 2, 3, 3], [1, 1, 1, 2, 3]],
    status_labels=STATUS_ROWS,
    change_labels=CHANGE_COLS_INCREASE_BAD,
    status_descriptions=SUSP_SCHOOL_HS_STATUS_DESC,
    change_descriptions=SUSP_HS_CHANGE_DESC,
    title="Suspension: High Schools",
    logic_summary="Rows = current-year suspension rate. Columns = change from prior year. High school cut points.",
)

SUSP_UNIFIED = _template(
    cells=[[0, 4, 5, 5, 5], [2, 3, 4, 4, 5], [2, 2, 3, 4, 4], [1, 2, 2, 3, 3], [1, 1, 1, 2, 3]],
    status_labels=STATUS_ROWS,
    change_labels=CHANGE_COLS_INCREASE_BAD,
    status_descriptions=SUSP_UNIFIED_STATUS_DESC,
    change_descriptions=SUSP_UNIFIED_CHANGE_DESC,
    title="Suspension: Unified / K–12",
    logic_summary="Rows = current-year suspension rate. Columns = change from prior year. Unified district and K–12 school cut points.",
)

SUSP_LEA_ELEM = _template(
    cells=[[0, 4, 5, 5, 5], [0, 3, 4, 4, 5], [2, 2, 3, 4, 4], [1, 2, 2, 3, 3], [1, 1, 1, 2, 3]],
    status_labels=STATUS_ROWS,
    change_labels=CHANGE_COLS_INCREASE_BAD,
    status_descriptions=SUSP_LEA_ELEM_STATUS_DESC,
    change_descriptions=SUSP_UNIFIED_CHANGE_DESC,
    title="Suspension: LEA Elementary District",
    logic_summary="Rows = current-year suspension rate. Columns = change from prior year. LEA Elementary District cut points.",
)

SUSP_LEA_HS = _template(
    cells=[[0, 4, 5, 5, 5], [2, 3, 4, 4, 5], [2, 2, 3, 4, 4], [1, 2, 2, 3, 3], [1, 1, 1, 2, 3]],
    status_labels=STATUS_ROWS,
    change_labels=CHANGE_COLS_INCREASE_BAD,
    status_descriptions=SUSP_LEA_HS_STATUS_DESC,
    change_descriptions=SUSP_LEA_HS_CHANGE_DESC,
    title="Suspension: LEA High School District",
    logic_summary="Rows = current-year suspension rate. Columns = change from prior year. LEA High School District cut points.",
)

GRAD = _template(
    cells=[[0, 5, 5, 5, 5], [2, 3, 4, 4, 5], [2, 2, 3, 4, 4], [1, 2, 2, 3, 3], [1, 1, 1, 1, 1]],
    status_labels=STATUS_ROWS_ELPI,
    change_labels=CHANGE_COLS_ELPI,
    status_descriptions=GRAD_STATUS_DESC,
    change_descriptions=GRAD_CHANGE_DESC,
    title="Graduation Rate",
    logic_summary="Rows = combined four- and five-year graduation rate (Status). Columns = change from prior year. Higher rate and increase are better.",
    status_top_is_low=False,
)

CCI = _template(
    cells=[[3, 4, 5, 5, 5], [2, 3, 4, 4, 5], [2, 2, 3, 4, 4], [1, 2, 2, 3, 3], [1, 1, 1, 2, 3]],
    status_labels=STATUS_ROWS_ELPI,
    change_labels=CHANGE_COLS_ELPI,
    status_descriptions=CCI_STATUS_DESC,
    change_descriptions=CCI_CHANGE_DESC,
    title="College/Career Indicator (CCI)",
    logic_summary="Rows = percent of graduates prepared for college/career (Status). Columns = change from prior year. Higher preparedness and increase are better.",
    status_top_is_low=False,
)

SCIENCE = _template(
    cells=[[4, 4, 5, 5, 5], [4, 4, 4, 4, 5], [3, 3, 3, 4, 4], [2, 2, 2, 3, 3], [1, 1, 1, 2, 2]],
    status_labels=STATUS_ROWS_ELPI,
    change_labels=CHANGE_COLS_ELPI,
    status_descriptions=SCIENCE_STATUS_DESC,
    change_descriptions=SCIENCE_CHANGE_DESC,
    title="Science Indicator",
    logic_summary="Rows = Science Points in current year (Status). Columns = change in Science Points from prior year. Higher performance and increase are better.",
    status_top_is_low=False,
)

GRID_TEMPLATES: dict[str, dict] = {
    "chronic": CHRONIC,
    "elpi": ELPI,
    "ela_38": ELA_38,
    "ela_11": ELA_11,
    "math_38": MATH_38,
    "math_11": MATH_11,
    "susp_lea_elem": SUSP_LEA_ELEM,
    "susp_lea_hs": SUSP_LEA_HS,
    "susp_unified": SUSP_UNIFIED,
    "susp_school_elem": SUSP_SCHOOL_ELEM,
    "susp_school_middle": SUSP_SCHOOL_MIDDLE,
    "susp_school_hs": SUSP_SCHOOL_HS,
    "grad": GRAD,
    "cci": CCI,
    "science": SCIENCE,
}

SUSP_TYPE_MAP: dict[str, str] = {
    "ES": "susp_school_elem",
    "MS": "susp_school_middle",
    "HS": "susp_school_hs",
    "UD": "susp_unified",
}

TYPE_GROUP_LABELS: dict[str, str] = {
    "chronic": "All Schools",
    "elpi": "All Schools",
    "ela_38": "Grades 3–8",
    "ela_11": "Grade 11",
    "math_38": "Grades 3–8",
    "math_11": "Grade 11",
    "susp_school_elem": "Elementary Schools",
    "susp_school_middle": "Middle Schools",
    "susp_school_hs": "High Schools",
    "susp_unified": "Unified / K–12",
    "susp_lea_elem": "LEA, Elementary District",
    "susp_lea_hs": "LEA, High School District",
    "grad": "High Schools",
    "cci": "High Schools",
    "science": "All Schools",
}


def resolve_template_key(
    base_key: str,
    variant_field: str | None = None,
    variant_value=None,
    rtype: str = "S",
) -> str:
    if base_key == "chronic":
        return "chronic"
    if base_key == "elpi":
        return "elpi"
    if base_key == "grad":
        return "grad"
    if base_key == "cci":
        return "cci"
    if base_key == "science":
        return "science"
    if base_key in ("ela_38", "ela"):
        return "ela_11" if variant_value and str(variant_value).upper() in ("Y", "1", "TRUE") else "ela_38"
    if base_key in ("math_38", "math"):
        return "math_11" if variant_value and str(variant_value).upper() in ("Y", "1", "TRUE") else "math_38"
    if base_key.startswith("susp") or base_key == "susp":
        if variant_value is not None:
            key = SUSP_TYPE_MAP.get(str(variant_value).strip())
            if key:
                return key
        return "susp_unified" if rtype == "D" else "susp_school_elem"
    return base_key if base_key in GRID_TEMPLATES else "chronic"


def resolve_grid_template(
    base_key: str,
    variant_field: str | None = None,
    variant_value=None,
    rtype: str = "S",
    template_key: str | None = None,
) -> dict:
    key = template_key or resolve_template_key(base_key, variant_field, variant_value, rtype)
    if key not in GRID_TEMPLATES:
        raise KeyError(f"Unknown grid template: {key}")
    return GRID_TEMPLATES[key]


def box_to_position(box: int, template: dict) -> tuple[int, int] | None:
    if not box:
        return None
    return template["box_map"].get(int(box))
