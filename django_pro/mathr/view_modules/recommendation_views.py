"""Save / update math recommendation views."""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.utils.html import escape
from django.views.decorators.http import require_POST

from mathr.models import MathRecommendation
from mathr.view_modules.access_utils import can_edit_recommendation
from mathr.view_modules.data_utils import (
    get_math_options,
    invalidate_mathr_caches,
)
from users.decorators import page_access_required


def _get_live_counts(school_year: str, state_school_id: str | None, staff_employee_id: str | None) -> tuple[int, int]:
    """Return (total_students, completed_count) for the current filter scope."""
    filters: list[str] = []
    params: list[str] = [school_year]

    if staff_employee_id:
        filters.append('AND sl.staff_employee_id = %s')
        params.append(staff_employee_id)
    if state_school_id:
        filters.append('AND sl.state_school_id = %s')
        params.append(state_school_id)

    extra = ' '.join(filters)
    sql = f"""
        SELECT
            COUNT(DISTINCT se.stu_annual_key),
            COUNT(DISTINCT mr.stu_annual_key)
        FROM student_enroll se
        INNER JOIN schedule_list sl ON sl.stu_annual_key = se.stu_annual_key
        LEFT JOIN mathr_recommendation mr
            ON mr.stu_annual_key = se.stu_annual_key
            AND mr.school_year = %s
        WHERE (sl.course_name LIKE '%%Math%%' OR sl.course_subject LIKE '%%Math%%')
            AND se.grade = '08'
            AND sl.schedule_status = 'Active'
            {extra}
    """
    with connection.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return (row[0] or 0, row[1] or 0) if row else (0, 0)


def _summary_bar_oob(total: int, completed: int) -> str:
    """Build an OOB-swapped summary bar with updated counts."""
    pct = round(completed / total * 100) if total else 0
    return (
        f'<div id="mathr-summary-bar" class="flex items-center justify-between px-1" hx-swap-oob="outerHTML">'
        f'<p class="text-sm text-slate-600">'
        f'Showing <strong>{total}</strong> student{"s" if total != 1 else ""}'
        f' — <strong>{completed}</strong> recommendation{"s" if completed != 1 else ""} completed'
        f'</p>'
        f'<div class="flex items-center gap-2">'
        f'<div class="w-32 bg-slate-200 rounded-full h-2">'
        f'<div class="bg-blue-600 h-2 rounded-full transition-all duration-500" style="width: {pct}%"></div>'
        f'</div>'
        f'<span class="text-xs text-slate-500">{pct}%</span>'
        f'</div>'
        f'</div>'
    )


@login_required
@page_access_required('mathr')
@require_POST
def save_recommendation(request: HttpRequest) -> HttpResponse:
    """HTMX POST: create or update a math recommendation for one student."""
    user = request.user
    if not can_edit_recommendation(user):
        return HttpResponse('<span class="text-red-600 text-xs">Permission denied</span>', status=403)

    stu_annual_key = request.POST.get('stu_annual_key', '')
    school_year = request.POST.get('school_year', '')
    math_selection = request.POST.get('math_selection', '')
    ssid = request.POST.get('ssid', '')
    state_school_id = request.POST.get('state_school_id', '')
    staff_employee_id = request.POST.get('staff_employee_id', '')

    if not stu_annual_key or not school_year:
        return HttpResponse('<span class="text-red-600 text-xs">Missing data</span>', status=400)

    # Empty selection = remove the recommendation
    if not math_selection:
        deleted_count, _ = MathRecommendation.objects.filter(
            stu_annual_key=stu_annual_key,
            school_year=school_year,
        ).delete()
        invalidate_mathr_caches(school_year)
        status_label = 'Removed' if deleted_count else ''
    else:
        _, created = MathRecommendation.objects.update_or_create(
            stu_annual_key=stu_annual_key,
            school_year=school_year,
            defaults={
                'ssid': ssid,
                'state_school_id': state_school_id,
                'staff_employee_id': staff_employee_id,
                'math_selection': math_selection,
                'selected_by': user,
            },
        )
        invalidate_mathr_caches(school_year)
        status_label = 'Saved' if created else 'Updated'

    # Determine options for the saved student to re-render the row
    course_subject = request.POST.get('course_subject', '')
    school_name = request.POST.get('school_name', '')
    course_name = request.POST.get('course_name', '')
    options = get_math_options(course_subject, school_name, course_name)

    option_html = ''.join(
        f'<option value="{escape(opt)}" {"selected" if opt == math_selection else ""}>{escape(opt)}</option>'
        for opt in options
    )

    # Compute live completion counts for the OOB summary bar update
    # Use staff_employee_id for teacher scope, state_school_id for school scope
    scope_staff = staff_employee_id if getattr(user, 'staff_id', None) else None
    total, completed = _get_live_counts(school_year, state_school_id or None, scope_staff or None)
    summary_oob = _summary_bar_oob(total, completed)

    # Primary target: re-render the dropdown with current selection + saved indicator
    is_removal = not math_selection
    border_cls = 'border-amber-400' if is_removal else 'border-green-400'
    bg_cls = 'bg-amber-50' if is_removal else 'bg-green-50'
    text_cls = 'text-amber-700' if is_removal else 'text-green-700'

    # Status indicator
    if status_label == 'Removed':
        status_icon = (
            f'<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">'
            f'<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>'
            f'</svg>'
        )
    elif status_label:
        status_icon = (
            f'<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">'
            f'<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>'
            f'</svg>'
        )
    else:
        status_icon = ''

    dropdown_html = (
        f'<div class="flex items-center gap-2" id="rec-{stu_annual_key}">'
        f'<select name="math_selection" data-student="{stu_annual_key}" '
        f'class="border {border_cls} text-sm px-2 py-1 {bg_cls} transition-colors duration-1000" '
        f'hx-post="/mathr/api/save/" '
        f'hx-target="#rec-{stu_annual_key}" '
        f'hx-swap="outerHTML" '
        f'hx-include="[data-student=\'{stu_annual_key}\']" '
        f'hx-trigger="change">'
        f'<option value="">-- Select --</option>'
        f'{option_html}'
        f'</select>'
        f'<span id="status-{stu_annual_key}" class="inline-flex items-center gap-1 text-xs font-medium {text_cls}">'
        f'{status_icon}{status_label}'
        f'</span>'
        f'</div>'
    )

    # Combine primary swap + OOB updates
    return HttpResponse(dropdown_html + summary_oob)
