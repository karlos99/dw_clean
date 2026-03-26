from __future__ import annotations

from typing import Iterable

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import HttpRequest

from placementcards.models import PlacementCard
from studata.models import CurrentStudent, School


ELEMENTARY_GRADES = {'TK', 'KN', 'KG', '01', '1', '02', '2', '03', '3', '04', '4', '05', '5'}
DISALLOWED_PLACEMENT_GRADES = {'06', '6', '07', '7', '08', '8', '09', '9', '10', '11', '12', 'US'}


def _is_elementary_school_group(value: str | None) -> bool:
    return (value or '').strip().lower() == 'elementary school'


def _active_role_name(user) -> str | None:
    if hasattr(user, 'active_role') and user.active_role:
        return user.active_role.name
    role = user.roles.first() if hasattr(user, 'roles') else None
    return role.name if role else None


def _user_role_names(user) -> set[str]:
    names: set[str] = set()
    if hasattr(user, 'active_role') and user.active_role:
        names.add(user.active_role.name)
    if hasattr(user, 'roles'):
        names.update(user.roles.values_list('name', flat=True))
    return names


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
    apply_grade_restrictions: bool = True
):
    user = request.user
    grade_values = set(grades or ELEMENTARY_GRADES)
    queryset = CurrentStudent.objects.filter(grade__in=grade_values)

    if school_year:
        queryset = queryset.filter(school_year=school_year)

    if not _is_unrestricted(user):
        school = get_request_school(request)
        
        # Strict school validation for restricted users
        user_schools_raw = getattr(user, 'schools', []) or []
        user_schools = set(user_schools_raw) if isinstance(user_schools_raw, list) else {str(user_schools_raw)}
        
        # If user has explicit schools assigned, strictly filter to those
        if user_schools:
            if school and school.state_id not in user_schools:
                # If inferred school is not in allowed list, drop it (it might be from a dangerous fallback)
                school = None
            
            if school:
                queryset = queryset.filter(school_state_id=school.state_id)
            else:
                queryset = queryset.filter(school_state_id__in=user_schools)
        else:
            # Restricted user with no schools assigned should see nothing
            queryset = queryset.none()

    role_name = _active_role_name(user) or ''
    if apply_grade_restrictions:
        if role_name == 'School Gr View' and isinstance(getattr(user, 'grade_levels', None), list) and user.grade_levels:
            queryset = queryset.filter(grade__in=user.grade_levels)

    allowed_keys = user.get_accessible_student_keys() if hasattr(user, 'get_accessible_student_keys') else None
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
    active_role = _active_role_name(user)
    if active_role == 'School Gr View':
        return False

    if user.is_superuser or 'Project Admin' in role_names or 'District Admin' in role_names:
        return True
    if 'School Admin' not in role_names:
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
        allowed_keys = user.get_accessible_student_keys() if hasattr(user, 'get_accessible_student_keys') else None
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

    if 'School Gr View' in role_names:
        if school is None:
            return False
        if isinstance(getattr(user, 'schools', None), list) and user.schools and school.state_id not in user.schools:
            return False
        return not _user_has_disallowed_placement_grades(user, school)

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

    return not _user_has_disallowed_placement_grades(user, school)


def can_user_view_classrooms(user, school: School | None) -> bool:
    """Can the user view classroom / placement pages (menu + read access)?"""
    role_names = _user_role_names(user)
    
    # If the user is actively viewing as a Teacher, hide classroom/placement tools
    # They must switch to School Gr View or School Admin to see these.
    active_role = _active_role_name(user)
    if active_role == 'Teacher':
        return False

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
    # School Gr View: require can_move_placement_cards to see classrooms
    prefs = getattr(user, 'preferences', None) or {}
    if not prefs.get('can_move_placement_cards'):
        return False
    # If the user has access to grades that are elementary, allow access.
    # We check if explicitly disallowed grades are present at the school,
    # but for School Gr View, we should trust the grade_levels assignment.
    # Therefore, check school group instead.
    if _is_elementary_school_group(getattr(school, 'school_group', None)):
        return True
    
    return not _user_has_disallowed_placement_grades(user, school)


def can_user_place_students(user, school: School | None) -> bool:
    """Can the user modify placements (move students, manage classrooms)?"""
    role_names = _user_role_names(user)
    
    # If the user is actively viewing as a Teacher, hide classroom/placement tools
    active_role = _active_role_name(user)
    if active_role == 'Teacher':
        return False

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


def can_user_export_classrooms(user, school: School | None) -> bool:
    """Can the user export classroom CSVs?"""
    role_names = _user_role_names(user)
    active_role = _active_role_name(user)
    if active_role == 'Teacher':
        return False

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
    
    # School Gr View: require can_export_classroom_csv preference
    prefs = getattr(user, 'preferences', None) or {}
    if not prefs.get('can_export_classroom_csv'):
        return False
    
    if _is_elementary_school_group(getattr(school, 'school_group', None)):
        return True
    return not _user_has_disallowed_placement_grades(user, school)


def can_user_configure_placements(user, school: School | None) -> bool:
    """Can the user access the placement configuration page?"""
    active_role = _active_role_name(user)
    # School Gr View users are not allowed to configure placement settings
    if active_role == 'School Gr View':
        return False

    return can_user_place_students(user, school)


def get_user_grade_filter(user) -> list[str] | None:
    """Return grade_levels list for School Gr View users, None for unrestricted users."""
    role_name = _active_role_name(user) or ''
    if role_name == 'School Gr View' and isinstance(getattr(user, 'grade_levels', None), list) and user.grade_levels:
        return user.grade_levels
    return None


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
