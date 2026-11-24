"""Custom template filters for dataviz app."""

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


def escape_json_for_script(raw_json: str) -> str:
    """Escape raw JSON text for safe embedding inside <script type="application/json">."""
    if not raw_json:
        return '{}'
    return (
        str(raw_json)
        .replace('<', '\\u003c')
        .replace('>', '\\u003e')
        .replace('&', '\\u0026')
        .replace('\u2028', '\\u2028')
        .replace('\u2029', '\\u2029')
    )


@register.filter(name='safe_script_json')
def safe_script_json(value):
    """Mark escaped JSON string safe for <script type="application/json">."""
    return mark_safe(escape_json_for_script(value))


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
