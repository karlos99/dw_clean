from __future__ import annotations

import re

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from placementcards.models import PlacementTag
from placementcards.view_modules.card_data_utils import (
    can_user_manage_tags,
    get_request_school,
    invalidate_school_caches,
    tags_usage_for_school,
)


HEX_COLOR_RE = re.compile(r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$')


def _normalize_tag_color(raw_color: str | None) -> str:
    color = (raw_color or '').strip()
    if not color:
        return '#64748b'
    if HEX_COLOR_RE.match(color):
        return color
    if re.fullmatch(r'(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})', color):
        return f'#{color}'
    if color.startswith('bg-'):
        return color
    return '#64748b'


@login_required
def manage_tags(request: HttpRequest):
    school = get_request_school(request)
    if not can_user_manage_tags(request.user, school):
        return HttpResponse('Forbidden', status=403)
    tags = PlacementTag.objects.filter(school=school, is_active=True).order_by('display_order', 'name')
    context = {
        'school': school,
        'tags': tags,
        'tag_usage': tags_usage_for_school(school.pk),
    }
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'placementcards/partials/tags_table.html', context)
    return render(request, 'placementcards/manage_tags.html', context)


@login_required
@transaction.atomic
def create_tag_modal(request: HttpRequest):
    school = get_request_school(request)
    if not can_user_manage_tags(request.user, school):
        return HttpResponse('Forbidden', status=403)

    if request.method == 'GET':
        return render(request, 'placementcards/partials/tag_form.html', {'mode': 'create'})

    name = request.POST.get('name', '').strip()
    color = _normalize_tag_color(request.POST.get('color'))
    description = request.POST.get('description', '').strip()
    if not name:
        return HttpResponse('Tag name is required', status=400)

    max_order = PlacementTag.objects.filter(school=school).order_by('-display_order').values_list('display_order', flat=True).first() or 0
    PlacementTag.objects.create(
        school=school,
        name=name,
        color=color,
        description=description,
        display_order=max_order + 1,
        created_by=request.user,
    )
    invalidate_school_caches(school.pk)
    return manage_tags(request)


@login_required
@transaction.atomic
def edit_tag_modal(request: HttpRequest, tag_id: int):
    school = get_request_school(request)
    if not can_user_manage_tags(request.user, school):
        return HttpResponse('Forbidden', status=403)

    tag = get_object_or_404(PlacementTag, id=tag_id, school=school)
    if request.method == 'GET':
        return render(request, 'placementcards/partials/tag_form.html', {'mode': 'edit', 'tag': tag})

    tag.name = request.POST.get('name', '').strip() or tag.name
    tag.color = _normalize_tag_color(request.POST.get('color', tag.color))
    tag.description = request.POST.get('description', '').strip()
    tag.save(update_fields=['name', 'color', 'description', 'updated_at'])
    invalidate_school_caches(school.pk)
    return manage_tags(request)


@login_required
@transaction.atomic
def delete_tag(request: HttpRequest, tag_id: int):
    school = get_request_school(request)
    if not can_user_manage_tags(request.user, school):
        return HttpResponse('Forbidden', status=403)

    tag = get_object_or_404(PlacementTag, id=tag_id, school=school)
    tag.is_active = False
    tag.save(update_fields=['is_active', 'updated_at'])
    tag.placement_cards.clear()
    invalidate_school_caches(school.pk)
    return manage_tags(request)


@login_required
@transaction.atomic
def reorder_tags(request: HttpRequest):
    school = get_request_school(request)
    if not can_user_manage_tags(request.user, school):
        return HttpResponse('Forbidden', status=403)

    ordered_ids = [int(tag_id) for tag_id in request.POST.getlist('tag_ids') if tag_id.isdigit()]
    tags = {tag.id: tag for tag in PlacementTag.objects.filter(id__in=ordered_ids, school=school)}
    for order, tag_id in enumerate(ordered_ids):
        tag = tags.get(tag_id)
        if tag is None:
            continue
        tag.display_order = order
        tag.save(update_fields=['display_order'])

    invalidate_school_caches(school.pk)
    return HttpResponse('ok')
