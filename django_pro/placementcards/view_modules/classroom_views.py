from __future__ import annotations

from collections import Counter

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.core.cache import cache
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from dataexploration.models import ElementaryHomeroomTeacher
from placementcards.models import NextYearClassroom, PlacementCard, PlacementTag, QuestionTemplate, StudentPlacement
from placementcards.view_modules.card_data_utils import (
    can_user_access_classroom_grade,
    can_user_configure_placements,
    can_user_place_students,
    can_user_manage_tags,
    get_request_school,
    tags_usage_for_school,
)
from studata.models import CurrentStudent, Result, StudentEnroll, Test


_GRADE_ORDER = ['TK', 'KN', '01', '02', '03', '04', '05']
_GRADES_LIST = [
    ('TK', 'TK'),
    ('KN', 'Kindergarten'),
    ('01', '1st Grade'),
    ('02', '2nd Grade'),
    ('03', '3rd Grade'),
    ('04', '4th Grade'),
    ('05', '5th Grade'),
]


def _classroom_list_context(school, school_year: str | None):
    classrooms = NextYearClassroom.objects.filter(school=school)
    if school_year:
        classrooms = classrooms.filter(school_year=school_year)
    classrooms = classrooms.annotate(assignment_count=Count('placements')).order_by('school_year', 'grade', 'classroom_name')
    return {'school': school, 'classrooms': classrooms, 'school_year': school_year}


def _render_classroom_list(request: HttpRequest, school, school_year: str | None):
    context = _classroom_list_context(school, school_year)
    return render(request, 'placementcards/partials/classroom_list.html', context)


@login_required
def manage_classrooms(request: HttpRequest):
    school = get_request_school(request)
    if not can_user_place_students(request.user, school):
        return HttpResponse('Forbidden', status=403)

    school_year = request.GET.get('school_year')
    context = _classroom_list_context(school, school_year)
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'placementcards/partials/classroom_list.html', context)
    return render(request, 'placementcards/manage_classrooms.html', context)


@login_required
def placement_configuration(request: HttpRequest):
    school = get_request_school(request)
    if not can_user_configure_placements(request.user, school):
        return HttpResponse('Forbidden', status=403)

    school_year = request.GET.get('school_year')
    classrooms = NextYearClassroom.objects.filter(school=school)
    if school_year:
        classrooms = classrooms.filter(school_year=school_year)
    classrooms = classrooms.annotate(assignment_count=Count('placements')).order_by('school_year', 'grade', 'classroom_name')

    tags = PlacementTag.objects.filter(school=school, is_active=True).order_by('display_order', 'name')
    questions = (
        QuestionTemplate.objects.filter(school=school, is_active=True)
        .only('id', 'question_category', 'question_text', 'question_type', 'field_options', 'requires_detail', 'display_order', 'created_by')
        .order_by('question_category', 'display_order', 'id')
    )
    hidden_questions = (
        QuestionTemplate.objects.filter(school=school, is_active=False)
        .only('id', 'question_category', 'question_text', 'question_type', 'display_order', 'created_by')
        .order_by('question_category', 'display_order', 'id')
    )
    can_manage_tags = can_user_manage_tags(request.user, school)

    context = {
        'school': school,
        'school_year': school_year,
        'classrooms': classrooms,
        'tags': tags,
        'tag_usage': tags_usage_for_school(school.pk),
        'questions': questions,
        'hidden_questions': hidden_questions,
        'mode': 'create',
        'question': None,
        'options_value': '',
        'can_manage_tags': can_manage_tags,
    }
    return render(request, 'placementcards/placement_configuration.html', context)


@login_required
def create_classroom_modal(request: HttpRequest):
    school = get_request_school(request)
    if not can_user_configure_placements(request.user, school):
        return HttpResponse('Forbidden', status=403)

    if request.method == 'GET':
        # Pass context for form, e.g., next school year calculation
        current_year = CurrentStudent.objects.filter(school_state_id=school.state_id).order_by('-school_year').values_list('school_year', flat=True).first()
        # Simple logic to guess next year, e.g., 2024-2025 -> 2025-2026
        next_year = ""
        if current_year:
            try:
                start, end = map(int, current_year.split('-'))
                next_year = f"{start+1}-{end+1}"
            except ValueError:
                pass
        
        return render(request, 'placementcards/partials/classroom_form.html', {
            'mode': 'create',
            'school_year': next_year,
            'selected_grades': [],
            'grades_list': _GRADES_LIST,
        })

    school_year = request.POST.get('school_year', '').strip()
    if not school_year:
        return HttpResponse('School year is required', status=400)

    raw_grades = [g.strip() for g in request.POST.getlist('grades') if g.strip()]
    grade_value = ','.join(g for g in _GRADE_ORDER if g in raw_grades)
    if not grade_value:
        return HttpResponse('At least one grade is required', status=400)

    NextYearClassroom.objects.create(
        school=school,
        school_year=school_year,
        grade=grade_value,
        classroom_name=request.POST.get('classroom_name', '').strip(),
        assigned_teacher_name=request.POST.get('assigned_teacher_name', '').strip(),
        max_capacity=int(request.POST.get('max_capacity')) if request.POST.get('max_capacity', '').isdigit() else None,
        created_by=request.user,
    )
    return _render_classroom_list(request, school, school_year)


@login_required
@transaction.atomic
def edit_classroom_modal(request: HttpRequest, classroom_id: int):
    school = get_request_school(request)
    if not can_user_configure_placements(request.user, school):
        return HttpResponse('Forbidden', status=403)

    classroom = get_object_or_404(NextYearClassroom, id=classroom_id, school=school)

    if request.method == 'GET':
        selected_grades = [g.strip() for g in classroom.grade.split(',') if g.strip()]
        return render(
            request,
            'placementcards/partials/classroom_form.html',
            {
                'mode': 'edit',
                'classroom': classroom,
                'school_year': classroom.school_year,
                'selected_grades': selected_grades,
                'grades_list': _GRADES_LIST,
            },
        )

    school_year = request.POST.get('school_year', '').strip()
    if not school_year:
        return HttpResponse('School year is required', status=400)

    raw_grades = [g.strip() for g in request.POST.getlist('grades') if g.strip()]
    grade_value = ','.join(g for g in _GRADE_ORDER if g in raw_grades)
    if not grade_value:
        return HttpResponse('At least one grade is required', status=400)

    classroom.school_year = school_year
    classroom.grade = grade_value
    classroom.classroom_name = request.POST.get('classroom_name', '').strip()
    classroom.assigned_teacher_name = request.POST.get('assigned_teacher_name', '').strip()
    classroom.max_capacity = int(request.POST.get('max_capacity')) if request.POST.get('max_capacity', '').isdigit() else None
    classroom.save(update_fields=['school_year', 'grade', 'classroom_name', 'assigned_teacher_name', 'max_capacity', 'updated_at'])

    return _render_classroom_list(request, school, school_year)


@login_required
@transaction.atomic
def delete_classroom(request: HttpRequest, classroom_id: int):
    school = get_request_school(request)
    if not can_user_configure_placements(request.user, school):
        return HttpResponse('Forbidden', status=403)

    classroom = get_object_or_404(NextYearClassroom, id=classroom_id, school=school)
    school_year = request.POST.get('school_year', '').strip() or classroom.school_year
    classroom.delete()
    return _render_classroom_list(request, school, school_year)


@login_required
def classroom_statistics(request: HttpRequest, classroom_id: int):
    classroom = get_object_or_404(NextYearClassroom, id=classroom_id)
    if not can_user_place_students(request.user, classroom.school):
        return HttpResponse('Forbidden', status=403)
    if not can_user_access_classroom_grade(request.user, classroom.grade):
        return HttpResponse('Forbidden', status=403)

    teacher_note_questions = list(
        QuestionTemplate.objects.filter(
            school=classroom.school,
            is_active=True,
            question_type__in=['checkbox', 'choice', 'multiselect', 'student_list'],
        )
        .only('id', 'question_text', 'question_type')
        .order_by('question_category', 'display_order', 'id')
    )
    teacher_note_metric_keys = {f'note_q_{question.id}' for question in teacher_note_questions}

    selected_metrics = [
        metric
        for metric in request.GET.getlist('metrics')
        if metric in {
            'gender', 'race', 'ell', 'sped', 'sed', 'foster', 'homeless',
            'gate', 'in_504', 'dli', 'ltel', 'status', 'homeroom_teacher',
            'assessment_pl', 'tags'
        } or metric in teacher_note_metric_keys
    ]
    if not selected_metrics:
        selected_metrics = ['ell', 'race', 'homeroom_teacher']
    student_year = request.GET.get('student_year', '').strip()

    version = cache.get(f'classroom_stats_version:{classroom_id}', 0)
    key = f'classroom_stats:{classroom_id}:{version}:{student_year}:{"-".join(selected_metrics)}'
    cached = cache.get(key)
    if cached is not None:
        return render(request, 'placementcards/partials/classroom_stats.html', cached)

    placements = StudentPlacement.objects.filter(classroom=classroom).only('student_ssid')
    ssids = list({item.student_ssid for item in placements})

    def _norm(value: str | None) -> str:
        cleaned = (value or '').strip().upper()
        return cleaned or 'UNKNOWN'

    if student_year:
        current_rows = CurrentStudent.objects.filter(ssid__in=ssids, school_year=student_year).only(
            'ssid', 'gender', 'race', 'ell', 'sped', 'sed', 'foster', 'homeless',
            'gate', 'in_504', 'dli', 'ltel', 'status'
        )
        enroll_rows = StudentEnroll.objects.filter(ssid__in=ssids, school_year=student_year).only('ssid', 'ell_status')
    else:
        current_rows = CurrentStudent.objects.filter(ssid__in=ssids).order_by('ssid', '-school_year').distinct('ssid').only(
            'ssid', 'gender', 'race', 'ell', 'sped', 'sed', 'foster', 'homeless',
            'gate', 'in_504', 'dli', 'ltel', 'status'
        )
        enroll_rows = StudentEnroll.objects.filter(ssid__in=ssids).order_by('ssid', '-school_year', '-enter_date').distinct('ssid').only('ssid', 'ell_status')

    current_map = {row.ssid: row for row in current_rows}
    enroll_map = {row.ssid: row for row in enroll_rows}

    gender_counter = Counter(_norm(getattr(current_map.get(ssid), 'gender', None)) for ssid in ssids)
    race_counter = Counter(_norm(getattr(current_map.get(ssid), 'race', None)) for ssid in ssids)
    sed_counter = Counter(_norm(getattr(current_map.get(ssid), 'sed', None)) for ssid in ssids)
    foster_counter = Counter(_norm(getattr(current_map.get(ssid), 'foster', None)) for ssid in ssids)
    homeless_counter = Counter(_norm(getattr(current_map.get(ssid), 'homeless', None)) for ssid in ssids)
    gate_counter = Counter(_norm(getattr(current_map.get(ssid), 'gate', None)) for ssid in ssids)
    in_504_counter = Counter(_norm(getattr(current_map.get(ssid), 'in_504', None)) for ssid in ssids)
    dli_counter = Counter(_norm(getattr(current_map.get(ssid), 'dli', None)) for ssid in ssids)
    ltel_counter = Counter(_norm(getattr(current_map.get(ssid), 'ltel', None)) for ssid in ssids)
    status_counter = Counter(_norm(getattr(current_map.get(ssid), 'status', None)) for ssid in ssids)

    ell_counter = Counter()
    sped_counter = Counter()
    for ssid in ssids:
        current_row = current_map.get(ssid)
        enroll_row = enroll_map.get(ssid)
        ell_value = getattr(current_row, 'ell', None) or getattr(enroll_row, 'ell_status', None)
        sped_value = getattr(current_row, 'sped', None)
        ell_counter[_norm(ell_value)] += 1
        sped_counter[_norm(sped_value)] += 1

    hr_counter = Counter()
    if student_year:
        hr_rows = ElementaryHomeroomTeacher.objects.filter(ssid__in=ssids, school_year=student_year).values('ssid', 'elem_teacher_name')
    else:
        hr_rows = ElementaryHomeroomTeacher.objects.filter(ssid__in=ssids).values('ssid', 'elem_teacher_name')
    for row in hr_rows:
        hr_counter[_norm(row.get('elem_teacher_name'))] += 1

    assessment_pl_counter = Counter()
    result_year = student_year or classroom.school_year
    result_rows = list(Result.objects.filter(ssid__in=ssids, school_year=result_year).values('test_group_key', 'pl'))
    assessment_group_keys = {row['test_group_key'] for row in result_rows if row.get('test_group_key')}
    tests_by_group = {test.test_group_key: test for test in Test.objects.filter(test_group_key__in=assessment_group_keys)}

    assessment_breakdown_map: dict[str, dict] = {}
    for result_row in result_rows:
        pl_value = _norm(result_row.get('pl'))
        assessment_pl_counter[_norm(result_row.get('pl'))] += 1

        group_key = result_row.get('test_group_key') or ''
        test = tests_by_group.get(group_key)
        breakdown_key = f"{getattr(test, 'test_name', 'Assessment')}::{getattr(test, 'subject', '')}::{getattr(test, 'test_window', '')}::{getattr(test, 'school_year', result_year)}"
        if breakdown_key not in assessment_breakdown_map:
            assessment_breakdown_map[breakdown_key] = {
                'test_name': getattr(test, 'test_name', 'Assessment'),
                'subject': getattr(test, 'subject', ''),
                'test_window': getattr(test, 'test_window', ''),
                'school_year': getattr(test, 'school_year', result_year),
                'pl_counts': Counter(),
            }
        assessment_breakdown_map[breakdown_key]['pl_counts'][pl_value] += 1

    assessment_breakdown = []
    for item in sorted(assessment_breakdown_map.values(), key=lambda row: (row['test_name'], row['subject'], row['test_window'], row['school_year'])):
        assessment_breakdown.append(
            {
                'test_name': item['test_name'],
                'subject': item['subject'],
                'test_window': item['test_window'],
                'school_year': item['school_year'],
                'pl_counts': dict(item['pl_counts']),
            }
        )

    tag_counter = Counter()
    card_school_year = student_year or classroom.school_year
    card_rows = list(PlacementCard.objects.filter(ssid__in=ssids, school_year=card_school_year).prefetch_related('tags'))
    for card in card_rows:
        for tag in card.tags.all():
            tag_counter[tag.name] += 1

    responses_by_ssid = {
        card.ssid: card.responses if isinstance(card.responses, dict) else {}
        for card in card_rows
    }
    teacher_note_metrics = []
    for question in teacher_note_questions:
        metric_key = f'note_q_{question.id}'
        if metric_key not in selected_metrics:
            continue

        counts = Counter()
        for ssid in ssids:
            response_value = responses_by_ssid.get(ssid, {}).get(str(question.id))

            if question.question_type == 'checkbox':
                counts['YES' if bool(response_value) else 'NO'] += 1
                continue

            if question.question_type == 'choice':
                counts[_norm(str(response_value) if response_value is not None else None)] += 1
                continue

            if question.question_type == 'multiselect':
                if isinstance(response_value, list):
                    normalized_items = [str(item).strip() for item in response_value if str(item).strip()]
                    if normalized_items:
                        for item in normalized_items:
                            counts[_norm(item)] += 1
                    else:
                        counts['NONE'] += 1
                else:
                    counts['NONE'] += 1
                continue

            if question.question_type == 'student_list':
                has_values = isinstance(response_value, list) and any(str(item).strip() for item in response_value)
                counts['HAS_SELECTED_STUDENTS' if has_values else 'NO_SELECTED_STUDENTS'] += 1

        teacher_note_metrics.append(
            {
                'metric_key': metric_key,
                'question_text': question.question_text,
                'counts': dict(counts),
            }
        )

    context = {
        'classroom': classroom,
        'total_students': len(ssids),
        'gender_counts': dict(gender_counter),
        'race_counts': dict(race_counter),
        'ell_counts': dict(ell_counter),
        'sped_counts': dict(sped_counter),
        'sed_counts': dict(sed_counter),
        'foster_counts': dict(foster_counter),
        'homeless_counts': dict(homeless_counter),
        'gate_counts': dict(gate_counter),
        'in_504_counts': dict(in_504_counter),
        'dli_counts': dict(dli_counter),
        'ltel_counts': dict(ltel_counter),
        'status_counts': dict(status_counter),
        'homeroom_teacher_counts': dict(hr_counter),
        'assessment_pl_counts': dict(assessment_pl_counter),
        'assessment_breakdown': assessment_breakdown,
        'tag_counts': dict(tag_counter),
        'teacher_note_metrics': teacher_note_metrics,
        'selected_metrics': selected_metrics,
    }
    cache.set(key, context, timeout=600)
    return render(request, 'placementcards/partials/classroom_stats.html', context)
