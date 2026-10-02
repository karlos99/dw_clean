"""Read-only CTE taxonomy from ``coursecatalog_cte_pathways`` (ETL-owned)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from .models import CtePathwayEntry

CTE_TAG_NAME = 'CTE'

# CTE course progression stages, in path order. Each stage maps to a progression
# year used by the graphical timeline on the paths page.
CTE_STAGE_ORDER = ('Introduction', 'Concentration', 'Capstone')
CTE_STAGE_YEARS = {'Introduction': 1, 'Concentration': 2, 'Capstone': 3}
CTE_STAGE_BY_LOWER = {stage.lower(): stage for stage in CTE_STAGE_ORDER}


def cte_stage_year(level: str) -> int | None:
    """Progression year for a CTE level (Introduction → 1, …), else None."""
    canonical = CTE_STAGE_BY_LOWER.get((level or '').strip().lower())
    return CTE_STAGE_YEARS.get(canonical) if canonical else None


@dataclass(frozen=True)
class CtePathwayAssignment:
    """One course's row in the CTE pathways table, as a taxonomy path."""

    career_cluster: str
    cte_pathway: str
    cte_course_level: str

    @property
    def tag_name(self) -> str:
        return CTE_TAG_NAME


def _dedupe_key(row: CtePathwayEntry) -> tuple[str, str, str]:
    return (
        (row.career_cluster or '').strip(),
        (row.cte_pathway or '').strip(),
        (row.cte_course_level or '').strip(),
    )


def pathways_for_course_ids(course_ids: Iterable[str]) -> dict[str, list[CtePathwayAssignment]]:
    """Map district ``course_id`` → CTE pathway assignments (live from source table)."""
    normalized = {str(cid).strip() for cid in course_ids if cid and str(cid).strip()}
    if not normalized:
        return {}

    rows = (
        CtePathwayEntry.objects.filter(course_id__in=normalized)
        .order_by('career_cluster', 'cte_pathway', 'cte_course_level', 'pathway_row_key')
    )
    by_course: dict[str, list[CtePathwayAssignment]] = defaultdict(list)
    seen: dict[str, set[tuple[str, str, str]]] = defaultdict(set)

    for row in rows:
        cid = (row.course_id or '').strip()
        if not cid:
            continue
        key = _dedupe_key(row)
        if key in seen[cid]:
            continue
        seen[cid].add(key)
        by_course[cid].append(
            CtePathwayAssignment(
                career_cluster=key[0],
                cte_pathway=key[1],
                cte_course_level=key[2],
            )
        )
    return dict(by_course)


def attach_cte_pathways_to_entries(entries) -> None:
    """Set ``entry.cte_pathway_assignments`` on each catalog entry (in place)."""
    course_ids = [getattr(e, 'course_id', None) for e in entries]
    by_id = pathways_for_course_ids(course_ids)
    for entry in entries:
        cid = (entry.course_id or '').strip() if entry.course_id else ''
        entry.cte_pathway_assignments = by_id.get(cid, [])


def build_cte_taxonomy_tree() -> list[dict]:
    """
    Nested read-only tree: career cluster → pathways → level labels.

    Each cluster dict: name, pathway_count, course_count, pathways (list).
    Each pathway dict: name, levels (sorted unique str), course_count.
    """
    rows = (
        CtePathwayEntry.objects.values(
            'career_cluster',
            'cte_pathway',
            'cte_course_level',
            'course_id',
        )
        .distinct()
    )

    clusters: dict[str, dict] = {}
    for row in rows:
        cluster = (row['career_cluster'] or '').strip() or '—'
        pathway = (row['cte_pathway'] or '').strip() or '—'
        level = (row['cte_course_level'] or '').strip()
        course_id = (row['course_id'] or '').strip()

        if cluster not in clusters:
            clusters[cluster] = {'name': cluster, 'pathways': {}, 'course_ids': set()}
        cnode = clusters[cluster]
        if course_id:
            cnode['course_ids'].add(course_id)

        pmap = cnode['pathways']
        if pathway not in pmap:
            pmap[pathway] = {'name': pathway, 'levels': set(), 'course_ids': set()}
        pnode = pmap[pathway]
        if level:
            pnode['levels'].add(level)
        if course_id:
            pnode['course_ids'].add(course_id)

    tree = []
    for cluster_name in sorted(clusters.keys(), key=lambda s: s.lower()):
        cnode = clusters[cluster_name]
        pathways_out = []
        for pname in sorted(cnode['pathways'].keys(), key=lambda s: s.lower()):
            pnode = cnode['pathways'][pname]
            pathways_out.append(
                {
                    'name': pname,
                    'levels': sorted(pnode['levels'], key=lambda s: s.lower()),
                    'course_count': len(pnode['course_ids']),
                }
            )
        tree.append(
            {
                'name': cluster_name,
                'pathways': pathways_out,
                'pathway_count': len(pathways_out),
                'course_count': len(cnode['course_ids']),
            }
        )
    return tree


def courses_for_cte_pathway(*, career_cluster: str, cte_pathway: str, limit: int = 200):
    """Catalog course IDs on a given cluster + pathway (for browse UI)."""
    return (
        CtePathwayEntry.objects.filter(
            career_cluster=career_cluster,
            cte_pathway=cte_pathway,
        )
        .exclude(course_id__isnull=True)
        .exclude(course_id='')
        .order_by('course_id', 'cte_course_level')
        .values('course_id', 'course_title', 'cte_course_level', 'state_course_code')[:limit]
    )


def _level_sort(level: str) -> tuple[int, str]:
    canonical = CTE_STAGE_BY_LOWER.get(level.lower())
    if canonical:
        return (CTE_STAGE_ORDER.index(canonical), '')
    return (len(CTE_STAGE_ORDER), level.lower())


def group_cte_pathway_rows(rows, limit_courses: int = 100) -> list[dict]:
    """Group raw CTE pathway rows into ordered stage columns per pathway.

    ``rows`` is any iterable of mappings with ``career_cluster``, ``cte_pathway``,
    ``cte_course_level``, ``course_id`` and ``course_title`` keys.
    """
    pathways: dict[tuple[str, str], dict] = {}
    for row in rows:
        cluster = (row.get('career_cluster') or '').strip() or '—'
        pathway = (row.get('cte_pathway') or '').strip() or '—'
        level = (row.get('cte_course_level') or '').strip() or 'Unassigned'
        pnode = pathways.setdefault(
            (cluster, pathway),
            {'career_cluster': cluster, 'cte_pathway': pathway, 'levels': {}},
        )
        bucket = pnode['levels'].setdefault(level, [])
        course_id = (row.get('course_id') or '').strip()
        course_title = (row.get('course_title') or '').strip()
        if not course_id:
            continue
        if any(c['course_id'] == course_id for c in bucket):
            continue
        if len(bucket) < limit_courses:
            bucket.append({'course_id': course_id, 'course_title': course_title})

    out: list[dict] = []
    for (cluster, pathway), pnode in pathways.items():
        columns = [
            {
                'level': level,
                'year': cte_stage_year(level),
                'courses': pnode['levels'][level],
            }
            for level in sorted(pnode['levels'].keys(), key=_level_sort)
        ]
        out.append({
            'career_cluster': cluster,
            'cte_pathway': pathway,
            'columns': columns,
            'course_count': sum(len(c['courses']) for c in columns),
        })
    out.sort(key=lambda p: (p['career_cluster'].lower(), p['cte_pathway'].lower()))
    return out


def list_cte_pathways() -> list[dict]:
    """Flat list of every CTE pathway (career cluster → pathway), for browsing."""
    out: list[dict] = []
    for cluster in build_cte_taxonomy_tree():
        for pathway in cluster['pathways']:
            out.append({
                'career_cluster': cluster['name'],
                'cte_pathway': pathway['name'],
                'course_count': pathway['course_count'],
            })
    return out


def cte_pathway_detail(career_cluster: str, cte_pathway: str, limit_courses: int = 200) -> dict | None:
    """One CTE pathway as ordered stage columns (read-only)."""
    if not career_cluster or not cte_pathway:
        return None
    rows = (
        CtePathwayEntry.objects.filter(
            career_cluster=career_cluster,
            cte_pathway=cte_pathway,
        )
        .order_by('cte_course_level', 'course_id')
        .values('career_cluster', 'cte_pathway', 'cte_course_level', 'course_id', 'course_title')
    )
    grouped = group_cte_pathway_rows(rows, limit_courses)
    return grouped[0] if grouped else None


def cte_pathways_for_discipline(discipline_id, limit_courses: int = 100) -> list[dict]:
    """CTE pathways covering the courses placed in one discipline.

    Each pathway is returned with ordered stage columns (Introduction,
    Concentration, Capstone, then any other observed levels) and the courses
    matched by district ``course_id``.
    """
    from .models import CatalogPlacement

    course_ids = list(
        CatalogPlacement.objects.filter(discipline_id=discipline_id)
        .exclude(catalog_entry__course_id__isnull=True)
        .exclude(catalog_entry__course_id='')
        .values_list('catalog_entry__course_id', flat=True)
        .distinct()
    )
    if not course_ids:
        return []

    rows = (
        CtePathwayEntry.objects.filter(course_id__in=course_ids)
        .order_by('career_cluster', 'cte_pathway', 'cte_course_level', 'course_id')
        .values('career_cluster', 'cte_pathway', 'cte_course_level', 'course_id', 'course_title')
    )
    return group_cte_pathway_rows(rows, limit_courses)
