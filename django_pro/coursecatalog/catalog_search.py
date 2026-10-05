"""Catalog course list search (single text or pasted IDs)."""
from __future__ import annotations

import re

from django.db.models import Q, QuerySet

from .models import CourseCatalogEntry

_MAX_ID_TERMS = 500


def _normalize_catalog_id_token(part: str) -> str:
    """Strip spreadsheet / Excel noise from one cell value."""
    token = str(part).strip().strip('"').strip("'")
    token = token.replace('\ufeff', '').replace('\u200b', '')
    return token


def parse_catalog_search_terms(raw: str) -> list[str]:
    """Split pasted or typed search input into distinct terms."""
    if not raw or not str(raw).strip():
        return []
    parts = re.split(r'[\s,;\n\r\t]+', str(raw).strip())
    seen = set()
    terms: list[str] = []
    for part in parts:
        token = _normalize_catalog_id_token(part)
        if not token or token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return terms[:_MAX_ID_TERMS]


def _semester_sibling_id(term: str) -> str | None:
    """District semester pair: IDs like 6005A / 6005B (digit before A or B)."""
    if len(term) < 2:
        return None
    last = term[-1]
    if last not in 'ABab' or not term[-2].isdigit():
        return None
    swap = {'A': 'B', 'a': 'b', 'B': 'A', 'b': 'a'}
    return term[:-1] + swap[last]


def expand_semester_pair_terms(terms: list[str]) -> list[str]:
    """
    When a semester half (…A or …B) is searched without its pair, include the other.
    """
    if not terms:
        return terms
    seen = set(terms)
    expanded = list(terms)
    for term in terms:
        sibling = _semester_sibling_id(term)
        if sibling and sibling not in seen:
            expanded.append(sibling)
            seen.add(sibling)
    return expanded[:_MAX_ID_TERMS]


def catalog_terms_matched(terms: list[str], entries) -> set[str]:
    """Which pasted terms appear on a catalog row (course key, course id, or state code)."""
    found: set[str] = set()
    if not terms:
        return found
    for entry in entries:
        values = {
            _normalize_catalog_id_token(v).casefold()
            for v in (
                getattr(entry, 'course_key', None),
                getattr(entry, 'course_id', None),
                getattr(entry, 'state_course_code', None),
            )
            if v
        }
        for term in terms:
            if _normalize_catalog_id_token(term).casefold() in values:
                found.add(term)
    return found


def filter_catalog_entries(queryset: QuerySet | None = None, q: str = '') -> QuerySet:
    """
    Search catalog rows.

    One term: broad match (title, key, course id, state code, subjects).
    Multiple terms: exact match on course_key, course_id, or state_course_code (paste lists).
    """
    qs = queryset if queryset is not None else CourseCatalogEntry.objects.all()
    terms = expand_semester_pair_terms(parse_catalog_search_terms(q))
    if not terms:
        return qs
    if len(terms) == 1:
        term = terms[0]
        return qs.filter(
            Q(course_title__icontains=term)
            | Q(state_course_name__icontains=term)
            | Q(course_key__icontains=term)
            | Q(course_id__icontains=term)
            | Q(state_course_code__icontains=term)
            | Q(subject_area_1_desc__icontains=term)
            | Q(subject_area_2_desc__icontains=term)
        )
    id_filter = Q()
    for term in terms:
        id_filter |= (
            Q(course_key__iexact=term)
            | Q(course_id__iexact=term)
            | Q(state_course_code__iexact=term)
        )
    return qs.filter(id_filter).distinct()
