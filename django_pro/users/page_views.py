from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse, HttpResponseForbidden
from django.utils.text import slugify

from .models_pages import Page
from .models import Role
from dataexploration.models import DefaultColumnSelection
from dataexploration.constants import COLUMN_DEFINITIONS, COLUMN_CATEGORIES, DEFAULT_COLUMNS
from dataexploration.column_utils import get_test_columns


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
        return redirect('index')
    
    # Get filter parameters
    search = request.GET.get('search', '')
    status_filter = request.GET.get('status', '')
    sort_by = request.GET.get('sort_by', 'menu_order')
    sort_dir = request.GET.get('sort_dir', 'asc')
    
    # Base queryset
    pages = Page.objects.all().prefetch_related('allowed_users')
    
    # Apply search filter
    if search:
        pages = pages.filter(
            Q(name__icontains=search) |
            Q(slug__icontains=search) |
            Q(description__icontains=search) |
            Q(url_path__icontains=search)
        )
    
    # Apply status filter
    if status_filter:
        pages = pages.filter(status=status_filter)
    
    # Apply sorting
    sort_field = sort_by
    if sort_dir == 'desc':
        sort_field = f'-{sort_by}'
    pages = pages.order_by(sort_field, 'id')
    
    # Get all roles for the add/edit form
    all_roles = Role.objects.all().order_by('name')
    
    context = {
        'pages': pages,
        'all_roles': all_roles,
        'search': search,
        'status_filter': status_filter,
        'sort_by': sort_by,
        'sort_dir': sort_dir,
    }
    
    return render(request, 'users/manage_pages.html', context)


@login_required
def add_page(request):
    """Add a new page."""
    if not user_is_project_admin(request.user):
        messages.error(request, 'You do not have permission to access this page.')
        return redirect('index')
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        slug = request.POST.get('slug', '').strip()
        description = request.POST.get('description', '').strip()
        url_path = request.POST.get('url_path', '').strip()
        icon = request.POST.get('icon', '').strip()
        status = request.POST.get('status', 'active')
        is_public = request.POST.get('is_public') == 'on'
        show_in_menu = request.POST.get('show_in_menu') == 'on'
        menu_order = int(request.POST.get('menu_order', 0))
        parent_id = request.POST.get('parent_page')
        
        # Get required roles
        required_roles = request.POST.getlist('required_roles')
        
        # Get allowed user emails
        allowed_user_emails = request.POST.get('allowed_user_emails', '').strip()
        
        # Auto-generate slug if not provided
        if not slug:
            slug = slugify(name)
        
        # Validate
        if not name:
            messages.error(request, 'Page name is required.')
            return redirect('users:manage_pages')
        
        if not url_path:
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
            description=description,
            url_path=url_path,
            icon=icon,
            status=status,
            is_public=is_public,
            show_in_menu=show_in_menu,
            menu_order=menu_order,
            parent_page=parent_page,
            required_roles=required_roles,
            created_by=request.user
        )
        
        # Add allowed users by email
        if allowed_user_emails:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            
            emails = [e.strip() for e in allowed_user_emails.split(',') if e.strip()]
            for email in emails:
                try:
                    user = User.objects.get(email=email)
                    page.allowed_users.add(user)
                except User.DoesNotExist:
                    messages.warning(request, f'User with email "{email}" not found.')
        
        messages.success(request, f'Page "{page.name}" created successfully.')
        return redirect('users:manage_pages')
    
    return redirect('users:manage_pages')


@login_required
def edit_page(request, page_id):
    """Edit an existing page."""
    if not user_is_project_admin(request.user):
        messages.error(request, 'You do not have permission to access this page.')
        return redirect('index')
    
    page = get_object_or_404(Page, id=page_id)
    
    if request.method == 'POST':
        page.name = request.POST.get('name', '').strip()
        page.slug = request.POST.get('slug', '').strip()
        page.description = request.POST.get('description', '').strip()
        page.url_path = request.POST.get('url_path', '').strip()
        page.icon = request.POST.get('icon', '').strip()
        page.status = request.POST.get('status', 'active')
        page.is_public = request.POST.get('is_public') == 'on'
        page.show_in_menu = request.POST.get('show_in_menu') == 'on'
        page.menu_order = int(request.POST.get('menu_order', 0))
        
        parent_id = request.POST.get('parent_page')
        if parent_id:
            try:
                page.parent_page = Page.objects.get(id=parent_id)
            except Page.DoesNotExist:
                page.parent_page = None
        else:
            page.parent_page = None
        
        # Get required roles
        page.required_roles = request.POST.getlist('required_roles')
        
        # Get allowed user emails
        allowed_user_emails = request.POST.get('allowed_user_emails', '').strip()
        
        # Validate
        if not page.name:
            messages.error(request, 'Page name is required.')
            return redirect('users:manage_pages')
        
        if not page.url_path:
            messages.error(request, 'URL path is required.')
            return redirect('users:manage_pages')
        
        # Check for duplicate slug (excluding current page)
        if Page.objects.filter(slug=page.slug).exclude(id=page.id).exists():
            messages.error(request, f'A page with slug "{page.slug}" already exists.')
            return redirect('users:manage_pages')
        
        page.save()
        
        # Update allowed users by email
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        page.allowed_users.clear()
        if allowed_user_emails:
            emails = [e.strip() for e in allowed_user_emails.split(',') if e.strip()]
            for email in emails:
                try:
                    user = User.objects.get(email=email)
                    page.allowed_users.add(user)
                except User.DoesNotExist:
                    messages.warning(request, f'User with email "{email}" not found.')
        
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
    
    data = {
        'success': True,
        'page': {
            'id': page.id,
            'name': page.name,
            'slug': page.slug,
            'description': page.description,
            'url_path': page.url_path,
            'icon': page.icon,
            'status': page.status,
            'is_public': page.is_public,
            'show_in_menu': page.show_in_menu,
            'menu_order': page.menu_order,
            'parent_page_id': page.parent_page.id if page.parent_page else None,
            'required_roles': page.required_roles,
            'allowed_user_emails': allowed_user_emails,
        }
    }
    
    return JsonResponse(data)


@login_required
def manage_default_columns(request):
    """Admin interface for setting default columns shown to users in data exploration."""
    if not user_is_project_admin(request.user):
        return HttpResponseForbidden("You don't have permission to manage default columns.")

    test_structure = get_test_columns()
    
    # Get saved default columns or fall back to constants
    selection = DefaultColumnSelection.objects.order_by('-updated_at', '-id').first()
    if selection and selection.columns:
        default_columns = selection.columns
    else:
        default_columns = DEFAULT_COLUMNS

    if request.method == 'POST':
        import json
        columns_json = request.POST.get('columns', '[]')
        try:
            columns = json.loads(columns_json)
            selection, created = DefaultColumnSelection.objects.update_or_create(
                id=1,
                defaults={'columns': columns}
            )
            messages.success(request, 'Default columns updated successfully.')
            return render(request, 'users/manage_default_columns.html', {
                'column_categories': COLUMN_CATEGORIES,
                'column_definitions': COLUMN_DEFINITIONS,
                'test_structure': test_structure,
                'selected_columns': columns,
                'success': True,
            })
        except Exception as e:
            messages.error(request, f'Error saving default columns: {str(e)}')
            return render(request, 'users/manage_default_columns.html', {
                'column_categories': COLUMN_CATEGORIES,
                'column_definitions': COLUMN_DEFINITIONS,
                'test_structure': test_structure,
                'selected_columns': default_columns,
                'error': str(e),
            })

    context = {
        'column_categories': COLUMN_CATEGORIES,
        'column_definitions': COLUMN_DEFINITIONS,
        'test_structure': test_structure,
        'selected_columns': default_columns,
    }

    return render(request, 'users/manage_default_columns.html', context)
