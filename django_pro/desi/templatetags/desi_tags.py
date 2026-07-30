from django import template

register = template.Library()

@register.filter(name='get_item')
def get_item(dictionary, key):
    if not dictionary:
        return None
    # Check key (supports both int and str representations)
    val = dictionary.get(key)
    if val is None:
        val = dictionary.get(str(key))
    return val
