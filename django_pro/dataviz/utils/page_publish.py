"""
page_publish.py — Auto-create / update a users.Page entry when a DataVizPage is published.

Usage:
    from dataviz.utils.page_publish import publish_page, unpublish_page
    publish_page(dataviz_page, owner)
    unpublish_page(dataviz_page)
"""
from django.utils.text import slugify


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
