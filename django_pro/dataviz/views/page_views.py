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

    pages = DataVizPage.objects.all().order_by('-updated_at')
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
        available_charts = SavedChart.objects.all().order_by('name')

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

    # Collect initial filter values from GET params
    filters = {}
    for f in dv_page.page_filters:
        fid = f.get('id', '')
        val = request.GET.get(fid, f.get('default_val', ''))
        if val:
            filters[fid] = val

    sections = dv_page.sections.select_related('dashboard', 'chart', 'row').prefetch_related(
        'dashboard__chart_slots__chart'
    ).all()

    rows = dv_page.rows.prefetch_related(
        'sections__chart', 'sections__dashboard'
    ).all()

    # Legacy sections without a row
    orphan_sections = [s for s in sections if s.row_id is None and s.element_type in ('chart', 'html')]

    return render(request, 'dataviz/pages/view.html', {
        'page':         dv_page,
        'dv_page':      dv_page,
        'rows':         rows,
        'sections':     sections,
        'orphan_sections': orphan_sections,
        'active_filters': filters,
        'active_filters_json': json.dumps(filters),
        'filters_json': json.dumps(filters),
    })
