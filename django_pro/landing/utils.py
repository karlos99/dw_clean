"""Utilities for the landing / site-config layer."""
import hashlib
from django.core.cache import cache

CACHE_KEY = 'landing:project_config'
CACHE_TTL  = 300  # 5 minutes


def get_project_config():
    """
    Return the ProjectConfig singleton.

    The result is cached in Redis for 5 minutes.  The cache is automatically
    invalidated by ProjectConfig.save(), so callers always see fresh values
    within one cache cycle.
    """
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    from landing.models import ProjectConfig
    obj = ProjectConfig.get()
    cache.set(CACHE_KEY, obj, CACHE_TTL)
    return obj


def get_disabled_school_years():
    """Return the list of school years hidden from the Data Explorer."""
    try:
        cfg = get_project_config()
        vals = getattr(cfg, 'disabled_school_years', None) or []
        return [str(v).strip() for v in vals if str(v).strip()]
    except Exception:
        return []


# Student-summary sections that can be toggled from Project Settings.
SUMMARY_SECTION_FIELDS = {
    'tests': 'summary_show_tests',
    'grades': 'summary_show_grades',
    'diploma': 'summary_show_diploma',
    'ag': 'summary_show_ag',
    'on_track': 'summary_show_on_track',
    'cte': 'summary_show_cte',
    'recognition': 'summary_show_recognition',
    'flags': 'summary_show_flags',
}

DEFAULT_SUMMARY_SECTIONS = {key: True for key in SUMMARY_SECTION_FIELDS}


def get_summary_sections():
    """Which student-summary sections are enabled (defaults to all on)."""
    sections = dict(DEFAULT_SUMMARY_SECTIONS)
    try:
        cfg = get_project_config()
        for key, field in SUMMARY_SECTION_FIELDS.items():
            value = getattr(cfg, field, None)
            if value is not None:
                sections[key] = bool(value)
    except Exception:
        pass
    return sections


def get_diploma_expected_credits():
    """Configured expected diploma credits per subject per grade ({} = defaults)."""
    try:
        cfg = get_project_config()
        value = getattr(cfg, 'diploma_expected_credits', None)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def summary_sections_version():
    """A short string that changes when the summary config changes.

    Folded into the student-summary cache key so section toggles and the
    diploma expected-credit settings take effect immediately.
    """
    sections = get_summary_sections()
    bits = ''.join('1' if sections[k] else '0' for k in sorted(sections))
    try:
        import json as _json
        cfg = get_project_config()
        extra = _json.dumps(cfg.diploma_expected_credits or {}, sort_keys=True)
    except Exception:
        extra = ''
    digest = hashlib.md5(extra.encode('utf-8')).hexdigest()[:8]
    return bits + ':' + digest
