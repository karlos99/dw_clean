from __future__ import annotations

from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from dataexploration.view_modules.student_data_utils import (
    get_student_info_cached,
    get_student_summary_partial_html_cached,
)
from placementcards.models import PlacementCard, PlacementTag, QuestionTemplate
from placementcards.view_modules.card_data_utils import (
    can_user_access_placement_cards,
    can_user_place_students,
    get_accessible_students,
    get_request_school,
    invalidate_school_caches,
    placement_card_map,
)
from studata.models import Schedule
from users.decorators import page_access_required


DEFAULT_QUESTIONS = [
    ('academic', 'Reading Standard', 'choice', {'choices': ['Above', 'At', 'Below', 'Well Below']}, False),
    ('academic', 'Math Standard', 'choice', {'choices': ['Above', 'At', 'Below', 'Well Below']}, False),
    ('behavior', 'Study/Work Habits', 'textarea', {}, False),
    ('behavior', 'Classroom Behavior', 'textarea', {}, False),
    ('physical', 'Adaptive PE', 'checkbox', {}, False),
    ('physical', 'Physical Handicap', 'checkbox', {}, False),
    ('physical', 'Wears Glasses', 'checkbox', {}, False),
    ('physical', 'Allergies', 'checkbox', {}, True),
    ('physical', 'Medication', 'checkbox', {}, False),
    ('physical', 'Counseling', 'checkbox', {}, True),
    ('attendance', 'Playground Habits', 'textarea', {}, False),
    ('attendance', 'Tardiness', 'textarea', {}, False),
    ('attendance', 'SARB', 'textarea', {}, False),
    ('parent', 'Parent Teacher Conference', 'checkbox', {}, False),
    ('parent', 'Classroom Involvement', 'checkbox', {}, False),
    ('parent', 'Field Trips', 'checkbox', {}, False),
    ('additional', 'Should Not Be In Same Class With', 'student_list', {}, False),
    ('additional', 'Other Information', 'textarea', {}, False),
    ('additional', 'Additional Comments', 'textarea', {}, False),
]


def _is_school_admin(user) -> bool:
    if user.is_superuser:
        return True
    role_names: set[str] = set()
    if hasattr(user, 'active_role') and user.active_role:
        role_names.add(user.active_role.name)
    if hasattr(user, 'roles'):
        role_names.update(user.roles.values_list('name', flat=True))
    return 'School Admin' in role_names


def _status_badge_span(ssid: str, is_complete: bool) -> str:
    if is_complete:
        return (
            f'<span id="student-status-{ssid}" class="text-green-700" hx-swap-oob="outerHTML">'
            'Complete'
            '</span>'
        )
    return (
        f'<span id="student-status-{ssid}" class="text-amber-700" hx-swap-oob="outerHTML">'
        'Pending'
        '</span>'
    )


def _panel_badge_span(is_complete: bool) -> str:
    if is_complete:
        return (
            '<span id="card-status-badge" class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-800" hx-swap-oob="outerHTML">'
            'Complete'
            '</span>'
        )
    return (
        '<span id="card-status-badge" class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-amber-100 text-amber-800" hx-swap-oob="outerHTML">'
        'Pending'
        '</span>'
    )


def _status_oob_updates(ssid: str, is_complete: bool) -> str:
    return f"{_panel_badge_span(is_complete)}{_status_badge_span(ssid, is_complete)}"


def _feedback_oob_span(message: str, tone: str = 'success') -> str:
    classes = 'inline-flex items-center px-2 py-1 rounded text-xs font-medium '
    if tone == 'success':
        classes += 'bg-green-100 text-green-800'
    elif tone == 'info':
        classes += 'bg-blue-100 text-blue-800'
    else:
        classes += 'bg-slate-100 text-slate-800'
    return f'<div id="save-feedback" hx-swap-oob="innerHTML"><span class="{classes}">{message}</span></div>'


def _get_school_tags(school_id: int):
    key = f'placement_tags:{school_id}'
    return cache.get_or_set(
        key,
        lambda: list(PlacementTag.objects.filter(school_id=school_id, is_active=True).order_by('display_order', 'name')),
        timeout=3600,
    )


def _ensure_default_questions(school_id: int, user):
    if QuestionTemplate.objects.filter(school_id=school_id).exists():
        return
    items = []
    for idx, row in enumerate(DEFAULT_QUESTIONS):
        category, text, qtype, options, requires_detail = row
        items.append(
            QuestionTemplate(
                school_id=school_id,
                question_category=category,
                question_text=text,
                question_type=qtype,
                field_options=options,
                display_order=idx,
                created_by=user,
                requires_detail=requires_detail,
            )
        )
    QuestionTemplate.objects.bulk_create(items)


def _get_questions(school_id: int):
    key = f'placement_questions:{school_id}'
    return cache.get_or_set(
        key,
        lambda: list(
            QuestionTemplate.objects.filter(school_id=school_id, is_active=True)
            .only('id', 'question_category', 'question_text', 'question_type', 'field_options', 'requires_detail')
            .order_by('question_category', 'display_order', 'id')
        ),
        timeout=3600,
    )


@login_required
@page_access_required('placement-cards-teacher-notes')
def teacher_notes_page(request: HttpRequest):
    school = get_request_school(request)
    if not can_user_access_placement_cards(request.user, school):
        return HttpResponse('Forbidden', status=403)

    is_school_admin = _is_school_admin(request.user)
    can_place = can_user_place_students(request.user, school)
    students_base = get_accessible_students(request)
    school_year = request.GET.get('school_year') or students_base.values_list('school_year', flat=True).order_by('-school_year').first()
    students = get_accessible_students(request, school_year=school_year)

    selected_homeroom_teachers = [value.strip() for value in request.GET.getlist('homeroom_teacher') if value.strip()]
    homeroom_teachers: list[str] = []
    if can_place and school_year:
        homeroom_teachers = list(
            Schedule.objects.filter(
                ssid__in=students.values('ssid'),
                school_year=school_year,
                period__in=['0', '00'],
            )
            .exclude(staff_name__isnull=True)
            .exclude(staff_name='')
            .values_list('staff_name', flat=True)
            .distinct()
            .order_by('staff_name')
        )
        if selected_homeroom_teachers:
            students = students.filter(
                ssid__in=Schedule.objects.filter(
                    school_year=school_year,
                    period__in=['0', '00'],
                    staff_name__in=selected_homeroom_teachers,
                ).values('ssid')
            ).distinct()

    selected_tag_ids = [int(tag_id) for tag_id in request.GET.getlist('tag_ids') if tag_id.isdigit()]
    if selected_tag_ids:
        students = students.filter(ssid__in=PlacementCard.objects.filter(school_year=school_year, tags__id__in=selected_tag_ids).values('ssid')).distinct()

    student_list = list(students)
    card_by_ssid = placement_card_map([student.ssid for student in student_list], school_year or '')
    for student in student_list:
        student.placement_card = card_by_ssid.get(student.ssid)

    tags = _get_school_tags(school.pk) if school else []
    context = {
        'students': student_list,
        'school_year': school_year,
        'school': school,
        'tags': tags,
        'selected_tag_ids': selected_tag_ids,
        'is_school_admin': is_school_admin,
        'can_place_students': can_place,
        'homeroom_teachers': homeroom_teachers,
        'selected_homeroom_teachers': selected_homeroom_teachers,
    }

    if request.headers.get('HX-Request') == 'true':
        return render(request, 'placementcards/partials/student_table.html', context)
    return render(request, 'placementcards/teacher_notes.html', context)


@login_required
def edit_placement_card_modal(request: HttpRequest, ssid: str):
    school = get_request_school(request)
    if not can_user_access_placement_cards(request.user, school):
        return HttpResponse('Forbidden', status=403)

    students = get_accessible_students(request).filter(ssid=ssid)
    student = students.order_by('-school_year').first()
    if not student:
        raise Http404('Student not found')

    school_year = request.GET.get('school_year') or student.school_year
    if school:
        _ensure_default_questions(school.pk, request.user)
    questions = _get_questions(school.pk) if school else []
    tags = _get_school_tags(school.pk) if school else []

    card = PlacementCard.objects.filter(ssid=ssid, school_year=school_year).prefetch_related('tags').first()
    selected_tag_ids = set(card.tags.values_list('id', flat=True)) if card else set()
    grouped_questions = defaultdict(list)
    for question in questions:
        grouped_questions[question.question_category].append(question)

    context = {
        'student': student,
        'school_year': school_year,
        'grouped_questions': dict(grouped_questions),
        'card': card,
        'tags': tags,
        'selected_tag_ids': selected_tag_ids,
        'student_summary_html': get_student_summary_partial_html_cached(request, ssid, embedded=True),
    }
    return render(request, 'placementcards/partials/placement_card_form.html', context)


@login_required
@transaction.atomic
def save_placement_card(request: HttpRequest):
    ssid = request.POST.get('ssid', '').strip()
    school_year = request.POST.get('school_year', '').strip()
    student = get_accessible_students(request, school_year=school_year).filter(ssid=ssid).first()
    if not student:
        return HttpResponse('Student unavailable', status=403)

    school = get_request_school(request)
    if not school:
        return HttpResponse('No active school context', status=400)
    if not can_user_access_placement_cards(request.user, school):
        return HttpResponse('Forbidden', status=403)

    questions = _get_questions(school.pk)
    responses = {}
    for question in questions:
        key = f'q_{question.id}'
        if question.question_type == 'checkbox':
            responses[str(question.id)] = key in request.POST
            detail_key = f'{key}_detail'
            if question.requires_detail:
                responses[f'{question.id}_detail'] = request.POST.get(detail_key, '').strip()
        elif question.question_type == 'multiselect':
            responses[str(question.id)] = request.POST.getlist(key)
        else:
            responses[str(question.id)] = request.POST.get(key, '').strip()

    card, _ = PlacementCard.objects.get_or_create(
        stu_annual_key=student.stu_annual_key,
        school_year=school_year,
        defaults={'ssid': ssid},
    )
    mark_complete = request.POST.get('is_complete') == 'on'
    card.ssid = ssid
    card.responses = responses
    card.completed_by = request.user if mark_complete else None
    card.is_complete = mark_complete
    card.save(update_fields=['ssid', 'responses', 'completed_by', 'is_complete', 'last_updated'])

    tag_ids = [int(tag_id) for tag_id in request.POST.getlist('tag_ids') if tag_id.isdigit()]
    valid_tags = PlacementTag.objects.filter(id__in=tag_ids, school_id=school.pk, is_active=True)
    card.tags.set(valid_tags)

    invalidate_school_caches(school.pk)
    status_text = 'Notes saved. Status: Complete.' if mark_complete else 'Notes saved. Status: Pending.'
    updates = _status_oob_updates(ssid, mark_complete)
    feedback = _feedback_oob_span(status_text, tone='success')
    return HttpResponse(f'{updates}{feedback}')


@login_required
@transaction.atomic
def update_card_status(request: HttpRequest):
    ssid = request.POST.get('ssid', '').strip()
    school_year = request.POST.get('school_year', '').strip()
    student = get_accessible_students(request, school_year=school_year).filter(ssid=ssid).first()
    if not student:
        return HttpResponse('Student unavailable', status=403)

    is_complete = request.POST.get('is_complete') == 'on'
    card, _ = PlacementCard.objects.get_or_create(
        stu_annual_key=student.stu_annual_key,
        school_year=school_year,
        defaults={'ssid': ssid},
    )
    card.ssid = ssid
    card.is_complete = is_complete
    card.completed_by = request.user if is_complete else None
    card.save(update_fields=['ssid', 'is_complete', 'completed_by', 'last_updated'])

    school = get_request_school(request)
    if not can_user_access_placement_cards(request.user, school):
        return HttpResponse('Forbidden', status=403)
    if school:
        invalidate_school_caches(school.pk)

    status_text = 'Status updated to Complete.' if is_complete else 'Status updated to Pending.'
    updates = _status_oob_updates(ssid, is_complete)
    feedback = _feedback_oob_span(status_text, tone='info')
    return HttpResponse(f'{updates}{feedback}')


@login_required
@transaction.atomic
def quick_tag_student(request: HttpRequest):
    ssid = request.POST.get('ssid', '').strip()
    tag_id = request.POST.get('tag_id', '').strip()
    action = request.POST.get('action', 'add').strip()
    school_year = request.POST.get('school_year', '').strip()

    if not tag_id.isdigit():
        return HttpResponse('Invalid tag', status=400)

    student = get_accessible_students(request, school_year=school_year).filter(ssid=ssid).first()
    if not student:
        return HttpResponse('Student unavailable', status=403)

    school = get_request_school(request)
    if not school:
        return HttpResponse('No active school context', status=400)
    if not can_user_access_placement_cards(request.user, school):
        return HttpResponse('Forbidden', status=403)

    tag = get_object_or_404(PlacementTag, id=int(tag_id), school_id=school.pk, is_active=True)
    card, _ = PlacementCard.objects.get_or_create(
        stu_annual_key=student.stu_annual_key,
        school_year=school_year,
        defaults={'ssid': ssid},
    )
    if action == 'remove':
        card.tags.remove(tag)
    else:
        card.tags.add(tag)

    invalidate_school_caches(school.pk)
    return HttpResponse('ok')
