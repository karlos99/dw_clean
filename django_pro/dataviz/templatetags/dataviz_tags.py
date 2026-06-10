from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Allows dynamic dictionary key lookups in django templates."""
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return getattr(dictionary, key, None)
