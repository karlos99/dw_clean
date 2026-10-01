"""Read-only CTE taxonomy from ``coursecatalog_cte_pathways`` (ETL-owned)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from .models import CtePathwayEntry

CTE_TAG_NAME = 'CTE'


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
