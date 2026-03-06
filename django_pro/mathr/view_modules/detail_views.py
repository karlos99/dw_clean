"""Student marks detail view (click-to-expand)."""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from mathr.view_modules.data_utils import get_current_school_year, get_math_students
from users.decorators import page_access_required


@login_required
@page_access_required('mathr')
def student_marks_detail(request: HttpRequest, stu_annual_key: str) -> HttpResponse:
    """HTMX partial: expanded marks view for a single student."""
    school_year = request.GET.get('school_year') or get_current_school_year()

    # Re-use the main query but scoped to one student (not ideal but simple
    # for first iteration — a dedicated query can be added later).
    students = get_math_students(school_year)
    student = next((s for s in students if s['stu_annual_key'] == stu_annual_key), None)

    if not student:
        return HttpResponse('<p class="text-sm text-slate-500">Student not found.</p>')

    return render(request, 'mathr/partials/student_detail.html', {
        'student': student,
        'school_year': school_year,
    })
