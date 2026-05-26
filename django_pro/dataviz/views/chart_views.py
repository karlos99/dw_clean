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
from dataexploration.school_year_utils import get_year_labels_map

from .mixins import ProjectAdminMixin, is_project_admin


def chart_index(request):
    """List charts in a file-explorer style structure."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not is_project_admin(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Project Admins only.')

    current_path = request.GET.get('folder', '').strip().strip('/')
    charts = SavedChart.objects.all().order_by('name')

    # Build the full tree structure
    root = {'folders': {}, 'charts': []}
    for chart in charts:
        parts = [p.strip() for p in chart.name.split('/')]
        curr = root
        for part in parts[:-1]:
            if part not in curr['folders']:
                curr['folders'][part] = {'folders': {}, 'charts': []}
            curr = curr['folders'][part]
        
        chart.display_name = parts[-1]
        curr['charts'].append(chart)

    # Navigate to the requested folder
    path_parts = [p for p in current_path.split('/') if p]
    display_curr = root
    breadcrumbs = []
    running_path = []
    
    for p in path_parts:
        if p in display_curr['folders']:
            running_path.append(p)
            display_curr = display_curr['folders'][p]
            breadcrumbs.append({'name': p, 'path': '/'.join(running_path)})
        else:
            # Path not found, fall back to root
            display_curr = root
            breadcrumbs = []
            current_path = ''
            break

    # Get subfolders and charts for the current view
    subfolders = sorted(display_curr['folders'].keys())
    folder_charts = display_curr['charts']

    return render(request, 'dataviz/charts/index.html', {
        'subfolders': subfolders,
        'charts': folder_charts,
        'current_path': current_path,
        'breadcrumbs': breadcrumbs,
    })


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
            existing_chart = get_object_or_404(SavedChart, pk=chart_id)

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

        # Flat list with SVG icons for the builder UI
        def _svg(path_d, vb='0 0 24 24'):
            return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" fill="currentColor" class="w-5 h-5">{path_d}</svg>'

        _ICONS = {
            'bar':          _svg('<rect x="3" y="5" width="4" height="14" rx="1"/><rect x="9" y="9" width="4" height="10" rx="1"/><rect x="15" y="2" width="4" height="17" rx="1"/>'),
            'column':       _svg('<rect x="3" y="13" width="4" height="8" rx="1"/><rect x="10" y="8" width="4" height="13" rx="1"/><rect x="17" y="3" width="4" height="18" rx="1"/>'),
            'grouped_bar':  _svg('<rect x="3" y="4" width="3" height="7" rx="1"/><rect x="3" y="13" width="3" height="7" rx="1"/><rect x="8" y="7" width="3" height="4" rx="1"/><rect x="8" y="13" width="3" height="4" rx="1"/><rect x="13" y="5" width="3" height="6" rx="1"/><rect x="13" y="13" width="3" height="6" rx="1"/>'),
            'stacked_bar':  _svg('<rect x="3" y="5" width="5" height="14" rx="1"/><rect x="3" y="5" width="5" height="6" rx="1" opacity=".5"/><rect x="10" y="8" width="5" height="11" rx="1"/><rect x="10" y="8" width="5" height="5" rx="1" opacity=".5"/><rect x="17" y="2" width="5" height="17" rx="1"/><rect x="17" y="2" width="5" height="8" rx="1" opacity=".5"/>'),
            'waterfall':    _svg('<rect x="2" y="14" width="4" height="6" rx="1"/><rect x="8" y="10" width="4" height="4" rx="1" opacity=".5"/><rect x="14" y="6" width="4" height="8" rx="1"/><rect x="20" y="4" width="2" height="16" rx="1"/>'),
            'line':         _svg('<polyline points="2,18 7,10 12,14 17,6 22,9" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'),
            'spline':       _svg('<path d="M2 18 C5 18 6 10 9 10 S14 14 17 8 S20 6 22 8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'),
            'area':         _svg('<path d="M2 18 L7 10 L12 14 L17 6 L22 9 L22 20 L2 20 Z" opacity=".3"/><polyline points="2,18 7,10 12,14 17,6 22,9" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'),
            'areaspline':   _svg('<path d="M2 18 C5 18 6 10 9 10 S14 14 17 8 S20 6 22 8 L22 20 L2 20 Z" opacity=".3"/><path d="M2 18 C5 18 6 10 9 10 S14 14 17 8 S20 6 22 8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'),
            'stacked_area': _svg('<path d="M2 20 L7 14 L12 16 L17 10 L22 12 L22 20 Z" opacity=".4"/><path d="M2 14 L7 8 L12 11 L17 4 L22 7 L22 12 L17 10 L12 16 L7 14 Z" opacity=".6"/>'),
            'pie':          _svg('<path d="M12 2 A10 10 0 0 1 22 12 L12 12 Z" opacity=".7"/><path d="M22 12 A10 10 0 1 1 12 2 L12 12 Z"/>'),
            'donut':        _svg('<circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="5" stroke-dasharray="30 57"/><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="5" stroke-dasharray="20 67" stroke-dashoffset="-30" opacity=".5"/>'),
            'scatter':      _svg('<circle cx="5" cy="17" r="2"/><circle cx="9" cy="9" r="2"/><circle cx="14" cy="14" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="20" cy="15" r="2"/>'),
            'bubble':       _svg('<circle cx="6" cy="15" r="3"/><circle cx="14" cy="10" r="5"/><circle cx="20" cy="17" r="2"/>'),
            'heatmap':      _svg('<rect x="2" y="2" width="5" height="5" rx=".5" opacity=".3"/><rect x="9" y="2" width="5" height="5" rx=".5" opacity=".6"/><rect x="16" y="2" width="5" height="5" rx=".5"/><rect x="2" y="9" width="5" height="5" rx=".5" opacity=".7"/><rect x="9" y="9" width="5" height="5" rx=".5" opacity=".4"/><rect x="16" y="9" width="5" height="5" rx=".5" opacity=".9"/><rect x="2" y="16" width="5" height="5" rx=".5" opacity=".5"/><rect x="9" y="16" width="5" height="5" rx=".5"/><rect x="16" y="16" width="5" height="5" rx=".5" opacity=".2"/>'),
            'histogram':    _svg('<rect x="2" y="16" width="3" height="5" rx=".5"/><rect x="6" y="10" width="3" height="11" rx=".5"/><rect x="10" y="6" width="3" height="15" rx=".5"/><rect x="14" y="10" width="3" height="11" rx=".5"/><rect x="18" y="14" width="3" height="7" rx=".5"/>'),
            'box':          _svg('<rect x="7" y="7" width="10" height="10" rx="1" fill="none" stroke="currentColor" stroke-width="1.5"/><line x1="12" y1="3" x2="12" y2="7" stroke="currentColor" stroke-width="1.5"/><line x1="12" y1="17" x2="12" y2="21" stroke="currentColor" stroke-width="1.5"/><line x1="9" y1="12" x2="15" y2="12" stroke="currentColor" stroke-width="1.5"/>'),
            'funnel':       _svg('<path d="M3 4 L21 4 L15 12 L15 20 L9 20 L9 12 Z"/>'),
            'treemap':      _svg('<rect x="2" y="2" width="9" height="12" rx="1"/><rect x="13" y="2" width="9" height="7" rx="1"/><rect x="2" y="16" width="5" height="6" rx="1"/><rect x="9" y="16" width="13" height="6" rx="1"/><rect x="13" y="11" width="9" height="3" rx="1"/>'),
            'radar':        _svg('<polygon points="12,2 20,8 17,18 7,18 4,8" fill="none" stroke="currentColor" stroke-width="1.5"/><polygon points="12,6 17,10 15,16 9,16 7,10" opacity=".4"/>'),
            'gauge':        _svg('<path d="M5 17 A8 8 0 0 1 19 17" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M12 17 L9 10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="17" r="1.5"/>'),
            'assessment_compare': _svg('<rect x="2" y="6" width="4" height="12" rx="1"/><rect x="2" y="6" width="4" height="5" rx="1" opacity=".5"/><rect x="8" y="9" width="4" height="9" rx="1"/><rect x="8" y="9" width="4" height="4" rx="1" opacity=".5"/><rect x="14" y="4" width="4" height="14" rx="1"/><rect x="14" y="4" width="4" height="6" rx="1" opacity=".5"/>'),
            'progress_growth': _svg('<path d="M3 17 C5 17 6 12 9 11 S13 13 16 8 S20 5 21 5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M15 5 L21 5 L21 11" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'),
            'summary_table': _svg('<rect x="2" y="3" width="20" height="3" rx=".5"/><rect x="2" y="8" width="20" height="3" rx=".5" opacity=".6"/><rect x="2" y="13" width="20" height="3" rx=".5" opacity=".4"/><rect x="2" y="18" width="20" height="3" rx=".5" opacity=".2"/>'),
        }
        chart_types_flat = [(k, l, _ICONS.get(k, _ICONS['bar']), g) for k, l, _, g in CHART_TYPES]

        agg_choices = [
            ('count', 'Count'), ('sum', 'Sum'), ('avg', 'Average'),
            ('min', 'Min'), ('max', 'Max'), ('median', 'Median'),
        ]

        all_years = list(test_structure.keys()) + list(marks_structure.keys())
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
            'year_labels':          get_year_labels_map(all_years),
            'year_labels_json':     json.dumps(get_year_labels_map(all_years)),
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
