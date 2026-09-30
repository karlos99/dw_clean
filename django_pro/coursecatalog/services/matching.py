"""Match schedule rows to catalog entries and resolve enrollment by placement."""
from __future__ import annotations

from django.db import connection

from coursecatalog.models import CatalogCategory, CatalogPlacement, CourseCatalogEntry
from studata.models import Schedule


def _schedule_field_values(row) -> list[str]:
    """Extract non-empty identifier values from a schedule-like row."""
    values = []
    for attr in ('course_key', 'course_code', 'course_state_equivalence_code'):
        val = getattr(row, attr, None) if not isinstance(row, dict) else row.get(attr)
        if val and str(val).strip():
            values.append(str(val).strip())
    return values


def match_schedule_row(schedule_row) -> CourseCatalogEntry | None:
    """
    Resolve a schedule row to a catalog entry by course_key, then code, then state code.
    """
    keys = _schedule_field_values(schedule_row)
    if not keys:
        return None
    for key in keys:
        entry = CourseCatalogEntry.objects.filter(course_key=key).first()
        if entry:
            return entry
    for key in keys:
        entry = CourseCatalogEntry.objects.filter(course_id=key).first()
        if entry:
            return entry
    for key in keys:
        entry = CourseCatalogEntry.objects.filter(state_course_code=key).first()
        if entry:
            return entry
    return None


def catalog_entries_for_category(category: CatalogCategory, *, include_descendants: bool = True):
    """Catalog entries with a placement in this category."""
    del include_descendants  # single level under tag; kept for call-site compatibility
    return CourseCatalogEntry.objects.filter(placements__category_id=category.id).distinct()


def _catalog_keys_for_placements(placement_ids: list[int]) -> set[str]:
    keys: set[str] = set()
    placements = CatalogPlacement.objects.filter(id__in=placement_ids).select_related('catalog_entry')
    for p in placements:
        keys.update(p.catalog_entry.canonical_schedule_match_values())
    return keys


def student_ssids_for_placements(
    placement_ids,
    school_year,
    *,
    state_school_id: str | None = None,
) -> list[str]:
    """
    Return distinct SSIDs enrolled in schedule courses matching any of the given placements.
    """
    if not placement_ids:
        return []
    match_keys = _catalog_keys_for_placements(list(placement_ids))
    if not match_keys:
        return []

    clauses = [
        '(sl.course_key = ANY(%s) OR sl.course_code = ANY(%s) OR sl.course_state_equivalence_code = ANY(%s))',
    ]
    key_list = sorted(match_keys)
    params = [key_list, key_list, key_list]

    if isinstance(school_year, list):
        if len(school_year) == 1:
            clauses.append('sl.school_year = %s')
            params.append(school_year[0])
        elif school_year:
            clauses.append('sl.school_year = ANY(%s)')
            params.append(school_year)
    elif school_year:
        clauses.append('sl.school_year = %s')
        params.append(school_year)

    if state_school_id:
        clauses.append('sl.state_school_id = %s')
        params.append(state_school_id)

    sql = f"""
        SELECT DISTINCT sl.ssid
        FROM schedule_list sl
        WHERE {' AND '.join(clauses)}
          AND sl.ssid IS NOT NULL AND sl.ssid <> ''
        ORDER BY sl.ssid
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return [row[0] for row in cursor.fetchall()]


def schedule_rows_matching_entry(entry: CourseCatalogEntry):
    """Schedules that likely refer to this catalog entry (for preview counts)."""
    keys = entry.canonical_schedule_match_values()
    if not keys:
        return Schedule.objects.none()
    from django.db.models import Q

    q = Q()
    for key in keys:
        q |= Q(course_key=key) | Q(course_code=key) | Q(course_state_equivalence_code=key)
    return Schedule.objects.filter(q)
