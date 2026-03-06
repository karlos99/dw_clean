"""Completion dashboard views (District → School → Teacher drill-down)."""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from mathr.view_modules.access_utils import is_school_admin, is_unrestricted
from mathr.view_modules.data_utils import (
    get_completion_by_school,
    get_completion_by_teacher,
    get_current_school_year,
)
from users.decorators import page_access_required


@login_required
@page_access_required('mathr')
def completion_by_school(request: HttpRequest) -> HttpResponse:
    """HTMX partial: district-level completion by school."""
    if not is_unrestricted(request.user):
        return HttpResponse('<p class="text-red-600 text-sm">Access denied</p>', status=403)

    school_year = request.GET.get('school_year') or get_current_school_year()
    stats = get_completion_by_school(school_year)

    return render(request, 'mathr/partials/completion_table.html', {
        'stats': stats,
        'school_year': school_year,
        'level': 'school',
    })


@login_required
@page_access_required('mathr')
def completion_by_teacher(request: HttpRequest, state_school_id: str) -> HttpResponse:
    """HTMX partial: school-level completion by teacher."""
    user = request.user
    if not (is_unrestricted(user) or is_school_admin(user)):
        return HttpResponse('<p class="text-red-600 text-sm">Access denied</p>', status=403)

    school_year = request.GET.get('school_year') or get_current_school_year()
    stats = get_completion_by_teacher(school_year, state_school_id)

    return render(request, 'mathr/partials/completion_table.html', {
        'stats': stats,
        'school_year': school_year,
        'level': 'teacher',
        'state_school_id': state_school_id,
    })
