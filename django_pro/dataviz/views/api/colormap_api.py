"""Color-mapping preset API views: save, list, detail/update/delete.

Lets a project admin save a chart's "Colour Mapping" (value -> hex color)
as a named, reusable preset, then load it into other charts. Presets are
shared across all project admins, same as SavedChart/Dataset/DataVizPage.
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST, require_GET

from dataviz.models import ColorMapPreset

from ..mixins import is_project_admin, FORBIDDEN


@login_required
@require_POST
def colormap_preset_save_api(request):
    """Create a new ColorMapPreset. Accepts JSON {name, color_map}."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    name = (body.get('name') or '').strip()
    color_map = body.get('color_map')
    if not name:
        return JsonResponse({'error': 'Preset name is required'}, status=400)
    if not isinstance(color_map, dict):
        return JsonResponse({'error': 'color_map must be an object'}, status=400)

    preset = ColorMapPreset.objects.create(
        name=name, color_map=color_map, owner=request.user,
    )
    return JsonResponse({'preset': preset.to_dict(), 'success': True})


@login_required
def colormap_preset_detail_api(request, preset_id):
    """GET a preset's full dict (load); PUT to update; DELETE to remove."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    preset = get_object_or_404(ColorMapPreset, pk=preset_id)

    if request.method == 'GET':
        return JsonResponse({'preset': preset.to_dict()})

    if request.method in ('POST', 'PUT'):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        name = body.get('name')
        color_map = body.get('color_map')
        if name is not None:
            name = name.strip()
            if not name:
                return JsonResponse({'error': 'Preset name is required'}, status=400)
            preset.name = name
        if color_map is not None:
            if not isinstance(color_map, dict):
                return JsonResponse({'error': 'color_map must be an object'}, status=400)
            preset.color_map = color_map
        preset.save()
        return JsonResponse({'preset': preset.to_dict(), 'success': True})

    if request.method == 'DELETE':
        preset.delete()
        return JsonResponse({'success': True})

    return JsonResponse({'error': 'Method not allowed'}, status=405)


@login_required
@require_GET
def colormap_preset_list_api(request):
    """List all color-mapping presets for the picker dropdown."""
    if not is_project_admin(request.user):
        return FORBIDDEN

    presets = ColorMapPreset.objects.all().order_by('name')
    return JsonResponse({'presets': [p.to_dict() for p in presets]})
