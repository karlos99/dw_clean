"""Project-wide CA Dashboard settings from ProjectConfig."""
from __future__ import annotations

from functools import lru_cache

DEFAULT_DISTRICT_CDS = '33670580000000'
DEFAULT_DISTRICT_SHORT = 'DSUSD'
DEFAULT_DISTRICT_FULL = 'Desert Sands Unified'

YEAR_COLORS = {
    2020: {'hex': '#64748b', 'label': '2020'},
    2021: {'hex': '#475569', 'label': '2021'},
    2022: {'hex': '#6366f1', 'label': '2022'},
    2023: {'hex': '#7c3aed', 'label': '2023'},
    2024: {'hex': '#0891b2', 'label': '2024'},
    2025: {'hex': '#059669', 'label': '2025'},
    2026: {'hex': '#d97706', 'label': '2026'},
}


def _project_config():
    from landing.utils import get_project_config

    return get_project_config()


def _format_district_cds(raw: str) -> str:
    digits = ''.join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) < 7:
        return ''
    if len(digits) >= 14:
        return digits[:14].zfill(14)
    return digits[:7].zfill(7) + '0000000'


def _district_from_dash_tables() -> str:
    """Prefer the district CDS row shipped in dash_indi_* (rtype=D)."""
    from django.db import connection

    for table in ('dash_indi_chronic', 'dash_indi_ela', 'dash_indi_math'):
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT lpad(
                        NULLIF(regexp_replace(trim(cds::text), '[^0-9]', '', 'g'), ''),
                        14, '0'
                    )
                    FROM "{table}"
                    WHERE upper(trim(rtype::text)) = 'D'
                    LIMIT 1
                    """
                )
                row = cursor.fetchone()
        except Exception:
            continue
        if row and row[0]:
            return str(row[0]).zfill(14)
    return ''


def _district_has_indicator_rows(district_cds: str) -> bool:
    from django.db import connection

    prefix = district_cds[:7]
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM "dash_indi_chronic"
                WHERE upper(trim(rtype::text)) = 'D'
                  AND substr(
                    lpad(NULLIF(regexp_replace(trim(cds::text), '[^0-9]', '', 'g'), ''), 14, '0'),
                    1, 7
                  ) = %s
                LIMIT 1
                """,
                [prefix],
            )
            return cursor.fetchone() is not None
    except Exception:
        return False


def district_cds() -> str:
    cfg = _project_config()
    explicit = (getattr(cfg, 'ca_dash_district_cds', None) or '').strip()
    if explicit:
        formatted = _format_district_cds(explicit)
        if formatted and _district_has_indicator_rows(formatted):
            return formatted

    from_tables = _district_from_dash_tables()
    if from_tables:
        return from_tables

    if explicit:
        formatted = _format_district_cds(explicit)
        if formatted:
            return formatted

    return DEFAULT_DISTRICT_CDS


def district_short_name() -> str:
    cfg = _project_config()
    name = (getattr(cfg, 'ca_dash_district_short_name', None) or '').strip()
    return name or DEFAULT_DISTRICT_SHORT


def district_full_name() -> str:
    cfg = _project_config()
    name = (getattr(cfg, 'ca_dash_district_full_name', None) or '').strip()
    return name or DEFAULT_DISTRICT_FULL


def enabled_reporting_years() -> list[int]:
    cfg = _project_config()
    raw = getattr(cfg, 'ca_dash_enabled_reporting_years', None) or []
    if not raw:
        return []
    out: list[int] = []
    for item in raw:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def year_palette(years: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for y in years:
        base = YEAR_COLORS.get(y, {'hex': '#334155', 'label': str(y)})
        out[y] = {**base, 'label': base.get('label', str(y))}
    return out