"""Utilities for the landing / site-config layer."""
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
