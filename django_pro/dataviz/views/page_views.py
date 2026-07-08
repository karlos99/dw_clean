"""DataVizPage builder and viewer page views."""
import json

from django.shortcuts import get_object_or_404, render
from django.views.generic import TemplateView

from dataviz.models import DataVizPage, Dashboard, SavedChart, DataVizPageRow

from .mixins import ProjectAdminMixin, is_project_admin


def page_index(request):
    """List all DataViz pages the user has built."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not is_project_admin(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Project Admins only.')

    pages = DataVizPage.objects.all().order_by('-updated_at')[:100]
    return render(request, 'dataviz/pages/index.html', {'pages': pages})


class PageBuilderView(ProjectAdminMixin, TemplateView):
    """Create or edit a DataVizPage with inline chart placement."""
    template_name = 'dataviz/pages/builder.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        page_slug = self.kwargs.get('slug')

        dv_page = None
        sections_json = '[]'
        rows_json = '[]'
        sections = []
        filters_json = '[]'
        if page_slug:
            dv_page = get_object_or_404(DataVizPage, slug=page_slug)
            sections = list(dv_page.sections.select_related('dashboard', 'chart').all())
            sections_json = json.dumps([s.to_dict() for s in sections])
            rows = list(dv_page.rows.prefetch_related('sections__chart', 'sections__dashboard').all())
            rows_json = json.dumps([r.to_dict() for r in rows])
            filters_json = json.dumps(dv_page.page_filters or [])

        # Charts available to add (same as dashboard builder)
        available_charts = SavedChart.objects.all().order_by('name')[:200]

        context.update({
            'page':             dv_page,
            'dv_page':          dv_page,
            'page_id':          dv_page.pk if dv_page else None,
            'page_json':        json.dumps(dv_page.to_dict() if dv_page else None),
            'sections':         sections,
            'sections_json':    sections_json,
            'rows_json':        rows_json,
            'filters_json':     filters_json,
            'available_charts': available_charts,
        })
        return context


page_builder = PageBuilderView.as_view()


def page_view(request, slug):
    """
    Render a published DataVizPage for end-users.
    Access is controlled via the linked users.Page and the users.decorators.page_access_required
    decorator applied at the URL level, OR via this view's own check below.
    """
    dv_page = get_object_or_404(DataVizPage, slug=slug)

    # Owner can always preview; everyone else needs active status + access check
    is_owner = request.user.is_authenticated and (dv_page.owner == request.user or is_project_admin(request.user))
    if not is_owner:
        if dv_page.status != DataVizPage.STATUS_ACTIVE:
            from django.http import Http404
            raise Http404
        if dv_page.linked_site_page:
            from users.context_processors import user_can_access_page
            if not user_can_access_page(request.user, dv_page.linked_site_page):
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied

    # Admin-configured default filter values, in the same {col, label, value}
    # shape the page's Alpine filter-tag JS already uses (see toggleValue()/
    # removeTag() in view.html) — seeded client-side on first load, only for
    # filters the visitor hasn't already picked a value for this session.
    default_filter_tags = []
    for f in dv_page.page_filters:
        default_val = (f.get('default_val') or '').strip()
        if not default_val:
            continue
        col = f.get('source_col', '')
        if not col:
            continue
        label = f.get('label', '')
        values = (
            [v.strip() for v in default_val.split(',') if v.strip()]
            if f.get('widget') == 'multi' else [default_val]
        )
        for v in values:
            default_filter_tags.append({'col': col, 'label': label, 'value': v})

    sections = dv_page.sections.select_related('dashboard', 'chart', 'row').prefetch_related(
        'dashboard__chart_slots__chart'
    ).all()

    rows = dv_page.rows.prefetch_related(
        'sections__chart', 'sections__dashboard'
    ).all()

    # Legacy sections without a row
    orphan_sections = [s for s in sections if s.row_id is None and s.element_type in ('chart', 'html')]

    # ── Precompute per-chart filter columns for selective refresh ──
    # Extract all column references from each chart's config + source_filters
    # so the JS can skip refreshing charts unaffected by a filter change.
    _CONFIG_COL_KEYS = frozenset({
        'x_col', 'y_col', 'stack_col', 'series_col', 'group_col', 'size_col',
        'st_row_col', 'st_col_col', 'st_row_cols', 'st_col_cols',
        'st_value_col', 'dyn_row_cols', 'dyn_col_cols', 'dyn_value_col',
        'pg_before_col', 'pg_after_col',
    })
    def _chart_filter_cols(chart_obj):
        """Return sorted list of column names referenced by a chart."""
        if not chart_obj:
            return ''
        cols = set()
        cfg = chart_obj.config or {}
        for k, v in cfg.items():
            if k in _CONFIG_COL_KEYS:
                if isinstance(v, str) and v:
                    cols.add(v)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and item:
                            cols.add(item)
        sf = chart_obj.source_filters or {}
        for k in sf:
            if isinstance(k, str) and k:
                cols.add(k)
        if chart_obj.source_type == 'columns' and 'selected_columns' in cfg:
            sel = cfg.get('selected_columns', [])
            if isinstance(sel, list):
                for item in sel:
                    if isinstance(item, str) and item:
                        cols.add(item)
        return ','.join(sorted(cols))

    all_sections = []
    for row_obj in rows:
        all_sections.extend(row_obj.sections.all())
    all_sections.extend(orphan_sections)

    # Attach filter cols via a flat dict keyed by section.id (Django templates
    # reject attribute names starting with underscore)
    section_filter_cols_map = {}
    for section_obj in all_sections:
        if section_obj.element_type == 'chart' and section_obj.chart:
            section_filter_cols_map[section_obj.id] = _chart_filter_cols(section_obj.chart)
        if section_obj.element_type == 'dashboard' and section_obj.dashboard:
            for slot in section_obj.dashboard.chart_slots.all():
                if slot.chart:
                    section_filter_cols_map[slot.id] = _chart_filter_cols(slot.chart)

    # Available filter columns on this page (for cross-page filter matching in JS)
    page_filter_cols = sorted(set(f.get('source_col', '') for f in dv_page.page_filters if f.get('source_col')))

    return render(request, 'dataviz/pages/view.html', {
        'page':         dv_page,
        'dv_page':      dv_page,
        'rows':         rows,
        'sections':     sections,
        'orphan_sections': orphan_sections,
        'default_filter_tags_json': json.dumps(default_filter_tags),
        'page_filter_cols': page_filter_cols,
        'page_filter_cols_json': json.dumps(page_filter_cols),
        'section_filter_cols_map': section_filter_cols_map,
    })
