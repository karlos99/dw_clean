"""API for schedule + marks filter dropdown options."""

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

from ..school_year_utils import resolve_school_year
from .schedule_filter_sql import fetch_schedule_filter_options


def _query_multi(request, key):
    vals = request.GET.getlist(key)
    if len(vals) > 1:
        return vals
    raw = request.GET.get(key)
    return raw if raw else None


@login_required
def schedule_filter_options(request):
    school_year = (request.GET.get('school_year') or '').strip()
    if not school_year:
        return JsonResponse({'error': 'school_year required'}, status=400)
    try:
        school_year = resolve_school_year(school_year)
    except Exception:
        pass

    period = _query_multi(request, 'period')
    subject = _query_multi(request, 'subject')
    course_name = _query_multi(request, 'course_name')

    column_filters = {}
    filters_json = request.GET.get('filters', '')
    if filters_json:
        try:
            column_filters = json.loads(filters_json)
        except Exception:
            column_filters = {}

    status = _query_multi(request, 'status')
    staff = _query_multi(request, 'staff')

    data = fetch_schedule_filter_options(
        request.user,
        school_year,
        period=period,
        subject=subject,
        course_name=course_name,
        column_filters=column_filters,
        status=status,
        staff=staff,
    )
    return JsonResponse(data)
