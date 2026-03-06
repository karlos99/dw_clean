"""Role-based access helpers for the mathR app.

Mirrors patterns from placementcards.view_modules.card_data_utils but scoped
to middle-school (grade 08) math recommendation workflows.
"""
from __future__ import annotations

from django.http import HttpRequest

from studata.models import CurrentStudent, School


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


def is_unrestricted(user) -> bool:
    """Project Admin, District Admin, any District-level role."""
    role_names = _user_role_names(user)
    return (
        user.is_superuser
        or 'Project Admin' in role_names
        or 'District Admin' in role_names
        or any('District' in r for r in role_names)
    )


def is_school_admin(user) -> bool:
    if user.is_superuser:
        return True
    return 'School Admin' in _user_role_names(user)


def is_district_viewer(user) -> bool:
    """Can see completion dashboards (District Admin or District View)."""
    return is_unrestricted(user)


def can_edit_recommendation(user) -> bool:
    """Teachers, School Admins, District Admins, and Project Admins can edit."""
    role_names = _user_role_names(user)
    return (
        user.is_superuser
        or 'Project Admin' in role_names
        or 'District Admin' in role_names
        or 'School Admin' in role_names
        or 'Teacher' in role_names
    )


def get_request_school(request: HttpRequest) -> School | None:
    user = request.user
    if hasattr(user, 'active_school') and user.active_school:
        return user.active_school
    user_schools = getattr(user, 'schools', None)
    if isinstance(user_schools, str) and user_schools:
        user_schools = [user_schools]
    if isinstance(user_schools, list) and user_schools:
        return School.objects.filter(state_id__in=user_schools).order_by('school_name').first()
    return None


_GRADE_8 = {'08', '8'}


def can_user_access_mathr(request: HttpRequest) -> bool:
    """Return True if the user should see the mathr (8th-grade math) menu item.

    - Project/District Admins: always visible.
    - School Admin: visible only when their school is a Middle School.
    - Teacher: visible only when they have 8th-grade students.
    - All other roles: hidden.
    """
    user = request.user
    if not user.is_authenticated:
        return False

    role_names = _user_role_names(user)

    if user.is_superuser or is_unrestricted(user):
        return True

    if 'Teacher' not in role_names and 'School Admin' not in role_names:
        return False

    if 'School Admin' in role_names:
        school = get_request_school(request)
        if school is not None:
            return (school.school_group or '').strip().lower() == 'middle school'
        user_schools = getattr(user, 'schools', None)
        if isinstance(user_schools, list) and user_schools:
            return School.objects.filter(
                state_id__in=user_schools, school_group__iexact='Middle School'
            ).exists()
        active_school = getattr(user, 'active_school', None)
        if active_school:
            return (getattr(active_school, 'school_group', '') or '').strip().lower() == 'middle school'
        return False

    # Teacher: check whether they have any 8th-grade students.
    allowed_keys = user.get_accessible_student_keys() if hasattr(user, 'get_accessible_student_keys') else None
    if not allowed_keys:
        return False
    teacher_scope = CurrentStudent.objects.filter(stu_annual_key__in=allowed_keys)
    latest_year = (
        teacher_scope.exclude(school_year__isnull=True)
        .exclude(school_year='')
        .values_list('school_year', flat=True)
        .order_by('-school_year')
        .first()
    )
    if latest_year:
        teacher_scope = teacher_scope.filter(school_year=latest_year)
    return teacher_scope.filter(grade__in=_GRADE_8).exists()
