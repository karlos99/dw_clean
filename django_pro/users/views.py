from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.cache import cache
from django.db.models import Q, Count
from django.utils import timezone
from zoneinfo import ZoneInfo
from django.urls import reverse
from urllib.parse import urlencode
from .models import User, Role, School, UsageSession, PageVisit
from .models_pages import Page
from .context_processors import user_can_access_page
from studata.models import School as StudataSchool
from typing import Optional
from datetime import datetime, timedelta


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

        # School Admin should not see district-level users.
        if user_is_school_admin(request.user):
            users_qs = users_qs.exclude(
                Q(roles__name__iexact='District View')
                | Q(roles__name__iexact='District Admin')
            )
    
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
    valid_sort_fields = ['name', 'email', 'job_title', 'is_active']
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

    # Build school name lookup for users on the current page
    page_school_ids = set()
    for u in page_obj:
        if u.schools and isinstance(u.schools, list):
            page_school_ids.update(u.schools)
    school_name_map = {}
    if page_school_ids:
        for row in StudataSchool.objects.filter(state_id__in=page_school_ids).values('state_id', 'school_name'):
            school_name_map[row['state_id']] = row['school_name']

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
        'school_name_map': school_name_map,
    }
    
    # Return partial for HTMX requests
    if request.headers.get('HX-Request'):
        return render(request, 'users/_user_table.html', context)
    
    return render(request, 'users/user_list.html', context)


@login_required
def usage_report(request):
    usage_page = Page.objects.filter(slug='usage').first()
    if usage_page:
        if not user_can_access_page(request.user, usage_page):
            messages.error(request, 'You do not have permission to access this page.')
            return redirect('index')
    else:
        is_project_admin = request.user.is_superuser or request.user.is_project_admin
        if hasattr(request.user, 'active_role') and request.user.active_role:
            is_project_admin = is_project_admin or request.user.active_role.name == 'Project Admin'
        if not is_project_admin:
            messages.error(request, 'You do not have permission to access this page.')
            return redirect('index')

    pacific_tz = ZoneInfo('America/Los_Angeles')
    now = timezone.now()
    pacific_now = timezone.localtime(now, pacific_tz)

    # Calculate time boundaries for statistics
    today_start = pacific_now.replace(hour=0, minute=0, second=0, microsecond=0)
    last_7_days_start = (pacific_now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
    
    # School year starts August 1st - if current month is before August, use previous year
    if pacific_now.month >= 8:
        school_year_start = pacific_now.replace(month=8, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        school_year_start = pacific_now.replace(year=pacific_now.year - 1, month=8, day=1, hour=0, minute=0, second=0, microsecond=0)

    # Statistics queries for unique users (excluding Project Admins)
    from django.db.models import Count

    # Exclude users with Project Admin role from all analytics
    project_admin_exclude = {'user__roles__name': 'Project Admin'}

    # Get unique user IDs and their emails for each time period
    users_today_ids = UsageSession.objects.filter(
        login_at__gte=today_start
    ).exclude(**project_admin_exclude).values_list('user', flat=True).distinct()
    users_today_list = list(User.objects.filter(id__in=users_today_ids).order_by('email').values_list('email', flat=True))
    unique_users_today = len(users_today_list)

    users_last_7_days_ids = UsageSession.objects.filter(
        login_at__gte=last_7_days_start
    ).exclude(**project_admin_exclude).values_list('user', flat=True).distinct()
    users_last_7_days_list = list(User.objects.filter(id__in=users_last_7_days_ids).order_by('email').values_list('email', flat=True))
    unique_users_last_7_days = len(users_last_7_days_list)

    users_school_year_ids = UsageSession.objects.filter(
        login_at__gte=school_year_start
    ).exclude(**project_admin_exclude).values_list('user', flat=True).distinct()
    users_school_year_list = list(User.objects.filter(id__in=users_school_year_ids).order_by('email').values_list('email', flat=True))
    unique_users_school_year = len(users_school_year_list)

    # Calculate average session time (excluding Project Admins, capped at 30 min)
    from django.db.models import Avg, Case, When, Value, IntegerField
    from django.db.models.functions import Least
    
    # Cap session time at 30 minutes (1800 seconds) to exclude users who forget to logout
    MAX_SESSION_SECONDS = 1800
    
    avg_session_today = UsageSession.objects.filter(
        login_at__gte=today_start,
        total_seconds__gt=0
    ).exclude(**project_admin_exclude).annotate(
        capped_seconds=Least('total_seconds', Value(MAX_SESSION_SECONDS))
    ).aggregate(avg=Avg('capped_seconds'))['avg']
    
    avg_session_last_7_days = UsageSession.objects.filter(
        login_at__gte=last_7_days_start,
        total_seconds__gt=0
    ).exclude(**project_admin_exclude).annotate(
        capped_seconds=Least('total_seconds', Value(MAX_SESSION_SECONDS))
    ).aggregate(avg=Avg('capped_seconds'))['avg']
    
    avg_session_school_year = UsageSession.objects.filter(
        login_at__gte=school_year_start,
        total_seconds__gt=0
    ).exclude(**project_admin_exclude).annotate(
        capped_seconds=Least('total_seconds', Value(MAX_SESSION_SECONDS))
    ).aggregate(avg=Avg('capped_seconds'))['avg']
    
    # Convert to minutes
    avg_time_today_minutes = int((avg_session_today or 0) / 60)
    avg_time_last_7_days_minutes = int((avg_session_last_7_days or 0) / 60)
    avg_time_school_year_minutes = int((avg_session_school_year or 0) / 60)

    # Role usage statistics - count sessions per role (excluding Project Admins)
    from collections import Counter, defaultdict
    role_session_counts = Counter()
    role_queryset = UsageSession.objects.filter(
        login_at__gte=school_year_start
    ).exclude(**project_admin_exclude).select_related('user').prefetch_related('user__roles')
    
    for session in role_queryset:
        for role in session.user.roles.all():
            if role.name != 'Project Admin':
                role_session_counts[role.name] += 1
    
    top_role = None
    if role_session_counts:
        top_role_name, top_role_count = role_session_counts.most_common(1)[0]
        top_role = {'name': top_role_name, 'session_count': top_role_count}

    # Top pages statistics (excluding home page, API endpoints, and Project Admins)
    page_stats = defaultdict(lambda: {'visits': 0, 'total_seconds': 0, 'users': set()})
    page_visits_qs = PageVisit.objects.filter(
        session__login_at__gte=school_year_start
    ).exclude(session__user__roles__name='Project Admin').select_related('session__user')
    
    for visit in page_visits_qs:
        path = visit.path
        # Skip home page and API endpoints
        if not path or path == '/' or '/api/' in path:
            continue
        page_stats[path]['visits'] += 1
        page_stats[path]['total_seconds'] += visit.duration_seconds or 0
        page_stats[path]['users'].add(visit.session.user_id)
    
    # Sort pages by visits and get top 5
    sorted_pages = sorted(
        page_stats.items(),
        key=lambda x: (x[1]['visits'], len(x[1]['users'])),
        reverse=True
    )[:5]
    
    top_pages = []
    for path, stats in sorted_pages:
        top_pages.append({
            'path': path,
            'visits': stats['visits'],
            'unique_users': len(stats['users']),
            'total_minutes': int((stats['total_seconds'] + 59) // 60),
        })

    # User login activity table (excluding Project Admins)
    days_7_ago = pacific_now - timedelta(days=7)
    days_30_ago = pacific_now - timedelta(days=30)
    days_90_ago = pacific_now - timedelta(days=90)
    days_180_ago = pacific_now - timedelta(days=180)
    
    # Get all non-Project Admin users who have logged in within 180 days
    from django.db.models import Max
    
    user_login_activity = []
    active_users = User.objects.exclude(
        roles__name='Project Admin'
    ).filter(
        usage_sessions__login_at__gte=days_180_ago
    ).distinct().prefetch_related('roles')
    
    for user in active_users:
        user_sessions = UsageSession.objects.filter(user=user)
        
        last_login = user_sessions.aggregate(last=Max('login_at'))['last']
        
        count_7_days = user_sessions.filter(login_at__gte=days_7_ago).count()
        count_7_to_30 = user_sessions.filter(login_at__gte=days_30_ago, login_at__lt=days_7_ago).count()
        count_30_to_90 = user_sessions.filter(login_at__gte=days_90_ago, login_at__lt=days_30_ago).count()
        count_90_to_180 = user_sessions.filter(login_at__gte=days_180_ago, login_at__lt=days_90_ago).count()
        
        user_login_activity.append({
            'email': user.email,
            'last_login': timezone.localtime(last_login, pacific_tz).strftime('%Y-%m-%d %H:%M') if last_login else '—',
            'last_login_sort': last_login.isoformat() if last_login else '',
            'count_7_days': count_7_days,
            'count_7_to_30': count_7_to_30,
            'count_30_to_90': count_30_to_90,
            'count_90_to_180': count_90_to_180,
        })
    
    # Sort by last login by default (most recent first)
    user_login_activity.sort(key=lambda x: x['last_login_sort'], reverse=True)

    sessions_qs = UsageSession.objects.select_related('user').prefetch_related(
        'page_visits',
        'user__roles',
    ).order_by('-login_at')

    user_filter = request.GET.get('user', '').strip()
    if user_filter:
        sessions_qs = sessions_qs.filter(user__id=user_filter)

    quick_filter = request.GET.get('quick', '').strip()
    start_date = request.GET.get('start_date', '').strip()
    end_date = request.GET.get('end_date', '').strip()

    date_start_dt = None
    date_end_dt = None

    if quick_filter == 'week':
        start_of_week = pacific_now - timedelta(days=pacific_now.weekday())
        date_start_dt = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        date_end_dt = pacific_now.replace(hour=23, minute=59, second=59, microsecond=999999)
    elif quick_filter == 'year':
        start_of_year = pacific_now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        date_start_dt = start_of_year
        date_end_dt = pacific_now.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        if start_date:
            try:
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
                date_start_dt = timezone.make_aware(start_dt, pacific_tz)
            except ValueError:
                date_start_dt = None
        if end_date:
            try:
                end_dt = datetime.strptime(end_date, '%Y-%m-%d')
                end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
                date_end_dt = timezone.make_aware(end_dt, pacific_tz)
            except ValueError:
                date_end_dt = None

    if date_start_dt:
        sessions_qs = sessions_qs.filter(login_at__gte=date_start_dt)
    if date_end_dt:
        sessions_qs = sessions_qs.filter(login_at__lte=date_end_dt)

    paginator = Paginator(sessions_qs, 25)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    group_by = request.GET.get('group_by', '').strip()
    sessions = []
    for session in page_obj.object_list:
        effective_end = session.logout_at or session.last_activity_at or now
        total_seconds = session.total_seconds
        if not total_seconds and session.login_at and effective_end:
            total_seconds = max(0, int((effective_end - session.login_at).total_seconds()))

        visits = list(session.page_visits.all())
        visits.sort(key=lambda v: v.started_at or timezone.make_aware(datetime.min))
        formatted_visits = []
        for visit in visits:
            visit_end = visit.ended_at or session.logout_at or session.last_activity_at or now
            duration_seconds = visit.duration_seconds
            if not duration_seconds and visit.started_at and visit_end:
                duration_seconds = max(0, int((visit_end - visit.started_at).total_seconds()))
            formatted_visits.append({
                'path': visit.path,
                'started_at_display': timezone.localtime(visit.started_at, pacific_tz).strftime('%Y-%m-%d %H:%M:%S') if visit.started_at else '—',
                'ended_at_display': timezone.localtime(visit_end, pacific_tz).strftime('%Y-%m-%d %H:%M:%S') if visit_end else '—',
                'duration_seconds': duration_seconds,
                'duration_minutes': int((duration_seconds + 59) // 60) if duration_seconds else 0,
            })

        role_name = session.user.roles.first().name if session.user.roles.exists() else 'No Role'
        title_name = session.user.job_title or 'No Title'

        sessions.append({
            'user': session.user,
            'login_at_display': timezone.localtime(session.login_at, pacific_tz).strftime('%Y-%m-%d %H:%M:%S') if session.login_at else '—',
            'logout_at_display': timezone.localtime(session.logout_at, pacific_tz).strftime('%Y-%m-%d %H:%M:%S') if session.logout_at else '—',
            'last_activity_at_display': timezone.localtime(session.last_activity_at, pacific_tz).strftime('%Y-%m-%d %H:%M:%S') if session.last_activity_at else '—',
            'total_minutes': int((total_seconds + 59) // 60) if total_seconds else 0,
            'visits': formatted_visits,
            'role_name': role_name,
            'title_name': title_name,
        })

    # Top page for filtered results (respecting user/date filters, excluding Project Admins)
    page_agg = defaultdict(lambda: {'total_seconds': 0, 'users': set()})
    for session in sessions:
        # Skip Project Admins from analytics (check all user roles)
        user_roles = [r.name for r in session['user'].roles.all()]
        if 'Project Admin' in user_roles:
            continue
        for visit in session['visits']:
            path = visit.get('path')
            if not path or path == '/' or '/api/' in path:
                continue
            duration_seconds = visit.get('duration_seconds') or 0
            page_agg[path]['total_seconds'] += duration_seconds
            page_agg[path]['users'].add(session['user'].email)

    top_page = None
    if page_agg:
        sorted_filtered_pages = sorted(
            page_agg.items(),
            key=lambda item: (len(item[1]['users']), item[1]['total_seconds']),
            reverse=True,
        )
        top_path, stats = sorted_filtered_pages[0]
        top_page = {
            'path': top_path,
            'user_count': len(stats['users']),
            'total_minutes': int((stats['total_seconds'] + 59) // 60),
            'users': sorted(stats['users']),
        }

    users = User.objects.order_by('email')

    grouped_sessions = None
    if group_by in {'role', 'title'}:
        grouped_sessions = {}
        for session in sessions:
            key = session['role_name'] if group_by == 'role' else session['title_name']
            grouped_sessions.setdefault(key, []).append(session)

    query_params = {
        'user': user_filter,
        'group_by': group_by,
        'quick': quick_filter,
        'start_date': start_date,
        'end_date': end_date,
    }
    query_params = {k: v for k, v in query_params.items() if v}

    # Format school year label
    if pacific_now.month >= 8:
        school_year_label = f"{pacific_now.year}-{pacific_now.year + 1}"
    else:
        school_year_label = f"{pacific_now.year - 1}-{pacific_now.year}"

    context = {
        'sessions': sessions,
        'grouped_sessions': grouped_sessions,
        'group_by': group_by,
        'page_obj': page_obj,
        'users': users,
        'selected_user': user_filter,
        'quick_filter': quick_filter,
        'start_date': start_date,
        'end_date': end_date,
        'query_string': urlencode(query_params),
        'top_page': top_page,
        'timezone_label': 'America/Los_Angeles',
        # New statistics
        'unique_users_today': unique_users_today,
        'users_today_list': users_today_list,
        'unique_users_last_7_days': unique_users_last_7_days,
        'users_last_7_days_list': users_last_7_days_list,
        'unique_users_school_year': unique_users_school_year,
        'users_school_year_list': users_school_year_list,
        'school_year_label': school_year_label,
        'avg_time_today_minutes': avg_time_today_minutes,
        'avg_time_last_7_days_minutes': avg_time_last_7_days_minutes,
        'avg_time_school_year_minutes': avg_time_school_year_minutes,
        'top_role': top_role,
        'top_pages': top_pages,
        'user_login_activity': user_login_activity,
    }

    return render(request, 'users/usage.html', context)

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
        # Check if school admin has any schools assigned
        if allowed_schools is None or (isinstance(allowed_schools, list) and len(allowed_schools) == 0):
            messages.error(request, 'You have no schools assigned. Please contact an administrator.')
            return redirect('users:manage_users')
        
        # Check intersection of admin's allowed schools and target user's schools
        user_schools = user_obj.schools if isinstance(user_obj.schools, list) else []
        
        # If target user has schools assigned, at least one must match the admin's schools
        if user_schools and not any(sid in allowed_schools for sid in user_schools):
            messages.error(request, 'You do not have permission to view this user.')
            return redirect('users:manage_users')

    roles_qs = Role.objects.all().order_by('name')
    schools_qs = StudataSchool.objects.all().order_by('school_name')
    
    # Define available grade levels (must match database values)
    GRADE_CHOICES = [
        ('PK', 'Pre-Kindergarten'),
        ('PS', 'Preschool'),
        ('TK', 'Transitional Kindergarten'),
        ('KN', 'Kindergarten'),
        ('01', '1st Grade'),
        ('02', '2nd Grade'),
        ('03', '3rd Grade'),
        ('04', '4th Grade'),
        ('05', '5th Grade'),
        ('06', '6th Grade'),
        ('07', '7th Grade'),
        ('08', '8th Grade'),
        ('09', '9th Grade'),
        ('10', '10th Grade'),
        ('11', '11th Grade'),
        ('12', '12th Grade'),
        ('AD', 'Adult Education'),
        ('US', 'Ungraded Secondary'),
    ]
    
    # Filter grade levels based on school admin's schools
    # Note: Grade filtering by school would require grade data in School model
    # For now, showing all grades for all admins
    available_grades = GRADE_CHOICES
    
    # Filter options for School Admin
    if is_school and not is_proj:
        # 1. Restrict visible/assignable roles
        allowed_role_names = ['School Gr View', 'School View', 'Teacher']
        roles_qs = roles_qs.filter(name__in=allowed_role_names)
        
        # 2. Restrict visible/assignable schools
        schools_qs = schools_qs.filter(state_id__in=allowed_schools)

    is_school_admin_only = is_school and not is_proj

    if request.method == 'POST':
        # School admins cannot edit name, job title, or staff ID
        if not is_school_admin_only:
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

        # Update grade levels
        grade_levels = request.POST.getlist('grade_levels')
        user_obj.grade_levels = grade_levels if grade_levels else []

        # Update placement/export permissions (stored in preferences)
        prefs = user_obj.preferences if isinstance(user_obj.preferences, dict) else {}
        prefs['can_move_placement_cards'] = request.POST.get('can_move_placement_cards') == 'on'
        prefs['can_export_classroom_csv'] = request.POST.get('can_export_classroom_csv') == 'on'
        user_obj.preferences = prefs

        user_obj.save()
        
        messages.success(request, f'User {user_obj.email} updated successfully.')
        
        # Check if this is a modal request
        is_modal = request.GET.get('modal') == '1'
        if is_modal:
            # Let HTMX close the overlay and refresh the table in the parent page.
            response = HttpResponse(status=204)
            response['HX-Trigger'] = 'user-updated'
            return response
        
        return redirect('users:user_detail', user_id=user_obj.id)
    
    # Check if this is a modal request
    is_modal = request.GET.get('modal') == '1'
    
    context = {
        'user_obj': user_obj,
        'roles': roles_qs,
        'schools': schools_qs,
        'available_grades': available_grades,
        'is_modal': is_modal,
        'is_school_admin_only': is_school_admin_only,
    }
    
    if is_modal:
        return render(request, 'users/user_detail_modal.html', context)
    
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
    
    # Redirect to home page instead of referer to prevent permission issues
    return redirect('index')


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

