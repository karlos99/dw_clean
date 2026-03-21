"""Custom template tags for dataexploration app."""

from django import template
from ..utils import can_user_view_folder

register = template.Library()


@register.filter(name='get_item')
def get_item(dictionary, key):
    """Get item from dictionary by key."""
    if isinstance(dictionary, dict):
        return dictionary.get(key, key)
    return key


@register.filter(name='filter_visible_folders')
def filter_visible_folders(folders, user):
    """Filter a list of folders to only those visible to the user."""
    if not user:
        return []
    
    return [f for f in folders if can_user_view_folder(user, f)]
