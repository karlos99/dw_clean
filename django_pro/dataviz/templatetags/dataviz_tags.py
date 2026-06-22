"""Custom template filters for dataviz app."""

from django import template

register = template.Library()


@register.filter(name='get_item')
def get_item(dictionary, key):
    """Look up *key* in a dict and return the value (or dash)."""
    if isinstance(dictionary, dict):
        return dictionary.get(key, '\u2014')
    return '\u2014'


@register.filter(name='dict_get')
def dict_get(dictionary, key):
    """Look up *key* in a dict, return '' on miss (for HTML attribute values)."""
    if isinstance(dictionary, dict):
        return dictionary.get(key, '')
    return ''


@register.filter(name='multiply')
def multiply(value, arg):
    """Multiply value by arg — handy for pagination math."""
    try:
        return int(value) * int(arg)
    except (ValueError, TypeError):
        return 0
