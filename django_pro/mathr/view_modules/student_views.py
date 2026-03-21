"""Main student list views for the mathR app."""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render

from mathr.view_modules.access_utils import (
    _active_role_name,
    get_request_school,
    is_district_viewer,
    is_school_admin,
    is_unrestricted,
)
from mathr.view_modules.data_utils import (
    get_current_school_year,
    get_math_options,
    get_math_students,
    get_recommendation_map,
    get_school_teachers,
    get_teacher_periods,
)
from studata.models import School
from users.decorators import page_access_required


@login_required
@page_access_required('mathr')
def mathr_home(request: HttpRequest) -> HttpResponse:
    """Full-page shell for the Math Recommendations app."""
    user = request.user
    school_year = get_current_school_year()
    role_name = _active_role_name(user) or ''

    context: dict = {
        'school_year': school_year,
        'role_name': role_name,
        'is_unrestricted': is_unrestricted(user),
        'is_school_admin': is_school_admin(user),
        'is_district_viewer': is_district_viewer(user),
        'periods': [],
        'schools': [],
        'teachers': [],
    }

    if role_name == 'Teacher':
        staff_id = getattr(user, 'staff_id', None) or ''
        context['periods'] = get_teacher_periods(school_year, staff_id)
    elif is_school_admin(user) and not is_unrestricted(user):
        # School Admin: show teacher list for their school
        school = get_request_school(request)
        if school:
            context['teachers'] = get_school_teachers(school_year, school.state_id)
    elif is_unrestricted(user):
        context['schools'] = list(
            School.objects.filter(school_group='Middle School')
            .order_by('school_name')
            .values('state_id', 'school_name')
        )

    return render(request, 'mathr/home.html', context)


@login_required
@page_access_required('mathr')
def mathr_student_list(request: HttpRequest) -> HttpResponse:
    """HTMX partial: returns the student table rows."""
    user = request.user
    school_year = request.GET.get('school_year') or get_current_school_year()
    period = request.GET.get('period') or None
    school_filter = request.GET.get('state_school_id') or None
    staff_filter = request.GET.get('staff_employee_id') or None
    rec_status = request.GET.get('rec_status') or ''
    role_name = _active_role_name(user) or ''

    # Determine query filters based on role
    staff_id: str | None = None
    state_school_id: str | None = None

    if role_name == 'Teacher':
        staff_id = getattr(user, 'staff_id', None) or ''
    elif is_school_admin(user) and not is_unrestricted(user):
        # School Admin — always scoped to their school
        school = get_request_school(request)
        if school:
            state_school_id = school.state_id
        # Optional teacher filter
        if staff_filter:
            staff_id = staff_filter
    elif not is_unrestricted(user):
        # Other school-level roles
        school = get_request_school(request)
        if school:
            state_school_id = school.state_id
    # District-level can optionally filter by school
    if is_unrestricted(user) and school_filter:
        state_school_id = school_filter

    students = get_math_students(
        school_year,
        staff_employee_id=staff_id,
        state_school_id=state_school_id,
        period=period,
    )

    # Attach recommendation data and dropdown options
    rec_map = get_recommendation_map(school_year, state_school_id)
    for s in students:
        s['math_selection'] = rec_map.get(s['stu_annual_key'], '')
        s['math_options'] = get_math_options(
            s.get('course_subject'),
            s.get('school_name'),
            s.get('course_name'),
        )

    # Compute overall counts before filtering (for summary bar)
    total_count = len(students)
    completed_count = sum(1 for s in students if s['math_selection'])

    # Filter by recommendation status if requested
    if rec_status == 'pending':
        students = [s for s in students if not s['math_selection']]
    elif rec_status == 'completed':
        students = [s for s in students if s['math_selection']]

    context = {
        'students': students,
        'school_year': school_year,
        'can_edit': role_name in ('Teacher', 'School Admin', 'Project Admin', 'District Admin') or user.is_superuser,
        'total_count': total_count,
        'completed_count': completed_count,
        'display_count': len(students),
        'rec_status': rec_status,
    }
    return render(request, 'mathr/partials/student_table.html', context)


@login_required
@page_access_required('mathr')
def mathr_teacher_periods(request: HttpRequest) -> HttpResponse:
    """HTMX partial: return <option> elements for a teacher's periods.

    Used by School Admin to cascade: Teacher → Period dropdown.
    """
    staff_employee_id = request.GET.get('staff_employee_id', '')
    school_year = request.GET.get('school_year') or get_current_school_year()

    if not staff_employee_id:
        return HttpResponse('<option value="">All Periods</option>')

    periods = get_teacher_periods(school_year, staff_employee_id)
    html = '<option value="">All Periods</option>'
    html += ''.join(f'<option value="{p}">Period {p}</option>' for p in periods)
    return HttpResponse(html)
