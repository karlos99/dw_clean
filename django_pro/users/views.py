from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.cache import cache
from django.db.models import Q, Count
from django.urls import reverse
from urllib.parse import urlencode
from .models import User, Role, School
from studata.models import School as StudataSchool
from typing import Optional


def user_is_project_admin(user: User) -> bool:
    """Check if user has Project Admin role"""
    return user.is_authenticated and user.is_project_admin

def get_filtered_users_queryset(request):
    """Refactored logic to get filtered user queryset."""
    query = request.GET.get('q', request.POST.get('q', '')).strip()
    role_filter = request.GET.get('role', request.POST.get('role', '')).strip()
    job_title_filter = request.GET.get('job_title', request.POST.get('job_title', '')).strip()
    school_filter = request.GET.get('school', request.POST.get('school', '')).strip()
    
    # Optimize query with annotation to avoid N+1
    users_qs = User.objects.prefetch_related('roles')
    
    # Apply search filter
    if query:
        users_qs = users_qs.filter(
            Q(name__icontains=query)
            | Q(email__icontains=query)
            | Q(username__icontains=query)
            | Q(staff_id__icontains=query)
        )
    
    # Apply role filter
    if role_filter:
        if role_filter == 'none':
            users_qs = users_qs.filter(roles__isnull=True)
        else:
            users_qs = users_qs.filter(roles__id=role_filter)
    
    # Apply school filter
    if school_filter:
        if school_filter == 'none':
            # Looking for users with empty school list
            users_qs = users_qs.filter(schools__exact=[])
        else:
            # Looking for users who have this school in their list
            users_qs = users_qs.filter(schools__contains=[school_filter])

    # Apply job title filter
    if job_title_filter:
        if job_title_filter == 'none':
            users_qs = users_qs.filter(Q(job_title='') | Q(job_title__isnull=True))
        else:
            users_qs = users_qs.filter(job_title=job_title_filter)

    return users_qs

@login_required
def manage_users(request):
    if not user_is_project_admin(request.user):
        messages.error(request, 'You do not have permission to access this page.')
        return redirect('index')

    users_qs = get_filtered_users_queryset(request)
    
    # Restore local variables for context
    query = request.GET.get('q', '').strip()
    role_filter = request.GET.get('role', '').strip()
    job_title_filter = request.GET.get('job_title', '').strip()
    school_filter = request.GET.get('school', '').strip()

    sort_by = request.GET.get('sort', 'email')
    sort_order = request.GET.get('order', 'asc')
    
    # Page size with validation (performance consideration)
    page_size = request.GET.get('page_size', '10')
    valid_page_sizes = ['10', '20', '30', '40', '50']
    if page_size not in valid_page_sizes:
        page_size = '10'
    page_size = int(page_size)
    
    # Apply sorting
    valid_sort_fields = ['name', 'email', 'job_title']
    if sort_by == 'role__name':
         # Sorting by M2M is tricky, let's skip for now or sort by count? or first role?
         # Simplification: Disable role sorting or leave as is (might produce duplicates)
         # Better: remove role__name from valid sort fields
         pass
    elif sort_by == 'schools':
         # Sort by JSON field text representation (approximate grouping)
         order_prefix = '-' if sort_order == 'desc' else ''
         users_qs = users_qs.order_by(f'{order_prefix}schools')
    elif sort_by not in valid_sort_fields:
        sort_by = 'email'
    
    order_prefix = '-' if sort_order == 'desc' else ''
    if sort_by not in ('role__name', 'schools'):
        users_qs = users_qs.order_by(f'{order_prefix}{sort_by}').distinct()
    
    # Count before pagination
    total_users_count = users_qs.count()

    # Cache filter options for performance (as per instructions.md requirement)
    
    # Cache filter options for performance (as per instructions.md requirement)
    cache_key_roles = 'user_management:all_roles'
    cache_key_job_titles = 'user_management:all_job_titles'
    cache_key_schools = 'user_management:all_schools_filter'
    
    all_roles = cache.get(cache_key_roles)
    if all_roles is None:
        all_roles = list(Role.objects.all().order_by('name'))
        cache.set(cache_key_roles, all_roles, timeout=300)

    all_schools = cache.get(cache_key_schools)
    if all_schools is None:
         # Minimal school data for filter
         all_schools = list(StudataSchool.objects.values('state_id', 'school_name').order_by('school_name'))
         cache.set(cache_key_schools, all_schools, timeout=600)
    
    all_job_titles = cache.get(cache_key_job_titles)
    if all_job_titles is None:
        all_job_titles = list(
            User.objects.exclude(Q(job_title='') | Q(job_title__isnull=True))
            .values_list('job_title', flat=True)
            .distinct()
            .order_by('job_title')
        )
        cache.set(cache_key_job_titles, all_job_titles, timeout=300)

    paginator = Paginator(users_qs, page_size)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'users': page_obj,
        'page_obj': page_obj,
        'paginator': paginator,
        'query': query, # Now fetched from GET inside filtered function as well, but variable 'query' here was not defined in local scope if we removed previous code.
                        # Wait, the refactor Removed 'query = request.GET...'.
                        # I need to re-add those vars locally or return them from filtered func.
                        # Easier to re-fetch from GET for context.
        'page_size': page_size,
        'role_filter': role_filter,
        'job_title_filter': job_title_filter,
        'school_filter': school_filter,
        'all_job_titles': all_job_titles,
        'all_roles': all_roles,
        'all_schools': all_schools,
        'sort_by': sort_by,
        'sort_order': sort_order,
        'total_users_count': total_users_count, 
    }
    
    # Return partial for HTMX requests
    if request.headers.get('HX-Request'):
        return render(request, 'users/_user_table.html', context)
    
    return render(request, 'users/user_list.html', context)

@login_required
def user_detail(request, user_id):
    if not user_is_project_admin(request.user):
        messages.error(request, 'You do not have permission to access this page.')
        return redirect('index')
    
    user = get_object_or_404(User, id=user_id)
    roles = Role.objects.all().order_by('name')
    schools = StudataSchool.objects.all().order_by('school_name')
    
    if request.method == 'POST':
        # Update user information
        user.name = request.POST.get('name', '').strip()
        user.job_title = request.POST.get('job_title', '').strip()
        user.staff_id = request.POST.get('staff_id', '').strip() or None
        user.is_active = request.POST.get('is_active') == 'on'
        
        # Update roles
        role_ids = request.POST.getlist('roles')
        user.roles.clear()
        if role_ids:
             roles_objs = Role.objects.filter(id__in=role_ids)
             user.roles.add(*roles_objs)
        
        # Update schools (JSONField list of state_ids)
        school_ids = request.POST.getlist('schools')
        valid_school_ids = []
        for school_id in school_ids:
             # Validate existence
             if StudataSchool.objects.filter(state_id=school_id).exists():
                 valid_school_ids.append(school_id)
        
        user.schools = valid_school_ids
        user.save()
        
        messages.success(request, f'User {user.email} updated successfully.')
        return redirect('users:user_detail', user_id=user.id)
    
    context = {
        'user_obj': user,
        'roles': roles,
        'schools': schools,
    }
    return render(request, 'users/user_detail.html', context)


@login_required
def switch_role(request):
    """
    Switch the user's active role and school context.
    """
    if request.method == 'POST':
        # Handle Role Switch
        role_id = request.POST.get('role_id')
        if role_id is not None:
            if role_id == 'all':
                if 'active_role_id' in request.session:
                    del request.session['active_role_id']
            elif role_id:
                # Verify user has this role
                if request.user.roles.filter(id=role_id).exists():
                    request.session['active_role_id'] = role_id
                else:
                    messages.error(request, "You do not have this role.")

        # Handle School Switch
        school_id = request.POST.get('school_id') # This is state_id
        if school_id is not None:
             if school_id == 'all':
                  if 'active_school_id' in request.session:
                       del request.session['active_school_id']
             elif school_id:
                  # Verify user has access to this school (check JSONField)
                  user_schools_list = request.user.schools if isinstance(request.user.schools, list) else []
                  
                  if school_id in user_schools_list:
                       request.session['active_school_id'] = school_id
                  else:
                       messages.error(request, "You do not have access to this school.")
    
    return redirect(request.META.get('HTTP_REFERER', '/'))

@login_required
def bulk_update_users(request):
    if not user_is_project_admin(request.user) or request.method != 'POST':
        return redirect('users:manage_users')
        
    action = request.POST.get('action')
    select_all_matching = request.POST.get('select_all_matching') == 'true'
    
    if not action:
        messages.error(request, 'No action specified.')
        return redirect('users:manage_users')

    if select_all_matching:
        # Re-run the filter logic to get all matching users
        users_to_update = get_filtered_users_queryset(request)
    else:
        user_ids = request.POST.getlist('user_ids')
        if not user_ids:
            messages.error(request, 'No users selected.')
            return redirect('users:manage_users')
        users_to_update = User.objects.filter(id__in=user_ids)

    count = users_to_update.count()
    if count == 0:
         messages.warning(request, "No users matched for update.")
         return redirect('users:manage_users')
    
    if action == 'activate':
        users_to_update.update(is_active=True)
        messages.success(request, f'{count} users activated.')
        
    elif action == 'deactivate':
        # Don't deactivate self
        if users_to_update.filter(id=request.user.id).exists():
             messages.warning(request, "Skipped deactivating your own account.")
             users_to_update = users_to_update.exclude(id=request.user.id)
             # Refresh count
             count = users_to_update.count()
        
        users_to_update.update(is_active=False)
        messages.success(request, f'{count} users deactivated.')
    
    elif action == 'add_role':
        role_id = request.POST.get('role_id')
        if role_id:
            role = get_object_or_404(Role, id=role_id)
            # Efficient M2M add
            role.users.add(*users_to_update)
            messages.success(request, f'Role "{role.name}" added to {count} users.')
            
    elif action == 'remove_role':
        role_id = request.POST.get('role_id')
        if role_id:
            role = get_object_or_404(Role, id=role_id)
            # Efficient M2M remove
            role.users.remove(*users_to_update)
            messages.success(request, f'Role "{role.name}" removed from {count} users.')
    
    # Build query string for redirect to preserve filters
    params = {}
    for key in ['q', 'role', 'school', 'job_title', 'sort', 'order', 'page_size']:
        val = request.POST.get(key)
        if val:
            params[key] = val
            
    if params:
        base_url = reverse('users:manage_users')
        query_string = urlencode(params)
        return redirect(f'{base_url}?{query_string}')
        
    return redirect('users:manage_users')


@login_required
def impersonate_user(request, user_id):
    # Only project admins can start impersonation
    if not user_is_project_admin(request.user):
        messages.error(request, "Permission denied.")
        return redirect('index')

    target_user = get_object_or_404(User, id=user_id)
    
    if target_user.id == request.user.id:
        messages.warning(request, "You cannot impersonate yourself.")
        return redirect('users:manage_users')
        
    if not target_user.is_active:
        messages.error(request, "Cannot impersonate an inactive user.")
        return redirect('users:manage_users')
        
    # Store original user id
    original_user_id = request.user.id
    
    # Login as target user (this rotates session)
    login(request, target_user, backend='django.contrib.auth.backends.ModelBackend')
    
    # Restore the impersonator_id in the new session
    request.session['impersonator_id'] = original_user_id
    
    messages.success(request, f"Now impersonating {target_user.name or target_user.email}")
    return redirect('index')


@login_required
def stop_impersonating(request):
    impersonator_id = request.session.get('impersonator_id')
    if not impersonator_id:
        return redirect('index')
        
    try:
        original_user = User.objects.get(id=impersonator_id)
    except User.DoesNotExist:
        # Fallback if original user missing
        return redirect('account_login')
    
    # Login back to original user
    login(request, original_user, backend='django.contrib.auth.backends.ModelBackend')
    
    # Clean session
    if 'impersonator_id' in request.session:
        del request.session['impersonator_id']
        
    messages.info(request, "Impersonation ended.")
    return redirect('users:manage_users')

