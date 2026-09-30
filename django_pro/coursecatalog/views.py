from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from users.decorators import page_access_required

from .forms import (
    BulkCatalogPlacementForm,
    BulkCatalogPlacementUpdateForm,
    CatalogCategoryEditForm,
    CatalogCategoryForm,
    CatalogDisciplineForm,
    CatalogLevelForm,
    CatalogPathForm,
    CatalogPathStepForm,
    CatalogTagForm,
)
from .models import (
    CatalogCategory,
    CatalogDiscipline,
    CatalogLevel,
    CatalogPath,
    CatalogPlacement,
    CatalogTag,
    CourseCatalogEntry,
)
from .placement_ops import upsert_placement_for_tag
from .catalog_search import (
    catalog_terms_matched,
    filter_catalog_entries,
    parse_catalog_search_terms,
)
from .services.matching import student_ssids_for_placements
from .taxonomy_utils import build_taxonomy_json, selected_tag_queryset


def _subnav_context(active: str) -> dict:
    return {'subnav_active': active}


def _placements_url(**params) -> str:
    base = '/coursecatalog/placements/'
    clean = {k: v for k, v in params.items() if v not in (None, '')}
    if not clean:
        return base
    return f'{base}?{urlencode(clean)}'


def _assignment_filters_from_request(request, *, use_post: bool = False) -> dict:
    src = request.POST if use_post else request.GET
    return {
        'filter_tag': (src.get('filter_tag') or '').strip(),
        'filter_category': (src.get('filter_category') or '').strip(),
        'filter_discipline': (src.get('filter_discipline') or '').strip(),
        'filter_level': (src.get('filter_level') or '').strip(),
        'filter_assignments_q': (src.get('filter_assignments_q') or '').strip(),
    }


def _assignments_filter_is_active(filters: dict) -> bool:
    return any(filters.values())


_CATALOG_SORT_FIELDS = frozenset({'id', 'title', 'state', 'assignments'})


def _catalog_list_params_from_request(request) -> dict:
    params = {}
    q = request.GET.get('q')
    if q:
        params['q'] = q
    params.update({k: v for k, v in _assignment_filters_from_request(request).items() if v})
    for key in ('tag', 'category', 'discipline'):
        val = (request.GET.get(key) or '').strip()
        if val:
            params[key] = val
    sort = (request.GET.get('catalog_sort') or 'title').strip()
    direction = (request.GET.get('catalog_dir') or 'asc').strip()
    if sort in _CATALOG_SORT_FIELDS:
        params['catalog_sort'] = sort
    if direction in ('asc', 'desc'):
        params['catalog_dir'] = direction
    return params


def _catalog_sort_order(sort: str, direction: str) -> list[str]:
    desc = direction == 'desc'
    prefix = '-' if desc else ''

    def o(field: str) -> str:
        return f'{prefix}{field}'

    if sort == 'id':
        return [o('course_id'), o('course_title')]
    if sort == 'state':
        return [o('state_course_code'), o('course_title')]
    if sort == 'assignments':
        return [o('placement_count'), o('course_title')]
    return [o('course_title'), o('course_id')]


def _catalog_sort_link_query(request, field: str) -> str:
    params = _catalog_list_params_from_request(request)
    current_sort = params.get('catalog_sort', 'title')
    current_dir = params.get('catalog_dir', 'asc')
    params['catalog_sort'] = field
    if field == current_sort:
        params['catalog_dir'] = 'desc' if current_dir == 'asc' else 'asc'
    else:
        params['catalog_dir'] = 'asc'
    return urlencode(params)


def _placement_taxonomy_label(tag, category, discipline, level) -> str:
    parts = [tag.name]
    if category:
        parts.append(category.name)
    if discipline:
        parts.append(discipline.name)
    if level:
        parts.append(level.name)
    return ' → '.join(parts)


def _catalog_entry_placements_prefetch() -> Prefetch:
    return Prefetch(
        'placements',
        queryset=CatalogPlacement.objects.select_related(
            'tag', 'category', 'discipline', 'level',
        ).order_by('tag__order', 'category__order', 'discipline__order', 'level__order', 'id'),
    )


def _filter_placements_queryset(
    *,
    filter_tag='',
    filter_category='',
    filter_discipline='',
    filter_level='',
    course_q='',
):
    qs = CatalogPlacement.objects.select_related(
        'catalog_entry', 'level', 'tag', 'category', 'discipline',
    )
    if filter_tag:
        qs = qs.filter(tag_id=filter_tag)
    if filter_category:
        qs = qs.filter(category_id=filter_category)
    if filter_discipline:
        qs = qs.filter(discipline_id=filter_discipline)
    if filter_level:
        qs = qs.filter(level_id=filter_level)
    if course_q:
        qs = filter_catalog_entries(queryset=CourseCatalogEntry.objects.all(), q=course_q)
        qs = CatalogPlacement.objects.filter(catalog_entry__in=qs).select_related(
            'catalog_entry', 'level', 'tag', 'category', 'discipline',
        )
        if filter_tag:
            qs = qs.filter(tag_id=filter_tag)
        if filter_category:
            qs = qs.filter(category_id=filter_category)
        if filter_discipline:
            qs = qs.filter(discipline_id=filter_discipline)
        if filter_level:
            qs = qs.filter(level_id=filter_level)
    return qs.order_by('catalog_entry__course_title', 'id')


def _taxonomy_redirect(tag_id='', category_id=''):
    params = []
    if tag_id:
        params.append(f'tag={tag_id}')
    if category_id:
        params.append(f'category={category_id}')
    suffix = '?' + '&'.join(params) if params else ''
    return redirect(f'/coursecatalog/categories/{suffix}')


@login_required
@page_access_required('coursecatalog-overview')
def overview(request):
    context = {
        **_subnav_context('overview'),
        'tag_count': CatalogTag.objects.filter(is_active=True).count(),
        'category_count': CatalogCategory.objects.filter(is_active=True).count(),
        'placement_count': CatalogPlacement.objects.count(),
        'path_count': CatalogPath.objects.filter(is_active=True).count(),
        'catalog_count': CourseCatalogEntry.objects.count(),
    }
    return render(request, 'coursecatalog/overview.html', context)


@login_required
@page_access_required('coursecatalog-categories')
def categories(request):
    tags_qs = selected_tag_queryset()
    tag_form = CatalogTagForm()
    selected_tag_id = (request.GET.get('tag') or '').strip()
    selected_category_id = (request.GET.get('category') or '').strip()
    edit_discipline_id = request.GET.get('edit_discipline')
    selected_tag = None
    selected_category = None
    category_form = None
    category_edit_form = None
    discipline_form = None
    discipline_edit_form = None
    discipline_rows = []

    if selected_category_id:
        selected_category = get_object_or_404(CatalogCategory, pk=selected_category_id)
        selected_tag = selected_category.tag
        selected_tag_id = str(selected_tag.id)
        category_edit_form = CatalogCategoryEditForm(instance=selected_category)
        discipline_form = CatalogDisciplineForm()
        discipline_rows = list(
            CatalogDiscipline.objects.filter(category=selected_category)
            .annotate(course_count=Count('placements__catalog_entry', distinct=True))
            .order_by('order', 'name')
        )
        if edit_discipline_id:
            edit_obj = get_object_or_404(
                CatalogDiscipline,
                pk=edit_discipline_id,
                category=selected_category,
            )
            discipline_edit_form = CatalogDisciplineForm(instance=edit_obj)
    elif selected_tag_id:
        selected_tag = get_object_or_404(CatalogTag, pk=selected_tag_id)
        category_form = CatalogCategoryForm()

    context = {
        **_subnav_context('categories'),
        'tags': tags_qs,
        'selected_tag': selected_tag,
        'selected_category': selected_category,
        'tag_form': tag_form,
        'category_form': category_form,
        'category_edit_form': category_edit_form,
        'discipline_form': discipline_form,
        'discipline_edit_form': discipline_edit_form,
        'discipline_rows': discipline_rows,
        'edit_discipline_id': edit_discipline_id,
        'global_levels': CatalogLevel.objects.order_by('order', 'name'),
        'level_form': CatalogLevelForm(),
    }
    return render(request, 'coursecatalog/categories.html', context)


def _taxonomy_post(request):
    """Ensure optional taxonomy fields exist for ModelForm binds (legacy + partial UI)."""
    post = request.POST.copy()
    if not str(post.get('order', '')).strip():
        post['order'] = '0'
    post['is_active'] = 'on'
    return post


@login_required
@page_access_required('coursecatalog-categories')
@require_POST
def tag_create(request):
    form = CatalogTagForm(_taxonomy_post(request))
    if form.is_valid():
        tag = form.save()
        messages.success(request, f'Tag “{tag.name}” saved.')
        return redirect(f'/coursecatalog/categories/?tag={tag.id}')
    messages.error(request, form.errors.as_text())
    return redirect('coursecatalog:categories')


@login_required
@page_access_required('coursecatalog-categories')
@require_POST
def tag_update(request, tag_id):
    tag = get_object_or_404(CatalogTag, pk=tag_id)
    form = CatalogTagForm(_taxonomy_post(request), instance=tag)
    if form.is_valid():
        form.save()
        messages.success(request, 'Tag updated.')
    else:
        messages.error(request, form.errors.as_text())
    return _taxonomy_redirect(tag_id=tag_id)


@login_required
@page_access_required('coursecatalog-categories')
@require_POST
def tag_delete(request, tag_id):
    tag = get_object_or_404(CatalogTag, pk=tag_id)
    name = tag.name
    category_count = tag.categories.count()
    placement_count = tag.placements.count()
    path_count = tag.paths.count()
    with transaction.atomic():
        tag.delete()
    detail_parts = []
    if category_count:
        detail_parts.append(f'{category_count} categor{"y" if category_count == 1 else "ies"}')
    if placement_count:
        detail_parts.append(f'{placement_count} placement{"s" if placement_count != 1 else ""}')
    if path_count:
        detail_parts.append(f'{path_count} path{"s" if path_count != 1 else ""}')
    suffix = f' (also removed {" and ".join(detail_parts)})' if detail_parts else ''
    messages.success(request, f'Deleted tag "{name}"{suffix}.')
    return redirect('coursecatalog:categories')


@login_required
@page_access_required('coursecatalog-categories')
@require_POST
def category_create(request, tag_id):
    tag = get_object_or_404(CatalogTag, pk=tag_id)
    form = CatalogCategoryForm(_taxonomy_post(request))
    if form.is_valid():
        obj = form.save(commit=False)
        obj.tag = tag
        try:
            obj.save()
            messages.success(request, 'Category saved.')
        except Exception as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, form.errors.as_text())
    return _taxonomy_redirect(tag_id=tag_id)


@login_required
@page_access_required('coursecatalog-categories')
@require_POST
def category_update(request, category_id):
    category = get_object_or_404(CatalogCategory, pk=category_id)
    form = CatalogCategoryEditForm(_taxonomy_post(request), instance=category)
    if form.is_valid():
        form.save()
        messages.success(request, 'Category updated.')
    else:
        messages.error(request, form.errors.as_text())
    return _taxonomy_redirect(tag_id=category.tag_id, category_id=category_id)


@login_required
@page_access_required('coursecatalog-categories')
@require_POST
def category_delete(request, category_id):
    category = get_object_or_404(CatalogCategory, pk=category_id)
    tag_id = category.tag_id
    if category.placements.exists():
        messages.error(request, 'Remove all course placements in this category before deleting it.')
        return _taxonomy_redirect(tag_id=tag_id, category_id=category_id)
    name = category.name
    category.delete()
    messages.success(request, f'Deleted category "{name}".')
    return _taxonomy_redirect(tag_id=tag_id)


@login_required
@page_access_required('coursecatalog-categories')
@require_POST
def discipline_create(request, category_id):
    category = get_object_or_404(CatalogCategory, pk=category_id)
    form = CatalogDisciplineForm(request.POST)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.category = category
        try:
            obj.full_clean()
            obj.save()
            messages.success(request, 'Discipline added.')
        except Exception as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, form.errors.as_text())
    return _taxonomy_redirect(tag_id=category.tag_id, category_id=category_id)


@login_required
@page_access_required('coursecatalog-categories')
@require_POST
def disciplines_bulk_create(request, category_id):
    category = get_object_or_404(CatalogCategory, pk=category_id)
    raw = request.POST.get('discipline_names', '')
    created = 0
    for line in raw.splitlines():
        name = line.strip()
        if not name:
            continue
        _, was_new = CatalogDiscipline.objects.get_or_create(
            category=category,
            name=name,
            defaults={'order': 0, 'is_active': True},
        )
        if was_new:
            created += 1
    if created:
        messages.success(request, f'Added {created} discipline(s) to {category.name}.')
    else:
        messages.info(request, 'No new disciplines were added (empty or duplicate names).')
    return _taxonomy_redirect(tag_id=category.tag_id, category_id=category_id)


@login_required
@page_access_required('coursecatalog-categories')
@require_POST
def discipline_update(request, discipline_id):
    discipline = get_object_or_404(CatalogDiscipline, pk=discipline_id)
    form = CatalogDisciplineForm(request.POST, instance=discipline)
    category = discipline.category
    if form.is_valid():
        try:
            obj = form.save(commit=False)
            obj.full_clean()
            obj.save()
            messages.success(request, 'Discipline updated.')
        except Exception as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, form.errors.as_text())
    return _taxonomy_redirect(tag_id=category.tag_id, category_id=category.id)


@login_required
@page_access_required('coursecatalog-categories')
@require_POST
def discipline_delete(request, discipline_id):
    discipline = get_object_or_404(CatalogDiscipline, pk=discipline_id)
    category = discipline.category
    if discipline.placements.exists():
        messages.error(request, 'Remove course placements for this discipline before deleting it.')
        return _taxonomy_redirect(tag_id=category.tag_id, category_id=category.id)
    name = discipline.name
    discipline.delete()
    messages.success(request, f'Deleted discipline "{name}".')
    return _taxonomy_redirect(tag_id=category.tag_id, category_id=category.id)


@login_required
@page_access_required('coursecatalog-categories')
@require_POST
def level_create(request):
    form = CatalogLevelForm(request.POST)
    tag_id = request.POST.get('return_tag', '')
    category_id = request.POST.get('return_category', '')
    if form.is_valid():
        try:
            form.save()
            messages.success(request, 'Class level saved.')
        except Exception as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, form.errors.as_text())
    return _taxonomy_redirect(tag_id=tag_id, category_id=category_id)


@login_required
@page_access_required('coursecatalog-categories')
@require_POST
def level_update(request, level_id):
    level = get_object_or_404(CatalogLevel, pk=level_id)
    form = CatalogLevelForm(request.POST, instance=level)
    tag_id = request.POST.get('return_tag', '')
    category_id = request.POST.get('return_category', '')
    if form.is_valid():
        form.save()
        messages.success(request, 'Class level updated.')
    else:
        messages.error(request, form.errors.as_text())
    return _taxonomy_redirect(tag_id=tag_id, category_id=category_id)


@login_required
@page_access_required('coursecatalog-categories')
@require_POST
def level_delete(request, level_id):
    level = get_object_or_404(CatalogLevel, pk=level_id)
    tag_id = request.POST.get('return_tag', '')
    category_id = request.POST.get('return_category', '')
    if level.placements.exists():
        messages.error(request, 'Remove placements using this level before deleting it.')
        return _taxonomy_redirect(tag_id=tag_id, category_id=category_id)
    name = level.name
    level.delete()
    messages.success(request, f'Deleted level "{name}".')
    return _taxonomy_redirect(tag_id=tag_id, category_id=category_id)


@login_required
@page_access_required('coursecatalog-placements')
def placements(request):
    q = request.GET.get('q') or ''
    catalog_sort = (request.GET.get('catalog_sort') or 'title').strip()
    catalog_dir = (request.GET.get('catalog_dir') or 'asc').strip()
    if catalog_sort not in _CATALOG_SORT_FIELDS:
        catalog_sort = 'title'
    if catalog_dir not in ('asc', 'desc'):
        catalog_dir = 'asc'
    entries = list(
        filter_catalog_entries(q=q)
        .annotate(placement_count=Count('placements'))
        .prefetch_related(_catalog_entry_placements_prefetch())
        .order_by(*_catalog_sort_order(catalog_sort, catalog_dir))[:500]
    )
    course_choices = [(e.course_key, e.display_title) for e in entries]
    initial_tag_id = (request.GET.get('tag') or request.GET.get('filter_tag') or '').strip()
    initial_category_id = (request.GET.get('category') or request.GET.get('filter_category') or '').strip()
    initial_discipline_id = (request.GET.get('discipline') or request.GET.get('filter_discipline') or '').strip()
    assignment_filters = _assignment_filters_from_request(request)
    filter_tag = assignment_filters['filter_tag']
    filter_category = assignment_filters['filter_category']
    filter_discipline_id = assignment_filters['filter_discipline']
    filter_level = assignment_filters['filter_level']
    filter_assignments_q = assignment_filters['filter_assignments_q']
    assignments_filter_active = _assignments_filter_is_active(assignment_filters)
    filtered_placements = []
    if assignments_filter_active:
        filtered_placements = list(
            _filter_placements_queryset(
                filter_tag=filter_tag,
                filter_category=filter_category,
                filter_discipline=filter_discipline_id,
                filter_level=filter_level,
                course_q=filter_assignments_q,
            )[:500]
        )
    placement_choices = [(str(p.id), p.catalog_entry.display_title) for p in filtered_placements]
    all_tags = CatalogTag.objects.filter(is_active=True).order_by('order', 'name')
    all_categories = CatalogCategory.objects.filter(is_active=True).order_by('tag__order', 'order', 'name')
    all_disciplines = CatalogDiscipline.objects.filter(is_active=True).order_by(
        'category__tag__order', 'category__order', 'order', 'name',
    )
    all_levels = CatalogLevel.objects.filter(is_active=True).order_by('order', 'name')
    filter_discipline_obj = None
    discipline_placements = []
    if initial_discipline_id:
        filter_discipline_obj = (
            CatalogDiscipline.objects.filter(pk=initial_discipline_id)
            .select_related('category', 'category__tag')
            .first()
        )
        if filter_discipline_obj:
            initial_category_id = str(filter_discipline_obj.category_id)
            initial_tag_id = str(filter_discipline_obj.category.tag_id)
            discipline_placements = list(
                CatalogPlacement.objects.filter(discipline_id=filter_discipline_obj.pk)
                .select_related('catalog_entry', 'level', 'tag', 'category', 'discipline')
                .order_by('catalog_entry__course_title')[:200]
            )
    recent = (
        CatalogPlacement.objects.select_related('catalog_entry', 'level', 'tag', 'category', 'discipline')
        .order_by('-id')[:50]
    )
    search_terms = parse_catalog_search_terms(q)
    matched_terms = catalog_terms_matched(search_terms, entries) if search_terms else set()
    search_missing_terms = [t for t in search_terms if t not in matched_terms]
    context = {
        **_subnav_context('placements'),
        'entries': entries,
        'query': q,
        'search_multi_id': len(search_terms) > 1,
        'search_term_count': len(search_terms),
        'search_matched_count': len(matched_terms),
        'search_missing_terms': search_missing_terms,
        'course_choices': course_choices,
        'taxonomy_json': build_taxonomy_json(),
        'initial_tag_id': initial_tag_id,
        'initial_category_id': initial_category_id,
        'initial_discipline_id': initial_discipline_id,
        'filter_discipline': filter_discipline_obj,
        'discipline_placements': discipline_placements,
        'recent_placements': recent,
        'filter_tag': filter_tag,
        'filter_category': filter_category,
        'filter_discipline_id': filter_discipline_id,
        'filter_level': filter_level,
        'filter_assignments_q': filter_assignments_q,
        'assignments_filter_active': assignments_filter_active,
        'filtered_placements': filtered_placements,
        'placement_choices': placement_choices,
        'all_tags': all_tags,
        'all_categories': all_categories,
        'all_disciplines': all_disciplines,
        'all_levels': all_levels,
        'catalog_sort': catalog_sort,
        'catalog_dir': catalog_dir,
        'catalog_sort_qs_id': _catalog_sort_link_query(request, 'id'),
        'catalog_sort_qs_title': _catalog_sort_link_query(request, 'title'),
        'catalog_sort_qs_state': _catalog_sort_link_query(request, 'state'),
        'catalog_sort_qs_assignments': _catalog_sort_link_query(request, 'assignments'),
    }
    return render(request, 'coursecatalog/placements.html', context)


@login_required
@page_access_required('coursecatalog-placements')
@require_POST
def placement_bulk_create(request):
    q = request.POST.get('q') or ''
    entries = filter_catalog_entries(q=q).order_by('course_title', 'course_key')[:500]
    course_choices = [(e.course_key, e.display_title) for e in entries]
    form = BulkCatalogPlacementForm(request.POST, course_choices=course_choices)
    if form.is_valid():
        tag = form.cleaned_data['tag']
        category = form.cleaned_data.get('category')
        discipline = form.cleaned_data.get('discipline')
        level = form.cleaned_data.get('level')
        keys = form.cleaned_data['course_keys']
        created = 0
        replaced = 0
        unchanged = 0
        with transaction.atomic():
            for key in keys:
                outcome = upsert_placement_for_tag(
                    catalog_entry_id=key,
                    tag=tag,
                    category=category,
                    discipline=discipline,
                    level=level,
                )
                if outcome == 'created':
                    created += 1
                elif outcome == 'replaced':
                    replaced += 1
                else:
                    unchanged += 1
        label = _placement_taxonomy_label(tag, category, discipline, level)
        parts = []
        if created:
            parts.append(f'{created} new')
        if replaced:
            parts.append(f'{replaced} updated (same tag)')
        if unchanged:
            parts.append(f'{unchanged} unchanged')
        summary = ', '.join(parts) if parts else '0 courses'
        messages.success(request, f'Assigned {summary} to {label}.')
    else:
        messages.error(request, form.errors.as_text())
    return redirect(_placements_url(q=q))


@login_required
@page_access_required('coursecatalog-placements')
@require_POST
def placement_bulk_update(request):
    assignment_filters = _assignment_filters_from_request(request, use_post=True)
    q = request.POST.get('q') or ''
    filtered = _filter_placements_queryset(
        filter_tag=assignment_filters['filter_tag'],
        filter_category=assignment_filters['filter_category'],
        filter_discipline=assignment_filters['filter_discipline'],
        filter_level=assignment_filters['filter_level'],
        course_q=assignment_filters['filter_assignments_q'],
    )
    allowed_ids = {str(p.id) for p in filtered[:500]}
    action = (request.POST.get('action') or 'update').strip()

    if action == 'delete':
        selected = [pid for pid in request.POST.getlist('placement_ids') if pid in allowed_ids]
        if not selected:
            messages.error(request, 'Select at least one assignment to remove.')
        else:
            deleted, _ = CatalogPlacement.objects.filter(id__in=selected).delete()
            messages.success(request, f'Removed {deleted} assignment(s).')
        return redirect(_placements_url(q=q, **assignment_filters))

    placement_choices = [(str(p.id), p.catalog_entry.display_title) for p in filtered[:500]]
    form = BulkCatalogPlacementUpdateForm(request.POST, placement_choices=placement_choices)
    if form.is_valid():
        tag = form.cleaned_data['tag']
        category = form.cleaned_data.get('category')
        discipline = form.cleaned_data.get('discipline')
        level = form.cleaned_data.get('level')
        ids = [int(x) for x in form.cleaned_data['placement_ids']]
        updated = 0
        skipped = 0
        label = _placement_taxonomy_label(tag, category, discipline, level)
        with transaction.atomic():
            for placement in CatalogPlacement.objects.filter(id__in=ids):
                placement.tag = tag
                placement.category = category
                placement.discipline = discipline
                placement.level = level
                placement.full_clean()
                try:
                    placement.save()
                    updated += 1
                except IntegrityError:
                    skipped += 1
        messages.success(
            request,
            f'Updated {updated} assignment(s) to {label}.'
            + (f' {skipped} could not be updated (duplicate path).' if skipped else ''),
        )
    else:
        messages.error(request, form.errors.as_text())
    return redirect(_placements_url(q=q, **assignment_filters))


@login_required
@page_access_required('coursecatalog-placements')
@require_POST
def placement_delete(request, pk):
    CatalogPlacement.objects.filter(pk=pk).delete()
    messages.success(request, 'Placement removed.')
    assignment_filters = _assignment_filters_from_request(request, use_post=True)
    return redirect(
        _placements_url(
            q=request.POST.get('q', ''),
            tag=request.POST.get('tag', ''),
            category=request.POST.get('category', ''),
            discipline=request.POST.get('discipline', ''),
            **assignment_filters,
        )
    )


@login_required
@page_access_required('coursecatalog-paths')
def paths(request):
    paths_qs = CatalogPath.objects.select_related('tag', 'category', 'discipline').annotate(
        step_count=Count('steps'),
    ).order_by('tag__order', 'category__order', 'name')
    path_form = CatalogPathForm()
    selected_id = request.GET.get('path')
    selected = None
    step_form = None
    steps = []
    if selected_id:
        selected = get_object_or_404(CatalogPath, pk=selected_id)
        step_form = CatalogPathStepForm()
        steps = selected.steps.select_related('catalog_entry').order_by('sequence')
    context = {
        **_subnav_context('paths'),
        'paths': paths_qs,
        'selected_path': selected,
        'path_form': path_form,
        'step_form': step_form,
        'steps': steps,
    }
    return render(request, 'coursecatalog/paths.html', context)


@login_required
@page_access_required('coursecatalog-paths')
@require_POST
def path_create(request):
    form = CatalogPathForm(request.POST)
    if form.is_valid():
        obj = form.save(commit=False)
        try:
            obj.full_clean()
            obj.save()
            messages.success(request, 'Path created.')
        except Exception as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, form.errors.as_text())
    return redirect('coursecatalog:paths')


@login_required
@page_access_required('coursecatalog-paths')
@require_POST
def path_step_create(request, path_id):
    path = get_object_or_404(CatalogPath, pk=path_id)
    form = CatalogPathStepForm(request.POST)
    if form.is_valid():
        step = form.save(commit=False)
        step.path = path
        try:
            step.full_clean()
            step.save()
            messages.success(request, 'Path step added.')
        except Exception as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, form.errors.as_text())
    return redirect(f'/coursecatalog/paths/?path={path_id}')


@login_required
@page_access_required('coursecatalog-enrollment')
def enrollment_preview(request):
    tags = CatalogTag.objects.filter(is_active=True).order_by('order', 'name')
    tag_id = request.GET.get('tag')
    level_id = request.GET.get('level')
    school_year = request.GET.get('school_year', '')
    ssids = []
    placement_ids = []
    if level_id:
        placement_ids = list(
            CatalogPlacement.objects.filter(level_id=level_id).values_list('id', flat=True)
        )
        if placement_ids and school_year:
            ssids = student_ssids_for_placements(placement_ids, school_year)
    elif tag_id:
        tag = get_object_or_404(CatalogTag, pk=tag_id)
        placement_ids = list(
            CatalogPlacement.objects.filter(tag=tag).values_list('id', flat=True)
        )
        if placement_ids and school_year:
            ssids = student_ssids_for_placements(placement_ids, school_year)
    levels = CatalogLevel.objects.filter(is_active=True).order_by('order', 'name')
    context = {
        **_subnav_context('enrollment'),
        'tags': tags,
        'levels': levels,
        'selected_tag_id': tag_id or '',
        'selected_level_id': level_id or '',
        'school_year': school_year,
        'ssids': ssids[:500],
        'ssid_total': len(ssids),
        'placement_count': len(placement_ids),
    }
    return render(request, 'coursecatalog/enrollment.html', context)
