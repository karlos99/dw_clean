"""Role-based access helpers for the mathR app.

Mirrors patterns from placementcards.view_modules.card_data_utils but scoped
to middle-school (grade 08) math recommendation workflows.
"""
from __future__ import annotations

from django.http import HttpRequest

from studata.models import School


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
