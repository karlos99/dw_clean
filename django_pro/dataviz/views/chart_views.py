"""Chart builder page views."""
import json

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import connections
from django.shortcuts import get_object_or_404, render
from django.views.generic import TemplateView

from dataviz.models import SavedChart
from dataviz.utils.chart_builder import CHART_TYPES
from dataexploration.column_utils import get_mark_columns, get_test_columns
from dataexploration.constants import COLUMN_CATEGORIES, COLUMN_DEFINITIONS
from dataexploration.models import SavedQuery
from dataexploration.utils import can_user_view_query
from dataexploration.view_modules.column_management import get_saved_default_columns

from .mixins import ProjectAdminMixin, is_project_admin


def chart_index(request):
    """List all charts belonging to the current user."""
    from django.contrib.auth.decorators import login_required
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not is_project_admin(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Project Admins only.')

    charts = SavedChart.objects.all().order_by('-updated_at')
    return render(request, 'dataviz/charts/index.html', {'charts': charts})


class ChartBuilderView(ProjectAdminMixin, TemplateView):
    """The chart builder page — create or edit a saved chart."""
    template_name = 'dataviz/charts/builder.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        chart_id = self.kwargs.get('chart_id')

        # Pre-load an existing chart if editing
        existing_chart = None
        if chart_id:
            existing_chart = get_object_or_404(SavedChart, pk=chart_id, owner=user)

        # Accessible saved queries
        query_list = SavedQuery.objects.select_related('owner', 'folder').order_by('name')
        accessible_queries = [q for q in query_list if can_user_view_query(user, q)]

        # Column definitions
        default_columns = get_saved_default_columns()
        test_structure  = get_test_columns()
        marks_structure = get_mark_columns()
        combined_defs   = dict(COLUMN_DEFINITIONS)

        # School years
        school_years = cache.get('dataexp:school_years')
        if not school_years:
            with connections['default'].cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT school_year FROM current_student "
                    "WHERE school_year IS NOT NULL ORDER BY school_year DESC"
                )
                school_years = [r[0] for r in cursor.fetchall()]
                cache.set('dataexp:school_years', school_years, 3600)

        # Chart type data for the picker
        from dataviz.utils.chart_builder import PALETTES
        chart_groups: dict = {}
        for key, label, icon, group in CHART_TYPES:
            chart_groups.setdefault(group, []).append((key, label, icon))

        # Flat list with emoji icons for the new builder UI
        _EMOJI = {
            'bar': '📊', 'column': '📊', 'grouped_bar': '📊', 'stacked_bar': '📊',
            'assessment_compare': '📊',
            'waterfall': '📉', 'line': '📈', 'spline': '📈', 'area': '📈',
            'areaspline': '📈', 'stacked_area': '📈', 'pie': '🥧', 'donut': '🍩',
            'funnel': '🔻', 'treemap': '🟩', 'scatter': '⚬', 'bubble': '🫧',
            'heatmap': '🟧', 'histogram': '📶', 'box': '📦', 'radar': '🕸️', 'gauge': '🎯',
        }
        chart_types_flat = [(k, l, _EMOJI.get(k, '📊'), g) for k, l, _, g in CHART_TYPES]

        agg_choices = [
            ('count', 'Count'), ('sum', 'Sum'), ('avg', 'Average'),
            ('min', 'Min'), ('max', 'Max'), ('median', 'Median'),
        ]

        context.update({
            'chart_id':             chart_id,
            'existing_chart':       existing_chart,
            'existing_chart_json':  json.dumps(existing_chart.to_dict() if existing_chart else None),
            'saved_queries':        accessible_queries,
            'column_definitions':   COLUMN_DEFINITIONS,
            'column_categories':    COLUMN_CATEGORIES,
            'test_structure':       test_structure,
            'marks_structure':      marks_structure,
            'default_columns_json': json.dumps(default_columns),
            'column_definitions_json': json.dumps(combined_defs),
            'school_years':         school_years,
            'chart_groups':         chart_groups,
            'chart_types_flat':     chart_types_flat,
            'agg_choices':          agg_choices,
            'palettes':             list(PALETTES.keys()),
        })
        return context


chart_builder = ChartBuilderView.as_view()


@login_required
def chart_view(request, chart_id):
    """Standalone rendered chart view — shows the chart full-width with drill enabled."""
    if not is_project_admin(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Project Admins only.')

    chart = get_object_or_404(SavedChart, pk=chart_id)
    return render(request, 'dataviz/charts/view.html', {'chart': chart})
