from __future__ import annotations

from functools import lru_cache
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
from ca_dash.services.grid.templates import resolve_template_key
from ca_dash.services.grid.thresholds import change_level_from_value, resolve_thresholds
from ca_dash.services.indicators.registry import INDICATORS, IndicatorConfig, get_indicator

def _load_school_short_names() -> dict[str, str]:
    from django.core.cache import cache
    from studata.models import School

    key = "ca_dash:school_short_names"
    cached = cache.get(key)
    if cached is not None:
        return cached
    mapping: dict[str, str] = {}
    qs = (
        School.objects.exclude(state_id__isnull=True)
        .exclude(state_id="")
        .exclude(short_name__isnull=True)
        .exclude(short_name="")
        .values_list("state_id", "short_name")
    )
    for sid, short in qs:
        full = format_cds(sid)
        if len(full) >= 7 and short:
            mapping[full[-7:]] = short.strip()
    cache.set(key, mapping, 3600)
    return mapping


def school_short_name(cds: str) -> str | None:
    mapping = _load_school_short_names()
    if not cds or len(cds) < 7:
        return None
    return mapping.get(cds[-7:])


def format_cds(cds) -> str:
    if cds is None:
        return ""
    digits = ''.join(ch for ch in str(cds) if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(14)[:14]


def district_cds_from_cds(cds) -> str:
    full = format_cds(cds)
    return full[:7] + "0000000"


def _json_val(val):
    if val is None:
        return None
    if hasattr(val, "item"):
        return val.item()
    if isinstance(val, float) and val != val:
        return None
    return val


def _is_small_denominator(row: dict[str, Any]) -> bool:
    val = row.get("smalldenom")
    if val is None:
        return False
    return str(val).strip().upper() in ("Y", "YES", "TRUE", "1")


def _box_from_row(row: dict[str, Any]) -> int | None:
    box = row.get("box")
    if box is None:
        return None
    try:
        b = int(float(box))
    except (TypeError, ValueError):
        return None
    return b if b > 0 else None


def _template_key_for_row(cfg: IndicatorConfig, row: dict[str, Any]) -> str:
    variant = row.get(cfg.grid_variant_field) if cfg.grid_variant_field else None
    if cfg.grid_variant_field == "type" and variant is None:
        variant = row.get("hscutpoints")
    return resolve_template_key(
        cfg.grid_template,
        cfg.grid_variant_field,
        variant,
        str(row.get("rtype") or "S"),
    )


def _box_status_for_formula(status_cde: int) -> int:
    """CDE box numbers use `(5 - statuslevel) * 50 + changelevel * 10` (statuslevel 1 = Very High rate)."""
    return status_cde


def _resolve_change_level_for_box(
    row: dict[str, Any],
    template_key: str,
    status_cde: int,
) -> int | None:
    """CDE change column 1–5 for the 5×5 grid (matches CHANGE_LEVEL_LABELS / template columns)."""
    derived = change_level_from_value(row.get("change"), template_key)
    if 1 <= derived <= 5:
        return derived

    change = row.get("changelevel")
    try:
        stored = int(float(change)) if change is not None else None
    except (TypeError, ValueError):
        stored = None
    if stored is not None and 1 <= stored <= 5:
        return stored

    box = row.get("box")
    if not box:
        return None
    try:
        col = (int(box) - (5 - status_cde) * 50) // 10
    except (TypeError, ValueError):
        return None
    return col if 1 <= col <= 5 else None


def normalize_five_by_five_placement(row: dict[str, Any], indicator_key: str) -> dict[str, Any]:
    """Recompute dashboard box from status + YoY change using CDE cut points for this indicator."""
    cfg = get_indicator(indicator_key)
    if str(row.get("indicator", "")).upper() != cfg.indicator_code:
        return row

    if _is_small_denominator(row):
        box = _box_from_row(row)
        if box is not None:
            row["box"] = box
        return row

    try:
        year = int(row.get("reportingyear") or 0)
        status_cde = int(row.get("statuslevel"))
    except (TypeError, ValueError):
        return row
    if not (1 <= status_cde <= 5):
        return row

    template_key = _template_key_for_row(cfg, row)
    change = _resolve_change_level_for_box(row, template_key, status_cde)
    if change is None:
        return row

    row["changelevel"] = change
    row["box"] = (5 - status_cde) * 50 + change * 10
    return row


def normalize_grid_placement(row: dict[str, Any]) -> dict[str, Any]:
    code = str(row.get("indicator", "")).upper()
    for key, cfg in INDICATORS.items():
        if cfg.indicator_code == code:
            return normalize_five_by_five_placement(row, key)
    return row


def subgroup_row_in_source_data(row: dict[str, Any]) -> bool:
    """Subgroup row reported in source data for this school (not an empty placeholder)."""
    code = str(row.get("studentgroup") or row.get("code") or "").strip()
    if not code:
        return False
    if int(row.get("currdenom") or 0) > 0:
        return True
    if row.get("currstatus") is not None:
        return True
    try:
        sl = int(float(row.get("statuslevel") or 0))
        if 1 <= sl <= 5:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _subgroup_eligible_for_chart(row: dict[str, Any], indicator_key: str) -> bool:
    """Student groups need source data plus dashboard color and valid box for charts."""
    if not subgroup_row_in_source_data(row):
        return False
    box = int(row.get("box") or 0)
    color = int(row.get("color") or 0)
    return box > 0 and color > 0


def _subgroup_list_eligible(row: dict[str, Any]) -> bool:
    """District/school subgroup picklists use box/color only (minimal SELECT)."""
    code = str(row.get("studentgroup") or row.get("code") or "").strip()
    if not code:
        return False
    box = int(row.get("box") or 0)
    color = int(row.get("color") or 0)
    return box > 0 and color > 0


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: _json_val(v) for k, v in row.items()}
    out["cds"] = format_cds(out.get("cds"))
    short = school_short_name(out["cds"])
    if short:
        out["school_short_name"] = short
    if "changeLevel" in out and out.get("changelevel") is None:
        out["changelevel"] = out["changeLevel"]
    out = normalize_grid_placement(out)
    color = out.get("color")
    if color is not None:
        try:
            c = int(color)
            out["color_name"] = COLOR_MAP.get(c, COLOR_MAP[0])["name"]
            out["color_hex"] = COLOR_MAP.get(c, COLOR_MAP[0])["hex"]
        except (TypeError, ValueError):
            pass
    sg = out.get("studentgroup")
    if sg:
        out["studentgroup_label"] = STUDENT_GROUP_LABELS.get(str(sg).strip(), str(sg))
    sl = out.get("statuslevel")
    if sl is not None:
        try:
            si = int(sl)
            ind_code = str(out.get("indicator", "")).upper()
            if ind_code in HIGHER_IS_BETTER_INDICATOR_CODES:
                out["statuslevel_label"] = STATUS_LEVEL_LABELS_HIGHER_IS_BETTER.get(si, str(sl))
            else:
                out["statuslevel_label"] = STATUS_LEVEL_LABELS.get(si, str(sl))
        except (TypeError, ValueError):
            pass
    cl = out.get("changelevel")
    if cl is not None:
        try:
            ci = int(cl)
            labels = (
                ELPI_CHANGE_LABELS
                if str(out.get("indicator", "")).upper() in ("ELPI", "SCIENCE")
                else CHANGE_LEVEL_LABELS
            )
            out["changelevel_label"] = labels.get(ci, str(cl))
        except (TypeError, ValueError):
            pass
    return out



_CDS_NORM = "lpad(NULLIF(regexp_replace(trim(cds::text), '[^0-9]', '', 'g'), ''), 14, '0')"
_CDS7 = "substr(lpad(NULLIF(regexp_replace(trim(cds::text), '[^0-9]', '', 'g'), ''), 14, '0'), 1, 7)"
_REPORTING_YEAR = "NULLIF(regexp_replace(trim(reportingyear::text), '[^0-9]', '', 'g'), '')::int"


def indicator_table_name(indicator_key: str) -> str:
    ind = get_indicator(indicator_key)
    return f"dash_indi_{ind.key}"


def table_exists(table_name: str) -> bool:
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
            """,
            [table_name],
        )
        return cursor.fetchone() is not None


class PostgresIndicatorLoader:
    def uses_official_2026(self, indicator_key: str) -> bool:
        return False

    def has_estimate_2026(self, indicator_key: str) -> bool:
        return False

    def _quoted_table(self, ind: IndicatorConfig) -> str:
        name = f"dash_indi_{ind.key}"
        if not table_exists(name):
            raise FileNotFoundError(f"Missing indicator table: public.{name}")
        return f'"{name}"'

    def _year_clause(self, ind: IndicatorConfig, reporting_years: list[int] | None = None) -> str:
        rel = self._quoted_table(ind)
        if reporting_years:
            if len(reporting_years) == 1:
                return f"{_REPORTING_YEAR} = {int(reporting_years[0])}"
            years = ",".join(str(int(y)) for y in sorted(reporting_years))
            return f"{_REPORTING_YEAR} IN ({years})"
        return f"{_REPORTING_YEAR} = (SELECT max({ _REPORTING_YEAR }) FROM {rel})"

    @staticmethod
    def _sort_reporting_years(years: list[int]) -> list[int]:
        return sorted(set(years))

    def _filter_years(self, years: list[int]) -> list[int]:
        from ca_dash.config_helpers import enabled_reporting_years

        allow = enabled_reporting_years()
        if not allow:
            return years
        return [y for y in years if y in allow]

    def get_reporting_years(self, indicator_key: str) -> list[int]:
        ind = get_indicator(indicator_key)
        table_name = f"dash_indi_{ind.key}"
        if not table_exists(table_name):
            return []
        rel = self._quoted_table(ind)
        sql = f"""
            SELECT DISTINCT {_REPORTING_YEAR} AS y
            FROM {rel}
            WHERE reportingyear IS NOT NULL AND trim(reportingyear::text) <> ''
            ORDER BY y
        """
        rows = self._query(sql)
        years = [int(r["y"]) for r in rows if r.get("y") is not None]
        excluded = set(ind.exclude_reporting_years)
        if excluded:
            years = [y for y in years if y not in excluded]
        years = self._sort_reporting_years(years)
        return self._filter_years(years)

    def get_latest_reporting_year(self, indicator_key: str) -> int | None:
        years = self.get_reporting_years(indicator_key)
        return years[-1] if years else None

    def _query(self, sql: str, params: list | None = None) -> list[dict]:
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(sql, params or [])
            cols = [col[0] for col in cursor.description]
            return [normalize_row(dict(zip(cols, row))) for row in cursor.fetchall()]


    @lru_cache(maxsize=32)
    def get_districts(self, indicator_key: str) -> list[dict]:
        return self._get_districts_for_years(indicator_key, None)

    def _get_districts_for_years(
        self, indicator_key: str, reporting_years: list[int] | None
    ) -> list[dict]:
        ind = get_indicator(indicator_key)
        rel = self._quoted_table(ind)
        sql = f"""
            SELECT DISTINCT
                districtname AS name,
                {_CDS_NORM} AS cds,
                {_CDS7} || '0000000' AS district_cds
            FROM {rel}
            WHERE rtype = 'D' AND {self._year_clause(ind, reporting_years)}
            ORDER BY districtname
        """
        return self._query(sql)

    @lru_cache(maxsize=64)
    def get_schools(self, indicator_key: str, district_cds: str) -> list[dict]:
        return self._get_schools_for_years(indicator_key, district_cds, None)

    def _get_schools_for_years(
        self,
        indicator_key: str,
        district_cds: str,
        reporting_years: list[int] | None,
    ) -> list[dict]:
        ind = get_indicator(indicator_key)
        rel = self._quoted_table(ind)
        prefix = district_cds[:7]
        sg_col = "COALESCE(studentgroup, 'EL')" if indicator_key == "elpi" else "studentgroup"
        sg_val = "EL" if indicator_key == "elpi" else "ALL"
        sql = f"""
            SELECT DISTINCT
                schoolname AS name,
                {_CDS_NORM} AS cds
            FROM {rel}
            WHERE rtype = 'S'
              AND {_CDS7} = %s
              AND {sg_col} = %s
              AND {self._year_clause(ind, reporting_years)}
            ORDER BY schoolname
        """
        rows = self._query(sql, [prefix, sg_val])
        for r in rows:
            sn = r.get("school_short_name")
            if sn:
                r["name"] = sn
        rows.sort(key=lambda r: (r.get("name") or "").casefold())
        return rows

    def get_school_map_rows(
        self,
        indicator_key: str,
        district_cds: str,
        studentgroup: str,
        reporting_years: list[int] | None = None,
    ) -> list[dict]:
        ind = get_indicator(indicator_key)
        rel = self._quoted_table(ind)
        prefix = district_cds[:7]
        sg_col = "COALESCE(studentgroup, 'EL')" if indicator_key == "elpi" else "studentgroup"
        sql = f"""
            SELECT *, {sg_col} AS studentgroup
            FROM {rel}
            WHERE rtype = 'S'
              AND {_CDS7} = %s
              AND {sg_col} = %s
              AND {self._year_clause(ind, reporting_years)}
            ORDER BY reportingyear, schoolname
        """
        return self._query(sql, [prefix, studentgroup])

    def get_subgroups(
        self,
        indicator_key: str,
        cds: str | None,
        rtype: str,
        district_cds: str | None = None,
        reporting_years: list[int] | None = None,
    ) -> list[dict]:
        ind = get_indicator(indicator_key)
        rel = self._quoted_table(ind)
        sg_col = "COALESCE(studentgroup, 'EL')" if indicator_key == "elpi" else "studentgroup"
        params: list = []
        if rtype == "S" and cds:
            scope_sql = f"rtype = 'S' AND {_CDS_NORM} = %s"
            params = [cds]
        elif district_cds:
            prefix = district_cds[:7]
            scope_sql = f"rtype = 'D' AND {_CDS7} = %s"
            params = [prefix]
        else:
            scope_sql = "rtype = 'D'"
        base_sg = "EL" if indicator_key == "elpi" else "ALL"
        sql = f"""
            SELECT {sg_col} AS code,
                NULLIF(regexp_replace(trim(box::text), '[^0-9]', '', 'g'), '')::int AS box,
                NULLIF(regexp_replace(trim(color::text), '[^0-9]', '', 'g'), '')::int AS color
            FROM {rel}
            WHERE {scope_sql} AND {self._year_clause(ind, reporting_years)}
        """
        rows = self._query(sql, params)
        available: dict[str, str] = {}
        for r in rows:
            code = str(r.get("code") or "").strip()
            if not code:
                continue
            if _subgroup_list_eligible(r):
                available[code] = STUDENT_GROUP_LABELS.get(code, code)
        return [
            {"code": code, "label": available[code]}
            for code in sorted(available, key=lambda c: (0 if c == base_sg else 1, available[c]))
        ]

    def get_school_subgroups(
        self,
        indicator_key: str,
        school_cds: str,
        reporting_years: list[int] | None = None,
    ) -> list[dict]:
        """Return student groups that have data for this school in the selected years."""
        rows = self.get_school_subgroup_records(indicator_key, school_cds, reporting_years)
        available: dict[str, str] = {}
        for row in rows:
            code = str(row.get("studentgroup") or "").strip()
            if not code:
                continue
            if _subgroup_eligible_for_chart(row, indicator_key):
                available[code] = STUDENT_GROUP_LABELS.get(code, code)

        return [
            {"code": code, "label": available[code]}
            for code in sorted(available, key=lambda c: (0 if c == "ALL" else 1, available[c]))
        ]

    def get_record(
        self,
        indicator_key: str,
        cds: str,
        rtype: str,
        studentgroup: str,
        reporting_year: int | None = None,
    ) -> dict | None:
        ind = get_indicator(indicator_key)
        rel = self._quoted_table(ind)
        years = [reporting_year] if reporting_year else None
        sg_col = "COALESCE(studentgroup, 'EL')" if indicator_key == "elpi" else "studentgroup"
        sql = f"""
            SELECT *, {sg_col} AS studentgroup
            FROM {rel}
            WHERE rtype = %s
              AND {_CDS_NORM} = %s
              AND {sg_col} = %s
              AND {self._year_clause(ind, years)}
            LIMIT 1
        """
        rows = self._query(sql, [rtype, cds, studentgroup])
        return rows[0] if rows else None

    def get_school_subgroup_records(
        self,
        indicator_key: str,
        school_cds: str,
        reporting_years: list[int] | None = None,
    ) -> list[dict]:
        ind = get_indicator(indicator_key)
        rel = self._quoted_table(ind)
        sg_col = "COALESCE(studentgroup, 'EL')" if indicator_key == "elpi" else "studentgroup"
        base_sg = "EL" if indicator_key == "elpi" else "ALL"
        sql = f"""
            SELECT *, {sg_col} AS studentgroup
            FROM {rel}
            WHERE rtype = 'S'
              AND {_CDS_NORM} = %s
              AND {self._year_clause(ind, reporting_years)}
            ORDER BY
                reportingyear,
                CASE WHEN {sg_col} = '{base_sg}' THEN 0 ELSE 1 END,
                {sg_col}
        """
        return self._query(sql, [school_cds])

    def get_subgroups_grouped_by_box(
        self,
        indicator_key: str,
        school_cds: str,
        reporting_years: list[int] | None = None,
    ) -> dict[int, list[dict]]:
        rows = self.get_school_subgroup_records(indicator_key, school_cds, reporting_years)
        grouped: dict[int, list[dict]] = {}
        for row in rows:
            if not _subgroup_eligible_for_chart(row, indicator_key):
                continue
            box = int(row.get("box") or 0)
            grouped.setdefault(box, []).append(
                {
                    "cds": row.get("cds"),
                    "code": row.get("studentgroup"),
                    "name": row.get("studentgroup_label") or row.get("studentgroup"),
                    "currstatus": row.get("currstatus"),
                    "change": row.get("change"),
                    "color": row.get("color"),
                    "box": box,
                    "reportingyear": row.get("reportingyear"),
                }
            )
        return grouped

    def get_district_map_rows(
        self,
        indicator_key: str,
        district_cds: str,
        studentgroup: str,
        reporting_years: list[int] | None = None,
    ) -> list[dict]:
        ind = get_indicator(indicator_key)
        rel = self._quoted_table(ind)
        prefix = district_cds[:7]
        sg_col = "COALESCE(studentgroup, 'EL')" if indicator_key == "elpi" else "studentgroup"
        sql = f"""
            SELECT *, {sg_col} AS studentgroup
            FROM {rel}
            WHERE rtype = 'D'
              AND {_CDS7} = %s
              AND {sg_col} = %s
              AND {self._year_clause(ind, reporting_years)}
            ORDER BY reportingyear
        """
        return self._query(sql, [prefix, studentgroup])

    def get_district_subgroups_by_box(
        self,
        indicator_key: str,
        district_cds: str,
        reporting_years: list[int] | None = None,
    ) -> dict[int, list[dict]]:
        ind = get_indicator(indicator_key)
        rel = self._quoted_table(ind)
        prefix = district_cds[:7]
        sg_col = "COALESCE(studentgroup, 'EL')" if indicator_key == "elpi" else "studentgroup"
        sql = f"""
            SELECT *, {sg_col} AS studentgroup
            FROM {rel}
            WHERE rtype = 'D'
              AND {_CDS7} = %s
              AND {self._year_clause(ind, reporting_years)}
            ORDER BY reportingyear, {sg_col}
        """
        rows = self._query(sql, [prefix])
        grouped: dict[int, list[dict]] = {}
        for row in rows:
            if not _subgroup_eligible_for_chart(row, indicator_key):
                continue
            box = int(row.get("box") or 0)
            grouped.setdefault(box, []).append(
                {
                    "cds": row.get("cds"),
                    "code": row.get("studentgroup"),
                    "name": row.get("studentgroup_label") or row.get("studentgroup"),
                    "currstatus": row.get("currstatus"),
                    "change": row.get("change"),
                    "color": row.get("color"),
                    "box": box,
                    "rtype": "D",
                    "reportingyear": row.get("reportingyear"),
                }
            )
        return grouped

    def get_entities_grouped_by_box(
        self,
        indicator_key: str,
        district_cds: str,
        rtype: str,
        studentgroup: str,
        reporting_years: list[int] | None = None,
    ) -> dict[int, list[dict]]:
        ind = get_indicator(indicator_key)
        rel = self._quoted_table(ind)
        prefix = district_cds[:7]
        sg_col = "COALESCE(studentgroup, 'EL')" if indicator_key == "elpi" else "studentgroup"
        sql = f"""
            SELECT
                {_CDS_NORM} AS cds,
                CASE WHEN rtype = 'S' THEN schoolname ELSE districtname END AS name,
                rtype,
                currstatus,
                change,
                NULLIF(regexp_replace(trim(color::text), '[^0-9]', '', 'g'), '')::int AS color,
                NULLIF(regexp_replace(trim(box::text), '[^0-9]', '', 'g'), '')::int AS box,
                {_REPORTING_YEAR} AS reportingyear
            FROM {rel}
            WHERE rtype = %s
              AND {_CDS7} = %s
              AND {sg_col} = %s
              AND box IS NOT NULL AND NULLIF(regexp_replace(trim(box::text), '[^0-9]', '', 'g'), '')::int > 0
              AND {self._year_clause(ind, reporting_years)}
            ORDER BY reportingyear, name
        """
        rows = self._query(sql, [rtype, prefix, studentgroup])
        grouped: dict[int, list[dict]] = {}
        for row in rows:
            box = int(row["box"])
            grouped.setdefault(box, []).append(row)
        return grouped

    def get_entities_in_box(
        self,
        indicator_key: str,
        box: int,
        rtype: str,
        district_cds: str | None,
        studentgroup: str,
        school_cds: str | None = None,
        reporting_years: list[int] | None = None,
    ) -> list[dict]:
        from django.db import connection

        ind = get_indicator(indicator_key)
        rel = self._quoted_table(ind)

        table_name = f"dash_indi_{ind.key}"
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                """,
                [table_name],
            )
            available_cols = {r[0].lower() for r in cur.fetchall()}

        
        type_col = "type" if "type" in available_cols else "NULL AS type"
        hscutpoints_col = "hscutpoints" if "hscutpoints" in available_cols else "NULL AS hscutpoints"

        params: list = [rtype]
        extra = ""
        if school_cds and rtype == "S":
            extra = f"AND {_CDS_NORM} = %s"
            params.append(school_cds)
        elif district_cds and rtype == "S":
            extra = f"AND {_CDS7} = %s"
            params.append(district_cds[:7])
        params.extend([box, studentgroup])
        sg_col = "COALESCE(studentgroup, 'EL')" if indicator_key == "elpi" else "studentgroup"
        sql = f"""
            SELECT
                {_CDS_NORM} AS cds,
                CASE WHEN rtype = 'S' THEN schoolname ELSE districtname END AS name,
                rtype,
                {sg_col} AS studentgroup,
                {type_col},
                {hscutpoints_col},
                currstatus,
                priorstatus,
                change,
                NULLIF(regexp_replace(trim(color::text), '[^0-9]', '', 'g'), '')::int AS color,
                NULLIF(regexp_replace(trim(box::text), '[^0-9]', '', 'g'), '')::int AS box,
                {_REPORTING_YEAR} AS reportingyear
            FROM {rel}
            WHERE rtype = %s
              {extra}
              AND NULLIF(regexp_replace(trim(box::text), '[^0-9]', '', 'g'), '')::int = %s
              AND NULLIF(regexp_replace(trim(box::text), '[^0-9]', '', 'g'), '')::int > 0
              AND {sg_col} = %s
              AND {self._year_clause(ind, reporting_years)}
            ORDER BY reportingyear, name
        """
        rows = self._query(sql, params)
        for r in rows:
            sg = r.get("studentgroup")
            if sg:
                r["subgroup_label"] = STUDENT_GROUP_LABELS.get(str(sg).strip(), sg)
        return rows
