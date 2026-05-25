from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Get an item from a dictionary using a key."""
    if dictionary is None:
        return None
    # Try string key first, then integer
    if isinstance(dictionary, dict):
        result = dictionary.get(str(key))
        if result is None:
            result = dictionary.get(key)
        return result
    return None
