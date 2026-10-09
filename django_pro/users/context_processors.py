from .models_pages import Page, NavbarConfig
from mathr.view_modules.access_utils import can_user_access_mathr
from placementcards.view_modules.card_data_utils import (
    can_user_access_placement_cards,
    can_user_access_teacher_notes,
    can_user_configure_placements,
    get_request_school,
)


def _is_project_admin_user(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if hasattr(user, 'active_role') and user.active_role and user.active_role.name == 'Project Admin':
        return True
    return user.roles.filter(name='Project Admin').exists() if hasattr(user, 'roles') else False


def _is_school_admin_user(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if hasattr(user, 'active_role') and user.active_role:
        return user.active_role.name == 'School Admin'
    return user.roles.filter(name='School Admin').exists() if hasattr(user, 'roles') else False


def _user_roles(user):
    if not user.is_authenticated:
        return set()
    roles = set(user.roles.values_list('name', flat=True)) if hasattr(user, 'roles') else set()
    if hasattr(user, 'active_role') and user.active_role:
        roles.add(user.active_role.name)
    return roles


def _page_is_accessible(user, page, user_roles: set[str]) -> bool:
    if user.is_authenticated and (user.is_superuser or 'Project Admin' in user_roles):
        return True
    if page.is_public:
        return True
    if not user.is_authenticated:
        return False
    if page.allowed_users.filter(id=user.id).exists():
        return True
    if not page.required_roles or len(page.required_roles) == 0:
        return True
    return bool(user_roles & set(page.required_roles))


def _navbar_config():
    try:
        return NavbarConfig.get()
    except Exception:
        return NavbarConfig()


def _user_charts():
    """Charts exposed in the 'User Charts' navbar menu."""
    try:
        from dataviz.models import SavedChart
        return list(
            SavedChart.objects
            .filter(is_user_menu=True)
            .order_by('user_menu_order', 'name')
            .values('id', 'name', 'chart_type', 'description')
        )
    except Exception:
        return []


def _menu_target_allows(user, page, user_roles: set[str]) -> bool:
    if not user.is_authenticated:
        return False

    if user.is_superuser or 'Project Admin' in user_roles:
        return True

    has_menu_roles = bool(page.menu_roles and len(page.menu_roles) > 0)
    has_menu_users = page.menu_allowed_users.exists()
    if not has_menu_roles and not has_menu_users:
        return True

    role_match = bool(user_roles & set(page.menu_roles)) if has_menu_roles else False
    user_match = page.menu_allowed_users.filter(id=user.id).exists() if has_menu_users else False
    return role_match or user_match


def _is_placeholder_page(page) -> bool:
    if getattr(page, 'is_menu_category', False):
        return True
    target = (page.url_path or '').strip()
    return target in {'', '#'}


def _is_preview_forced(user, page) -> bool:
    """Return True when the user was explicitly added to menu_allowed_users (preview/testing override).

    Iterates the prefetch cache (populated via prefetch_related) to avoid extra DB queries.
    """
    if not user.is_authenticated:
        return False
    # .all() uses the prefetch cache when prefetch_related('menu_allowed_users') was used
    return any(u.id == user.id for u in page.menu_allowed_users.all())


def _has_preview_grant_on_page_or_ancestor(user, page) -> bool:
    """Preview grant on this page or a menu ancestor (e.g. CCGI category email allow-list)."""
    if not user.is_authenticated or page is None:
        return False
    current = page
    while current is not None:
        if current.allowed_users.filter(id=user.id).exists():
            return True
        if _is_preview_forced(user, current):
            return True
        current = current.parent_page
    return False


def _preview_menu_pages_for_user(user):
    """
    Non-active menu pages visible for preview/testing.

    Includes descendants under a preview-granted parent so a menu category allow-list
    exposes draft child links (without adding every child by email).
    """
    from django.db.models import Q as _Q

    if not user.is_authenticated:
        return []

    roots = list(
        Page.objects.filter(show_in_menu=True)
        .filter(_Q(allowed_users=user) | _Q(menu_allowed_users=user))
        .exclude(status='active')
        .distinct()
        .prefetch_related('menu_allowed_users')
        .order_by('menu_order', 'name')
    )
    if not roots:
        return []

    seen = {p.id for p in roots}
    queue = list(roots)
    while queue:
        parent = queue.pop(0)
        children = Page.objects.filter(
            parent_page_id=parent.id,
            show_in_menu=True,
        ).exclude(status='active').prefetch_related('menu_allowed_users')
        for child in children:
            if child.id in seen:
                continue
            seen.add(child.id)
            roots.append(child)
            queue.append(child)
    return roots


def user_can_preview_draft_site_page(user, page) -> bool:
    """Whether ``page`` (or its menu ancestors) grants draft/preview access."""
    return _has_preview_grant_on_page_or_ancestor(user, page)


def _can_show_page_in_navigation(request, page, force_preview: bool = False) -> bool:
    # If the user was explicitly added for preview/testing, skip business-logic gates
    if force_preview:
        return True
    if page.slug == 'placement-cards-teacher-notes':
        school = get_request_school(request)
        return can_user_access_teacher_notes(request.user, school)
    if page.slug == 'placement-cards-place-students':
        school = get_request_school(request)
        return can_user_access_placement_cards(request.user, school)
    if page.slug == 'placement-cards-configuration':
        school = get_request_school(request)
        return can_user_configure_placements(request.user, school)
    if page.slug == 'mathr':
        return can_user_access_mathr(request)
    return True


def _build_public_children(parent_id, children_by_parent):
    accessible_children = []
    for child in children_by_parent.get(parent_id, []):
        child.accessible_children = _build_public_children(child.id, children_by_parent)
        if _is_placeholder_page(child) and not child.accessible_children:
            continue
        accessible_children.append(child)
    return accessible_children


def _build_accessible_children(request, parent_id, children_by_parent, user_roles: set[str], is_project_admin: bool):
    accessible_children = []
    for child in children_by_parent.get(parent_id, []):
        if child.slug in {'pages', 'manage-pages'} or child.url_path.rstrip('/') in {'/users/pages', '/users/manage-pages'}:
            if not is_project_admin:
                continue

        if _is_placeholder_page(child):
            # Placeholder sub-menus: skip role checks, show only if they have accessible descendants.
            child.accessible_children = _build_accessible_children(
                request,
                child.id,
                children_by_parent,
                user_roles,
                is_project_admin,
            )
            if not child.accessible_children:
                continue
        else:
            if not _page_is_accessible(request.user, child, user_roles):
                continue
            if not _menu_target_allows(request.user, child, user_roles):
                continue
            force_preview = _is_preview_forced(request.user, child)
            if not _can_show_page_in_navigation(request, child, force_preview=force_preview):
                continue

            child.accessible_children = _build_accessible_children(
                request,
                child.id,
                children_by_parent,
                user_roles,
                is_project_admin,
            )

        accessible_children.append(child)
    return accessible_children


def navigation_pages(request):
    """
    Add navigation pages to template context.
    Only shows pages the current user has access to.
    """
    from landing.utils import get_project_config
    try:
        default_school_year = get_project_config().default_school_year
    except Exception:
        default_school_year = ''

    usage_page = Page.objects.filter(slug='usage').first()
    usage_page_access = False
    if usage_page and request.user.is_authenticated:
        usage_page_access = user_can_access_page(request.user, usage_page)
    elif request.user.is_authenticated:
        # Fallback if usage page record not created yet
        usage_page_access = bool(
            request.user.is_superuser
            or getattr(request.user, 'is_project_admin', False)
            or (hasattr(request.user, 'active_role') and request.user.active_role and request.user.active_role.name == 'Project Admin')
        )

    if not request.user.is_authenticated:
        public_pages = list(
            Page.objects.filter(
                status='active',
                is_public=True,
                show_in_menu=True,
            ).order_by('menu_order', 'name')
        )
        children_by_parent = {}
        for page in public_pages:
            children_by_parent.setdefault(page.parent_page_id, []).append(page)

        pages = []
        for page in children_by_parent.get(None, []):
            page.accessible_children = _build_public_children(page.id, children_by_parent)
            if _is_placeholder_page(page) and not page.accessible_children:
                continue
            pages.append(page)

        return {
            'navigation_pages': pages,
            'usage_page_access': False,
            'usage_page_url': usage_page.url_path if usage_page else '/users/usage/',
            'is_school_admin': False,
            'is_project_admin': False,
            'placement_cards_access': False,
            'default_school_year': default_school_year,
            'navbar_config': _navbar_config(),
            'user_charts': _user_charts(),
        }

    user_roles = _user_roles(request.user)
    school = get_request_school(request)
    placement_cards_access = can_user_access_placement_cards(request.user, school)
    
    all_pages = list(Page.objects.filter(
        status='active',
        show_in_menu=True,
    ).prefetch_related('menu_allowed_users').order_by('menu_order', 'name'))
    children_by_parent = {}
    for page in all_pages:
        children_by_parent.setdefault(page.parent_page_id, []).append(page)

    # Preview/access override: draft/inactive pages (and their draft menu children)
    # for users explicitly granted access on the page or a menu category ancestor.
    preview_pages = _preview_menu_pages_for_user(request.user)
    for page in preview_pages:
        children_by_parent.setdefault(page.parent_page_id, []).append(page)
    for children in children_by_parent.values():
        children.sort(key=lambda p: (p.menu_order, p.name))
    
    # Filter pages by access
    accessible_pages = []
    is_project_admin = _is_project_admin_user(request.user)
    for page in children_by_parent.get(None, []):
        # Keep page-management navigation restricted to Project Admin only
        if page.slug in {'pages', 'manage-pages'} or page.url_path.rstrip('/') in {'/users/pages', '/users/manage-pages'}:
            if not is_project_admin:
                continue

        is_placeholder = _is_placeholder_page(page)

        if is_placeholder:
            # For placeholder containers, skip role checks — show only if children are accessible.
            page.accessible_children = _build_accessible_children(
                request,
                page.id,
                children_by_parent,
                user_roles,
                is_project_admin,
            )
            if not page.accessible_children:
                continue
        else:
            # Real pages: enforce access and menu-target checks.
            if not _page_is_accessible(request.user, page, user_roles):
                continue
            if not _menu_target_allows(request.user, page, user_roles):
                continue

            force_preview = _is_preview_forced(request.user, page)
            if not _can_show_page_in_navigation(request, page, force_preview=force_preview):
                continue

            page.accessible_children = _build_accessible_children(
                request,
                page.id,
                children_by_parent,
                user_roles,
                is_project_admin,
            )

        accessible_pages.append(page)
    
    return {
        'navigation_pages': accessible_pages,
        'usage_page_access': usage_page_access,
        'usage_page_url': usage_page.url_path if usage_page else '/users/usage/',
        'is_school_admin': _is_school_admin_user(request.user),
        'is_project_admin': is_project_admin,
        'placement_cards_access': placement_cards_access,
        'default_school_year': default_school_year,
        'navbar_config': _navbar_config(),
        'user_charts': _user_charts(),
    }


def user_can_access_page(user, page):
    """
    Helper function to check if a user can access a specific page.
    """
    # Project Admin and superuser can access all pages
    user_roles = _user_roles(user)
    if user.is_superuser or 'Project Admin' in user_roles:
        return True

    # Public pages are always accessible
    if page.is_public:
        return True
    
    # User must be authenticated for non-public pages
    if not user.is_authenticated:
        return False
    
    # Check if user is specifically allowed
    if page.allowed_users.filter(id=user.id).exists():
        return True
    
    # If no required roles, any authenticated user can access
    if not page.required_roles or len(page.required_roles) == 0:
        return True
    
    # Check if user has any of the required roles
    return bool(user_roles & set(page.required_roles))
