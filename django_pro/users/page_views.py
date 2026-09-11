from django.shortcuts import render, redirect, get_object_or_404, reverse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.contrib.auth import get_user_model
from django.http import JsonResponse, HttpResponseForbidden
from django.utils.text import slugify
import re
import json

from .models_pages import Page, NavbarConfig
from .models import Role
from dataexploration.models import DefaultColumnSelection
from dataexploration.constants import COLUMN_DEFINITIONS, COLUMN_CATEGORIES, DEFAULT_COLUMNS
from dataexploration.column_utils import get_test_columns
from dataexploration.school_year_utils import get_year_labels_map


def _build_parent_page_options():
    pages = list(Page.objects.all().only('id', 'name', 'parent_page_id').order_by('menu_order', 'name', 'id'))
    children_by_parent = {}
    for page in pages:
        children_by_parent.setdefault(page.parent_page_id, []).append(page)

    options = []

    def walk(parent_id, depth, path_ids):
        for item in children_by_parent.get(parent_id, []):
            if item.id in path_ids:
                continue
            options.append({
                'id': item.id,
                'label': f"Level {depth + 1} · {item.name}",
            })
            walk(item.id, depth + 1, path_ids | {item.id})

    walk(None, 0, set())
    return options


def _build_admin_tree():
    """Build a nested [{'page': Page, 'children': [...]}] structure of ALL pages
    (including inactive/draft), ordered for the admin tree + menu preview."""
    pages = list(Page.objects.all().only(
        'id', 'name', 'slug', 'icon', 'status', 'is_public', 'show_in_menu',
        'menu_order', 'url_path', 'is_menu_category', 'parent_page_id',
    ).order_by('menu_order', 'name', 'id'))
    children_by_parent = {}
    for page in pages:
        children_by_parent.setdefault(page.parent_page_id, []).append(page)

    def walk(parent_id):
        nodes = []
        for page in children_by_parent.get(parent_id, []):
            nodes.append({'page': page, 'children': walk(page.id)})
        return nodes

    return walk(None)


def _creates_parent_cycle(page, candidate_parent):
    current = candidate_parent
    seen_ids = set()
    while current is not None:
        if current.id == page.id:
            return True
        if current.id in seen_ids:
            return True
        seen_ids.add(current.id)
        current = current.parent_page
    return False


def _parse_emails(raw_value: str) -> list[str]:
    if not raw_value:
        return []
    tokens = re.split(r'[\n,;]+', raw_value)
    cleaned = []
    seen = set()
    for token in tokens:
        email = token.strip()
        if not email:
            continue
        email_key = email.lower()
        if email_key in seen:
            continue
        seen.add(email_key)
        cleaned.append(email)
    return cleaned


def _resolve_users_by_emails(raw_value: str):
    User = get_user_model()
    found_users = []
    not_found = []
    for email in _parse_emails(raw_value):
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            not_found.append(email)
            continue
        found_users.append(user)
    return found_users, not_found


def user_is_project_admin(user):
    """Check if user is a Project Admin."""
    if user.is_superuser:
        return True
    if hasattr(user, 'active_role') and user.active_role:
        return user.active_role.name == 'Project Admin'
    return user.roles.filter(name='Project Admin').exists()


@login_required
def manage_pages(request):
    """List all pages with filtering and sorting."""
    if not user_is_project_admin(request.user):
        messages.error(request, 'You do not have permission to access this page.')
        return redirect('landing:index')
    
    # Get filter parameters
    search = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
    sort_by = request.GET.get('sort_by', 'app_reference')
    sort_dir = request.GET.get('sort_dir', 'asc')
    active_tab = request.GET.get('tab', 'pages')
    if active_tab not in {'pages', 'navbar'}:
        active_tab = 'pages'
    
    # Base queryset
    pages = Page.objects.all().select_related('parent_page').prefetch_related('allowed_users', 'menu_allowed_users')
    
    # Apply search filter
    if search:
        pages = pages.filter(
            Q(name__icontains=search) |
            Q(slug__icontains=search) |
            Q(description__icontains=search) |
            Q(url_path__icontains=search) |
            Q(app_reference__icontains=search)
        )
    
    # Apply status filter
    if status_filter:
        pages = pages.filter(status=status_filter)
    
    # Apply sorting
    sort_field = sort_by
    if sort_dir == 'desc':
        sort_field = f'-{sort_by}'
    pages = pages.order_by(sort_field, 'menu_order', 'name', 'id')
    
    # Get all roles for the add/edit form
    all_roles = Role.objects.all().order_by('name')
    
    context = {
        'pages': pages,
        'page_tree': _build_admin_tree(),
        'navbar_config': NavbarConfig.get(),
        'parent_page_options': _build_parent_page_options(),
        'all_roles': all_roles,
        'search': search,
        'status_filter': status_filter,
        'sort_by': sort_by,
        'sort_dir': sort_dir,
        'active_tab': active_tab,
    }
    
    return render(request, 'users/manage_pages.html', context)


@login_required
def navbar_settings(request):
    """Save the navbar configuration from the Navbar tab in Page Management."""
    if not user_is_project_admin(request.user):
        messages.error(request, 'You do not have permission to access this page.')
        return redirect('landing:index')

    if request.method == 'POST':
        config = NavbarConfig.get()
        config.sticky = request.POST.get('sticky') == 'on'
        navbar_style = request.POST.get('navbar_style', 'classic')
        if navbar_style not in dict(NavbarConfig.NAVBAR_STYLE_CHOICES):
            navbar_style = 'classic'
        config.navbar_style = navbar_style
        label_case = request.POST.get('label_case', 'uppercase')
        if label_case not in {'uppercase', 'normal'}:
            label_case = 'uppercase'
        config.label_case = label_case
        submenu_style = request.POST.get('submenu_style', 'dropdown')
        if submenu_style not in {'dropdown', 'megamenu'}:
            submenu_style = 'dropdown'
        config.submenu_style = submenu_style
        config.updated_by = request.user
        config.save()
        messages.success(request, 'Navbar settings saved.')
        return redirect(reverse('users:manage_pages') + '?tab=navbar')

    return redirect(reverse('users:manage_pages') + '?tab=navbar')


@login_required
def reorder_page(request):
    """Reorder and re-parent a page via drag-and-drop from Page Management."""
    if not user_is_project_admin(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)

    try:
        data = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    page_id = data.get('page_id')
    if not page_id:
        return JsonResponse({'success': False, 'error': 'Missing page_id'}, status=400)
    page = get_object_or_404(Page, id=page_id)

    old_parent_id = page.parent_page_id
    new_parent_id = data.get('new_parent_id') or None

    if new_parent_id:
        new_parent = get_object_or_404(Page, id=new_parent_id)
        if new_parent.id == page.id or _creates_parent_cycle(page, new_parent):
            return JsonResponse({'success': False, 'error': 'Invalid parent (would create a menu loop).'}, status=400)
        page.parent_page = new_parent
    else:
        page.parent_page = None

    # Recompute order within the new parent's children
    siblings = list(Page.objects.filter(parent_page_id=new_parent_id).order_by('menu_order', 'name', 'id'))
    siblings = [s for s in siblings if s.id != page.id]

    insert_at = None
    if data.get('after_id'):
        insert_at = next(
            (i + 1 for i, s in enumerate(siblings) if s.id == int(data['after_id'])), None
        )
    elif data.get('before_id'):
        insert_at = next(
            (i for i, s in enumerate(siblings) if s.id == int(data['before_id'])), None
        )
    if insert_at is None:
        insert_at = len(siblings)
    siblings.insert(insert_at, page)

    for i, s in enumerate(siblings):
        if s.id == page.id:
            page.menu_order = i
        else:
            Page.objects.filter(id=s.id).update(menu_order=i)
    page.save()

    # Reindex the previous parent's remaining children so no gaps remain
    if old_parent_id and old_parent_id != new_parent_id:
        for i, s in enumerate(Page.objects.filter(parent_page_id=old_parent_id).order_by('menu_order', 'name', 'id')):
            Page.objects.filter(id=s.id).update(menu_order=i)

    return JsonResponse({'success': True})


@login_required
def add_page(request):
    """Add a new page."""
    if not user_is_project_admin(request.user):
        messages.error(request, 'You do not have permission to access this page.')
        return redirect('landing:index')
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        slug = request.POST.get('slug', '').strip()
        description = request.POST.get('description', '').strip()
        url_path = request.POST.get('url_path', '').strip()
        icon = request.POST.get('icon', '').strip()
        app_reference = request.POST.get('app_reference', '').strip()
        status = request.POST.get('status', 'active')
        is_public = request.POST.get('is_public') == 'on'
        show_in_menu = request.POST.get('show_in_menu') == 'on'
        is_menu_category = request.POST.get('is_menu_category') == 'on'
        if is_menu_category:
            show_in_menu = True
        menu_order = int(request.POST.get('menu_order', 0))
        parent_id = request.POST.get('parent_page')
        
        # Get required roles
        required_roles = request.POST.getlist('required_roles')
        menu_roles = request.POST.getlist('menu_roles')
        
        # Get allowed user emails
        allowed_user_emails = request.POST.get('allowed_user_emails', '').strip()
        menu_allowed_user_emails = request.POST.get('menu_allowed_user_emails', '').strip()
        
        # Auto-generate slug if not provided
        if not slug:
            slug = slugify(name)
        
        # Validate
        if not name:
            messages.error(request, 'Page name is required.')
            return redirect('users:manage_pages')
        
        if not url_path and not is_menu_category:
            messages.error(request, 'URL path is required.')
            return redirect('users:manage_pages')
        
        # Check for duplicate slug
        if Page.objects.filter(slug=slug).exists():
            messages.error(request, f'A page with slug "{slug}" already exists.')
            return redirect('users:manage_pages')
        
        # Create page
        parent_page = None
        if parent_id:
            try:
                parent_page = Page.objects.get(id=parent_id)
            except Page.DoesNotExist:
                pass
        
        page = Page.objects.create(
            name=name,
            slug=slug,
            app_reference=app_reference,
            description=description,
            url_path=url_path,
            icon=icon,
            status=status,
            is_public=is_public,
            show_in_menu=show_in_menu,
            is_menu_category=is_menu_category,
            menu_order=menu_order,
            parent_page=parent_page,
            required_roles=required_roles,
            menu_roles=menu_roles,
            created_by=request.user
        )
        
        # Add allowed users by email
        users, not_found = _resolve_users_by_emails(allowed_user_emails)
        if users:
            page.allowed_users.add(*users)
        for email in not_found:
            messages.warning(request, f'User with email "{email}" not found.')

        menu_users, menu_not_found = _resolve_users_by_emails(menu_allowed_user_emails)
        if menu_users:
            page.menu_allowed_users.add(*menu_users)
        for email in menu_not_found:
            messages.warning(request, f'User with email "{email}" not found for menu visibility.')
        
        messages.success(request, f'Page "{page.name}" created successfully.')
        return redirect('users:manage_pages')
    
    return redirect('users:manage_pages')


@login_required
def edit_page(request, page_id):
    """Edit an existing page."""
    if not user_is_project_admin(request.user):
        messages.error(request, 'You do not have permission to access this page.')
        return redirect('landing:index')
    
    page = get_object_or_404(Page, id=page_id)
    
    if request.method == 'POST':
        page.name = request.POST.get('name', '').strip()
        page.slug = request.POST.get('slug', '').strip()
        page.app_reference = request.POST.get('app_reference', '').strip()
        page.description = request.POST.get('description', '').strip()
        page.url_path = request.POST.get('url_path', '').strip()
        page.icon = request.POST.get('icon', '').strip()
        page.status = request.POST.get('status', 'active')
        page.is_public = request.POST.get('is_public') == 'on'
        page.show_in_menu = request.POST.get('show_in_menu') == 'on'
        page.is_menu_category = request.POST.get('is_menu_category') == 'on'
        if page.is_menu_category:
            page.show_in_menu = True
        page.menu_order = int(request.POST.get('menu_order', 0))
        
        parent_id = request.POST.get('parent_page')
        if parent_id:
            try:
                candidate_parent = Page.objects.get(id=parent_id)
                if candidate_parent.id == page.id or _creates_parent_cycle(page, candidate_parent):
                    messages.error(request, 'Invalid parent page selection (would create a menu loop).')
                    return redirect('users:manage_pages')
                page.parent_page = candidate_parent
            except Page.DoesNotExist:
                page.parent_page = None
        else:
            page.parent_page = None
        
        # Get required roles
        page.required_roles = request.POST.getlist('required_roles')
        page.menu_roles = request.POST.getlist('menu_roles')
        
        # Get allowed user emails
        allowed_user_emails = request.POST.get('allowed_user_emails', '').strip()
        menu_allowed_user_emails = request.POST.get('menu_allowed_user_emails', '').strip()
        
        # Validate
        if not page.name:
            messages.error(request, 'Page name is required.')
            return redirect('users:manage_pages')
        
        if not page.url_path and not page.is_menu_category:
            messages.error(request, 'URL path is required.')
            return redirect('users:manage_pages')
        
        # Check for duplicate slug (excluding current page)
        if Page.objects.filter(slug=page.slug).exclude(id=page.id).exists():
            messages.error(request, f'A page with slug "{page.slug}" already exists.')
            return redirect('users:manage_pages')
        
        page.save()
        
        page.allowed_users.clear()
        users, not_found = _resolve_users_by_emails(allowed_user_emails)
        if users:
            page.allowed_users.add(*users)
        for email in not_found:
            messages.warning(request, f'User with email "{email}" not found.')

        page.menu_allowed_users.clear()
        menu_users, menu_not_found = _resolve_users_by_emails(menu_allowed_user_emails)
        if menu_users:
            page.menu_allowed_users.add(*menu_users)
        for email in menu_not_found:
            messages.warning(request, f'User with email "{email}" not found for menu visibility.')
        
        messages.success(request, f'Page "{page.name}" updated successfully.')
        return redirect('users:manage_pages')
    
    return redirect('users:manage_pages')


@login_required
def delete_page(request, page_id):
    """Delete a page."""
    if not user_is_project_admin(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)
    
    page = get_object_or_404(Page, id=page_id)
    
    if request.method == 'POST':
        page_name = page.name
        page.delete()
        
        messages.success(request, f'Page "{page_name}" deleted successfully.')
        return JsonResponse({'success': True})
    
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)


@login_required
def get_page_detail(request, page_id):
    """Get page details as JSON for editing modal."""
    if not user_is_project_admin(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)
    
    page = get_object_or_404(Page, id=page_id)
    
    # Get allowed user emails
    allowed_user_emails = ', '.join(page.allowed_users.values_list('email', flat=True))
    menu_allowed_user_emails = ', '.join(page.menu_allowed_users.values_list('email', flat=True))
    
    data = {
        'success': True,
        'page': {
            'id': page.id,
            'name': page.name,
            'slug': page.slug,
            'app_reference': page.app_reference,
            'description': page.description,
            'url_path': page.url_path,
            'is_menu_category': page.is_menu_category,
            'icon': page.icon,
            'status': page.status,
            'is_public': page.is_public,
            'show_in_menu': page.show_in_menu,
            'menu_order': page.menu_order,
            'parent_page_id': page.parent_page.id if page.parent_page else None,
            'required_roles': page.required_roles,
            'menu_roles': page.menu_roles,
            'allowed_user_emails': allowed_user_emails,
            'menu_allowed_user_emails': menu_allowed_user_emails,
        }
    }
    
    return JsonResponse(data)


@login_required
def manage_default_columns(request):
    """Admin interface for setting default columns shown to users in data exploration."""
    if not user_is_project_admin(request.user):
        return HttpResponseForbidden("You don't have permission to manage default columns.")

    test_structure = get_test_columns()
    from dataexploration.column_utils import get_ap_columns, get_sat_columns
    ap_structure = get_ap_columns()
    sat_structure = get_sat_columns()
    
    # Get saved default columns or fall back to constants
    selection = DefaultColumnSelection.objects.order_by('-updated_at', '-id').first()
    if selection and selection.columns:
        default_columns = selection.columns
    else:
        default_columns = DEFAULT_COLUMNS

    from dataexploration.school_year_utils import get_year_labels_map, get_year_alias_map
    _all_years = list(test_structure.keys()) + list(ap_structure.keys()) + list(sat_structure.keys())

    if request.method == 'POST':
        import json
        columns_json = request.POST.get('columns', '[]')
        try:
            try:
                columns = json.loads(columns_json)
                if isinstance(columns, str):
                    columns = [columns]
            except (json.JSONDecodeError, TypeError, ValueError):
                columns = request.POST.getlist('columns')

            selection, created = DefaultColumnSelection.objects.update_or_create(
                id=1,
                defaults={'columns': columns}
            )
            messages.success(request, 'Default columns updated successfully.')
            return render(request, 'users/manage_default_columns.html', {
                'column_categories': COLUMN_CATEGORIES,
                'column_definitions': COLUMN_DEFINITIONS,
                'test_structure': test_structure,
                'ap_structure': ap_structure,
                'sat_structure': sat_structure,
                'selected_columns': columns,
                'year_labels': get_year_labels_map(_all_years),
                'year_to_alias': get_year_alias_map(),
                'success': True,
            })
        except Exception as e:
            messages.error(request, f'Error saving default columns: {str(e)}')
            return render(request, 'users/manage_default_columns.html', {
                'column_categories': COLUMN_CATEGORIES,
                'column_definitions': COLUMN_DEFINITIONS,
                'test_structure': test_structure,
                'ap_structure': ap_structure,
                'sat_structure': sat_structure,
                'selected_columns': default_columns,
                'year_labels': get_year_labels_map(_all_years),
                'year_to_alias': get_year_alias_map(),
                'error': str(e),
            })

    context = {
        'column_categories': COLUMN_CATEGORIES,
        'column_definitions': COLUMN_DEFINITIONS,
        'test_structure': test_structure,
        'ap_structure': ap_structure,
                'sat_structure': sat_structure,
        'selected_columns': default_columns,
        'year_labels': get_year_labels_map(_all_years),
        'year_to_alias': get_year_alias_map(),
    }

    return render(request, 'users/manage_default_columns.html', context)
