"""Build taxonomy structures for catalog UI (tags → categories → disciplines → levels)."""
from __future__ import annotations

import json

from django.db.models import Prefetch

from .models import CatalogCategory, CatalogDiscipline, CatalogLevel, CatalogTag


def build_taxonomy_json() -> str:
    """JSON for Alpine.js cascading selects on placements page."""
    tags = list(
        CatalogTag.objects.filter(is_active=True)
        .order_by('order', 'name')
        .values('id', 'name', 'slug')
    )
    categories = list(
        CatalogCategory.objects.filter(is_active=True, tag__is_active=True)
        .order_by('tag_id', 'order', 'name')
        .values('id', 'tag_id', 'name')
    )
    disciplines = list(
        CatalogDiscipline.objects.filter(is_active=True, category__is_active=True, category__tag__is_active=True)
        .order_by('category_id', 'order', 'name')
        .values('id', 'category_id', 'name')
    )
    levels = list(
        CatalogLevel.objects.filter(is_active=True)
        .order_by('order', 'name')
        .values('id', 'name', 'code')
    )
    return json.dumps(
        {'tags': tags, 'categories': categories, 'disciplines': disciplines, 'levels': levels},
    )


def selected_tag_queryset():
    return (
        CatalogTag.objects.filter(is_active=True)
        .prefetch_related(
            Prefetch(
                'categories',
                queryset=CatalogCategory.objects.filter(is_active=True)
                .prefetch_related(
                    Prefetch(
                        'disciplines',
                        queryset=CatalogDiscipline.objects.filter(is_active=True).order_by('order', 'name'),
                    ),
                )
                .order_by('order', 'name'),
            ),
        )
        .order_by('order', 'name')
    )
