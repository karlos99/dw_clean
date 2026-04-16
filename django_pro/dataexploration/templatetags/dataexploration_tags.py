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


@register.filter(name='replace')
def replace_filter(value, args):
    """Replace characters in a string. Usage: {{ value|replace:"_: " }}"""
    if not value:
        return value
    try:
        old, new = args.split(':')
        return str(value).replace(old, new)
    except ValueError:
        return value


@register.filter(name='filter_visible_folders')
def filter_visible_folders(folders, user):
    """Filter a list of folders to only those visible to the user."""
    if not user:
        return []
    
    return [f for f in folders if can_user_view_folder(user, f)]


@register.filter(name='survey_cell_style')
def survey_cell_style(value, survey_colors_and_col):
    """
    Get the style string for a survey cell based on its value and color mapping.
    
    Usage: {{ value|survey_cell_style:survey_colors_and_col }}
    where survey_colors_and_col is a dict with 'colors' and 'col' keys.
    """
    if not survey_colors_and_col:
        return ''
    
    colors = survey_colors_and_col.get('colors', {})
    col = survey_colors_and_col.get('col', '')
    
    # Only apply to survey columns
    if not col or not col.startswith('survey_'):
        return ''
    
    # Get color for this column (all questions in same survey share colors)
    col_colors = colors.get(col, {})
    if not col_colors:
        return ''
    
    # Get color for this value
    str_value = str(value).strip() if value else ''
    if not str_value or str_value == '—':
        return ''
    
    color = col_colors.get(str_value, '')
    if not color:
        return ''
    
    # Determine text color based on background brightness
    text_color = '#1e293b' if _is_light_color(color) else '#ffffff'
    
    return f'background-color: {color}; color: {text_color};'


def _is_light_color(hex_color):
    """Determine if a hex color is light (for text contrast)."""
    if not hex_color or not hex_color.startswith('#'):
        return True
    
    try:
        hex_color = hex_color.lstrip('#')
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        luma = 0.299 * r + 0.587 * g + 0.114 * b
        return luma > 150
    except (ValueError, IndexError):
        return True


@register.simple_tag
def get_survey_cell_color(col, value, survey_colors):
    """
    Get the background color for a survey cell.
    
    Usage: {% get_survey_cell_color col value survey_colors as cell_style %}
    
    survey_colors structure:
    {
        'col_key': {
            'colors': {'scale_5': {'1': '#...', ...}, 'scale_7': {'1': '#...', ...}},
            'scale_max': 5 or 7
        }
    }
    """
    if not col or not col.startswith('survey_'):
        return ''
    
    if not survey_colors or not isinstance(survey_colors, dict):
        return ''
    
    # Get color info for this column
    col_info = survey_colors.get(col, {})
    if not col_info:
        return ''
    
    # Get the colors dict and scale for this column
    colors_dict = col_info.get('colors', {})
    scale_max = col_info.get('scale_max', 5)
    
    # Determine which scale's colors to use
    scale_key = f'scale_{scale_max}'
    scale_colors = colors_dict.get(scale_key, {})
    
    if not scale_colors:
        return ''
    
    # Get color for this value
    str_value = str(value).strip() if value else ''
    if not str_value or str_value == '—':
        return ''
    
    color = scale_colors.get(str_value, '')
    if not color:
        return ''
    
    # Determine text color based on background brightness
    text_color = '#1e293b' if _is_light_color(color) else '#ffffff'
    
    return f'background-color: {color}; color: {text_color};'


@register.filter(name='get_window_topics')
def get_window_topics(survey_data, window):
    """Return the topics_grouped dict for a given window within a survey_data entry.

    Usage: {% for topic_name, topic_questions in survey_data|get_window_topics:window %}
    """
    if not isinstance(survey_data, dict):
        return {}
    return survey_data.get('topics_grouped', {}).get(window, {})
