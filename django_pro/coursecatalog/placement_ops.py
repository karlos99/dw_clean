"""Create or replace catalog placements (one row per course + tag)."""
from __future__ import annotations

from django.db import IntegrityError

from .models import CatalogPlacement


def _same_taxonomy(placement, *, category, discipline, level) -> bool:
    return (
        placement.category_id == (category.pk if category else None)
        and placement.discipline_id == (discipline.pk if discipline else None)
        and placement.level_id == (level.pk if level else None)
    )


def upsert_placement_for_tag(
    *,
    catalog_entry_id: str,
    tag,
    category=None,
    discipline=None,
    level=None,
) -> str:
    """
    Assign taxonomy to a catalog course.

    If the course already has a placement under ``tag``, update it (and drop extra
    duplicate rows for the same tag). Otherwise create a new placement.

    Returns ``created``, ``replaced``, or ``unchanged``.
    """
    existing_qs = CatalogPlacement.objects.filter(
        catalog_entry_id=catalog_entry_id,
        tag=tag,
    ).order_by('id')
    if existing_qs.exists():
        placement = existing_qs.first()
        existing_qs.exclude(pk=placement.pk).delete()
        if _same_taxonomy(placement, category=category, discipline=discipline, level=level):
            return 'unchanged'
        placement.category = category
        placement.discipline = discipline
        placement.level = level
        placement.clean()
        try:
            placement.save()
        except IntegrityError:
            placement.delete()
            placement = CatalogPlacement(
                catalog_entry_id=catalog_entry_id,
                tag=tag,
                category=category,
                discipline=discipline,
                level=level,
            )
            placement.clean()
            placement.save()
        return 'replaced'

    placement = CatalogPlacement(
        catalog_entry_id=catalog_entry_id,
        tag=tag,
        category=category,
        discipline=discipline,
        level=level,
    )
    placement.clean()
    placement.save()
    return 'created'
