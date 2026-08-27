"""Page CRUD + section management API views."""
import json

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.text import slugify
from django.views.decorators.http import require_GET

from dataviz.models import Dashboard, DataVizPage, DataVizPageSection, DataVizPageRow, DataVizPageTab, DataVizPageFolder, SavedChart
from dataviz.utils.page_publish import (
    publish_page, unpublish_page, validate_page_slug, normalize_page_folder,
    all_page_folder_paths, rewrite_folder_prefix, delete_folder_and_pages,
)

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

    raw_slug = (body.get('slug') or '').strip()
    page_id = body.get('id')
    if page_id:
        page = get_object_or_404(DataVizPage, pk=page_id)
    else:
        page = DataVizPage(owner=request.user)

    page.title           = title
    page.description     = body.get('description', '')
    if 'folder' in body:
        page.folder = normalize_page_folder(body.get('folder', ''))
        if page.folder:
            DataVizPageFolder.objects.get_or_create(path=page.folder)
    page.page_filters    = body.get('page_filters', []) if isinstance(body.get('page_filters'), list) else []
    page.container_width = body.get('container_width', page.container_width or 'xl')
    if 'show_header' in body:
        page.show_header = bool(body['show_header'])

    was_draft = page.status != DataVizPage.STATUS_ACTIVE
    new_status = body.get('status', page.status)
    page.status = new_status

    if raw_slug:
        slug, slug_error = validate_page_slug(raw_slug, exclude_page_id=page.pk)
        if slug_error:
            return JsonResponse({'error': slug_error}, status=400)
        page.slug = slug

    page.save()

    if page.status == DataVizPage.STATUS_ACTIVE:
        publish_page(page, request.user)
    elif not was_draft:
        unpublish_page(page)

    return JsonResponse({'page': page.to_dict(), 'success': True})


@login_required
def page_duplicate_api(request, slug):
    """Duplicate a page and its layout without copying shared charts."""
    if not is_project_admin(request.user):
        return FORBIDDEN
    if request.method not in ('POST', 'PUT'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    source = get_object_or_404(DataVizPage, slug=slug)
    copy_number = 1
    with transaction.atomic():
        while True:
            suffix = ' (Copy)' if copy_number == 1 else f' (Copy {copy_number})'
            title = f'{source.title[:200 - len(suffix)]}{suffix}'
            new_slug = slugify(title)[:200] or f'page-{source.pk}-copy'
            if not DataVizPage.objects.filter(slug=new_slug).exists():
                break
            copy_number += 1

        page = DataVizPage.objects.create(
            title=title,
            slug=new_slug,
            folder=source.folder or '',
            description=source.description,
            container_width=source.container_width,
            show_header=source.show_header,
            owner=request.user,
            status=DataVizPage.STATUS_DRAFT,
            page_filters=source.page_filters or [],
        )

        row_map = {}
        tab_map = {}
        for source_tab in source.tabs.all():
            tab_map[source_tab.pk] = DataVizPageTab.objects.create(
                page=page, name=source_tab.name, position=source_tab.position,
            )
        for source_row in source.rows.all():
            row_map[source_row.pk] = DataVizPageRow.objects.create(
                page=page,
                tab=tab_map.get(source_row.tab_id),
                label=source_row.label,
                position=source_row.position,
                bg_color=source_row.bg_color,
                collapsed=source_row.collapsed,
                col_guide=source_row.col_guide,
            )

        for source_section in source.sections.all():
            DataVizPageSection.objects.create(
                page=page,
                row=row_map.get(source_section.row_id),
                element_type=source_section.element_type,
                chart=source_section.chart,
                show_title=source_section.show_title,
                slot_filters=source_section.slot_filters or [],
                gs_x=source_section.gs_x,
                gs_y=source_section.gs_y,
                gs_w=source_section.gs_w,
                gs_h=source_section.gs_h,
                dashboard=source_section.dashboard,
                html_content=source_section.html_content,
                position=source_section.position,
            )

    return JsonResponse({'page': page.to_dict(), 'success': True})


@login_required
@require_GET
def page_folders_list_api(request):
    """Return all known page folder paths."""
    if not is_project_admin(request.user):
        return FORBIDDEN
    return JsonResponse({'folders': all_page_folder_paths()})


@login_required
def page_folder_create_api(request):
    """Create an empty folder on the pages list."""
    if not is_project_admin(request.user):
        return FORBIDDEN
    if request.method not in ('POST', 'PUT'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    path = normalize_page_folder(body.get('folder', ''))
    if not path:
        return JsonResponse({'error': 'Folder name is required'}, status=400)

    DataVizPageFolder.objects.get_or_create(path=path)
    return JsonResponse({'success': True, 'folder': path})


@login_required
def page_folder_rename_api(request):
    """Rename a folder (and nested paths) without deleting pages."""
    if not is_project_admin(request.user):
        return FORBIDDEN
    if request.method not in ('POST', 'PUT'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    old_path = normalize_page_folder(body.get('folder', ''))
    new_leaf = (body.get('name') or '').strip()
    if not old_path:
        return JsonResponse({'error': 'Folder is required'}, status=400)
    if not new_leaf or '/' in new_leaf:
        return JsonResponse({'error': 'Enter a folder name without /'}, status=400)

    parts = [p.strip() for p in old_path.split(' / ') if p.strip()]
    parts[-1] = new_leaf
    new_path = normalize_page_folder(' / '.join(parts))
    with transaction.atomic():
        moved = rewrite_folder_prefix(old_path, new_path)
    return JsonResponse({'success': True, 'folder': new_path, 'moved': moved})


@login_required
def page_folder_delete_api(request):
    """Delete a folder and any pages nested under it."""
    if not is_project_admin(request.user):
        return FORBIDDEN
    if request.method not in ('POST', 'DELETE'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    path = normalize_page_folder(body.get('folder', ''))
    if not path:
        return JsonResponse({'error': 'Folder is required'}, status=400)

    with transaction.atomic():
        deleted = delete_folder_and_pages(path)
    return JsonResponse({'success': True, 'deleted_pages': deleted})


@login_required
def page_publish_api(request, slug):
    """Quickly publish a draft page (add to navbar)."""
    if not is_project_admin(request.user):
        return FORBIDDEN
    if request.method not in ('POST', 'PUT'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    page = get_object_or_404(DataVizPage, slug=slug)
    if page.status == DataVizPage.STATUS_ACTIVE:
        return JsonResponse({'error': 'Page is already published'}, status=400)

    page.status = DataVizPage.STATUS_ACTIVE
    page.save(update_fields=['status'])
    publish_page(page, request.user)
    page.refresh_from_db()
    return JsonResponse({'page': page.to_dict(), 'success': True})


@login_required
def page_unpublish_api(request, slug):
    """Quickly unpublish a page (set to draft and hide from navbar)."""
    if not is_project_admin(request.user):
        return FORBIDDEN
    if request.method not in ('POST', 'PUT'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    page = get_object_or_404(DataVizPage, slug=slug)
    if page.status != DataVizPage.STATUS_ACTIVE:
        return JsonResponse({'error': 'Page is not published'}, status=400)

    unpublish_page(page)
    page.refresh_from_db()
    return JsonResponse({'page': page.to_dict(), 'success': True})


@login_required
def page_batch_move_api(request):
    """Move multiple pages to a folder in one request."""
    if not is_project_admin(request.user):
        return FORBIDDEN
    if request.method not in ('POST', 'PUT'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    slugs = [s for s in (body.get('slugs') or []) if s]
    if not slugs:
        return JsonResponse({'error': 'No pages selected'}, status=400)

    folder = normalize_page_folder(body.get('folder', ''))
    updated = DataVizPage.objects.filter(slug__in=slugs).update(folder=folder)
    if folder:
        DataVizPageFolder.objects.get_or_create(path=folder)
    return JsonResponse({'success': True, 'moved': updated})


@login_required
def page_move_api(request, slug):
    """Move a page to a different folder."""
    if not is_project_admin(request.user):
        return FORBIDDEN
    if request.method not in ('POST', 'PUT'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    page = get_object_or_404(DataVizPage, slug=slug)
    page.folder = normalize_page_folder(body.get('folder', ''))
    if page.folder:
        DataVizPageFolder.objects.get_or_create(path=page.folder)
    page.save(update_fields=['folder', 'updated_at'])
    return JsonResponse({'page': page.to_dict(), 'success': True})


@login_required
def page_delete_api(request, slug):
    """DELETE a DataVizPage and unpublish its users.Page."""
    if not is_project_admin(request.user):
        return FORBIDDEN
    if request.method not in ('POST', 'DELETE'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    page = get_object_or_404(DataVizPage, slug=slug)
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

    page = get_object_or_404(DataVizPage, slug=slug)

    if request.method == 'GET':
        rows = page.rows.prefetch_related('sections__chart', 'sections__dashboard').all()
        # Also return orphan sections (no row) for backward compat
        orphan_sections = page.sections.filter(row__isnull=True).select_related('chart', 'dashboard').order_by('position')
        return JsonResponse({
            'rows': [r.to_dict() for r in rows],
            'tabs': [{'id': t.id, 'name': t.name, 'position': t.position} for t in page.tabs.all()],
            'orphan_sections': [s.to_dict() for s in orphan_sections],
        })

    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    action = body.get('action', '')

    if action == 'add_tab':
        name = (body.get('name') or '').strip()
        if not name:
            return JsonResponse({'error': 'Tab name is required'}, status=400)
        tab = DataVizPageTab.objects.create(page=page, name=name, position=page.tabs.count())
        return JsonResponse({'tab': {'id': tab.id, 'name': tab.name, 'position': tab.position}, 'success': True})

    if action == 'update_tab':
        tab = get_object_or_404(DataVizPageTab, pk=body.get('tab_id'), page=page)
        name = (body.get('name') or '').strip()
        if not name:
            return JsonResponse({'error': 'Tab name is required'}, status=400)
        tab.name = name
        tab.position = int(body.get('position', tab.position))
        tab.save(update_fields=['name', 'position'])
        return JsonResponse({'success': True})

    if action == 'delete_tab':
        tab = get_object_or_404(DataVizPageTab, pk=body.get('tab_id'), page=page)
        tab.delete()
        return JsonResponse({'success': True})

    if action == 'add_row':
        pos = page.rows.count()
        row = DataVizPageRow.objects.create(
            page=page,
            label=body.get('label', ''),
            position=pos,
            tab_id=body.get('tab_id') or None,
        )
        return JsonResponse({'row': row.to_dict(), 'success': True})

    if action == 'add_chart':
        row_id = body.get('row_id')
        row = get_object_or_404(DataVizPageRow, pk=row_id, page=page)
        chart = get_object_or_404(SavedChart, pk=body.get('chart_id'))
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
                tab_id=row_data.get('tab_id') or None,
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
        pk=section_id,
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
        if 'chart_name' in body and section.chart:
            section.chart.name = body['chart_name'].strip()
            section.chart.save()
        # Per-chart container sizing (persisted to chart config)
        if section.chart and ('container_width' in body or 'container_height' in body):
            cfg = section.chart.config or {}
            if 'container_width' in body:
                val = body['container_width']
                cfg['container_width'] = int(val) if val not in (None, '', 'null') else None
            if 'container_height' in body:
                val = body['container_height']
                cfg['container_height'] = int(val) if val not in (None, '', 'null') else None
            section.chart.config = cfg
            section.chart.save()
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
    Results are cached for 5 minutes per (slug, filter_id) pair.
    """
    from django.db import connections
    from django.core.cache import cache
    from clickhouse_utils.executor import QueryExecutor, CLICKHOUSE_SYNCED_TABLES

    page = get_object_or_404(DataVizPage, slug=slug)

    # find the filter definition by id
    filter_def = next((f for f in (page.page_filters or []) if str(f.get('id')) == str(filter_id)), None)
    if not filter_def:
        return JsonResponse({'error': 'filter not found'}, status=404)

    table = filter_def.get('source_table', '') or 'current_student'
    col   = filter_def.get('source_col', '')
    if not col:
        return JsonResponse({'values': []})

    cache_key = f"dv_page_filter:{slug}:{filter_id}:{table}:{col}"
    cached = cache.get(cache_key)
    if cached is not None:
        values = cached
    else:
        try:
            # Only route through ClickHouse when this table is actually mirrored
            # there — otherwise the data doesn't exist and Postgres is correct.
            cursor_ctx = QueryExecutor().cursor() if table in CLICKHOUSE_SYNCED_TABLES else connections['default'].cursor()
            with cursor_ctx as cursor:
                cursor.execute(
                    f'SELECT DISTINCT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL ORDER BY "{col}"'
                )
                values = [row[0] for row in cursor.fetchall()]
            cache.set(cache_key, values, 300)
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
        pk=row_id,
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
        if 'tab_id' in body:
            row.tab = DataVizPageTab.objects.filter(pk=body.get('tab_id'), page=row.page).first() if body.get('tab_id') else None
        if 'bg_color' in body:
            row.bg_color = body['bg_color']
        if 'collapsed' in body:
            row.collapsed = bool(body['collapsed'])
        if 'col_guide' in body:
            row.col_guide = int(body['col_guide'])
        row.save()
        return JsonResponse({'row': row.to_dict(), 'success': True})

    return JsonResponse({'error': 'Method not allowed'}, status=405)


@require_GET
def clear_cache_api(request):
    """Clear all dataviz-related Redis caches. Returns JSON so the
    frontend can call it and then reload the page."""
    from django.core.cache import cache

    patterns = ['chart_*', 'dv:*', 'drill_*', 'dataexp:*', 'nextyear:*']
    deleted = 0
    try:
        for pattern in patterns:
            keys = cache.keys(pattern)
            if keys:
                cache.delete_many(keys)
                deleted += len(keys)
    except Exception:
        pass

    return JsonResponse({'success': True, 'cleared': deleted})

    return JsonResponse({'error': 'Method not allowed'}, status=405)
