"""Student photos on disk as {ssid}.jpg (see STUDENT_PICTURES_DIR)."""
import re
from pathlib import Path

from django.conf import settings

_SAFE_SSID = re.compile(r'[^A-Za-z0-9]')
_EXTS = ('.jpg', '.jpeg', '.png', '.webp')

PLACEHOLDER_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128"'
    b' role="img" aria-label="No student photo">'
    b'<rect width="128" height="128" fill="#e2e8f0"/>'
    b'<circle cx="64" cy="48" r="22" fill="#94a3b8"/>'
    b'<path d="M20 118c8-28 28-42 44-42s36 14 44 42" fill="#94a3b8"/>'
    b'</svg>'
)


def pictures_dir():
    configured = getattr(settings, 'STUDENT_PICTURES_DIR', None)
    return Path(configured) if configured else Path(settings.BASE_DIR).parent / 'pictures'


def picture_path(ssid):
    """Absolute path to the student's photo, or None if missing."""
    safe = _SAFE_SSID.sub('', str(ssid or ''))
    if not safe:
        return None
    root = pictures_dir()
    for ext in _EXTS:
        candidate = root / f'{safe}{ext}'
        if candidate.is_file():
            return candidate
    return None
