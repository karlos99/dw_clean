from .models_pages import Page


def navigation_pages(request):
    """
    Add navigation pages to template context.
    Only shows pages the current user has access to.
    """
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
        # Show only public pages for anonymous users
        pages = Page.objects.filter(
            status='active',
            is_public=True,
            show_in_menu=True,
            parent_page__isnull=True  # Top-level pages only
        ).order_by('menu_order', 'name')
        
        return {
            'navigation_pages': pages,
            'usage_page_access': False,
            'usage_page_url': usage_page.url_path if usage_page else '/users/usage/',
        }
    
    # Get user's roles
    user_roles = set()
    if hasattr(request.user, 'active_role') and request.user.active_role:
        user_roles.add(request.user.active_role.name)
    else:
        user_roles = set(request.user.roles.values_list('name', flat=True))
    
    # Get all active pages that should show in menu
    all_pages = Page.objects.filter(
        status='active',
        show_in_menu=True,
        parent_page__isnull=True  # Top-level pages only
    ).order_by('menu_order', 'name')
    
    # Filter pages by access
    accessible_pages = []
    for page in all_pages:
        # Public pages are always accessible
        if page.is_public:
            accessible_pages.append(page)
            continue
        
        # If no required roles, any authenticated user can access
        if not page.required_roles or len(page.required_roles) == 0:
            accessible_pages.append(page)
            continue
        
        # Check if user has any of the required roles
        if user_roles & set(page.required_roles):
            accessible_pages.append(page)
    
    return {
        'navigation_pages': accessible_pages,
        'usage_page_access': usage_page_access,
        'usage_page_url': usage_page.url_path if usage_page else '/users/usage/',
    }


def user_can_access_page(user, page):
    """
    Helper function to check if a user can access a specific page.
    """
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
    
    # Get user's roles
    user_roles = set()
    if hasattr(user, 'active_role') and user.active_role:
        user_roles.add(user.active_role.name)
    else:
        user_roles = set(user.roles.values_list('name', flat=True))
    
    # Check if user has any of the required roles
    return bool(user_roles & set(page.required_roles))
