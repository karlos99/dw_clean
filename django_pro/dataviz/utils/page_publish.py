"""
page_publish.py — Auto-create / update a users.Page entry when a DataVizPage is published.

Usage:
    from dataviz.utils.page_publish import publish_page, unpublish_page
    publish_page(dataviz_page, owner)
    unpublish_page(dataviz_page)
"""
import re

from django.utils.text import slugify

_SLUG_RE = re.compile(r'^[-a-zA-Z0-9_]+$')


def normalize_page_folder(raw: str) -> str:
    """Normalize a folder path (e.g. 'Academics / Enrollment')."""
    parts = [p.strip() for p in (raw or '').replace('\\', '/').split('/') if p.strip()]
    return ' / '.join(parts)[:500]


def build_page_tree(pages, extra_folder_paths=None):
    """Group pages into a nested folder tree, including empty folder paths."""
    root = {'folders': {}, 'pages': []}

    def _ensure_path(parts):
        curr = root
        for part in parts:
            if part not in curr['folders']:
                curr['folders'][part] = {'folders': {}, 'pages': []}
            curr = curr['folders'][part]

    for path in extra_folder_paths or []:
        parts = [p.strip() for p in (path or '').split(' / ') if p.strip()]
        if parts:
            _ensure_path(parts)

    for page in pages:
        parts = [p.strip() for p in (page.folder or '').split(' / ') if p.strip()]
        curr = root
        for part in parts:
            if part not in curr['folders']:
                curr['folders'][part] = {'folders': {}, 'pages': []}
            curr = curr['folders'][part]
        curr['pages'].append(page)
    return root


def folder_path_from_url(raw: str) -> str:
    """Convert a URL folder param (Reports/ELA) to stored form (Reports / ELA)."""
    parts = [p.strip() for p in (raw or '').replace('\\', '/').split('/') if p.strip()]
    return ' / '.join(parts)[:500]


def collect_folder_paths(root) -> list[str]:
    """Return sorted full folder paths from a page tree."""
    paths = set()

    def walk(node, prefix=''):
        for name, child in sorted(node.get('folders', {}).items()):
            full = f"{prefix} / {name}".strip(' / ') if prefix else name
            paths.add(full)
            walk(child, full)

    walk(root)
    return sorted(paths)


def all_page_folder_paths() -> list[str]:
    """All known folder paths from pages and empty folder records."""
    from dataviz.models import DataVizPage, DataVizPageFolder

    paths = set(DataVizPageFolder.objects.values_list('path', flat=True))
    paths.update(
        DataVizPage.objects.exclude(folder='').values_list('folder', flat=True)
    )
    return sorted(p for p in paths if p)


def normalize_page_slug(raw: str) -> str:
    """Normalize a page URL slug (lowercase, no spaces, URL-safe characters only)."""
    return slugify((raw or '').strip())[:200]


def validate_page_slug(raw: str, *, exclude_page_id=None) -> tuple[str | None, str | None]:
    """
    Validate and normalize a DataVizPage slug.

    Returns (normalized_slug, error_message). error_message is None when valid.
    """
    from dataviz.models import DataVizPage

    slug = normalize_page_slug(raw)
    if not slug:
        return None, 'URL slug is required (use lowercase letters, numbers, and hyphens — no spaces)'

    if not _SLUG_RE.match(slug):
        return None, 'URL slug may only contain letters, numbers, hyphens, and underscores'

    qs = DataVizPage.objects.filter(slug=slug)
    if exclude_page_id:
        qs = qs.exclude(pk=exclude_page_id)
    if qs.exists():
        return None, f'A page with URL slug "{slug}" already exists'

    return slug, None


def publish_page(dataviz_page, owner) -> None:
    """
    Create or update the linked users.Page so the published DataVizPage
    appears in site navigation and access control.
    """
    from users.models_pages import Page

    url_path = f'/dataviz/pages/{dataviz_page.slug}/'
    site_slug = f'dataviz-{dataviz_page.slug}'[:255]

    if dataviz_page.linked_site_page_id:
        # Update existing
        try:
            site_page = Page.objects.get(pk=dataviz_page.linked_site_page_id)
            site_page.name       = dataviz_page.title
            site_page.url_path   = url_path
            site_page.status     = 'active'
            site_page.save(update_fields=['name', 'url_path', 'status'])
            return
        except Page.DoesNotExist:
            pass  # fall through to create

    # Create new users.Page
    site_page, _ = Page.objects.update_or_create(
        slug=site_slug,
        defaults={
            'name':          dataviz_page.title,
            'app_reference': 'dataviz',
            'description':   dataviz_page.description,
            'url_path':      url_path,
            'status':        'active',
            'required_roles': [],
            'is_public':     False,
            'show_in_menu':  True,
            'menu_order':    100,
            'created_by':    owner,
        },
    )

    # Link back
    dataviz_page.linked_site_page = site_page
    dataviz_page.status = 'active'
    dataviz_page.save(update_fields=['linked_site_page', 'status'])


def unpublish_page(dataviz_page) -> None:
    """Set the linked users.Page to inactive when the DataVizPage is deleted/deactivated."""
    if dataviz_page.linked_site_page_id:
        from users.models_pages import Page
        try:
            Page.objects.filter(pk=dataviz_page.linked_site_page_id).update(status='inactive')
        except Exception:
            pass
    dataviz_page.status = 'draft'
    dataviz_page.save(update_fields=['status'])
