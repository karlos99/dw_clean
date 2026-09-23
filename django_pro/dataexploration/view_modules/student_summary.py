"""Student test summary views."""

from django.shortcuts import render
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from django.http import JsonResponse, HttpResponse

from ..models import StudentWithTests
from .student_data_utils import (
    get_student_info_cached,
    get_test_records_cached,
    get_student_marks_cached,
    get_student_summary_partial_html_cached,
    pivot_student_marks,
    _resolve_ssid,
)


class StudentTestSummaryView(LoginRequiredMixin, ListView):
    """Summary view showing all tests for a specific student (by SSID)."""
    model = StudentWithTests
    template_name = 'dataexploration/student_test_summary.html'
    context_object_name = 'tests'
    paginate_by = None

    def get_queryset(self):
        ssid = _resolve_ssid(self.kwargs.get('ssid'))
        return StudentWithTests.objects.filter(ssid=ssid).none()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        ssid = _resolve_ssid(self.kwargs.get('ssid'))
        context['ssid'] = ssid

        user_id = getattr(getattr(self.request, 'user', None), 'pk', None)
        context['student_info'] = get_student_info_cached(ssid, user_id=user_id)
        context['student_name'] = context['student_info'].get('full_name')
        context['test_records'] = get_test_records_cached(ssid, user_id=user_id)
        marks = get_student_marks_cached(ssid, user_id=user_id)
        context['student_marks'] = marks
        context['student_marks_grid'] = pivot_student_marks(marks)
        return context


class StudentTestSummaryPartialView(StudentTestSummaryView):
    """Partial view for HTMX loading of student summary."""
    template_name = 'dataexploration/partials/student_test_summary_content.html'

    def get(self, request, *args, **kwargs):
        ssid = _resolve_ssid(kwargs.get('ssid'))
        html = get_student_summary_partial_html_cached(request, ssid)
        return self._html_response(html)

    @staticmethod
    def _html_response(html):
        return HttpResponse(html, content_type='text/html; charset=utf-8')


@login_required
@require_GET
def warm_student_test_summary_cache(request):
    """Warm Redis cache for the student summary overlay."""
    ssids_raw = (request.GET.get('ssids') or '').strip()
    try:
        limit = int(request.GET.get('limit') or 10)
    except Exception:
        limit = 10
    limit = max(0, min(limit, 25))

    if not ssids_raw:
        return JsonResponse({'warmed': 0, 'limit': limit})

    ssids = [s.strip() for s in ssids_raw.split(',') if s.strip()]
    ssids = ssids[:limit]
    if not ssids:
        return JsonResponse({'warmed': 0, 'limit': limit})

    # Warm concurrently through the shared bounded I/O pool instead of one
    # student at a time. Each summary does ORM + ClickHouse + template work, so
    # a sequential loop held the request (and its DB connections) open for
    # seconds on every page load. Worker threads must close their thread-local
    # PostgreSQL connection when done so pooled threads don't leak connections.
    from clickhouse_utils.thread_pool import get_io_pool
    from django.db import connections

    def _warm_one(ssid):
        try:
            get_student_summary_partial_html_cached(request, ssid)
            return True
        except Exception:
            return False
        finally:
            try:
                connections['default'].close()
            except Exception:
                pass

    pool = get_io_pool()
    warmed = sum(1 for ok in pool.map(_warm_one, ssids) if ok)

    return JsonResponse({'warmed': warmed, 'limit': limit})
