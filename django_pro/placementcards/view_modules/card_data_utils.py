from __future__ import annotations

from typing import Iterable

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import HttpRequest

from dataexploration.models import ElementaryHomeroomTeacher
from placementcards.models import PlacementCard
from studata.models import CurrentStudent, School, Schedule


ELEMENTARY_GRADES = {'TK', 'KN', 'KG', '01', '1', '02', '2', '03', '3', '04', '4', '05', '5', '250'}
DISALLOWED_PLACEMENT_GRADES = {'05', '5', '06', '6', '07', '7', '08', '8', '09', '9', '10', '11', '12', 'US'}


def _is_elementary_school_group(value: str | None) -> bool:
    return (value or '').strip().lower() == 'elementary school'


def _normalize_grade_token(value: str | None) -> str:
    token = (value or '').strip().upper()
    if not token:
        return ''
    if token == 'KG':
        return 'KN'
    if token.isdigit() and len(token) == 1:
        return f'0{token}'
    return token


def _grade_aliases(token: str) -> set[str]:
    if token in {'KN', 'KG'}:
        return {'KN', 'KG'}
    return {token}


def _next_year_grade(token: str) -> str | None:
    progression = {
        'TK': 'KN',
        'KN': '01',
        'KG': '01',
        '01': '02',
        '02': '03',
        '03': '04',
        '04': '05',
        '05': '06',
        '06': '07',
        '07': '08',
        '08': '09',
        '09': '10',
        '10': '11',
        '11': '12',
    }
    return progression.get(token)


def _school_gr_view_delegated_grades(user) -> set[str]:
    grade_levels = getattr(user, 'grade_levels', None)
    if not isinstance(grade_levels, list):
        return set()
    delegated: set[str] = set()
    for value in grade_levels:
        token = _normalize_grade_token(value)
        if token:
            delegated.update(_grade_aliases(token))
    return delegated


def _school_gr_view_student_keys_if_any(user) -> list[str]:
    # If a delegated user has a staff_id and schedule roster, limit to those students.
    staff_id = (getattr(user, 'staff_id', None) or '').strip()
    if not staff_id:
        return []
    valid_schools = School.objects.values('state_id')
    return list(
        Schedule.objects.filter(
            staff_employee_id=staff_id,
            state_school_id__in=valid_schools,
        )
        .values_list('stu_annual_key', flat=True)
        .distinct()
    )


def _teacher_placement_student_keys(user) -> list[str] | None:
    """Return teacher-accessible student keys for placement cards.

    Placement cards should include a teacher's elementary homeroom roster even
    when those rows are only keyed by staff email on the period-0 roster.
    """
    allowed_keys = user.get_accessible_student_keys() if hasattr(user, 'get_accessible_student_keys') else None
    if allowed_keys is None:
        return None

    valid_schools = School.objects.values('state_id')
    staff_id = (getattr(user, 'staff_id', None) or '').strip()
    email = (getattr(user, 'email', None) or '').strip()

    homeroom_filters = Q(period__in=['0', '00'], state_school_id__in=valid_schools)
    schedule_identity_filters = Q()
    homeroom_identity_filters = Q()
    if staff_id:
        schedule_identity_filters |= Q(staff_employee_id=staff_id)
    if email:
        schedule_identity_filters |= Q(staff_email__iexact=email)
        homeroom_identity_filters |= Q(staff_email__iexact=email)

    if schedule_identity_filters or homeroom_identity_filters:
        homeroom_keys = Schedule.objects.filter(homeroom_filters & schedule_identity_filters).values_list('stu_annual_key', flat=True)
        eht_keys = ElementaryHomeroomTeacher.objects.filter(homeroom_identity_filters, school_year__isnull=False).values_list('stu_annual_key', flat=True)
        return list({*allowed_keys, *homeroom_keys, *eht_keys})

    return list(allowed_keys)


def can_user_access_classroom_grade(user, classroom_grade: str | None) -> bool:
    role_names = _user_role_names(user)
    if user.is_superuser or 'Project Admin' in role_names or 'District Admin' in role_names:
        return True
    if 'School Admin' in role_names:
        return True
    if 'School Gr View' not in role_names:
        return False

    delegated_grades = _school_gr_view_delegated_grades(user)
    if not delegated_grades:
        return False

    # Placement uses current-year student scopes and next-year classrooms.
    # Example: delegated 01 (1st grade) can place into 02 classrooms.
    placement_target_grades: set[str] = set()
    for grade in delegated_grades:
        next_grade = _next_year_grade(grade)
        if next_grade:
            placement_target_grades.update(_grade_aliases(next_grade))

    classroom_grades = {
        _normalize_grade_token(value)
        for value in (classroom_grade or '').split(',')
        if _normalize_grade_token(value)
    }
    if not classroom_grades:
        return False
    return bool(classroom_grades & placement_target_grades)


def can_user_unassign_from_classroom_grade(user, classroom_grade: str | None) -> bool:
    role_names = _user_role_names(user)
    if user.is_superuser or 'Project Admin' in role_names or 'District Admin' in role_names:
        return True
    if 'School Admin' in role_names:
        return True
    if 'School Gr View' not in role_names:
        return False

    delegated_grades = _school_gr_view_delegated_grades(user)
    if not delegated_grades:
        return False

    # Unassign should allow delegated current-grade scope and next-year classroom scope.
    allowed_grades = set(delegated_grades)
    for grade in delegated_grades:
        next_grade = _next_year_grade(grade)
        if next_grade:
            allowed_grades.update(_grade_aliases(next_grade))

    classroom_grades = {
        _normalize_grade_token(value)
        for value in (classroom_grade or '').split(',')
        if _normalize_grade_token(value)
    }
    if not classroom_grades:
        return False
    return bool(classroom_grades & allowed_grades)


def _active_role_name(user) -> str | None:
    if hasattr(user, 'active_role') and user.active_role:
        return user.active_role.name
    role = user.roles.first() if hasattr(user, 'roles') else None
    return role.name if role else None


def _user_role_names(user) -> set[str]:
    # Prefer active role when present so role switching changes behavior predictably.
    if hasattr(user, 'active_role') and user.active_role:
        return {user.active_role.name}
    if hasattr(user, 'roles'):
        return set(user.roles.values_list('name', flat=True))
    return set()


def _is_unrestricted(user) -> bool:
    role_names = _user_role_names(user)
    return user.is_superuser or 'Project Admin' in role_names or 'District Admin' in role_names or any('District' in role for role in role_names)


def get_request_school(request: HttpRequest) -> School | None:
    user = request.user
    if hasattr(user, 'active_school') and user.active_school:
        return user.active_school
    user_schools = getattr(user, 'schools', None)
    if isinstance(user_schools, str) and user_schools:
        user_schools = [user_schools]
    if isinstance(user_schools, list) and user_schools:
        return School.objects.filter(state_id__in=user_schools).order_by('school_name').first()

    inferred_state_id = request.GET.get('school_state_id') or request.POST.get('school_state_id')
    if not inferred_state_id:
        inferred_state_id = (
            CurrentStudent.objects.exclude(school_state_id__isnull=True)
            .exclude(school_state_id='')
            .values_list('school_state_id', flat=True)
            .first()
        )
    if inferred_state_id:
        return School.objects.filter(state_id=inferred_state_id).first()
    return None


def get_accessible_students(
    request: HttpRequest,
    school_year: str | None = None,
    grades: Iterable[str] | None = None,
    for_placement: bool = False,
):
    """Return students accessible to the requesting user.

    ``for_placement=True`` skips the schedule-based student-key restriction for
    School Gr View users so they can see *all* students in their delegated grade
    level(s) when placing students between classrooms, not just their own roster.
    """
    user = request.user
    role_name = _active_role_name(user) or ''
    grade_values = set(grades or ELEMENTARY_GRADES)
    queryset = CurrentStudent.objects.filter(grade__in=grade_values)

    if school_year:
        queryset = queryset.filter(school_year=school_year)

    if not _is_unrestricted(user):
        if role_name == 'Teacher':
            # Teacher placement card access is already row-scoped by roster.
            # Do not blank the page because of stale or missing user.school
            # metadata; use the exact teacher roster instead.
            allowed_keys = _teacher_placement_student_keys(user)
            if allowed_keys is not None:
                queryset = queryset.filter(stu_annual_key__in=allowed_keys)
            else:
                queryset = queryset.none()
        else:
            school = get_request_school(request)
            if school:
                queryset = queryset.filter(school_state_id=school.state_id)
            elif isinstance(getattr(user, 'schools', None), list) and user.schools:
                queryset = queryset.filter(school_state_id__in=user.schools)
            else:
                queryset = queryset.none()

    if role_name == 'School Gr View':
        delegated_grades = _school_gr_view_delegated_grades(user)
        if delegated_grades:
            queryset = queryset.filter(grade__in=delegated_grades)

        # Only apply schedule-based restriction outside of placement contexts.
        # When placing students a School Gr View user must see every student in
        # their delegated grade, not just those on their personal schedule.
        if not for_placement:
            delegated_student_keys = _school_gr_view_student_keys_if_any(user)
            if delegated_student_keys:
                queryset = queryset.filter(stu_annual_key__in=delegated_student_keys)

    allowed_keys = _teacher_placement_student_keys(user) if role_name == 'Teacher' else (
        user.get_accessible_student_keys() if hasattr(user, 'get_accessible_student_keys') else None
    )
    if role_name == 'Teacher' and allowed_keys is not None:
        queryset = queryset.filter(stu_annual_key__in=allowed_keys)

    return queryset.only(
        'stu_annual_key',
        'ssid',
        'student_name',
        'grade',
        'school',
        'school_state_id',
        'school_year',
    ).order_by('student_name')


def can_user_manage_tags(user, school: School | None) -> bool:
    role_names = _user_role_names(user)
    if user.is_superuser or 'Project Admin' in role_names or 'District Admin' in role_names:
        return True
    if 'School Admin' not in role_names and 'School Gr View' not in role_names:
        return False
    if school is None:
        return False
    if isinstance(getattr(user, 'schools', None), list) and user.schools:
        return school.state_id in user.schools
    return False


def _user_has_disallowed_placement_grades(user, school: School | None) -> bool:
    restricted_qs = CurrentStudent.objects.filter(grade__in=DISALLOWED_PLACEMENT_GRADES)
    role_names = _user_role_names(user)

    def latest_year_for_queryset(queryset):
        return (
            queryset.exclude(school_year__isnull=True)
            .exclude(school_year='')
            .values_list('school_year', flat=True)
            .order_by('-school_year')
            .first()
        )

    if 'Teacher' in role_names:
        allowed_keys = _teacher_placement_student_keys(user)
        if not allowed_keys:
            return True
        teacher_scope = CurrentStudent.objects.filter(stu_annual_key__in=allowed_keys)
        latest_year = latest_year_for_queryset(teacher_scope)
        if latest_year:
            teacher_scope = teacher_scope.filter(school_year=latest_year)
        return teacher_scope.filter(grade__in=DISALLOWED_PLACEMENT_GRADES).exists()

    if school is not None:
        school_scope = CurrentStudent.objects.filter(school_state_id=school.state_id)
        latest_year = latest_year_for_queryset(school_scope)
        scoped_restricted = restricted_qs.filter(school_state_id=school.state_id)
        if latest_year:
            scoped_restricted = scoped_restricted.filter(school_year=latest_year)
        return scoped_restricted.exists()

    user_schools = getattr(user, 'schools', None)
    if isinstance(user_schools, list) and user_schools:
        for state_id in user_schools:
            school_scope = CurrentStudent.objects.filter(school_state_id=state_id)
            latest_year = latest_year_for_queryset(school_scope)
            scoped_restricted = restricted_qs.filter(school_state_id=state_id)
            if latest_year:
                scoped_restricted = scoped_restricted.filter(school_year=latest_year)
            if scoped_restricted.exists():
                return True
        return False

    return True


def can_user_access_placement_cards(user, school: School | None) -> bool:
    role_names = _user_role_names(user)
    if user.is_superuser or 'Project Admin' in role_names or 'District Admin' in role_names or any('District' in role for role in role_names):
        return True

    if 'Teacher' not in role_names and 'School Admin' not in role_names and 'School Gr View' not in role_names:
        return False

    if 'School Admin' in role_names and school is not None:
        if isinstance(getattr(user, 'schools', None), list) and user.schools and school.state_id not in user.schools:
            return False
        return _is_elementary_school_group(getattr(school, 'school_group', None))

    if 'School Admin' in role_names and school is None:
        user_schools = getattr(user, 'schools', None)
        if isinstance(user_schools, list) and user_schools:
            return School.objects.filter(state_id__in=user_schools).filter(school_group__iexact='Elementary School').exists()
        active_school = getattr(user, 'active_school', None)
        if active_school:
            return _is_elementary_school_group(getattr(active_school, 'school_group', None))
        return False

    if 'School Gr View' in role_names:
        prefs = getattr(user, 'preferences', None) or {}
        if not (prefs.get('can_move_placement_cards') or prefs.get('can_export_classroom_csv')):
            return False

        if school is None:
            user_schools = getattr(user, 'schools', None)
            if isinstance(user_schools, list) and user_schools:
                return School.objects.filter(state_id__in=user_schools).filter(school_group__iexact='Elementary School').exists()
            return False

        if isinstance(getattr(user, 'schools', None), list) and user.schools and school.state_id not in user.schools:
            return False
        if _is_elementary_school_group(getattr(school, 'school_group', None)):
            return True
        return not _user_has_disallowed_placement_grades(user, school)

    return not _user_has_disallowed_placement_grades(user, school)


def can_user_place_students(user, school: School | None) -> bool:
    """Can the user modify placements (move students, manage classrooms)?"""
    role_names = _user_role_names(user)
    if user.is_superuser or 'Project Admin' in role_names or 'District Admin' in role_names:
        return True
    if 'School Admin' not in role_names and 'School Gr View' not in role_names:
        return False
    if school is None:
        return False
    if isinstance(getattr(user, 'schools', None), list) and user.schools and school.state_id not in user.schools:
        return False
    if 'School Admin' in role_names:
        return _is_elementary_school_group(getattr(school, 'school_group', None))
    # School Gr View: require can_move_placement_cards preference
    prefs = getattr(user, 'preferences', None) or {}
    if not prefs.get('can_move_placement_cards'):
        return False
    # If the user has access to grades that are elementary, allow access.
    if _is_elementary_school_group(getattr(school, 'school_group', None)):
        return True
    return not _user_has_disallowed_placement_grades(user, school)


def can_user_configure_placements(user, school: School | None) -> bool:
    role_names = _user_role_names(user)
    if user.is_superuser or 'Project Admin' in role_names or 'District Admin' in role_names:
        return True
    if 'School Admin' not in role_names and 'School Gr View' not in role_names:
        return False
    if school is None:
        return False
    if isinstance(getattr(user, 'schools', None), list) and user.schools and school.state_id not in user.schools:
        return False

    if 'School Gr View' in role_names and 'School Admin' not in role_names:
        prefs = getattr(user, 'preferences', None) or {}
        if not prefs.get('can_manage_placement_configuration'):
            return False

    return _is_elementary_school_group(getattr(school, 'school_group', None))


def can_user_access_teacher_notes(user, school: School | None) -> bool:
    """Can the user access the Teacher Notes page and exports."""
    if not can_user_access_placement_cards(user, school):
        return False

    role_names = _user_role_names(user)
    if 'School Gr View' in role_names and 'School Admin' not in role_names:
        prefs = getattr(user, 'preferences', None) or {}
        return bool(prefs.get('can_access_teacher_notes'))

    return True


def placement_card_map(ssids: list[str], school_year: str):
    cards = (
        PlacementCard.objects.filter(ssid__in=ssids, school_year=school_year)
        .prefetch_related('tags')
        .only('id', 'ssid', 'school_year', 'is_complete', 'responses')
    )
    return {card.ssid: card for card in cards}


def tags_usage_for_school(school_id: int):
    cache_key = f'tag_usage:{school_id}'
    data = cache.get(cache_key)
    if data is not None:
        return data
    from placementcards.models import PlacementTag

    data = list(
        PlacementTag.objects.filter(school_id=school_id, is_active=True)
        .annotate(usage_count=Count('placement_cards', distinct=True))
        .values('id', 'name', 'color', 'usage_count')
        .order_by('display_order', 'name')
    )
    cache.set(cache_key, data, timeout=300)
    return data


def invalidate_school_caches(school_id: int):
    cache.delete(f'placement_questions:{school_id}')
    cache.delete(f'placement_tags:{school_id}')
    cache.delete(f'tag_usage:{school_id}')
