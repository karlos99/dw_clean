"""DataVizPage builder and viewer page views."""
import json

from django.shortcuts import get_object_or_404, render
from django.views.generic import TemplateView

from dataviz.models import DataVizPage, Dashboard, SavedChart, DataVizPageRow, DataVizPageTab, DataVizPageFolder
from dataviz.utils.page_publish import (
    build_page_tree, folder_path_from_url, collect_folder_paths, all_page_folder_paths,
    folder_contents_q,
)

from .mixins import ProjectAdminMixin, is_project_admin


def page_index(request):
    """List DataViz pages in a folder tree with publish/draft filtering."""
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())
    if not is_project_admin(request.user):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden('Project Admins only.')

    status_filter = (request.GET.get('status') or 'all').strip().lower()
    current_path = request.GET.get('folder', '').strip().strip('/')

    pages_qs = DataVizPage.objects.all().order_by('-updated_at')
    if status_filter == 'active':
        pages_qs = pages_qs.filter(status=DataVizPage.STATUS_ACTIVE)
    elif status_filter == 'draft':
        pages_qs = pages_qs.filter(status=DataVizPage.STATUS_DRAFT)

    total_count = pages_qs.count()
    pages_list = list(pages_qs[:500])
    extra_paths = list(DataVizPageFolder.objects.values_list('path', flat=True))
    root = build_page_tree(pages_list, extra_folder_paths=extra_paths)

    path_parts = [p for p in current_path.split('/') if p]
    display_curr = root
    breadcrumbs = []
    running_path = []
    for part in path_parts:
        if part in display_curr['folders']:
            running_path.append(part)
            display_curr = display_curr['folders'][part]
            breadcrumbs.append({'name': part, 'path': '/'.join(running_path)})
        else:
            display_curr = root
            breadcrumbs = []
            current_path = ''
            break

    subfolders = sorted(display_curr['folders'].keys())
    folder_pages = display_curr['pages']
    parent_folder = folder_path_from_url(current_path)
    subfolder_items = []
    for name in subfolders:
        full_path = f"{parent_folder} / {name}".strip(' / ') if parent_folder else name
        url_path = f"{current_path}/{name}" if current_path else name
        page_count = DataVizPage.objects.filter(folder_contents_q('folder', full_path)).count()
        subfolder_items.append({
            'name': name,
            'full_path': full_path,
            'url_path': url_path,
            'page_count': page_count,
        })

    all_folders = sorted(set(collect_folder_paths(root)) | set(all_page_folder_paths()))

    def _qs(extra=None):
        params = []
        if current_path:
            params.append(f'folder={current_path}')
        sf = extra if extra is not None else status_filter
        if sf and sf != 'all':
            params.append(f'status={sf}')
        return ('?' + '&'.join(params)) if params else ''

    return render(request, 'dataviz/pages/index.html', {
        'subfolders': subfolders,
        'subfolder_items': subfolder_items,
        'pages': folder_pages,
        'all_folders': all_folders,
        'all_folders_json': json.dumps(all_folders),
        'current_path': current_path,
        'breadcrumbs': breadcrumbs,
        'status_filter': status_filter,
        'query_suffix': _qs(),
        'query_suffix_all': _qs('all'),
        'query_suffix_active': _qs('active'),
        'query_suffix_draft': _qs('draft'),
        'total_count': total_count,
        'current_folder_label': folder_path_from_url(current_path),
    })


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
        tabs_json = '[]'
        if page_slug:
            dv_page = get_object_or_404(DataVizPage, slug=page_slug)
            sections = list(dv_page.sections.select_related('dashboard', 'chart').all())
            sections_json = json.dumps([s.to_dict() for s in sections])
            rows = list(dv_page.rows.prefetch_related('sections__chart', 'sections__dashboard').all())
            rows_json = json.dumps([r.to_dict() for r in rows])
            filters_json = json.dumps(dv_page.page_filters or [])
            tabs_json = json.dumps([{'id': t.id, 'name': t.name, 'position': t.position} for t in dv_page.tabs.all()])

        # Charts available to add (same as dashboard builder)
        available_charts = SavedChart.objects.all().order_by('name')[:200]

        context.update({
            'page':             dv_page,
            'dv_page':          dv_page,
            'page_id':          dv_page.pk if dv_page else None,
            'default_folder':   folder_path_from_url(self.request.GET.get('folder', '')) if not dv_page else '',
            'all_folders':      all_page_folder_paths(),
            'page_json':        json.dumps(dv_page.to_dict() if dv_page else None),
            'sections':         sections,
            'sections_json':    sections_json,
            'rows_json':        rows_json,
            'filters_json':     filters_json,
            'tabs_json':        tabs_json,
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
            from users.context_processors import user_can_preview_draft_site_page
            linked = dv_page.linked_site_page
            if not linked or not user_can_preview_draft_site_page(request.user, linked):
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
    hidden_filter_cols = []
    for f in dv_page.page_filters:
        default_val = (f.get('default_val') or '').strip()
        if f.get('hidden'):
            hidden_filter_cols.append(f.get('source_col', ''))
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
        is_hidden = bool(f.get('hidden'))
        for v in values:
            default_filter_tags.append({'col': col, 'label': label, 'value': v, 'hidden': is_hidden})

    sections = dv_page.sections.select_related('dashboard', 'chart', 'row').prefetch_related(
        'dashboard__chart_slots__chart'
    ).all()

    tabs = list(dv_page.tabs.all())
    rows = dv_page.rows.prefetch_related(
        'sections__chart', 'sections__dashboard'
    ).all()
    has_unassigned_rows = bool(tabs) and any(row.tab_id is None for row in rows)

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
            elif k == 'dyn_calculations' and isinstance(v, list):
                for item in v:
                    if isinstance(item, dict) and item.get('value_col'):
                        cols.add(item['value_col'])
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

    # Available filter columns on this page (for cross-page filter matching in JS)
    page_filter_cols = sorted(set(f.get('source_col', '') for f in dv_page.page_filters if f.get('source_col')))

    # Attach filter cols via a flat dict keyed by section.id (Django templates
    # reject attribute names starting with underscore).
    #
    # Page-level filters apply to every chart on the page, but a chart config
    # need not reference the filter column (e.g. a "Top Careers" bar whose
    # x_col is ``career`` is still narrowed by the page's ``school``/``grade``
    # filters). Union the page filter columns into each chart's declared
    # dependency set so changing a page filter refreshes every affected chart
    # instead of leaving tiles stale.
    page_filter_col_set = set(page_filter_cols)

    def _with_page_filters(chart_obj):
        declared = set(filter(None, _chart_filter_cols(chart_obj).split(',')))
        return ','.join(sorted(declared | page_filter_col_set))

    section_filter_cols_map = {}
    for section_obj in all_sections:
        if section_obj.element_type == 'chart' and section_obj.chart:
            section_filter_cols_map[section_obj.id] = _with_page_filters(section_obj.chart)
        if section_obj.element_type == 'dashboard' and section_obj.dashboard:
            for slot in section_obj.dashboard.chart_slots.all():
                if slot.chart:
                    section_filter_cols_map[slot.id] = _with_page_filters(slot.chart)

    has_visible_filters = any(not f.get('hidden') for f in dv_page.page_filters)
    has_hidden_filters = any(bool(f.get('hidden')) for f in dv_page.page_filters)

    from django.utils.http import urlencode
    default_qs = urlencode([('pf_' + t['col'], t['value']) for t in default_filter_tags])

    active_school = getattr(request.user, 'active_school', None)

    response = render(request, 'dataviz/pages/view.html', {
        'page':         dv_page,
        'dv_page':      dv_page,
        'rows':         rows,
        'tabs':         tabs,
        'has_unassigned_rows': has_unassigned_rows,
        'sections':     sections,
        'orphan_sections': orphan_sections,
        'default_filter_tags_json': json.dumps(default_filter_tags),
        'hidden_filter_cols_json': json.dumps(hidden_filter_cols),
        'default_qs':   default_qs,
        'has_visible_filters': has_visible_filters,
        'has_hidden_filters': has_hidden_filters,
        'page_filter_cols': page_filter_cols,
        'page_filter_cols_json': json.dumps(page_filter_cols),
        'section_filter_cols_map': section_filter_cols_map,
        'active_school_id': active_school.state_id if active_school else '',
        'active_school_name': active_school.school_name if active_school else '',
    })
    # The page ships inline JS that carries a client cache-key version; never let
    # a browser reuse a stale page whose JS would restore old cached chart HTML.
    response['Cache-Control'] = 'private, no-cache, no-store, must-revalidate, max-age=0'
    return response
