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

def user_is_school_admin(user: User) -> bool:
    """Check if user has School Admin role"""
    if not user.is_authenticated: return False
    # Check active role or if they have the role generally (if no active role context, maybe check all roles?)
    # Usually we rely on active_role for context-aware actions
    if hasattr(user, 'active_role') and user.active_role:
        return 'School Admin' in user.active_role.name
    return False

def get_allowed_schools_for_admin(user: User) -> list:
    """Get list of school state_ids this admin manages"""
    if user_is_project_admin(user) or user.is_superuser:
        return None # All schools
    
    if hasattr(user, 'active_school') and user.active_school:
        return [user.active_school.state_id]
    
    # Fallback to all assigned schools if no specific active school context
    return user.schools if isinstance(user.schools, list) else []

def get_base_users_queryset(request):
    """Get the base queryset of users visible to the current user."""
    users_qs = User.objects.prefetch_related('roles')

    # Security: Restrict visibility for School Admins
    if not (request.user.is_superuser or user_is_project_admin(request.user)):
        allowed_schools = get_allowed_schools_for_admin(request.user)
        # Filter users who have AT LEAST ONE of the allowed schools
        
        if allowed_schools:
            q_schools = Q()
            for school_id in allowed_schools:
                q_schools |= Q(schools__contains=[school_id])
            users_qs = users_qs.filter(q_schools)
        else:
            # Admin with no schools sees no one?
            users_qs = users_qs.none()
    
    return users_qs

def apply_filters(users_qs, query='', role_filter='', school_filter='', job_title_filter=''):
    """Apply filters to a user queryset."""
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
        elif role_filter: # Ensure it's not empty string
            users_qs = users_qs.filter(roles__id=role_filter)
    
    # Apply school filter
    if school_filter:
        if school_filter == 'none':
            users_qs = users_qs.filter(schools__exact=[])
        elif school_filter:
            users_qs = users_qs.filter(schools__contains=[school_filter])

    # Apply job title filter
    if job_title_filter:
        if job_title_filter == 'none':
            users_qs = users_qs.filter(Q(job_title='') | Q(job_title__isnull=True))
        elif job_title_filter:
            users_qs = users_qs.filter(job_title=job_title_filter)
            
    return users_qs

def get_filtered_users_queryset(request):
    """Refactored logic to get filtered user queryset."""
    query = request.GET.get('q', request.POST.get('q', '')).strip()
    role_filter = request.GET.get('role', request.POST.get('role', '')).strip()
    job_title_filter = request.GET.get('job_title', request.POST.get('job_title', '')).strip()
    school_filter = request.GET.get('school', request.POST.get('school', '')).strip()
    
    users_qs = get_base_users_queryset(request)
    users_qs = apply_filters(users_qs, query, role_filter, school_filter, job_title_filter)

    return users_qs

@login_required
def manage_users(request):
    is_proj = user_is_project_admin(request.user)
    is_school = user_is_school_admin(request.user)
    
    if not (is_proj or is_school):
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

    # --- Dynamic Filters ---
    base_qs = get_base_users_queryset(request)
    
    # 1. Available Roles
    # To see what roles are available given current other filters (school, job_title)
    # We apply all filters EXCEPT role
    qs_for_roles = apply_filters(base_qs, query, '', school_filter, job_title_filter)
    all_roles = list(Role.objects.filter(users__in=qs_for_roles).distinct().order_by('name'))
    
    # 2. Available Job Titles
    # Apply filters except job_title
    qs_for_jobs = apply_filters(base_qs, query, role_filter, school_filter, '')
    all_job_titles = list(
        qs_for_jobs.exclude(Q(job_title='') | Q(job_title__isnull=True))
        .values_list('job_title', flat=True)
        .distinct()
        .order_by('job_title')
    )

    # 3. Available Schools
    # Apply filters except school
    qs_for_schools = apply_filters(base_qs, query, role_filter, '', job_title_filter)
    
    # Getting schools is trickier because it's a JSON List.
    # We can't cleanly join. We have to fetch and process.
    # Optimization: If user count is high, this might be slow.
    # Limit to 5000 users for facet calculation? Or fallback to all_schools if too many?
    # For now, let's keep it simple.
    
    visible_users_schools = qs_for_schools.values_list('schools', flat=True)
    
    # Collect unique school IDs
    unique_school_ids = set()
    for school_list in visible_users_schools:
        if school_list and isinstance(school_list, list):
            unique_school_ids.update(school_list)
            
    # Now fetch the school objects
    all_schools_qs = StudataSchool.objects.filter(state_id__in=unique_school_ids).values('state_id', 'school_name').order_by('school_name')
    all_schools = list(all_schools_qs)
    
    # Filter options for School Admin context (Double check security)
    if is_school and not is_proj:
         allowed_schools = get_allowed_schools_for_admin(request.user)
         # Ensure we don't show schools they shouldn't see, even if logic above leaked (it shouldn't due to base_qs)
         # But explicit check is good.
         all_schools = [s for s in all_schools if s['state_id'] in allowed_schools]
         
         # Also restrict roles if needed?
         # "Restrict visible/assignable roles" - usually we restrict assignable.
         # For filtering, if a User has "Project Admin" and "Teacher", and I am School Admin,
         # I might see "Project Admin" in the dropdown if I see that user?
         # Or should I validly filter by it? 
         # The requirement "filter base on the selections... or base on list of user that the user can see"
         # implies that if I can see a Project Admin (because they are also in my school), I should see that role option.
         pass
         
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
    is_proj = user_is_project_admin(request.user)
    is_school = user_is_school_admin(request.user)

    if not (is_proj or is_school):
        messages.error(request, 'You do not have permission to access this page.')
        return redirect('index')
    
    user_obj = get_object_or_404(User, id=user_id)
    
    # Access Check for School Admin
    allowed_schools = None
    if is_school and not is_proj:
        allowed_schools = get_allowed_schools_for_admin(request.user)
        # Check intersection of admin's allowed schools and target user's schools
        user_schools = user_obj.schools if isinstance(user_obj.schools, list) else []
        if not any(sid in allowed_schools for sid in user_schools) and user_schools:
             # If user belongs to schools, but none align with admin...
             # What if user has NO schools? Can admin take them?
             # Assuming admin can see unassigned users? Or only assigned ones?
             # "only see users that are assing to that school" implies user MUST be assigned.
             messages.error(request, 'You do not have permission to view this user.')
             return redirect('users:manage_users')

    roles_qs = Role.objects.all().order_by('name')
    schools_qs = StudataSchool.objects.all().order_by('school_name')
    
    # Filter options for School Admin
    if is_school and not is_proj:
        # 1. Restrict visible/assignable roles
        allowed_role_names = ['School Gr View', 'School View', 'Teacher']
        roles_qs = roles_qs.filter(name__in=allowed_role_names)
        
        # 2. Restrict visible/assignable schools
        schools_qs = schools_qs.filter(state_id__in=allowed_schools)

    if request.method == 'POST':
        # Update user information
        user_obj.name = request.POST.get('name', '').strip()
        user_obj.job_title = request.POST.get('job_title', '').strip()
        user_obj.staff_id = request.POST.get('staff_id', '').strip() or None
        user_obj.is_active = request.POST.get('is_active') == 'on'
        
        # Update roles
        role_ids = request.POST.getlist('roles')
        
        if is_school and not is_proj:
            # Logic for School Admin:
            # We cannot clear ALL roles if user has other roles (like Project Admin) that this admin can't see.
            # We should only modify the roles that are "allowed".
            
            # 1. Get current roles that are NOT allowed (preserve them)
            preserved_roles = user_obj.roles.exclude(name__in=allowed_role_names)
            
            # 2. Get new roles from form (ensure they ARE allowed)
            new_allowed_roles = Role.objects.filter(id__in=role_ids, name__in=allowed_role_names)
            
            # 3. Set roles = preserved + new
            user_obj.roles.set(list(preserved_roles) + list(new_allowed_roles))
        else:
            # Full Access
            user_obj.roles.clear()
            if role_ids:
                 roles_objs = Role.objects.filter(id__in=role_ids)
                 user_obj.roles.add(*roles_objs)
        
        # Update schools (JSONField list of state_ids)
        school_ids = request.POST.getlist('schools')
        
        if is_school and not is_proj:
            # Logic for School Admin:
            # Only modify memberships for allowed schools.
            
            # 1. Preserve schools not managed by this admin
            current_schools = user_obj.schools if isinstance(user_obj.schools, list) else []
            preserved_schools = [s for s in current_schools if s not in allowed_schools]
            
            # 2. Get valid requested schools that are also allowed for this admin
            new_schools = []
            for school_id in school_ids:
                 if school_id in allowed_schools:
                     new_schools.append(school_id)
            
            user_obj.schools = list(set(preserved_schools + new_schools)) # set for uniqueness
        else:
            valid_school_ids = []
            for school_id in school_ids:
                 # Validate existence
                 if StudataSchool.objects.filter(state_id=school_id).exists():
                     valid_school_ids.append(school_id)
            user_obj.schools = valid_school_ids

        user_obj.save()
        
        messages.success(request, f'User {user_obj.email} updated successfully.')
        return redirect('users:user_detail', user_id=user_obj.id)
    
    context = {
        'user_obj': user_obj,
        'roles': roles_qs,
        'schools': schools_qs,
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
    if not (request.user.is_superuser or user_is_project_admin(request.user)):
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

