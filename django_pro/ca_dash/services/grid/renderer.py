from __future__ import annotations

from typing import Any

from ca_dash.services.constants import (
    CHANGE_LEVEL_LABELS,
    COLOR_MAP,
    ELPI_CHANGE_LABELS,
    HIGHER_IS_BETTER_INDICATOR_CODES,
    STATUS_LEVEL_LABELS,
    STATUS_LEVEL_LABELS_HIGHER_IS_BETTER,
    STUDENT_GROUP_LABELS,
)
from ca_dash.services.grid.templates import (
    GRID_TEMPLATES,
    TYPE_GROUP_LABELS,
    box_to_position,
    resolve_grid_template,
    resolve_template_key,
)
from ca_dash.services.grid.thresholds import resolve_thresholds
from ca_dash.services.indicators.registry import IndicatorConfig
from ca_dash.services.loader import subgroup_row_in_source_data


def _metrics_from_row(row: dict | None, indicator_code: str = "") -> dict | None:
    if not row:
        return None
    color = int(row.get("color") or 0)
    box = int(row.get("box") or 0)
    change_labels = (
        ELPI_CHANGE_LABELS
        if str(indicator_code).upper() in HIGHER_IS_BETTER_INDICATOR_CODES
        else CHANGE_LEVEL_LABELS
    )
    sl = row.get("statuslevel")
    cl = row.get("changelevel")
    ind_upper = str(indicator_code).upper()
    if row.get("statuslevel_label"):
        status_label = row.get("statuslevel_label")
    elif sl is not None:
        try:
            si = int(sl)
            if ind_upper in HIGHER_IS_BETTER_INDICATOR_CODES:
                status_label = STATUS_LEVEL_LABELS_HIGHER_IS_BETTER.get(si, str(sl))
            else:
                status_label = STATUS_LEVEL_LABELS.get(si, str(sl))
        except (TypeError, ValueError):
            status_label = str(sl)
    else:
        status_label = None
    return {
        "cds": row.get("cds"),
        "name": row.get("school_short_name") or row.get("schoolname") or row.get("districtname"),
        "rtype": row.get("rtype"),
        "studentgroup": row.get("studentgroup"),
        "studentgroup_label": row.get("studentgroup_label"),
        "reportingyear": row.get("reportingyear"),
        "currstatus": row.get("currstatus"),
        "priorstatus": row.get("priorstatus"),
        "change": row.get("change"),
        "currnumer": row.get("currnumer"),
        "currdenom": row.get("currdenom"),
        "priornumer": row.get("priornumer"),
        "priordenom": row.get("priordenom"),
        "color": color,
        "color_name": COLOR_MAP.get(color, COLOR_MAP[0])["name"],
        "color_hex": COLOR_MAP.get(color, COLOR_MAP[0])["hex"],
        "box": box,
        "statuslevel": sl,
        "statuslevel_label": status_label,
        "changelevel": cl,
        "changelevel_label": change_labels.get(int(cl), str(cl)) if cl is not None else None,
        "accountabilitymet": row.get("accountabilitymet"),
        "currnsizemet": row.get("currnsizemet"),
        "priornsizemet": row.get("priornsizemet"),
        "smalldenom": row.get("smalldenom"),
        "charter_flag": row.get("charter_flag"),
        "dass_flag": row.get("dass_flag"),
        "school_type": row.get("type"),
        "hscutpoints": row.get("hscutpoints"),
        "has_color": box > 0 and str(row.get("accountabilitymet", "")).upper() == "Y",
        "variant_value": row.get("type") or row.get("hscutpoints"),
    }


def _subgroup_grid_eligible(metrics: dict) -> bool:
    """5×5 chart / sidebar chips: requires valid box and performance color."""
    return int(metrics.get("box") or 0) > 0 and int(metrics.get("color") or 0) > 0


def build_school_detail(all_row: dict | None, subgroup_rows: list[dict], indicator_code: str) -> dict | None:
    if not all_row:
        return None
    detail = _metrics_from_row(all_row, indicator_code)
    if not detail:
        return None
    
    subgroups = []
    for r in subgroup_rows:
        base_sg = "EL" if indicator_code == "ELPI" else "ALL"
        if r.get("studentgroup") == base_sg:
            continue
        if not subgroup_row_in_source_data(r):
            continue
        metrics = _metrics_from_row(r, indicator_code)
        if metrics:
            subgroups.append(
                {
                    **metrics,
                    "code": r.get("studentgroup"),
                    "grid_eligible": _subgroup_grid_eligible(metrics),
                }
            )
            
    detail["subgroups"] = subgroups
    return detail


def _subgroup_base_code(indicator_code: str) -> str:
    return "EL" if str(indicator_code).upper() == "ELPI" else "ALL"


def _entity_visible_on_chart(entity: dict, indicator_code: str) -> bool:
    """Hide student groups with no 5×5 placement or performance color. Schools always show if placed."""
    code = entity.get("code")
    base = _subgroup_base_code(indicator_code)
    if not code or str(code).strip() == base:
        return True
    return int(entity.get("box") or 0) > 0 and int(entity.get("color") or 0) > 0


def _normalize_studentgroup_code(code: str) -> str:
    c = str(code).strip()
    if c in ("LT EL", "LTEL"):
        return "LTEL"
    return c


def build_calculator_subgroups(
    subgroup_rows: list[dict],
    indicator_code: str,
    reporting_year: int | None = None,
) -> list[dict]:
    """Return student groups present in source data for this school (not the full CDE catalog)."""
    year_rows = subgroup_rows
    if reporting_year is not None:
        year_rows = [
            r for r in subgroup_rows if int(r.get("reportingyear") or 0) == reporting_year
        ]
        if not year_rows:
            year_rows = subgroup_rows

    by_code: dict[str, dict] = {}
    for r in year_rows:
        sg = r.get("studentgroup")
        base_sg = "EL" if indicator_code == "ELPI" else "ALL"
        if not sg or sg == base_sg:
            continue
        if not subgroup_row_in_source_data(r):
            continue
        code = _normalize_studentgroup_code(str(sg))
        by_code[code] = r
        if sg == "LT EL":
            by_code["LT EL"] = r

    result: list[dict] = []
    for code in sorted(by_code, key=lambda c: STUDENT_GROUP_LABELS.get(c, c)):
        row = by_code[code]
        metrics = _metrics_from_row(row, indicator_code) or {}
        has_data = _subgroup_grid_eligible(metrics)
        result.append(
            {
                **metrics,
                "code": code,
                "studentgroup_label": STUDENT_GROUP_LABELS.get(code, code),
                "has_data": has_data,
                "n_size_met": str(row.get("currnsizemet", "")).upper() == "Y",
                "accountability_met": str(row.get("accountabilitymet", "")).upper() == "Y",
            }
        )
    return result


def build_grid_payload(
    indicator: IndicatorConfig,
    row: dict | None,
    box_counts: dict[int, int] | None = None,
    entities_by_box: dict[int, list[dict]] | None = None,
    highlight_cds: str | None = None,
    highlight_code: str | None = None,
    highlight_box: int | None = None,
    title: str = "",
    entity_label: str = "School",
    tag_field: str = "name",
    template_key: str | None = None,
) -> dict[str, Any]:
    variant = None
    rtype = "S"
    if row:
        variant = row.get("type") or row.get("hscutpoints")
        rtype = row.get("rtype", "S")

    tkey = template_key or resolve_template_key(
        indicator.grid_template, indicator.grid_variant_field, variant, rtype
    )
    template = resolve_grid_template(
        indicator.grid_template, indicator.grid_variant_field, variant, rtype, template_key=tkey
    )

    box = highlight_box
    if box is None and row:
        box = int(row.get("box") or 0)

    position = box_to_position(box, template) if box else None

    cells = []
    for ri, row_colors in enumerate(template["cells"]):
        row_cells = []
        for ci, color_int in enumerate(row_colors):
            cell_box = next(
                (b for b, (r, c) in template["box_map"].items() if r == ri and c == ci), None
            )
            count = (box_counts or {}).get(cell_box, 0) if cell_box else 0
            cell_entities = (entities_by_box or {}).get(cell_box, []) if cell_box else []
            schools = [
                {
                    "cds": e.get("cds"),
                    "code": e.get("code"),
                    "name": e.get(tag_field) or e.get("name"),
                    "reportingyear": e.get("reportingyear"),
                    "box": e.get("box"),
                    "highlighted": (
                        (highlight_code and e.get("code") == highlight_code)
                        or (highlight_cds and e.get("cds") == highlight_cds)
                    ),
                }
                for e in cell_entities
                if _entity_visible_on_chart(e, indicator.indicator_code)
            ]
            row_cells.append(
                {
                    "row": ri,
                    "col": ci,
                    "box": cell_box,
                    "color": color_int,
                    "color_hex": COLOR_MAP.get(color_int, COLOR_MAP[0])["hex"],
                    "color_name": COLOR_MAP.get(color_int, COLOR_MAP[0])["name"],
                    "count": count or len(schools),
                    "schools": schools,
                    "highlighted": cell_box == box and box > 0,
                    "na": color_int == 0,
                }
            )
        cells.append(row_cells)

    metrics = _metrics_from_row(row, indicator.indicator_code)
    no_color_msg = None
    if row and not (metrics and metrics.get("has_color")):
        parts = []
        if str(row.get("accountabilitymet", "")).upper() != "Y":
            parts.append("Minimum n-size not met for accountability.")
        if int(row.get("box") or 0) == 0:
            parts.append("No performance color assigned.")
        no_color_msg = " ".join(parts) or "No performance color assigned."

    display_title = title or template.get("title") or TYPE_GROUP_LABELS.get(tkey, tkey)
    thresholds = resolve_thresholds(tkey)

    return {
        "title": display_title,
        "type_key": tkey,
        "type_label": TYPE_GROUP_LABELS.get(tkey, display_title),
        "entity_label": entity_label,
        "template_key": tkey,
        "logic_summary": template.get("logic_summary", ""),
        "axis_status_title": template.get("axis_status_title", "Status Level"),
        "axis_change_title": template.get("axis_change_title", "Change Level"),
        "status_labels": template["status_labels"],
        "change_labels": template["change_labels"],
        "status_descriptions": template.get("status_descriptions", []),
        "change_descriptions": template.get("change_descriptions", []),
        "thresholds": thresholds,
        "cells": cells,
        "highlight_box": box,
        "highlight_position": {"row": position[0], "col": position[1]} if position else None,
        "metrics": metrics,
        "no_color_message": no_color_msg,
        "box_counts": box_counts or {},
    }


def _entity_key(entity: dict) -> tuple:
    return (
        entity.get("cds"),
        entity.get("code"),
        entity.get("reportingyear"),
    )


def _append_entity(bucket: list[dict], entity: dict) -> None:
    key = _entity_key(entity)
    if any(_entity_key(existing) == key for existing in bucket):
        return
    bucket.append(entity)


def build_typed_grids(
    indicator: IndicatorConfig,
    school_rows: list[dict],
    studentgroup: str,
    highlight_cds: str | None = None,
    reporting_years: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Build separate 5x5 grids grouped by CDE school type / cut point table."""
    by_type: dict[str, dict[int, list[dict]]] = {}
    highlight_by_type: dict[str, int | None] = {}

    for row in school_rows:
        variant = row.get("type") or row.get("hscutpoints")
        tkey = resolve_template_key(
            indicator.grid_template, indicator.grid_variant_field, variant, "S"
        )
        box = row.get("box")
        if not box or int(box) <= 0:
            continue
        box = int(box)
        bucket = by_type.setdefault(tkey, {}).setdefault(box, [])
        _append_entity(
            bucket,
            {
                "cds": row.get("cds"),
                "name": row.get("school_short_name") or row.get("schoolname"),
                "code": row.get("studentgroup"),
                "reportingyear": row.get("reportingyear"),
            },
        )
        if highlight_cds and row.get("cds") == highlight_cds:
            highlight_by_type[tkey] = box

    order = list(GRID_TEMPLATES.keys())
    grids = []
    for tkey in sorted(by_type.keys(), key=lambda k: order.index(k) if k in order else 99):
        entities = by_type[tkey]
        template = GRID_TEMPLATES[tkey]
        title = template.get("title", TYPE_GROUP_LABELS.get(tkey, tkey))
        if reporting_years and len(reporting_years) > 1:
            years_label = ", ".join(str(y) for y in sorted(reporting_years))
            title = f"{title} · {years_label}"
        grids.append(
            build_grid_payload(
                indicator,
                None,
                entities_by_box=entities,
                highlight_cds=highlight_cds,
                highlight_box=highlight_by_type.get(tkey),
                title=title,
                entity_label="School",
                template_key=tkey,
            )
        )
    return grids
