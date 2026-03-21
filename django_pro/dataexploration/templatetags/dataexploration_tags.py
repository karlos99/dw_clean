"""Custom template tags for dataexploration app."""

from django import template

register = template.Library()


@register.filter(name='get_item')
def get_item(dictionary, key):
    """Get item from dictionary by key."""
    if isinstance(dictionary, dict):
        return dictionary.get(key, key)
    return key
