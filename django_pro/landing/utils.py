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


def get_summary_sections(user=None):
    """Which student-summary sections are enabled for a user (defaults to all on).

    Evaluates the granular Access Policy per module:
    - mode == 'all': enabled for all users
    - mode == 'disabled': disabled for all users
    - mode == 'restricted': enabled only if user has any of the allowed roles
      OR user's email is in the allowed emails list.

    Superusers and Project Admins always retain visibility to non-disabled sections.
    """
    sections = dict(DEFAULT_SUMMARY_SECTIONS)
    try:
        cfg = get_project_config()
        rules = getattr(cfg, 'summary_access_rules', None) or {}

        # Precompute user role names & email if user provided
        is_admin = False
        user_roles = set()
        user_email = ""
        if user and getattr(user, 'is_authenticated', False):
            if getattr(user, 'is_superuser', False) or getattr(user, 'is_project_admin', False):
                is_admin = True
            if hasattr(user, 'role_names'):
                user_roles = set(user.role_names())
            elif hasattr(user, 'roles'):
                user_roles = set(user.roles.values_list('name', flat=True))
            user_email = (getattr(user, 'email', '') or '').strip().lower()

        for key, field in SUMMARY_SECTION_FIELDS.items():
            rule = rules.get(key)
            if isinstance(rule, dict) and 'mode' in rule:
                mode = rule.get('mode', 'all')
                if mode == 'all':
                    sections[key] = True
                elif mode == 'disabled':
                    sections[key] = False
                elif mode == 'restricted':
                    if is_admin:
                        sections[key] = True
                    elif user and getattr(user, 'is_authenticated', False):
                        allowed_roles = set(rule.get('roles', []))
                        allowed_emails = {
                            e.strip().lower() for e in rule.get('emails', []) if e.strip()
                        }
                        has_role = bool(user_roles.intersection(allowed_roles))
                        has_email = user_email in allowed_emails if user_email else False
                        sections[key] = has_role or has_email
                    else:
                        sections[key] = False
            else:
                # Fallback to legacy boolean field
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


def summary_sections_version(user=None):
    """A short string that changes when the summary config changes.

    Folded into the student-summary cache key so section toggles and the
    diploma expected-credit settings take effect immediately.
    """
    sections = get_summary_sections(user)
    bits = ''.join('1' if sections[k] else '0' for k in sorted(sections))
    try:
        import json as _json
        cfg = get_project_config()
        extra_parts = [
            _json.dumps(cfg.diploma_expected_credits or {}, sort_keys=True),
            _json.dumps(cfg.summary_access_rules or {}, sort_keys=True),
        ]
        extra = ':'.join(extra_parts)
    except Exception:
        extra = ''
    digest = hashlib.md5(extra.encode('utf-8')).hexdigest()[:8]
    return bits + ':' + digest
