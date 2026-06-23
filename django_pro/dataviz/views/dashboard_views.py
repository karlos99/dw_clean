"""Dashboard builder page views."""
import json

from django.db.models import Count
from django.shortcuts import get_object_or_404, render
from django.views.generic import TemplateView

from dataviz.models import Dashboard, SavedChart

from .mixins import ProjectAdminMixin, is_project_admin


def dashboard_index(request):
    """List all dashboards belonging to the current user."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not is_project_admin(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Project Admins only.')

    dashboards = (
        Dashboard.objects
        .all()
        .annotate(chart_count=Count('chart_slots'))
        .order_by('-updated_at')[:100]
    )
    return render(request, 'dataviz/dashboards/index.html', {'dashboards': dashboards})


class DashboardBuilderView(ProjectAdminMixin, TemplateView):
    """Create or edit a dashboard using a GridStack drag-and-resize grid."""
    template_name = 'dataviz/dashboards/builder.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        dashboard_id = self.kwargs.get('dashboard_id')

        dashboard = None
        slots_json = '[]'
        if dashboard_id:
            dashboard = get_object_or_404(Dashboard, pk=dashboard_id)
            slots = list(dashboard.chart_slots.select_related('chart').all())
            slots_json = json.dumps([s.to_dict() for s in slots])

        # Charts available to add
        charts = SavedChart.objects.all().order_by('name')[:200]
        slots = []
        if dashboard:
            slots = list(dashboard.chart_slots.select_related('chart').all())

        context.update({
            'dashboard':        dashboard,
            'dashboard_id':     dashboard_id,
            'dashboard_json':   json.dumps(dashboard.to_dict() if dashboard else None),
            'slots':            slots,
            'slots_json':       slots_json,
            'available_charts': charts,
        })
        return context


dashboard_builder = DashboardBuilderView.as_view()
