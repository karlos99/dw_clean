"""Dashboard CRUD + slot management API views."""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from dataviz.models import Dashboard, DashboardChart, SavedChart

from ..mixins import is_project_admin, FORBIDDEN


@login_required
def dashboard_save_api(request):
    """Create or update a Dashboard. POST JSON body."""
    if not is_project_admin(request.user):
        return FORBIDDEN
    if request.method not in ('POST', 'PUT'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    name = (body.get('name') or '').strip()
    if not name:
        return JsonResponse({'error': 'name is required'}, status=400)

    db_id = body.get('id')
    if db_id:
        db = get_object_or_404(Dashboard, pk=db_id)
    else:
        db = Dashboard(owner=request.user)

    db.name = name
    db.description = body.get('description', '')
    db.save()
    return JsonResponse({'dashboard': db.to_dict(), 'success': True})


@login_required
def dashboard_detail_api(request, dashboard_id):
    """GET / PUT / DELETE a single Dashboard."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    db = get_object_or_404(Dashboard, pk=dashboard_id)

    if request.method == 'GET':
        return JsonResponse({'dashboard': db.to_dict(include_charts=True)})
    if request.method == 'DELETE':
        db.delete()
        return JsonResponse({'success': True})
    if request.method in ('POST', 'PUT'):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        db.name        = (body.get('name') or db.name).strip()
        db.description = body.get('description', db.description)
        db.save()
        return JsonResponse({'dashboard': db.to_dict(), 'success': True})

    return JsonResponse({'error': 'Method not allowed'}, status=405)


@login_required
def dashboard_slots_api(request, dashboard_id):
    """
    Manage the chart slots of a dashboard:
      POST action=add         → add a SavedChart slot
      POST action=add_html    → add an HTML text slot
      POST action=remove      → remove a slot by slot_id
      POST action=reorder     → reorder as [{slot_id, gs_x, gs_y, gs_w, gs_h}, ...]
    Returns HTMX partial (dashboard_grid.html) on success.
    """
    if not is_project_admin(request.user):
        return FORBIDDEN

    db = get_object_or_404(Dashboard, pk=dashboard_id)

    if request.method == 'GET':
        from django.shortcuts import render
        slots = db.chart_slots.select_related('chart').all()
        return render(request, 'dataviz/partials/dashboard_grid.html', {'dashboard': db, 'slots': slots})

    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    action = body.get('action', 'add')

    if action == 'add':
        chart_id = body.get('chart_id')
        chart = get_object_or_404(SavedChart, pk=chart_id)
        last_pos = db.chart_slots.count()
        slot = DashboardChart.objects.create(
            dashboard=db, chart=chart, position=last_pos,
            gs_w=body.get('gs_w', 6), gs_h=body.get('gs_h', 4),
        )
        return JsonResponse({'slot': slot.to_dict(), 'success': True})

    if action == 'add_html':
        last_pos = db.chart_slots.count()
        slot = DashboardChart.objects.create(
            dashboard=db, chart=None,
            element_type=DashboardChart.ELEMENT_HTML,
            html_content=body.get('html_content', '<h3>Title</h3>'),
            position=last_pos,
            gs_w=body.get('gs_w', 12), gs_h=body.get('gs_h', 2),
        )
        return JsonResponse({'slot': slot.to_dict(), 'success': True})

    if action == 'remove':
        slot_id = body.get('slot_id')
        DashboardChart.objects.filter(pk=slot_id, dashboard=db).delete()
        return JsonResponse({'success': True})

    if action == 'reorder':
        for item in body.get('layout', []):
            DashboardChart.objects.filter(pk=item['slot_id'], dashboard=db).update(
                gs_x=item.get('gs_x', 0),
                gs_y=item.get('gs_y', 0),
                gs_w=item.get('gs_w', 6),
                gs_h=item.get('gs_h', 4),
                position=item.get('position', 0),
            )
        return JsonResponse({'success': True})

    return JsonResponse({'error': 'Invalid action'}, status=400)


@login_required
def home_dashboard_api(request):
    """Per-user private home dashboard used on the landing page.

    Any authenticated user has their own home Dashboard (is_home=True).

    GET  → {dashboard, slots}
    POST actions (JSON body):
      add_chart: {action, chart_id, gs_w?, gs_h?}
      remove:    {action, slot_id}
      reorder:   {action, layout: [{slot_id, gs_x, gs_y, gs_w, gs_h}]}
    """
    from dataviz.models import Dashboard, DashboardChart, SavedChart

    try:
        from landing.utils import get_project_config
        cfg = get_project_config()
        charts_enabled = bool(getattr(cfg, 'enable_home_charts', True))
    except Exception:
        charts_enabled = True

    if not charts_enabled:
        if request.method == 'GET':
            return JsonResponse({'dashboard': None, 'slots': [], 'success': True, 'disabled': True})
        return JsonResponse(
            {'error': 'Home dashboard charts are disabled by project settings.', 'success': False},
            status=403,
        )

    dash = Dashboard.get_home(request.user)

    if request.method == 'GET':
        slots = list(dash.chart_slots.select_related('chart').order_by('position'))
        return JsonResponse({
            'dashboard': dash.to_dict(),
            'slots': [s.to_dict() for s in slots],
            'success': True,
        })

    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    action = body.get('action', 'add_chart')

    if action == 'add_chart':
        chart_id = body.get('chart_id')
        if not chart_id:
            return JsonResponse({'error': 'chart_id is required'}, status=400)
        chart = get_object_or_404(SavedChart, pk=chart_id)
        # Only charts exposed in the User Charts menu can be added, unless admin
        if not (chart.is_user_menu or is_project_admin(request.user)):
            return JsonResponse({'error': 'This chart is not available in User Charts.'}, status=403)

        existing = dash.chart_slots.filter(chart_id=chart.pk).exists()
        if existing:
            return JsonResponse({'error': 'This chart is already on your home dashboard.'}, status=400)

        slot = DashboardChart.objects.create(
            dashboard=dash,
            chart=chart,
            position=dash.chart_slots.count(),
            gs_w=int(body.get('gs_w') or 8),
            gs_h=int(body.get('gs_h') or 5),
        )
        return JsonResponse({'slot': slot.to_dict(), 'success': True})

    if action == 'remove':
        try:
            slot_id = int(body.get('slot_id'))
        except (TypeError, ValueError):
            return JsonResponse({'error': 'Invalid slot_id'}, status=400)
        deleted = DashboardChart.objects.filter(pk=slot_id, dashboard=dash).delete()[0]
        if not deleted:
            return JsonResponse({'error': 'Slot not found'}, status=404)
        return JsonResponse({'success': True})

    if action == 'reorder':
        layout = body.get('layout') or []
        for item in layout:
            try:
                slot_id = int(item.get('slot_id'))
            except (TypeError, ValueError):
                continue
            DashboardChart.objects.filter(pk=slot_id, dashboard=dash).update(
                gs_x=int(item.get('gs_x', 0)),
                gs_y=int(item.get('gs_y', 0)),
                gs_w=int(item.get('gs_w', 8)),
                gs_h=int(item.get('gs_h', 5)),
                position=int(item.get('position', 0)),
            )
        return JsonResponse({'success': True})

    return JsonResponse({'error': 'Invalid action'}, status=400)


@login_required
def slot_detail_api(request, slot_id):
    """PUT to update slot properties; DELETE to remove."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    slot = get_object_or_404(
        DashboardChart.objects.select_related('dashboard'),
        pk=slot_id,
    )

    if request.method == 'DELETE':
        slot.delete()
        return JsonResponse({'success': True})

    if request.method in ('POST', 'PUT'):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        for field in ('width_class', 'height_class', 'html_content'):
            if field in body:
                setattr(slot, field, body[field])
        if 'show_title' in body:
            slot.show_title = bool(body['show_title'])
        if 'position' in body:
            slot.position = int(body['position'])
        if 'slot_filters' in body:
            slot.slot_filters = body['slot_filters'] if isinstance(body['slot_filters'], list) else []
        for gs_field in ('gs_x', 'gs_y', 'gs_w', 'gs_h'):
            if gs_field in body:
                setattr(slot, gs_field, int(body[gs_field]))
        slot.save()
        return JsonResponse({'slot': slot.to_dict(), 'success': True})

    return JsonResponse({'error': 'Method not allowed'}, status=405)
