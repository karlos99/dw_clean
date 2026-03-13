"""Folder, query, and list management for Next Year List.

Delegates to dataexploration's generic folder management for all operations
that share the same underlying models (ReportFolder, SavedQuery).
Only the save/browse views are wrapped to reference NYL-specific templates.
"""
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponseForbidden, HttpResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib.auth import get_user_model
from django.db import transaction
import json
import traceback

from dataexploration.models import ReportFolder, SavedQuery, StudentList, FolderShare, QueryShare
from ..utils import (
    get_user_folders,
    can_user_edit_folder,
    can_user_view_query,
    can_user_manage_query,
    can_user_manage_list,
    can_create_in_folder,
    get_user_roles,
)

# ---------------------------------------------------------------------------
# Thin re-exports from dataexploration (fully generic, no template rendering)
# ---------------------------------------------------------------------------
from dataexploration.view_modules.folder_management import (
    delete_query,
    rename_query,
    share_query,
    unshare_query,
    get_query_shares,
    share_folder,
    unshare_folder,
    delete_student_list,
    rename_student_list,
    rename_folder,
    delete_folder,
    copy_query_to_folder,
    get_query_details,
    rename_modal,
    share_modal,
    create_folder_modal,
    create_folder,
)


# ---------------------------------------------------------------------------
# NYL-specific wrappers that render nextyearlist templates
# ---------------------------------------------------------------------------

@login_required
def folder_browser(request):
    """Return the folder browser HTML partial (nextyearlist template)."""
    try:
        ReportFolder.objects.get_or_create(
            owner=request.user,
            name="My Reports",
            folder_type='USER',
            defaults={'description': 'My personal saved reports'},
        )
        folders = get_user_folders(request.user).filter(parent__isnull=True).prefetch_related(
            'queries', 'student_lists', 'subfolders', 'shares'
        ).order_by('folder_type', 'name')

        shared_queries = SavedQuery.objects.filter(
            shares__shared_with=request.user
        ).select_related('owner').prefetch_related('shares').order_by('-updated_at')

        shared_queries_with_perms = []
        for query in shared_queries:
            share = query.shares.filter(shared_with=request.user).first()
            shared_queries_with_perms.append({
                'query': query,
                'can_edit': share.can_edit if share else False,
                'can_reshare': share.can_reshare if share else False,
            })

        context = {'folders': folders, 'shared_queries': shared_queries_with_perms}
        return render(request, 'nextyearlist/partials/folder_browser.html', context)
    except Exception as e:
        traceback.print_exc()
        return HttpResponse(f"Error loading folders: {str(e)}", status=500)


@login_required
def save_query_modal(request):
    ReportFolder.objects.get_or_create(
        owner=request.user, name="My Reports", folder_type='USER',
        defaults={'description': 'My personal saved reports'},
    )
    all_folders = get_user_folders(request.user)
    folders = [f for f in all_folders if can_create_in_folder(request.user, f)]
    folder_ids = [f.id for f in folders]
    existing_queries = SavedQuery.objects.filter(
        folder_id__in=folder_ids, query_type='filter', is_active=True,
    ).order_by('name')
    existing_queries_data = [
        {'id': q.id, 'name': q.name, 'description': q.description, 'folder_id': q.folder_id}
        for q in existing_queries
    ]
    context = {
        'folders': folders,
        'existing_queries': existing_queries,
        'existing_queries_json': json.dumps(existing_queries_data),
    }
    return render(request, 'nextyearlist/partials/save_query_modal.html', context)


@login_required
def save_student_list_modal(request):
    ReportFolder.objects.get_or_create(
        owner=request.user, name="My Reports", folder_type='USER',
        defaults={'description': 'My personal saved reports'},
    )
    all_folders = get_user_folders(request.user)
    folders = [f for f in all_folders if can_create_in_folder(request.user, f)]
    existing_lists = SavedQuery.objects.filter(
        owner=request.user, query_type='student_list', is_active=True,
    ).order_by('name')
    context = {'folders': folders, 'existing_lists': existing_lists}
    return render(request, 'nextyearlist/partials/save_list_modal.html', context)


@require_POST
@login_required
def save_query(request):
    """Save or update a query configuration."""
    User = get_user_model()
    name = (request.POST.get('name') or '').strip()
    description = request.POST.get('description', '')
    folder_id = request.POST.get('folder_id')
    config_json = request.POST.get('query_config')
    query_type = request.POST.get('query_type', 'filter')
    privacy = request.POST.get('privacy', 'private')
    query_id = request.POST.get('query_id')
    overwrite = request.POST.get('overwrite') == 'true'
    share_with_emails = request.POST.get('share_with_emails', '')

    if not name:
        return JsonResponse({'status': 'error', 'message': 'Name is required'}, status=400)

    def _normalize_config(raw_config, qtype):
        if not isinstance(raw_config, dict):
            raw_config = {}
        if qtype == 'student_list':
            student_ids = raw_config.get('student_ids') or []
            return {'student_ids': [str(s).strip() for s in student_ids if str(s).strip()]}
        columns = raw_config.get('columns') or []
        filters = raw_config.get('filters') or {}
        sort_by = raw_config.get('sort_by') or ''
        sort_dir = raw_config.get('sort_dir') or 'asc'
        return {'columns': columns if isinstance(columns, list) else [], 'filters': filters if isinstance(filters, dict) else {}, 'sort_by': sort_by, 'sort_dir': sort_dir}

    try:
        if query_type not in {'filter', 'student_list'}:
            return JsonResponse({'status': 'error', 'message': 'Invalid query type'}, status=400)
        try:
            raw_config = json.loads(config_json) if config_json else {}
        except Exception:
            raw_config = {}
        if query_type == 'student_list' and not raw_config:
            ssids_json = request.POST.get('ssids')
            if ssids_json:
                try:
                    raw_config = {'student_ids': json.loads(ssids_json)}
                except Exception:
                    raw_config = {}
        normalized_config = _normalize_config(raw_config, query_type)

        folder = None
        if folder_id:
            folder = ReportFolder.objects.get(pk=folder_id)
            if not can_create_in_folder(request.user, folder):
                return HttpResponseForbidden("You do not have permission to save to this folder")
        else:
            folder, _ = ReportFolder.objects.get_or_create(
                owner=request.user, name="My Reports", folder_type='USER',
                defaults={'description': 'My personal saved reports'},
            )

        if query_id:
            query = SavedQuery.objects.get(pk=query_id)
            if not can_user_manage_query(request.user, query):
                return HttpResponseForbidden("You do not have permission to update this query")
            query_type = query.query_type
            normalized_config = _normalize_config(raw_config, query_type)
            query.name = name
            query.description = description
            query.config = normalized_config
            query.privacy = privacy
            if folder:
                query.folder = folder
            message = f'{query.get_query_type_display()} updated successfully!'
        else:
            existing = SavedQuery.objects.filter(owner=request.user, query_type=query_type, name__iexact=name, folder=folder, is_active=True).first()
            if existing and not overwrite:
                return JsonResponse({'status': 'confirm', 'message': f'A {existing.get_query_type_display().lower()} named "{existing.name}" already exists. Overwrite it?', 'query_id': existing.id}, status=409)
            if existing and overwrite:
                if not can_user_manage_query(request.user, existing):
                    return HttpResponseForbidden("You do not have permission to overwrite this query")
                existing.name = name
                existing.description = description
                existing.config = normalized_config
                existing.privacy = privacy
                existing.folder = folder
                existing.save()
                query = existing
                message = f'{query.get_query_type_display()} updated successfully!'
            else:
                query = SavedQuery.objects.create(name=name, description=description, query_type=query_type, folder=folder, owner=request.user, config=normalized_config, privacy=privacy)
                message = f'{query.get_query_type_display()} saved successfully!'

        pending_emails = []
        if privacy == 'shared' and share_with_emails:
            for email in [e.strip() for e in share_with_emails.split(',') if e.strip()]:
                user = User.objects.filter(email=email).first()
                if user:
                    QueryShare.objects.get_or_create(query=query, shared_with=user, defaults={'shared_by': request.user, 'can_edit': False, 'can_reshare': False})
                else:
                    pending_emails.append(email)
        query.pending_shares = pending_emails or None
        query.save()

        response = render(request, 'nextyearlist/partials/save_success.html', {'message': message, 'query_id': query.id})
        response['HX-Trigger'] = 'refresh-folders'
        return response
    except SavedQuery.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Query not found or access denied'}, status=404)
    except ReportFolder.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Folder not found'}, status=404)
    except Exception as e:
        traceback.print_exc()
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@require_POST
@login_required
def save_student_list(request):
    ssids_json = request.POST.get('ssids')
    if ssids_json:
        ssids = json.loads(ssids_json)
        post_data = request.POST.copy()
        post_data['query_config'] = json.dumps({'student_ids': ssids})
        post_data['query_type'] = 'student_list'
        request.POST = post_data
    return save_query(request)
