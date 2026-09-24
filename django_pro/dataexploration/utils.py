from django.db.models import Q
from .models import ReportFolder

def get_user_roles(user):
    """
    Get the effective roles for the user, respecting active_role context.
    """
    if hasattr(user, 'active_role') and user.active_role:
        return {user.active_role.name}
    return set(user.roles.values_list('name', flat=True))


def is_unrestricted(user):
    """Return True for superusers and District/Project-level roles.

    Respects the active-role context exactly like ``get_user_roles``: when the
    user has no active role selected (e.g. the "All Roles" switcher), the full
    role list is used, so a District Admin / District View user still sees all
    students instead of being restricted to their assigned schools.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    user_roles = get_user_roles(user)
    if user_roles & {'Project Admin', 'District Admin', 'District View'}:
        return True
    return any('District' in r for r in user_roles)

def _user_has_access_to_user_school(user, user_school):
    """
    Check if user has access to the given users.models.School object.
    Handles both legacy ManyToManyField and new JSONField (list of state_ids).
    """
    if not user_school:
        return False
        
    if isinstance(user.schools, list):
        # New JSONField storage: list of state_ids
        from studata.models import School as StudataSchool
        # Match by name since users.School lacks state_id
        return StudataSchool.objects.filter(state_id__in=user.schools, school_name=user_school.name).exists()
    
    if hasattr(user.schools, 'all'):
        # Legacy M2M
        return user_school in user.schools.all()
        
    return False

def get_user_folders(user):
    """
    Return queryset of folders a user can VIEW.
    """
    if not user.is_authenticated:
        return ReportFolder.objects.none()

    if user.is_superuser:
        return ReportFolder.objects.all()
        
    # User can view:
    # 1. Own folders (USER type, owner=user)
    # 2. District folders (DISTRICT type)
    # 3. School folders (SCHOOL type) if user.schools contains the folder.school
    
    # 1. My folders
    q_owner = Q(owner=user)
    
    # 2. Shared folders
    q_shared = Q(shares__shared_with=user)
    
    # 3. District folders
    q_district = Q(folder_type='DISTRICT')
    
    # 4. School folders
    user_roles = get_user_roles(user)
    unrestricted_roles = {'District Admin', 'Project Admin', 'District View'}
    
    if user_roles & unrestricted_roles:
         # Admins and District View can see all school folders
         q_school = Q(folder_type='SCHOOL')
    else:
        user_school_ids = []
        if isinstance(user.schools, list):
             # Map state_ids from user.schools to users.models.School ids (best effort)
             from studata.models import School as StudataSchool
             from users.models import School as UserSchool
             
             # Fetch school names from studata
             studata_names = StudataSchool.objects.filter(state_id__in=user.schools).values_list('school_name', flat=True)
             # Find matching UserSchools
             user_school_ids = UserSchool.objects.filter(name__in=studata_names).values_list('id', flat=True)
        elif hasattr(user.schools, 'values_list'):
             # Fallback for old ManyToManyField (if not fully migrated)
             user_school_ids = user.schools.values_list('id', flat=True)
             
        q_school = Q(folder_type='SCHOOL', school__id__in=user_school_ids)
    
    # Also check hierarchy: If can view parent, can view child? 
    # Usually yes, but sticking to basic scope for now.
    
    return ReportFolder.objects.filter(q_owner | q_shared | q_district | q_school).distinct()

def can_user_view_folder(user, folder):
    """
    Check if user can view the specific folder.
    This logic MUST match get_user_folders query logic.
    """
    if not user.is_authenticated:
        return False
        
    if user.is_superuser:
        return True
        
    # 1. Own folders
    if folder.owner == user:
        return True
        
    # 2. Shared folders (check prefetched shares if available to save DB hits)
    # Using .all() calculates on prefetched data if available
    for share in folder.shares.all():
        if share.shared_with_id == user.id:
            return True
            
    # 3. District folders - accessible to all authenticated users currently
    if folder.folder_type == 'DISTRICT':
        return True
        
    # 4. School folders
    if folder.folder_type == 'SCHOOL':
        user_roles = get_user_roles(user)
        unrestricted_roles = {'District Admin', 'Project Admin', 'District View'}
        
        if user_roles & unrestricted_roles:
            return True
            
        # Check if user has access to this specific school
        return _user_has_access_to_user_school(user, folder.school)
        
    # USER folders that are not owned and not shared are hidden
    return False


def can_user_edit_folder(user, folder):
    """
    Check if user can edit this folder (rename, change desc) OR add items to it.
    """
    if not user.is_authenticated:
        return False
        
    if user.is_superuser:
        return True
        
    if folder.folder_type == 'USER':
        if folder.owner == user:
            return True
        # Check if shared with edit permission
        if folder.shares.filter(shared_with=user, can_edit=True).exists():
            return True
        return False
        
    user_roles = get_user_roles(user)
    
    if folder.folder_type == 'DISTRICT':
        # Only admins can edit root district folder
        if folder.parent is None:
            return bool({'Project Admin', 'District Admin'} & user_roles)
        # Subfolders? Maybe department heads?
        if {'Project Admin', 'District Admin'} & user_roles:
            return True
        # If a non-USER district subfolder tracks an owner (creator), allow them to manage it.
        return folder.owner == user
        
    if folder.folder_type == 'SCHOOL':
        # Project Admin and District Admin can edit ALL school folders
        if {'Project Admin', 'District Admin'} & user_roles:
            return True

        # User must have access to the school to even consider editing
        has_school_access = folder.school and _user_has_access_to_user_school(user, folder.school)
        if not has_school_access:
            return False

        # School Admin can edit folders in their school
        if 'School Admin' in user_roles:
            # Cannot edit the root school folder (legacy name of the school)
            if folder.parent is None:
                return False
            return True

        # Non-admins (Teachers) cannot edit school folders
        return False
        
    return False

    return False

def can_create_in_folder(user, folder):
    """
    Check if user can create Query/List/Subfolder inside 'folder'.
    """
    if not user.is_authenticated:
        return False
        
    if user.is_superuser:
        return True

    # My Folder - always yes
    if folder.folder_type == 'USER' and folder.owner == user:
        return True
    
    # Check roles
    # user_roles = {r.name for r in user.roles.all()} 
    
    # Actually just:
    user_roles = get_user_roles(user)
    
    # District / School folders
    # "departments will be able to create department specific queries"
    # This implies standard users (or Dept heads) can create items in these shared folders.
    
    if folder.folder_type == 'DISTRICT':
        # Allow Project Admin and District Admin to create subfolders
        if {'Project Admin', 'District Admin'} & user_roles:
            return True
        # Allow others? Maybe not at root level.
        return False
        
    if folder.folder_type == 'SCHOOL':
        if {'Project Admin', 'District Admin'} & user_roles:
            return True
        # If user user belongs to school, allow them to create?
        if 'School Admin' in user_roles and _user_has_access_to_user_school(user, folder.school):
            return True # Allow school admin to create subfolders/queries
            
    return False


def can_user_view_query(user, query):
    """
    Check if user can view/access a saved query.
    Considers ownership, privacy settings, and direct shares.
    """
    if not user.is_authenticated:
        return False
        
    if user.is_superuser:
        return True
        
    # Owner can always view
    if query.owner == user:
        return True
        
    # Public queries are visible to all authenticated users
    if query.privacy == 'public':
        return True
        
    # Check if directly shared with user
    if query.shares.filter(shared_with=user).exists():
        return True
        
    # If query has a folder, check folder access
    if query.folder:
        # User can view if they have folder access
        user_folders = get_user_folders(user)
        if query.folder in user_folders:
            return True
    
    return False


def can_user_manage_query(user, query):
    """
    Check if user can edit/delete a saved query.
    """
    if not user.is_authenticated:
        return False
        
    if user.is_superuser:
        return True
        
    # Owner can always manage
    if query.owner == user:
        return True
        
    # Check if user has edit permission via QueryShare
    if query.shares.filter(shared_with=user, can_edit=True).exists():
        return True
        
    # Check if user has edit permission on the folder (if folder exists)
    if query.folder:
        return can_user_edit_folder(user, query.folder)
    
    return False


def can_user_manage_list(user, student_list):
    """
    Check if user can edit/delete a student list.
    """
    if not user.is_authenticated:
        return False
        
    if user.is_superuser:
        return True
        
    # Owner can always manage
    if student_list.owner == user:
        return True
        
    # Check if user has edit permission on the folder
    return can_user_edit_folder(user, student_list.folder)

SCHEDULE_FILTER_SCHOOL_ROLES = frozenset({
    'School Admin',
    'School Gr View',
    'School View',
    'Testing Coordinator',
})

SCHEDULE_FILTER_MODE_DISTRICT = 'district_full'
SCHEDULE_FILTER_MODE_SCHOOL = 'school_marks'
SCHEDULE_FILTER_MODE_SCHOOL_ELEM = 'school_elementary'
SCHEDULE_FILTER_MODE_TEACHER = 'teacher_schedule'
SCHEDULE_FILTER_MODE_TEACHER_ELEM = 'teacher_course'


def _is_elementary_school_group(value):
    return (value or '').strip().casefold() == 'elementary school'


def _teacher_only(user):
    if not user or not user.is_authenticated:
        return False
    roles = get_user_roles(user)
    return 'Teacher' in roles and not (roles & SCHEDULE_FILTER_SCHOOL_ROLES)


def _resolve_schedule_filter_school(user, column_filters=None):
    """Resolve a single School row for schedule UI (uses ``school`` table)."""
    from studata.models import School

    state_id = single_school_state_id_from_column_filters(column_filters)
    if state_id:
        row = School.objects.filter(state_id=state_id).first()
        if row:
            return row

    active = getattr(user, 'active_school', None)
    if active is not None:
        state_id = getattr(active, 'state_id', None)
        if state_id:
            row = School.objects.filter(state_id=state_id).first()
            if row:
                return row
        if getattr(active, 'pk', None):
            row = School.objects.filter(pk=active.pk).first()
            if row:
                return row

    if isinstance(column_filters, dict):
        names = column_filters.get('school')
        if isinstance(names, list):
            cleaned = [str(v).strip() for v in names if str(v).strip()]
            if len(cleaned) == 1:
                row = School.objects.filter(school_name=cleaned[0]).first()
                if row:
                    return row

    schools = getattr(user, 'schools', None)
    if isinstance(schools, list) and len(schools) == 1:
        return School.objects.filter(state_id=schools[0]).first()
    return None


def _school_role_schedule_mode(user, column_filters=None):
    """School-level roles: period for non-elementary schools only."""
    school = _resolve_schedule_filter_school(user, column_filters)
    if school and _is_elementary_school_group(getattr(school, 'school_group', None)):
        return SCHEDULE_FILTER_MODE_SCHOOL_ELEM
    schools = getattr(user, 'schools', None)
    if isinstance(schools, list) and len(schools) > 1 and not school:
        from studata.models import School
        groups = list(
            School.objects.filter(state_id__in=schools).values_list('school_group', flat=True)
        )
        non_empty = [g for g in groups if g]
        if non_empty and all(_is_elementary_school_group(g) for g in non_empty):
            return SCHEDULE_FILTER_MODE_SCHOOL_ELEM
    return SCHEDULE_FILTER_MODE_SCHOOL


def schedule_filter_ui_mode(user, column_filters=None):
    """Which schedule filter controls this user may use."""
    if is_unrestricted(user):
        # District/project admins must pick one school; then use that school's
        # schedule shape (elementary = no period filter, etc.) instead of always
        # district_full, which applies period cascades that empty options for ES.
        if not single_school_state_id_from_column_filters(column_filters):
            return SCHEDULE_FILTER_MODE_DISTRICT
        school = _resolve_schedule_filter_school(user, column_filters)
        if school and _is_elementary_school_group(getattr(school, 'school_group', None)):
            return SCHEDULE_FILTER_MODE_SCHOOL_ELEM
        return SCHEDULE_FILTER_MODE_SCHOOL
    if _teacher_only(user):
        school = _resolve_schedule_filter_school(user, column_filters)
        if school and _is_elementary_school_group(getattr(school, 'school_group', None)):
            return SCHEDULE_FILTER_MODE_TEACHER_ELEM
        return SCHEDULE_FILTER_MODE_TEACHER
    return _school_role_schedule_mode(user, column_filters)


def single_school_state_id_from_column_filters(column_filters):
    """When the School column filter pins exactly one school, return its state id."""
    if not isinstance(column_filters, dict):
        return None

    def _one(key):
        vals = column_filters.get(key)
        if not isinstance(vals, list):
            return None
        cleaned = [str(v).strip() for v in vals if str(v).strip() not in ('', 'null', 'None')]
        if len(cleaned) != 1:
            return None
        return cleaned[0]

    state_id = _one('school_state_id')
    if state_id:
        return state_id

    school_name = _one('school')
    if not school_name:
        return None

    from studata.models import School

    state_ids = list(
        School.objects.filter(school_name=school_name)
        .exclude(state_id__isnull=True)
        .exclude(state_id='')
        .values_list('state_id', flat=True)
        .distinct()[:2]
    )
    if len(state_ids) == 1:
        return state_ids[0]
    if len(state_ids) > 1:
        return None

    # ``current_student.school`` labels often differ from ``School.school_name``.
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT cs.school_state_id
            FROM current_student cs
            WHERE cs.school = %s
              AND cs.school_state_id IS NOT NULL
              AND TRIM(cs.school_state_id) <> ''
            LIMIT 2
            """,
            [school_name],
        )
        rows = [r[0] for r in cursor.fetchall() if r and r[0]]
    if len(rows) == 1:
        return rows[0]
    return None


def should_show_schedule_filters(user):
    """Show the schedule/marks filter bar for school-scoped Data Exploration users."""
    if not user or not user.is_authenticated:
        return False
    if getattr(user, 'active_school', None):
        return True
    roles = get_user_roles(user)
    if 'Teacher' in roles:
        return True
    if roles & SCHEDULE_FILTER_SCHOOL_ROLES:
        return True
    return False


def get_project_default_school_year():
    try:
        from landing.utils import get_project_config
        cfg = get_project_config()
        return (cfg.default_school_year or '').strip()
    except Exception:
        return ''


def is_schedule_filter_project_year(school_year):
    """True when the selected school year is the project default (aliases resolved)."""
    from .school_year_utils import resolve_school_year

    if not school_year:
        return False
    if isinstance(school_year, list):
        if len(school_year) != 1:
            return False
        school_year = school_year[0]
    resolved = resolve_school_year(str(school_year).strip())
    default = get_project_default_school_year()
    if not default or not resolved:
        return False
    return resolved == resolve_school_year(default)


def schedule_filters_eligible(user):
    """User may use schedule filters (bar in DOM), including district/project roles."""
    if should_show_schedule_filters(user):
        return True
    return is_unrestricted(user)


def schedule_filter_unrestricted_requires_school(user):
    """District/project roles must pin one school via column filter."""
    return is_unrestricted(user) and not should_show_schedule_filters(user)


def schedule_filter_toggle_visible(user, column_filters=None, school_year=None):
    """Show the schedule filter toggle and allow options / query filtering."""
    if not schedule_filters_eligible(user):
        return False
    if school_year is not None and not is_schedule_filter_project_year(school_year):
        return False
    if should_show_schedule_filters(user):
        return True
    if is_unrestricted(user):
        return bool(single_school_state_id_from_column_filters(column_filters))
    return False


def schedule_filter_bar_visible(user, column_filters=None, school_year=None):
    """Whether schedule filters may affect the student query or load options."""
    return schedule_filter_toggle_visible(user, column_filters, school_year)


def schedule_filter_staff_eligible(user):
    """User may see the Staff schedule filter (not teacher-only)."""
    if not user or not user.is_authenticated:
        return False
    if not schedule_filters_eligible(user):
        return False
    roles = get_user_roles(user)
    if 'Teacher' in roles and not (roles & SCHEDULE_FILTER_SCHOOL_ROLES):
        return False
    return True


def schedule_filter_show_staff(user, column_filters=None, school_year=None):
    """Staff dropdown visible in the UI for the current bar visibility state."""
    if not schedule_filter_staff_eligible(user):
        return False
    return schedule_filter_bar_visible(user, column_filters, school_year)


def _apply_de_school_filter(where_clauses, params, user):
    """Apply the active school context or enforce school permissions.

    District/Project-level roles see all students regardless of assigned
    schools or navbar school selection.
    """
    if is_unrestricted(user):
        return

    if hasattr(user, 'active_school') and user.active_school:
        where_clauses.append("cs.school_state_id = %s")
        params.append(user.active_school.state_id)
    elif hasattr(user, 'schools') and isinstance(user.schools, list) and user.schools:
        where_clauses.append("cs.school_state_id = ANY(%s)")
        params.append(user.schools)
    else:
        where_clauses.append("1=0")
