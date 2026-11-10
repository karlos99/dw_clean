"""Dataset folder CRUD and move APIs."""
import json

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from dataviz.models import DataVizDatasetFolder, Dataset
from dataviz.utils.dataset_folders import (
    all_dataset_folder_paths,
    delete_folder_and_datasets,
    rewrite_dataset_folder_prefix,
)
from dataviz.utils.page_publish import normalize_page_folder

from ..mixins import is_project_admin

FORBIDDEN = HttpResponseForbidden('Project Admins only.')


@login_required
@require_GET
def dataset_folders_list_api(request):
    if not is_project_admin(request.user):
        return FORBIDDEN
    return JsonResponse({'folders': all_dataset_folder_paths()})


@login_required
def dataset_folder_create_api(request):
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

    DataVizDatasetFolder.objects.get_or_create(path=path)
    return JsonResponse({'success': True, 'folder': path})


@login_required
def dataset_folder_rename_api(request):
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
        moved = rewrite_dataset_folder_prefix(old_path, new_path)
    return JsonResponse({'success': True, 'folder': new_path, 'moved': moved})


@login_required
def dataset_folder_delete_api(request):
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
        deleted = delete_folder_and_datasets(path)
    return JsonResponse({'success': True, 'deleted_datasets': deleted})


@login_required
def dataset_move_api(request, dataset_id):
    if not is_project_admin(request.user):
        return FORBIDDEN
    if request.method not in ('POST', 'PUT'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    ds = get_object_or_404(Dataset, pk=dataset_id)
    ds.folder = normalize_page_folder(body.get('folder', ''))
    if ds.folder:
        DataVizDatasetFolder.objects.get_or_create(path=ds.folder)
    ds.save(update_fields=['folder', 'updated_at'])
    return JsonResponse({'dataset': ds.to_dict(), 'success': True})


@login_required
def dataset_batch_move_api(request):
    if not is_project_admin(request.user):
        return FORBIDDEN
    if request.method not in ('POST', 'PUT'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    ids = [int(x) for x in (body.get('ids') or []) if str(x).isdigit()]
    if not ids:
        return JsonResponse({'error': 'No datasets selected'}, status=400)

    folder = normalize_page_folder(body.get('folder', ''))
    updated = Dataset.objects.filter(pk__in=ids).update(folder=folder)
    if folder:
        DataVizDatasetFolder.objects.get_or_create(path=folder)
    return JsonResponse({'success': True, 'moved': updated})
