"""Page CRUD + section management API views."""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from dataviz.models import Dashboard, DataVizPage, DataVizPageSection, DataVizPageRow, SavedChart
from dataviz.utils.page_publish import publish_page, unpublish_page

from ..mixins import is_project_admin, FORBIDDEN


@login_required
def page_save_api(request):
    """Create or update a DataVizPage. POST JSON body."""
    if not is_project_admin(request.user):
        return FORBIDDEN
    if request.method not in ('POST', 'PUT'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    title = (body.get('title') or '').strip()
    if not title:
        return JsonResponse({'error': 'title is required'}, status=400)

    slug = body.get('slug', '').strip() or None
    page_id = body.get('id')
    if page_id:
        page = get_object_or_404(DataVizPage, pk=page_id, owner=request.user)
    else:
        page = DataVizPage(owner=request.user)

    page.title           = title
    page.description     = body.get('description', '')
    page.page_filters    = body.get('page_filters', []) if isinstance(body.get('page_filters'), list) else []
    page.container_width = body.get('container_width', page.container_width or 'xl')
    if 'show_header' in body:
        page.show_header = bool(body['show_header'])

    was_draft = page.status != DataVizPage.STATUS_ACTIVE
    new_status = body.get('status', page.status)
    page.status = new_status

    if slug:
        page.slug = slug
    page.save()

    if page.status == DataVizPage.STATUS_ACTIVE and was_draft:
        publish_page(page, request.user)
    elif page.status == DataVizPage.STATUS_DRAFT and not was_draft:
        unpublish_page(page)

    return JsonResponse({'page': page.to_dict(), 'success': True})


@login_required
def page_delete_api(request, slug):
    """DELETE a DataVizPage and unpublish its users.Page."""
    if not is_project_admin(request.user):
        return FORBIDDEN
    if request.method not in ('POST', 'DELETE'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    page = get_object_or_404(DataVizPage, slug=slug, owner=request.user)
    unpublish_page(page)
    page.delete()
    return JsonResponse({'success': True})


@login_required
def page_sections_api(request, slug):
    """
    Manage rows + sections on a DataVizPage:
      GET               → return rows with nested sections JSON
      POST action=add_row        → { label? }
      POST action=add_chart      → { row_id, chart_id, gs_w?, gs_h? }
      POST action=add_html       → { row_id, html_content?, gs_w?, gs_h? }
      POST action=remove         → { section_id }
      POST action=remove_row     → { row_id }
      POST action=reorder        → { rows: [{row_id, position, sections: [{section_id, gs_x, gs_y, gs_w, gs_h}]}] }
    """
    if not is_project_admin(request.user):
        return FORBIDDEN

    page = get_object_or_404(DataVizPage, slug=slug, owner=request.user)

    if request.method == 'GET':
        rows = page.rows.prefetch_related('sections__chart', 'sections__dashboard').all()
        # Also return orphan sections (no row) for backward compat
        orphan_sections = page.sections.filter(row__isnull=True).select_related('chart', 'dashboard').order_by('position')
        return JsonResponse({
            'rows': [r.to_dict() for r in rows],
            'orphan_sections': [s.to_dict() for s in orphan_sections],
        })

    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    action = body.get('action', '')

    if action == 'add_row':
        pos = page.rows.count()
        row = DataVizPageRow.objects.create(
            page=page,
            label=body.get('label', ''),
            position=pos,
        )
        return JsonResponse({'row': row.to_dict(), 'success': True})

    if action == 'add_chart':
        row_id = body.get('row_id')
        row = get_object_or_404(DataVizPageRow, pk=row_id, page=page)
        chart = get_object_or_404(SavedChart, pk=body.get('chart_id'), owner=request.user)
        pos = row.sections.count()
        s = DataVizPageSection.objects.create(
            page=page, row=row,
            element_type=DataVizPageSection.TYPE_CHART,
            chart=chart, position=pos,
            gs_w=body.get('gs_w', 6), gs_h=body.get('gs_h', 4),
        )
        return JsonResponse({'section': s.to_dict(), 'success': True})

    if action == 'add_html':
        row_id = body.get('row_id')
        row = get_object_or_404(DataVizPageRow, pk=row_id, page=page)
        pos = row.sections.count()
        s = DataVizPageSection.objects.create(
            page=page, row=row,
            element_type=DataVizPageSection.TYPE_HTML,
            html_content=body.get('html_content', '<h3>Title</h3><p>Description text…</p>'),
            position=pos,
            gs_w=body.get('gs_w', 12), gs_h=body.get('gs_h', 2),
        )
        return JsonResponse({'section': s.to_dict(), 'success': True})

    if action == 'remove':
        DataVizPageSection.objects.filter(pk=body.get('section_id'), page=page).delete()
        return JsonResponse({'success': True})

    if action == 'remove_row':
        DataVizPageRow.objects.filter(pk=body.get('row_id'), page=page).delete()
        return JsonResponse({'success': True})

    if action == 'reorder':
        for row_data in body.get('rows', []):
            DataVizPageRow.objects.filter(pk=row_data['row_id'], page=page).update(
                position=row_data.get('position', 0),
            )
            for item in row_data.get('sections', []):
                DataVizPageSection.objects.filter(pk=item['section_id'], page=page).update(
                    row_id=row_data['row_id'],
                    gs_x=item.get('gs_x', 0),
                    gs_y=item.get('gs_y', 0),
                    gs_w=item.get('gs_w', 6),
                    gs_h=item.get('gs_h', 4),
                    position=item.get('position', 0),
                )
        return JsonResponse({'success': True})

    return JsonResponse({'error': 'Invalid action'}, status=400)


@login_required
def section_detail_api(request, section_id):
    """PUT to update section properties; DELETE to remove."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    section = get_object_or_404(
        DataVizPageSection.objects.select_related('page'),
        pk=section_id, page__owner=request.user,
    )

    if request.method == 'DELETE':
        section.delete()
        return JsonResponse({'success': True})

    if request.method in ('POST', 'PUT'):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        if 'html_content' in body:
            section.html_content = body['html_content']
        if 'show_title' in body:
            section.show_title = bool(body['show_title'])
        if 'position' in body:
            section.position = int(body['position'])
        if 'slot_filters' in body:
            section.slot_filters = body['slot_filters'] if isinstance(body['slot_filters'], list) else []
        for gs_field in ('gs_x', 'gs_y', 'gs_w', 'gs_h'):
            if gs_field in body:
                setattr(section, gs_field, int(body[gs_field]))
        section.save()
        return JsonResponse({'section': section.to_dict(), 'success': True})

    return JsonResponse({'error': 'Method not allowed'}, status=405)


@login_required
def page_filter_options_api(request, slug, filter_id):
    """
    Return distinct values for a page filter's source column.
    Used by HTMX to populate <select> widgets on page view.
    """
    from django.db import connections

    page = get_object_or_404(DataVizPage, slug=slug)

    # find the filter definition by id
    filter_def = next((f for f in (page.page_filters or []) if str(f.get('id')) == str(filter_id)), None)
    if not filter_def:
        return JsonResponse({'error': 'filter not found'}, status=404)

    table = filter_def.get('source_table', '') or 'current_student'
    col   = filter_def.get('source_col', '')
    if not col:
        return JsonResponse({'values': []})

    try:
        with connections['default'].cursor() as cursor:
            cursor.execute(
                f'SELECT DISTINCT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL ORDER BY "{col}" LIMIT 500'
            )
            values = [row[0] for row in cursor.fetchall()]
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)

    if request.headers.get('HX-Request'):
        return render(request, 'dataviz/partials/page_filter_options.html', {
            'filter_def': filter_def, 'values': values,
        })
    return JsonResponse({'values': values})


@login_required
@require_GET
def page_filter_columns_api(request):
    """Return available filter columns (from data explorer) grouped by category."""
    from dataexploration.constants import COLUMN_DEFINITIONS, COLUMN_CATEGORIES

    if not is_project_admin(request.user):
        return FORBIDDEN

    categories = []
    for cat_key, cat in COLUMN_CATEGORIES.items():
        cols = []
        for col_key in cat['columns']:
            label = COLUMN_DEFINITIONS.get(col_key, col_key)
            cols.append({'key': col_key, 'label': label})
        categories.append({'key': cat_key, 'title': cat['title'], 'columns': cols})

    return JsonResponse({'categories': categories})


@login_required
def row_detail_api(request, row_id):
    """PUT to update row properties; DELETE to remove row + its sections."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    row = get_object_or_404(
        DataVizPageRow.objects.select_related('page'),
        pk=row_id, page__owner=request.user,
    )

    if request.method == 'DELETE':
        row.delete()
        return JsonResponse({'success': True})

    if request.method in ('POST', 'PUT'):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        if 'label' in body:
            row.label = body['label']
        if 'position' in body:
            row.position = int(body['position'])
        if 'bg_color' in body:
            row.bg_color = body['bg_color']
        if 'collapsed' in body:
            row.collapsed = bool(body['collapsed'])
        row.save()
        return JsonResponse({'row': row.to_dict(), 'success': True})

    return JsonResponse({'error': 'Method not allowed'}, status=405)
