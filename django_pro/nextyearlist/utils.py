"""Shared permission and folder helpers for the Next Year List app.

Mirrors dataexploration/utils.py, scoped to nextyearlist models and roles.
"""
from django.db.models import Q
from dataexploration.models import ReportFolder


def get_user_roles(user):
    if hasattr(user, 'active_role') and user.active_role:
        return {user.active_role.name}
    return set(user.roles.values_list('name', flat=True))


def _user_has_access_to_user_school(user, user_school):
    if not user_school:
        return False
    if isinstance(user.schools, list):
        from studata.models import School as StudataSchool
        return StudataSchool.objects.filter(state_id__in=user.schools, school_name=user_school.name).exists()
    if hasattr(user.schools, 'all'):
        return user_school in user.schools.all()
    return False


def get_user_folders(user):
    """Return viewable ReportFolder queryset (shared with dataexploration)."""
    if not user.is_authenticated:
        return ReportFolder.objects.none()
    if user.is_superuser:
        return ReportFolder.objects.all()

    q_owner = Q(owner=user)
    q_shared = Q(shares__shared_with=user)
    q_district = Q(folder_type='DISTRICT')

    user_roles = get_user_roles(user)
    unrestricted_roles = {'District Admin', 'Project Admin', 'District View'}

    if user_roles & unrestricted_roles:
        q_school = Q(folder_type='SCHOOL')
    else:
        user_school_ids = []
        if isinstance(user.schools, list):
            from studata.models import School as StudataSchool
            from users.models import School as UserSchool
            studata_names = StudataSchool.objects.filter(state_id__in=user.schools).values_list('school_name', flat=True)
            user_school_ids = UserSchool.objects.filter(name__in=studata_names).values_list('id', flat=True)
        elif hasattr(user.schools, 'values_list'):
            user_school_ids = user.schools.values_list('id', flat=True)
        q_school = Q(folder_type='SCHOOL', school__id__in=user_school_ids)

    return ReportFolder.objects.filter(q_owner | q_shared | q_district | q_school).distinct()


def can_user_view_folder(user, folder):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if folder.owner == user:
        return True
    for share in folder.shares.all():
        if share.shared_with_id == user.id:
            return True
    if folder.folder_type == 'DISTRICT':
        return True
    if folder.folder_type == 'SCHOOL':
        user_roles = get_user_roles(user)
        unrestricted_roles = {'District Admin', 'Project Admin', 'District View'}
        if user_roles & unrestricted_roles:
            return True
        return _user_has_access_to_user_school(user, folder.school)
    return False


def can_user_edit_folder(user, folder):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if folder.folder_type == 'USER':
        if folder.owner == user:
            return True
        if folder.shares.filter(shared_with=user, can_edit=True).exists():
            return True
        return False
    user_roles = get_user_roles(user)
    if folder.folder_type == 'DISTRICT':
        if folder.parent is None:
            return bool({'Project Admin', 'District Admin'} & user_roles)
        if {'Project Admin', 'District Admin'} & user_roles:
            return True
        return folder.owner == user
    if folder.folder_type == 'SCHOOL':
        if {'Project Admin', 'District Admin'} & user_roles:
            return True
        has_school_access = folder.school and _user_has_access_to_user_school(user, folder.school)
        if not has_school_access:
            return False
        if 'School Admin' in user_roles:
            if folder.parent is None:
                return False
            return True
        return False
    return False


def can_create_in_folder(user, folder):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if folder.folder_type == 'USER' and folder.owner == user:
        return True
    user_roles = get_user_roles(user)
    if folder.folder_type == 'DISTRICT':
        if {'Project Admin', 'District Admin'} & user_roles:
            return True
        return False
    if folder.folder_type == 'SCHOOL':
        if {'Project Admin', 'District Admin'} & user_roles:
            return True
        if 'School Admin' in user_roles and _user_has_access_to_user_school(user, folder.school):
            return True
    return False


def can_user_view_query(user, query):
    from dataexploration.utils import can_user_view_query as _base
    return _base(user, query)


def can_user_manage_query(user, query):
    from dataexploration.utils import can_user_manage_query as _base
    return _base(user, query)


def can_user_manage_list(user, lst):
    from dataexploration.utils import can_user_manage_list as _base
    return _base(user, lst)
