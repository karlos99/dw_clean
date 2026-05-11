from __future__ import annotations

import csv

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import Trim, Upper
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from dataexploration.models import ElementaryHomeroomTeacher
from dataexploration.view_modules.student_data_utils import get_student_summary_partial_html_cached
from placementcards.models import NextYearClassroom, PlacementCard, PlacementTag, QuestionTemplate, StudentPlacement
from placementcards.view_modules.card_data_utils import (
    can_user_access_classroom_grade,
    can_user_place_students,
    can_user_unassign_from_classroom_grade,
    get_accessible_students,
    get_request_school,
    placement_card_map,
)
from studata.models import CurrentStudent, Result, StudentEnroll, Test
from users.decorators import page_access_required


_TEACHER_NOTE_SUPPORTED_TYPES = {'checkbox', 'choice', 'multiselect', 'student_list'}


def _bump_classroom_stats_version(classroom_id: int):
    version_key = f'classroom_stats_version:{classroom_id}'
    current_version = cache.get(version_key, 0)
    try:
        next_version = int(current_version) + 1
    except (TypeError, ValueError):
        next_version = 1
    cache.set(version_key, next_version, timeout=86400)


def _apply_tag_filter(students, school_year: str, tag_ids: list[int]):
    if not tag_ids:
        return students
    base = PlacementCard.objects.filter(school_year=school_year, tags__id__in=tag_ids)
    return students.filter(ssid__in=base.values('ssid')).distinct()


def _getlist(data, key: str) -> list[str]:
    if hasattr(data, 'getlist'):
        return data.getlist(key)
    value = data.get(key)
    return [value] if value else []


def _clean_values(queryset, field: str) -> list[str]:
    return list(
        queryset.exclude(**{f'{field}__isnull': True})
        .exclude(**{field: ''})
        .annotate(clean_val=Upper(Trim(field)))
        .values_list('clean_val', flat=True)
        .distinct()
        .order_by('clean_val')
    )


def _teacher_note_questions(school) -> list[dict]:
    if not school:
        return []

    questions = (
        QuestionTemplate.objects.filter(school=school, is_active=True, question_type__in=_TEACHER_NOTE_SUPPORTED_TYPES)
        .only('id', 'question_text', 'question_type', 'field_options', 'display_order', 'question_category')
        .order_by('question_category', 'display_order', 'id')
    )

    teacher_notes: list[dict] = []
    for question in questions:
        choices = []
        if question.question_type == 'checkbox':
            choices = [
                {'value': 'true', 'label': 'Yes'},
                {'value': 'false', 'label': 'No'},
            ]
        elif question.question_type in {'choice', 'multiselect'}:
            field_options = question.field_options if isinstance(question.field_options, dict) else {}
            option_values = field_options.get('choices', []) if isinstance(field_options.get('choices', []), list) else []
            choices = [{'value': str(option), 'label': str(option)} for option in option_values if str(option).strip()]
        elif question.question_type == 'student_list':
            choices = [
                {'value': '__HAS_VALUES__', 'label': 'Has Selected Students'},
                {'value': '__NO_VALUES__', 'label': 'No Selected Students'},
            ]

        teacher_notes.append(
            {
                'id': str(question.id),
                'question_text': question.question_text,
                'question_type': question.question_type,
                'choices': choices,
                'metric_key': f'note_q_{question.id}',
            }
        )

    return teacher_notes


def _matches_teacher_note_filter(question_type: str, value, selected_values: list[str]) -> bool:
    selected_set = {str(item).strip() for item in selected_values if str(item).strip()}
    if not selected_set:
        return True

    if question_type == 'checkbox':
        value_str = 'true' if bool(value) else 'false'
        return value_str in selected_set

    if question_type == 'choice':
        normalized = str(value).strip()
        return normalized in selected_set

    if question_type == 'multiselect':
        if not isinstance(value, list):
            return False
        normalized_values = {str(item).strip() for item in value if str(item).strip()}
        return bool(normalized_values & selected_set)

    if question_type == 'student_list':
        has_values = isinstance(value, list) and len([item for item in value if str(item).strip()]) > 0
        if '__HAS_VALUES__' in selected_set and has_values:
            return True
        if '__NO_VALUES__' in selected_set and not has_values:
            return True
        return False

    return True


def _conflict_question_ids_for_school(school) -> list[str]:
    if not school:
        return []
    question_ids = QuestionTemplate.objects.filter(
        school=school,
        is_active=True,
        question_type='student_list',
        question_text__iexact='Should Not Be In Same Class With',
    ).values_list('id', flat=True)
    return [str(question_id) for question_id in question_ids]


def _student_conflicts_for_classroom(
    *,
    school,
    classroom,
    student_year: str,
    newly_assigned_ssids: list[str],
) -> list[dict[str, str]]:
    if not school or not classroom or not student_year:
        return []

    question_ids = _conflict_question_ids_for_school(school)
    if not question_ids:
        return []

    classroom_student_ssids = list(
        StudentPlacement.objects.filter(
            classroom=classroom,
            school_year=classroom.school_year,
        ).values_list('student_ssid', flat=True)
    )
    if not classroom_student_ssids:
        return []

    cards = PlacementCard.objects.filter(
        ssid__in=classroom_student_ssids,
        school_year=student_year,
    ).only('ssid', 'responses')

    blocked_by_student: dict[str, set[str]] = {}
    for card in cards:
        responses = card.responses if isinstance(card.responses, dict) else {}
        blocked_ssids: set[str] = set()
        for question_id in question_ids:
            value = responses.get(question_id)
            if isinstance(value, list):
                blocked_ssids.update(str(item).strip() for item in value if str(item).strip())
        if blocked_ssids:
            blocked_by_student[card.ssid] = blocked_ssids

    if not blocked_by_student:
        return []

    student_rows = CurrentStudent.objects.filter(
        ssid__in=classroom_student_ssids,
        school_year=student_year,
    ).only('ssid', 'student_name', 'grade')
    student_info = {
        row.ssid: {
            'name': row.student_name or row.ssid,
            'grade': str(row.grade or ''),
        }
        for row in student_rows
    }

    newly_assigned_set = set(newly_assigned_ssids)
    seen_pairs: set[tuple[str, str]] = set()
    conflicts: list[dict[str, str]] = []

    for left_ssid in classroom_student_ssids:
        left_blocked = blocked_by_student.get(left_ssid, set())
        for right_ssid in classroom_student_ssids:
            if left_ssid == right_ssid:
                continue
            if right_ssid not in left_blocked and left_ssid not in blocked_by_student.get(right_ssid, set()):
                continue
            if left_ssid not in newly_assigned_set and right_ssid not in newly_assigned_set:
                continue

            pair = tuple(sorted((left_ssid, right_ssid)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            left_meta = student_info.get(left_ssid, {'name': left_ssid, 'grade': ''})
            right_meta = student_info.get(right_ssid, {'name': right_ssid, 'grade': ''})
            conflicts.append(
                {
                    'student_a': left_meta['name'],
                    'student_a_grade': left_meta['grade'],
                    'student_b': right_meta['name'],
                    'student_b_grade': right_meta['grade'],
                    'classroom_name': classroom.classroom_name,
                    'classroom_grade': str(classroom.grade or ''),
                }
            )

    return conflicts


def _build_placement_context(request: HttpRequest, school, data):
    students_base = get_accessible_students(request, for_placement=True)
    student_year = data.get('student_year') or students_base.values_list('school_year', flat=True).order_by('-school_year').first()
    latest_classroom_year = (
        NextYearClassroom.objects.filter(school=school).values_list('school_year', flat=True).order_by('-school_year').first()
        if school
        else None
    )
    placement_year = data.get('placement_year') or latest_classroom_year or student_year

    selected_tag_ids = [int(tag_id) for tag_id in _getlist(data, 'tag_ids') if str(tag_id).isdigit()]

    enabled_filters = set(_getlist(data, 'enabled_filters'))
    if not enabled_filters:
        enabled_filters = {'ell', 'race', 'homeroom_teacher'}

    selected_metrics = [
        metric
        for metric in _getlist(data, 'metrics')
        if metric in {
            'gender', 'race', 'ell', 'sped', 'sed', 'foster', 'homeless',
            'gate', 'in_504', 'dli', 'ltel', 'status', 'homeroom_teacher',
            'assessment_pl', 'tags'
        }
    ]
    if not selected_metrics:
        selected_metrics = ['ell', 'race', 'homeroom_teacher']

    grade_filter = data.get('grade', '').strip().upper()
    gender_filter = data.get('gender', '').strip().upper()
    race_filter = data.get('race', '').strip().upper()
    ell_filter = data.get('ell', '').strip().upper()
    sped_filter = data.get('sped', '').strip().upper()
    sed_filter = data.get('sed', '').strip().upper()
    foster_filter = data.get('foster', '').strip().upper()
    homeless_filter = data.get('homeless', '').strip().upper()
    gate_filter = data.get('gate', '').strip().upper()
    in_504_filter = data.get('in_504', '').strip().upper()
    dli_filter = data.get('dli', '').strip().upper()
    ltel_filter = data.get('ltel', '').strip().upper()
    status_filter = data.get('status', '').strip().upper()
    search = data.get('search', '').strip()
    homeroom_teacher_filter = data.get('homeroom_teacher', '').strip()
    selected_assessment_keys = [value.strip() for value in _getlist(data, 'assessment_keys') if '::' in value]

    base_queryset = get_accessible_students(request, school_year=student_year, for_placement=True)
    base_ssids = list(base_queryset.values_list('ssid', flat=True))

    hr_queryset = ElementaryHomeroomTeacher.objects.filter(school_year=student_year)
    if base_ssids:
        hr_queryset = hr_queryset.filter(ssid__in=base_ssids)

    grade_options = _clean_values(base_queryset, 'grade')
    gender_options = _clean_values(base_queryset, 'gender')
    race_options = _clean_values(base_queryset, 'race')
    sped_options = _clean_values(base_queryset, 'sped')
    sed_options = _clean_values(base_queryset, 'sed')
    foster_options = _clean_values(base_queryset, 'foster')
    homeless_options = _clean_values(base_queryset, 'homeless')
    gate_options = _clean_values(base_queryset, 'gate')
    in_504_options = _clean_values(base_queryset, 'in_504')
    dli_options = _clean_values(base_queryset, 'dli')
    ltel_options = _clean_values(base_queryset, 'ltel')
    status_options = _clean_values(base_queryset, 'status')

    ell_from_current = _clean_values(base_queryset, 'ell')
    ell_from_enroll = _clean_values(StudentEnroll.objects.filter(school_year=student_year, ssid__in=base_ssids), 'ell_status')
    ell_options = sorted({value for value in [*ell_from_current, *ell_from_enroll] if value})

    homeroom_teacher_options = list(
        hr_queryset.exclude(elem_teacher_name__isnull=True)
        .exclude(elem_teacher_name='')
        .values_list('elem_teacher_name', flat=True)
        .distinct()
        .order_by('elem_teacher_name')
    )

    assessment_result_qs = Result.objects.filter(ssid__in=base_ssids, school_year=student_year).exclude(pl__isnull=True).exclude(pl='')
    assessment_group_keys = list(assessment_result_qs.values_list('test_group_key', flat=True).distinct())
    tests_by_group = {
        test.test_group_key: test
        for test in Test.objects.filter(test_group_key__in=assessment_group_keys)
    }
    pl_by_group: dict[str, set[str]] = {}
    for row in assessment_result_qs.annotate(pl_clean=Upper(Trim('pl'))).values('test_group_key', 'pl_clean').distinct():
        group_key = row['test_group_key']
        pl_by_group.setdefault(group_key, set()).add(row['pl_clean'])

    assessment_groups = []
    for group_key in sorted(pl_by_group.keys(), key=lambda key: (
        getattr(tests_by_group.get(key), 'test_name', ''),
        getattr(tests_by_group.get(key), 'subject', ''),
        getattr(tests_by_group.get(key), 'test_window', ''),
        getattr(tests_by_group.get(key), 'school_year', ''),
    )):
        test = tests_by_group.get(group_key)
        assessment_groups.append(
            {
                'test_group_key': group_key,
                'test_name': getattr(test, 'test_name', 'Assessment'),
                'subject': getattr(test, 'subject', ''),
                'test_window': getattr(test, 'test_window', ''),
                'school_year': getattr(test, 'school_year', student_year or ''),
                'pl_options': sorted({value for value in pl_by_group.get(group_key, set()) if value}),
            }
        )

    assessment_label_map = {
        group['test_group_key']: f"{group['test_name']} · {group['subject']} · {group['test_window'] or 'N/A'} · {group['school_year']}"
        for group in assessment_groups
    }

    teacher_note_questions = _teacher_note_questions(school)
    teacher_note_metric_keys = {question['metric_key'] for question in teacher_note_questions}
    selected_metrics += [metric for metric in _getlist(data, 'metrics') if metric in teacher_note_metric_keys and metric not in selected_metrics]

    selected_teacher_note_filters: dict[str, list[str]] = {}
    for question in teacher_note_questions:
        key = f"note_filter_{question['id']}"
        selected_values = [value.strip() for value in _getlist(data, key) if value.strip()]
        if selected_values:
            selected_teacher_note_filters[question['id']] = selected_values

    queryset = _apply_tag_filter(base_queryset, student_year or '', selected_tag_ids)

    normalized_field_filters = [
        ('grade', grade_filter, 'grade'),
        ('gender', gender_filter, 'gender'),
        ('race', race_filter, 'race'),
        ('sped', sped_filter, 'sped'),
        ('sed', sed_filter, 'sed'),
        ('foster', foster_filter, 'foster'),
        ('homeless', homeless_filter, 'homeless'),
        ('gate', gate_filter, 'gate'),
        ('in_504', in_504_filter, 'in_504'),
        ('dli', dli_filter, 'dli'),
        ('ltel', ltel_filter, 'ltel'),
        ('status', status_filter, 'status'),
    ]
    for filter_key, filter_value, field_name in normalized_field_filters:
        if filter_value and filter_key in enabled_filters:
            queryset = queryset.annotate(**{f'{field_name}_clean': Upper(Trim(field_name))}).filter(**{f'{field_name}_clean': filter_value})

    if ell_filter and 'ell' in enabled_filters:
        enroll_ssids_for_ell = StudentEnroll.objects.filter(school_year=student_year).annotate(
            ell_clean=Upper(Trim('ell_status'))
        ).filter(ell_clean=ell_filter).values('ssid')
        queryset = queryset.annotate(ell_clean=Upper(Trim('ell'))).filter(
            Q(ell_clean=ell_filter) | Q(ssid__in=enroll_ssids_for_ell)
        )

    if search and 'search' in enabled_filters:
        queryset = queryset.filter(Q(student_name__icontains=search) | Q(ssid__icontains=search))

    if homeroom_teacher_filter and 'homeroom_teacher' in enabled_filters:
        queryset = queryset.filter(ssid__in=hr_queryset.filter(elem_teacher_name=homeroom_teacher_filter).values('ssid'))

    if selected_assessment_keys and 'assessment_pl' in enabled_filters:
        selection_filter = Q()
        for selection in selected_assessment_keys:
            group_key, pl_value = selection.split('::', 1)
            selection_filter |= Q(test_group_key=group_key, pl_clean=pl_value)
        pl_results = Result.objects.filter(ssid__in=base_ssids, school_year=student_year).annotate(
            pl_clean=Upper(Trim('pl'))
        ).filter(selection_filter)
        queryset = queryset.filter(ssid__in=pl_results.values('ssid'))

    if selected_teacher_note_filters:
        candidate_ssids = list(queryset.values_list('ssid', flat=True))
        cards = PlacementCard.objects.filter(ssid__in=candidate_ssids, school_year=student_year or '').only('ssid', 'responses')
        responses_by_ssid = {
            card.ssid: card.responses if isinstance(card.responses, dict) else {}
            for card in cards
        }
        question_type_by_id = {question['id']: question['question_type'] for question in teacher_note_questions}

        allowed_ssids = []
        for ssid in candidate_ssids:
            responses = responses_by_ssid.get(ssid, {})
            keep_student = True
            for question_id, selected_values in selected_teacher_note_filters.items():
                question_type = question_type_by_id.get(question_id)
                response_value = responses.get(question_id)
                if not _matches_teacher_note_filter(question_type or '', response_value, selected_values):
                    keep_student = False
                    break
            if keep_student:
                allowed_ssids.append(ssid)

        queryset = queryset.filter(ssid__in=allowed_ssids)

    students = list(queryset.distinct())
    student_ssids = [student.ssid for student in students]

    card_by_ssid = placement_card_map(student_ssids, student_year or '')
    student_hr_map = {
        row['ssid']: row['elem_teacher_name']
        for row in hr_queryset.values('ssid', 'elem_teacher_name')
    }

    classrooms = list(
        NextYearClassroom.objects.filter(school=school, school_year=placement_year)
        .annotate(assignment_count=Count('placements'))
        .order_by('grade', 'classroom_name')
    )
    classrooms = [classroom for classroom in classrooms if can_user_access_classroom_grade(request.user, classroom.grade)]

    placement_rows = list(
        StudentPlacement.objects.filter(classroom__in=classrooms, school_year=placement_year)
        .select_related('classroom')
        .only('student_ssid', 'classroom_id', 'school_year')
    )
    assigned_ssids = {placement.student_ssid for placement in placement_rows}

    student_lookup = {
        item.ssid: item
        for item in CurrentStudent.objects.filter(ssid__in=assigned_ssids, school_year=student_year).only('ssid', 'student_name', 'grade')
    }

    classroom_assignments: dict[int, list[dict]] = {classroom.id: [] for classroom in classrooms}
    for placement in placement_rows:
        student_info = student_lookup.get(placement.student_ssid)
        classroom_assignments[placement.classroom_id].append(
            {
                'ssid': placement.student_ssid,
                'student_name': student_info.student_name if student_info else placement.student_ssid,
                'grade': student_info.grade if student_info else '',
                'current_hr_teacher': student_hr_map.get(placement.student_ssid, ''),
            }
        )

    unassigned_students = [student for student in students if student.ssid not in assigned_ssids]
    for student in unassigned_students:
        student.placement_card = card_by_ssid.get(student.ssid)
        student.current_hr_teacher = student_hr_map.get(student.ssid, '')

    tags = PlacementTag.objects.filter(school=school, is_active=True).order_by('display_order', 'name')

    applied_filters = []
    if selected_tag_ids and 'tags' in enabled_filters:
        tag_lookup = {tag.id: tag.name for tag in tags}
        tag_names = [tag_lookup[tag_id] for tag_id in selected_tag_ids if tag_id in tag_lookup]
        if tag_names:
            applied_filters.append(f"Tags: {', '.join(tag_names)}")

    for label, filter_value, filter_key in [
        ('Grade', grade_filter, 'grade'),
        ('Gender', gender_filter, 'gender'),
        ('Race', race_filter, 'race'),
        ('ELL', ell_filter, 'ell'),
        ('SPED', sped_filter, 'sped'),
        ('SED', sed_filter, 'sed'),
        ('Foster', foster_filter, 'foster'),
        ('Homeless', homeless_filter, 'homeless'),
        ('GATE', gate_filter, 'gate'),
        ('504', in_504_filter, 'in_504'),
        ('DLI', dli_filter, 'dli'),
        ('LTEL', ltel_filter, 'ltel'),
        ('Status', status_filter, 'status'),
    ]:
        if filter_value and filter_key in enabled_filters:
            applied_filters.append(f"{label}: {filter_value}")

    if search and 'search' in enabled_filters:
        applied_filters.append(f"Search: {search}")
    if homeroom_teacher_filter and 'homeroom_teacher' in enabled_filters:
        applied_filters.append(f"HR Teacher: {homeroom_teacher_filter}")
    if selected_assessment_keys and 'assessment_pl' in enabled_filters:
        selected_labels = []
        for selection in selected_assessment_keys:
            group_key, pl_value = selection.split('::', 1)
            selected_labels.append(f"{assessment_label_map.get(group_key, group_key)} → PL {pl_value}")
        applied_filters.append(f"Assessment PL: {', '.join(selected_labels)}")

    if selected_teacher_note_filters:
        teacher_note_lookup = {question['id']: question for question in teacher_note_questions}
        for question_id, selected_values in selected_teacher_note_filters.items():
            question = teacher_note_lookup.get(question_id)
            if not question:
                continue
            label_lookup = {choice['value']: choice['label'] for choice in question['choices']}
            selected_labels = [label_lookup.get(value, value) for value in selected_values]
            applied_filters.append(f"{question['question_text']}: {', '.join(selected_labels)}")

    selected_teacher_note_filters_flat = []
    for question_id, values in selected_teacher_note_filters.items():
        for value in values:
            selected_teacher_note_filters_flat.append({'question_id': question_id, 'value': value})

    return {
        'school': school,
        'student_year': student_year,
        'placement_year': placement_year,
        'students': unassigned_students,
        'classrooms': classrooms,
        'classroom_assignments': classroom_assignments,
        'tags': tags,
        'selected_tag_ids': selected_tag_ids,
        'enabled_filters': sorted(enabled_filters),
        'selected_metrics': selected_metrics,
        'applied_filters': applied_filters,
        'grade_filter': grade_filter,
        'gender_filter': gender_filter,
        'race_filter': race_filter,
        'ell_filter': ell_filter,
        'sped_filter': sped_filter,
        'sed_filter': sed_filter,
        'foster_filter': foster_filter,
        'homeless_filter': homeless_filter,
        'gate_filter': gate_filter,
        'in_504_filter': in_504_filter,
        'dli_filter': dli_filter,
        'ltel_filter': ltel_filter,
        'status_filter': status_filter,
        'search': search,
        'homeroom_teacher_filter': homeroom_teacher_filter,
        'selected_assessment_keys': selected_assessment_keys,
        'grade_options': grade_options,
        'gender_options': gender_options,
        'race_options': race_options,
        'ell_options': ell_options,
        'sped_options': sped_options,
        'sed_options': sed_options,
        'foster_options': foster_options,
        'homeless_options': homeless_options,
        'gate_options': gate_options,
        'in_504_options': in_504_options,
        'dli_options': dli_options,
        'ltel_options': ltel_options,
        'status_options': status_options,
        'homeroom_teacher_options': homeroom_teacher_options,
        'assessment_groups': assessment_groups,
        'placement_conflicts': [],
        'teacher_note_questions': teacher_note_questions,
        'selected_teacher_note_filters': selected_teacher_note_filters,
        'selected_teacher_note_filters_flat': selected_teacher_note_filters_flat,
    }


@login_required
@page_access_required('placement-cards-place-students')
def placement_page(request: HttpRequest):
    school = get_request_school(request)
    if not can_user_place_students(request.user, school):
        return HttpResponse('Forbidden', status=403)

    context = _build_placement_context(request, school, request.GET)
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'placementcards/partials/placement_workspace.html', context)
    return render(request, 'placementcards/place_students.html', context)


@login_required
def export_classrooms_csv(request: HttpRequest):
    school = get_request_school(request)
    if not can_user_place_students(request.user, school):
        return HttpResponse('Forbidden', status=403)

    students_base = get_accessible_students(request, for_placement=True)
    student_year = request.GET.get('student_year') or students_base.values_list('school_year', flat=True).order_by('-school_year').first()
    latest_classroom_year = (
        NextYearClassroom.objects.filter(school=school).values_list('school_year', flat=True).order_by('-school_year').first()
        if school
        else None
    )
    placement_year = request.GET.get('placement_year') or latest_classroom_year or student_year

    selected_classroom_ids = [int(value) for value in request.GET.getlist('classroom_ids') if value.isdigit()]
    scope = request.GET.get('scope', 'all').strip().lower()

    classrooms_qs = NextYearClassroom.objects.filter(school=school, school_year=placement_year).order_by('grade', 'classroom_name')
    if selected_classroom_ids:
        classrooms_qs = classrooms_qs.filter(id__in=selected_classroom_ids)
    elif scope == 'selected':
        return HttpResponse('No classrooms selected for export.', status=400)

    classrooms = [classroom for classroom in classrooms_qs if can_user_access_classroom_grade(request.user, classroom.grade)]
    if not classrooms:
        return HttpResponse('No classrooms found for export.', status=404)

    accessible_ssids = get_accessible_students(request, school_year=student_year, for_placement=True).values('ssid')
    placement_rows = list(
        StudentPlacement.objects.filter(
            classroom__in=classrooms,
            school_year=placement_year,
            student_ssid__in=accessible_ssids,
        ).only('student_ssid', 'classroom_id')
    )

    ssids = sorted({row.student_ssid for row in placement_rows})
    students_by_ssid = {
        row.ssid: row
        for row in CurrentStudent.objects.filter(ssid__in=ssids, school_year=student_year).only('ssid', 'student_id', 'grade')
    }

    hr_rows = (
        ElementaryHomeroomTeacher.objects.filter(ssid__in=ssids, school_year=student_year)
        .values('ssid', 'elem_teacher_name')
        .order_by('ssid', 'elem_teacher_name')
    )
    hr_by_ssid: dict[str, str] = {}
    for row in hr_rows:
        ssid = row.get('ssid')
        if ssid and ssid not in hr_by_ssid:
            hr_by_ssid[ssid] = row.get('elem_teacher_name') or ''

    rows_by_classroom: dict[int, list[dict[str, str]]] = {classroom.id: [] for classroom in classrooms}
    for placement in placement_rows:
        student_row = students_by_ssid.get(placement.student_ssid)
        rows_by_classroom[placement.classroom_id].append(
            {
                'ssid': placement.student_ssid,
                'student_id': str(getattr(student_row, 'student_id', '') or ''),
                'grade': str(getattr(student_row, 'grade', '') or ''),
                'current_teacher_name': hr_by_ssid.get(placement.student_ssid, ''),
            }
        )

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="classrooms_{placement_year or "current"}.csv"'
    writer = csv.writer(response)

    for index, classroom in enumerate(classrooms):
        if classroom.classroom_name:
            writer.writerow(['Classroom Name', classroom.classroom_name])
        if classroom.grade:
            writer.writerow(['Grade', classroom.grade])
        if classroom.assigned_teacher_name:
            writer.writerow(['Teacher', classroom.assigned_teacher_name])

        writer.writerow(['SSID', 'Student ID', 'Grade', 'Current Teacher Name'])

        classroom_rows = sorted(
            rows_by_classroom.get(classroom.id, []),
            key=lambda row: (row.get('grade', ''), row.get('student_id', ''), row.get('ssid', '')),
        )
        for row in classroom_rows:
            writer.writerow([row['ssid'], row['student_id'], row['grade'], row['current_teacher_name']])

        if index < len(classrooms) - 1:
            writer.writerow([])

    return response


@login_required
@transaction.atomic
def assign_student_to_classroom(request: HttpRequest):
    classroom_id = request.POST.get('classroom_id', '').strip()
    single_student_ssid = request.POST.get('student_ssid', '').strip()
    student_ssids = [ssid.strip() for ssid in _getlist(request.POST, 'student_ssids') if ssid.strip()]
    if single_student_ssid:
        student_ssids.append(single_student_ssid)
    student_ssids = list(dict.fromkeys(student_ssids))

    notes = request.POST.get('notes', '').strip()
    if not classroom_id.isdigit() or not student_ssids:
        return HttpResponse('Invalid request', status=400)

    classroom = get_object_or_404(NextYearClassroom, id=int(classroom_id))
    if not can_user_place_students(request.user, classroom.school):
        return HttpResponse('Forbidden', status=403)
    if not can_user_access_classroom_grade(request.user, classroom.grade):
        return HttpResponse('Forbidden', status=403)

    student_year = request.POST.get('student_year', '').strip() or classroom.school_year

    accessible_ssids = set(
        get_accessible_students(request, school_year=student_year, for_placement=True)
        .filter(ssid__in=student_ssids)
        .values_list('ssid', flat=True)
    )
    if len(accessible_ssids) != len(student_ssids):
        return HttpResponse('One or more students are outside your delegated scope', status=403)

    for student_ssid in student_ssids:
        StudentPlacement.objects.update_or_create(
            student_ssid=student_ssid,
            school_year=classroom.school_year,
            defaults={
                'classroom': classroom,
                'placed_by': request.user,
                'notes': notes,
            },
        )

    placement_conflicts = _student_conflicts_for_classroom(
        school=classroom.school,
        classroom=classroom,
        student_year=student_year,
        newly_assigned_ssids=student_ssids,
    )

    _bump_classroom_stats_version(classroom.id)
    context = _build_placement_context(request, classroom.school, request.POST)
    context['placement_conflicts'] = placement_conflicts
    return render(request, 'placementcards/partials/placement_workspace.html', context)


@login_required
@transaction.atomic
def unassign_student_from_classroom(request: HttpRequest):
    student_ssid = request.POST.get('student_ssid', '').strip()
    placement_year = request.POST.get('placement_year', '').strip()
    student_year = request.POST.get('student_year', '').strip()
    if not student_ssid or not placement_year:
        return HttpResponse('Invalid request', status=400)

    placement = get_object_or_404(StudentPlacement, student_ssid=student_ssid, school_year=placement_year)
    if not can_user_place_students(request.user, placement.classroom.school):
        return HttpResponse('Forbidden', status=403)
    if not can_user_unassign_from_classroom_grade(request.user, placement.classroom.grade):
        return HttpResponse('Forbidden', status=403)

    scope_year = student_year or placement_year
    can_touch_student = get_accessible_students(request, school_year=scope_year, for_placement=True).filter(ssid=student_ssid).exists()
    if not can_touch_student:
        return HttpResponse('Student unavailable', status=403)

    classroom_id = placement.classroom_id
    placement.delete()
    _bump_classroom_stats_version(classroom_id)

    context = _build_placement_context(request, placement.classroom.school, request.POST)
    return render(request, 'placementcards/partials/placement_workspace.html', context)


@login_required
def filter_students_by_tags(request: HttpRequest):
    return placement_page(request)


@login_required
def view_student_card_overlay(request: HttpRequest, ssid: str):
    school = get_request_school(request)
    if not can_user_place_students(request.user, school):
        return HttpResponse('Forbidden', status=403)

    student_year = request.GET.get('student_year')
    student_qs = get_accessible_students(request, school_year=student_year, for_placement=True) if student_year else get_accessible_students(request, for_placement=True)
    student = student_qs.filter(ssid=ssid).order_by('-school_year').first()
    if not student:
        return HttpResponse('Student not found', status=404)

    resolved_year = student_year or student.school_year
    card = PlacementCard.objects.filter(ssid=ssid, school_year=resolved_year).prefetch_related('tags').first()

    question_lookup = {
        str(item.id): item.question_text
        for item in QuestionTemplate.objects.filter(school=school, is_active=True)
    } if school else {}

    responses = []
    if card and isinstance(card.responses, dict):
        for key, value in card.responses.items():
            if str(key).endswith('_detail'):
                continue
            label = question_lookup.get(str(key), f'Question {key}')
            detail = card.responses.get(f'{key}_detail', '')
            if isinstance(value, bool):
                value_text = 'Yes' if value else 'No'
            elif isinstance(value, list):
                value_text = ', '.join(str(v) for v in value) if value else '—'
            else:
                value_text = str(value).strip() if str(value).strip() else '—'
            if detail:
                value_text = f"{value_text} ({detail})"
            responses.append({'label': label, 'value': value_text})

    hr_teacher = (
        ElementaryHomeroomTeacher.objects.filter(ssid=ssid, school_year=resolved_year)
        .values_list('elem_teacher_name', flat=True)
        .first()
    )

    context = {
        'student': student,
        'student_year': resolved_year,
        'card': card,
        'responses': responses,
        'current_hr_teacher': hr_teacher,
        'student_summary_html': get_student_summary_partial_html_cached(request, ssid, embedded=True),
    }
    return render(request, 'placementcards/partials/student_card_readonly_overlay.html', context)
