from django import template

register = template.Library()

_GRADE_DISPLAY = {
    'TK': 'TK',
    'KN': 'KN',
    '01': '1st',
    '02': '2nd',
    '03': '3rd',
    '04': '4th',
    '05': '5th',
}


@register.filter
def format_grade(value):
    """Render a grade value (single or comma-separated combo) as a human-friendly string."""
    if not value:
        return ''
    parts = [_GRADE_DISPLAY.get(g.strip(), g.strip()) for g in value.split(',') if g.strip()]
    return ' / '.join(parts)


def _hex_to_rgb(value: str):
    color = (value or '').strip()
    if not color.startswith('#'):
        return None
    hex_code = color[1:]
    if len(hex_code) == 3:
        hex_code = ''.join(ch * 2 for ch in hex_code)
    if len(hex_code) != 6:
        return None
    try:
        return tuple(int(hex_code[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return None


@register.filter
def in_list(value, values):
    return value in values


@register.filter
def get_item(value, key):
    if value is None:
        return ''
    if not hasattr(value, 'get'):
        return ''
    return value.get(str(key), value.get(key, ''))


@register.filter
def get_item_detail(value, key):
    if value is None or not hasattr(value, 'get'):
        return ''
    detail_key = f"{key}_detail"
    return value.get(detail_key, value.get(str(detail_key), ''))


@register.filter
def tag_color_class(color_value):
    color = (color_value or '').strip()
    return color if color.startswith('bg-') else ''


@register.filter
def tag_color_style(color_value):
    rgb = _hex_to_rgb(color_value)
    if rgb is None:
        return ''
    return f'background-color: {color_value};'


@register.filter
def tag_text_class(color_value):
    rgb = _hex_to_rgb(color_value)
    if rgb is None:
        return 'text-white'
    red, green, blue = rgb
    brightness = (red * 299 + green * 587 + blue * 114) / 1000
    return 'text-slate-900' if brightness >= 180 else 'text-white'


@register.filter
def tag_picker_value(color_value):
    rgb = _hex_to_rgb(color_value)
    if rgb is None:
        return '#64748b'
    return color_value
