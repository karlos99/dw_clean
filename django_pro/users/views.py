from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.cache import cache
from django.db.models import Q, Count
from .models import User, Role, School
from typing import Optional


def user_is_project_admin(user: User) -> bool:
    """Check if user has Project Admin role"""
    return user.is_authenticated and user.role and user.role.name == 'Project Admin'

@login_required
def manage_users(request):
    if not user_is_project_admin(request.user):
        messages.error(request, 'You do not have permission to access this page.')
        return redirect('index')

    query = request.GET.get('q', '').strip()
    role_filter = request.GET.get('role', '').strip()
    job_title_filter = request.GET.get('job_title', '').strip()
    sort_by = request.GET.get('sort', 'email')
    sort_order = request.GET.get('order', 'asc')
    
    # Page size with validation (performance consideration)
    page_size = request.GET.get('page_size', '10')
    valid_page_sizes = ['10', '20', '30', '40', '50']
    if page_size not in valid_page_sizes:
        page_size = '10'
    page_size = int(page_size)
    
    # Optimize query with annotation to avoid N+1
    users_qs = User.objects.select_related('role').annotate(
        school_count=Count('schools')
    )
    
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
            users_qs = users_qs.filter(role__isnull=True)
        else:
            users_qs = users_qs.filter(role_id=role_filter)
    
    # Apply job title filter
    if job_title_filter:
        if job_title_filter == 'none':
            users_qs = users_qs.filter(Q(job_title='') | Q(job_title__isnull=True))
        else:
            users_qs = users_qs.filter(job_title=job_title_filter)
    
    # Apply sorting
    valid_sort_fields = ['name', 'email', 'job_title', 'role__name']
    if sort_by not in valid_sort_fields:
        sort_by = 'email'
    
    order_prefix = '-' if sort_order == 'desc' else ''
    users_qs = users_qs.order_by(f'{order_prefix}{sort_by}')
    
    # Cache filter options for performance (as per instructions.md requirement)
    cache_key_roles = 'user_management:all_roles'
    cache_key_job_titles = 'user_management:all_job_titles'
    
    all_roles = cache.get(cache_key_roles)
    if all_roles is None:
        all_roles = list(Role.objects.all().order_by('name'))
        cache.set(cache_key_roles, all_roles, timeout=300)
    
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
        'query': query,
        'page_size': page_size,
        'role_filter': role_filter,
        'job_title_filter': job_title_filter,
        'all_job_titles': all_job_titles,
        'all_roles': all_roles,
        'sort_by': sort_by,
        'sort_order': sort_order,
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
    
    user = get_object_or_404(User.objects.prefetch_related('schools'), id=user_id)
    roles = Role.objects.all().order_by('name')
    schools = School.objects.all().order_by('name')
    
    if request.method == 'POST':
        # Update user information
        user.name = request.POST.get('name', '').strip()
        user.job_title = request.POST.get('job_title', '').strip()
        user.staff_id = request.POST.get('staff_id', '').strip() or None
        user.is_active = request.POST.get('is_active') == 'on'
        
        # Update role
        role_id = request.POST.get('role')
        if role_id:
            try:
                user.role = Role.objects.get(id=role_id)
            except Role.DoesNotExist:
                user.role = None
        else:
            user.role = None
        
        user.save()
        
        # Update schools (many-to-many)
        school_ids = request.POST.getlist('schools')
        user.schools.clear()
        for school_id in school_ids:
            try:
                school = School.objects.get(id=school_id)
                user.schools.add(school)
            except School.DoesNotExist:
                pass
        
        messages.success(request, f'User {user.email} updated successfully.')
        return redirect('users:user_detail', user_id=user.id)
    
    context = {
        'user_obj': user,
        'roles': roles,
        'schools': schools,
    }
    return render(request, 'users/user_detail.html', context)

