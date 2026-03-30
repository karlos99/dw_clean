from django import template

register = template.Library()


@register.filter
def school_names(school_ids, school_name_map):
    """Resolve a list of school state_ids to a comma-separated string of names."""
    if not school_ids or not isinstance(school_ids, list):
        return '-'
    names = [school_name_map.get(sid, sid) for sid in school_ids if sid]
    return ', '.join(sorted(names)) if names else '-'
