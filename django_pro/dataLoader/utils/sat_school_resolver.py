"""
Resolve a SAT file's institution to the district's school short name.

The College Board SAT export carries a long institution name (``AI_NAME``) and
an institution code (``AI_CODE``). The district's ``School`` table holds the
canonical short names used across the application. This module maps one to the
other, normalizing common spelling differences (e.g. "INDIO HIGH SCHOOL" vs
"Indio HS"), and falls back to ``(non-DS School)`` when there is no match.
"""
import re

from django.core.cache import cache

from studata.models import School


NON_DS_SCHOOL = '(non-DS School)'

_LOOKUP_CACHE_KEY = 'sat_school_short_name_lookup_v1'
_LOOKUP_CACHE_TIMEOUT = 3600


def _normalize(value) -> str:
    """Uppercase and collapse everything that is not alphanumeric."""
    text = str(value or '').upper()
    text = re.sub(r'[^A-Z0-9]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _canonical(value) -> str:
    """Normalize plus expand/contract common school-type suffixes."""
    text = _normalize(value)
    replacements = (
        ('HIGH SCHOOL', 'HS'),
        ('HIGH SCH', 'HS'),
        ('MIDDLE SCHOOL', 'MS'),
        ('MIDDLE SCH', 'MS'),
        ('ELEMENTARY SCHOOL', 'ES'),
        ('ELEMENTARY SCH', 'ES'),
        ('INTERMEDIATE SCHOOL', 'IS'),
        ('SCHOOL', ''),
        ('ACADEMY', ''),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    return re.sub(r'\s+', ' ', text).strip()


def _code_variants(value) -> set:
    """Return exact and leading-zero-stripped forms of a code."""
    raw = str(value or '').strip()
    if not raw:
        return set()
    variants = {raw}
    stripped = raw.lstrip('0')
    if stripped:
        variants.add(stripped)
    return variants


def _build_lookup():
    by_name = {}
    by_code = {}

    for school in School.objects.all():
        short = (school.short_name or '').strip()
        if not short:
            short = (school.school_name or '').strip()
        if not short:
            continue

        for candidate in (school.school_name, school.short_name):
            if not candidate:
                continue
            by_name.setdefault(_normalize(candidate), short)
            by_name.setdefault(_canonical(candidate), short)

        for code in (school.assessment_school_code, school.state_id, school.sis_key):
            for variant in _code_variants(code):
                by_code.setdefault(variant, short)

    return by_name, by_code


def _get_lookup():
    cached = cache.get(_LOOKUP_CACHE_KEY)
    if cached:
        return cached
    by_name, by_code = _build_lookup()
    cache.set(_LOOKUP_CACHE_KEY, (by_name, by_code), _LOOKUP_CACHE_TIMEOUT)
    return by_name, by_code


def resolve_school_short_name(ai_name: str = '', ai_code: str = '') -> str:
    """Return the district short name for a SAT institution, or ``(non-DS School)``."""
    try:
        by_name, by_code = _get_lookup()
    except Exception:
        return NON_DS_SCHOOL

    for variant in _code_variants(ai_code):
        if variant in by_code:
            return by_code[variant]

    name = str(ai_name or '').strip()
    if name:
        for key in (_normalize(name), _canonical(name)):
            if key in by_name:
                return by_name[key]

    return NON_DS_SCHOOL
